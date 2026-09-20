import json
from datetime import timedelta

import pytest
from fakes import T0, FakeLLM, SmartLLM, batch_response, entry, make_item

from pia.db import connect, store_items
from pia.llm.client import LLMBadOutput, LLMError, LLMUnavailable
from pia.llm.enrich import enrich_pending
from pia.llm.prompts import PROMPT_VERSION
from pia.state import enriched_items

HOUR = timedelta(hours=1)


@pytest.fixture
def conn():
    connection = connect(":memory:")
    yield connection
    connection.close()


def add_items(conn, count, **extra):
    extra.setdefault("content", "Some article text.")  # pass content=None for title-only items
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


# ---------- items with no text: never keep a summary the model had to invent ----------


def test_title_only_items_get_an_empty_summary_even_if_the_model_invents_one(conn):
    store_items(
        conn,
        [make_item("a", 0, T0 - HOUR, content="A real abstract."), make_item("a", 1, T0 - 2 * HOUR)],
        now=T0,
    )
    llm = FakeLLM(batch_response(entry(0, summary="Grounded."), entry(1, summary="Invented from the title.")))
    enrich_pending(conn, llm, now=T0)
    rows = conn.execute("SELECT i.title, e.summary FROM enrichments e JOIN items i ON i.id = e.item_id").fetchall()
    assert {r["title"]: r["summary"] for r in rows} == {"a item 0": "Grounded.", "a item 1": ""}


def test_a_blank_summary_is_accepted_for_title_only_items(conn):
    store_items(conn, [make_item("a", 0, T0 - HOUR, content="   ")], now=T0)  # whitespace counts as no text
    report = enrich_pending(conn, FakeLLM(batch_response(entry(0, summary=""))), now=T0)
    assert report.enriched == 1


def test_the_prompt_tells_the_model_what_to_do_when_there_is_no_text(conn):
    add_items(conn, 1, content=None)
    llm = FakeLLM(all_ok(1))
    enrich_pending(conn, llm, now=T0)
    assert "no text" in llm.calls[0]["system"].lower()


# ---------- poison items: isolate them, never let them starve the rest ----------


def attempts(conn) -> dict[str, int]:
    return {r["title"]: r["triage_attempts"] for r in conn.execute("SELECT title, triage_attempts FROM items")}


def test_one_poison_item_does_not_stop_the_others_from_being_triaged(conn):
    add_items(conn, 10)
    llm = SmartLLM(poison_titles={"a item 3"})
    report = enrich_pending(conn, llm, now=T0, batch_size=10)
    assert report.enriched == 9 and not report.stopped_early
    assert statuses(conn).count("enriched") == 9
    assert attempts(conn)["a item 3"] == 1
    assert len(llm.calls) <= 9  # found by splitting the batch, not by trying each item alone


def test_a_poison_item_at_the_front_no_longer_starves_everything_behind_it(conn):
    add_items(conn, 30)
    newest = conn.execute("SELECT title FROM items ORDER BY id DESC LIMIT 1").fetchone()["title"]
    report = enrich_pending(conn, SmartLLM(poison_titles={newest}), now=T0, batch_size=10)
    assert report.enriched == 29


def test_an_item_the_model_keeps_failing_on_is_eventually_given_up_on(conn):
    add_items(conn, 3)
    for _ in range(3):
        enrich_pending(conn, SmartLLM(poison_titles={"a item 1"}), now=T0)
    assert conn.execute("SELECT status FROM items WHERE title = 'a item 1'").fetchone()["status"] == "failed"
    llm = SmartLLM()
    enrich_pending(conn, llm, now=T0)
    assert llm.calls == []  # nothing left to try: failed items are not retried


def test_an_outage_is_never_held_against_the_items(conn):
    add_items(conn, 4)
    enrich_pending(conn, FakeLLM(LLMUnavailable("down")), now=T0, batch_size=2)
    assert set(attempts(conn).values()) == {0}


def test_items_the_model_silently_skips_are_counted_too(conn):
    add_items(conn, 3)
    enrich_pending(conn, FakeLLM(batch_response(entry(0))), now=T0)  # answers 1 of 3
    counts = sorted(attempts(conn).values())
    assert counts == [0, 1, 1]


def test_the_failure_is_reported(conn):
    add_items(conn, 1)
    for _ in range(3):
        report = enrich_pending(conn, FakeLLM(LLMBadOutput("cut off")), now=T0)
    assert report.failed_items == 1 and report.remaining == 0


# ---------- changing the prompt must not strand work done under the old one ----------


def test_items_triaged_under_an_older_prompt_version_are_still_briefable(conn):
    add_items(conn, 1)
    enrich_pending(conn, FakeLLM(all_ok(1)), now=T0)
    conn.execute("UPDATE enrichments SET prompt_version = 'stage1-old'")
    assert [r["title"] for r in enriched_items(conn)] == ["a item 0"]


def test_when_an_item_has_several_enrichments_the_latest_one_is_used(conn):
    add_items(conn, 1)
    enrich_pending(conn, FakeLLM(batch_response(entry(0, "ai", 2, "Old view."))), now=T0)
    conn.execute("UPDATE enrichments SET prompt_version = 'stage1-old'")
    conn.execute(
        "INSERT INTO enrichments (item_id, model, prompt_version, category, importance, summary, created_at) "
        "VALUES (1, 'm', 'stage1-new', 'software', 4, 'New view.', '2026-09-21T00:00:00+00:00')"
    )
    (row,) = enriched_items(conn)
    assert (row["category"], row["importance"], row["summary"]) == ("software", 4, "New view.")
