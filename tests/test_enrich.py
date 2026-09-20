import json
from datetime import timedelta

import pytest
from fakes import T0, FakeLLM, batch_response, entry, make_item

from pia.db import connect, store_items
from pia.llm.client import LLMError
from pia.llm.enrich import enrich_pending
from pia.llm.prompts import PROMPT_VERSION

HOUR = timedelta(hours=1)


@pytest.fixture
def conn():
    connection = connect(":memory:")
    yield connection
    connection.close()


def add_items(conn, count, **extra):
    store_items(conn, [make_item("a", n, T0 - HOUR, **extra) for n in range(count)], now=T0)


def statuses(conn) -> list[str]:
    return [r["status"] for r in conn.execute("SELECT status FROM items ORDER BY id")]


def all_ok(size):
    return batch_response(*[entry(i) for i in range(size)])


def test_enriches_items_and_persists_the_results_with_provenance(conn):
    add_items(conn, 2)
    llm = FakeLLM(batch_response(entry(0, "software", 4, "First."), entry(1, "other", 1, "Second.")))
    report = enrich_pending(conn, llm, now=T0, model="test-model")

    assert (report.enriched, report.remaining) == (2, 0)
    assert statuses(conn) == ["enriched", "enriched"]
    rows = conn.execute(
        "SELECT i.title, e.* FROM enrichments e JOIN items i ON i.id = e.item_id"
    ).fetchall()
    # Items are sent newest first, so index 0 is the last-inserted item ("a item 1").
    assert {r["title"]: (r["category"], r["importance"], r["summary"]) for r in rows} == {
        "a item 1": ("software", 4, "First."),
        "a item 0": ("other", 1, "Second."),
    }
    assert {(r["model"], r["prompt_version"]) for r in rows} == {("test-model", PROMPT_VERSION)}


def test_items_are_sent_in_batches(conn):
    add_items(conn, 25)
    llm = FakeLLM(all_ok(10), all_ok(10), all_ok(5))
    report = enrich_pending(conn, llm, now=T0, batch_size=10)
    assert len(llm.calls) == 3
    assert report.enriched == 25


def test_the_request_contains_item_details_and_treats_web_text_as_data(conn):
    store_items(
        conn,
        [make_item("hn", 1, T0 - HOUR, content="x" * 5000, signals={"points": 249})],
        now=T0,
    )
    llm = FakeLLM(all_ok(1))
    enrich_pending(conn, llm, now=T0)

    call = llm.calls[0]
    assert "untrusted" in call["system"].lower()
    payload = call["user"]
    assert "hn item 1" in payload and "249" in payload
    assert "x" * 5000 not in payload  # long content is truncated
    assert call["schema_name"] and call["schema"]["required"] == ["results"]


def test_only_unprocessed_items_are_sent(conn):
    add_items(conn, 3)
    conn.execute("UPDATE items SET status = 'enriched' WHERE id = 1")
    conn.execute("UPDATE items SET status = 'skipped' WHERE id = 2")
    llm = FakeLLM(all_ok(1))
    enrich_pending(conn, llm, now=T0)
    sent = json.loads(llm.calls[0]["user"].split("<items>")[1].split("</items>")[0])
    assert [i["title"] for i in sent] == ["a item 2"]  # only id 3, the untouched item


def test_invalid_duplicate_and_out_of_range_entries_are_dropped_and_left_pending(conn):
    add_items(conn, 4)
    llm = FakeLLM(
        batch_response(
            entry(0, "ai", 5, "Good."),
            entry(0, "ai", 1, "Duplicate index: first one wins."),
            entry(1, "ai", 7, "Importance out of range."),
            entry(2, "made_up_category", 3, "Bad category."),
            entry(99, "ai", 3, "No such item."),
            entry(3, "ai", 3, "   "),  # blank summary
        )
    )
    report = enrich_pending(conn, llm, now=T0)
    assert (report.enriched, report.remaining) == (1, 3)
    assert statuses(conn).count("enriched") == 1
    assert conn.execute("SELECT summary FROM enrichments").fetchone()["summary"] == "Good."


def test_a_failed_batch_leaves_its_items_pending_and_later_batches_still_run(conn):
    add_items(conn, 6)
    llm = FakeLLM(LLMError("boom"), all_ok(3))
    report = enrich_pending(conn, llm, now=T0, batch_size=3)
    assert (report.enriched, report.remaining, report.failed_batches) == (3, 3, 1)
    assert not report.stopped_early


def test_repeated_failures_stop_the_run_instead_of_hammering_the_api(conn):
    add_items(conn, 10)
    llm = FakeLLM(LLMError("rate limited"))
    report = enrich_pending(conn, llm, now=T0, batch_size=2, max_consecutive_failures=2)
    assert len(llm.calls) == 2  # gave up before batches 3, 4 and 5
    assert report.stopped_early and report.enriched == 0 and report.remaining == 10


def test_work_from_earlier_batches_survives_a_later_failure(conn):
    add_items(conn, 4)
    llm = FakeLLM(all_ok(2), LLMError("down"), LLMError("down"))
    enrich_pending(conn, llm, now=T0, batch_size=2, max_consecutive_failures=1)
    # Newest first: the first batch is the two highest ids, which succeeded.
    assert statuses(conn) == ["discovered", "discovered", "enriched", "enriched"]


def test_a_second_run_only_retries_what_is_still_pending(conn):
    add_items(conn, 3)
    enrich_pending(conn, FakeLLM(batch_response(entry(0))), now=T0)  # only 1 of 3 answered
    llm = FakeLLM(all_ok(2))
    enrich_pending(conn, llm, now=T0)
    assert len(json.loads(llm.calls[0]["user"].split("<items>")[1].split("</items>")[0])) == 2
    assert set(statuses(conn)) == {"enriched"}


def test_limit_caps_how_many_items_are_processed(conn):
    add_items(conn, 10)
    llm = FakeLLM(all_ok(3))
    report = enrich_pending(conn, llm, now=T0, limit=3)
    assert report.enriched == 3 and statuses(conn).count("discovered") == 7
