"""The triager with question set v2: several requests per item, each with only the data it needs, stored as ONE result."""

import json

import pytest
from fakes import T0, make_item
from jevfakes import SyntheticJev
from test_jev_design import PROFILE

from pia.db import connect, store_items
from pia.jev import design as dz
from pia.jev import triage as jt
from pia.jev.triage import JevTriager, make_jev_triage
from pia.llm.client import LLMBadOutput, LLMUnavailable
from pia.profile import Profile


def make_profile():
    """The design tests' profile, as a Profile object (jev_state() turns core_interest_areas into interest_areas)."""
    data = {k: v for k, v in PROFILE.items() if k != "interest_areas"}
    data["core_interest_areas"] = {"area": PROFILE["interest_areas"]}
    return Profile(data=data, hash="p123")


@pytest.fixture
def profile():
    return make_profile()


@pytest.fixture
def conn():
    connection = connect(":memory:")
    yield connection
    connection.close()


def add_items(conn, n, **extra):
    store_items(conn, [make_item("hn", i, T0, **extra) for i in range(n)], now=T0)


def rows(conn):
    return conn.execute("SELECT i.title, e.* FROM enrichments e JOIN items i ON i.id = e.item_id ORDER BY i.id").fetchall()


def v2(jev, profile, **kw):
    return JevTriager(jev, profile, workers=kw.pop("workers", 1), design=dz.CURRENT_DESIGN, **kw)


# ---------- which design runs where ----------


def test_the_low_level_triager_defaults_to_the_legacy_single_request_design(conn, profile):
    """Existing callers (including the frozen benchmark tooling) construct JevTriager without a design and must get exactly v1."""
    add_items(conn, 1)
    jev = SyntheticJev()
    JevTriager(jev, profile, workers=1).triage_pending(conn, T0)
    assert len(jev.calls) == 1 and set(jev.calls[0]["questions"]) == set(jt.build_questions())
    assert rows(conn)[0]["prompt_version"] == "jev-triage-v1"


def test_the_production_factory_uses_the_current_design_unless_told_otherwise(conn, profile):
    add_items(conn, 1)
    make_jev_triage(SyntheticJev(), profile, workers=1)(conn, T0)
    assert rows(conn)[0]["prompt_version"] == dz.VERSION == "jev-triage-v2"
    other = connect(":memory:")
    add_items(other, 1)
    make_jev_triage(SyntheticJev(), profile, workers=1, design=jt.LEGACY_V1)(other, T0)
    assert other.execute("SELECT prompt_version FROM enrichments").fetchone()[0] == "jev-triage-v1"


def test_the_design_is_looked_up_by_the_version_stored_with_each_row():
    assert jt.design_for("jev-triage-v1") is jt.LEGACY_V1 and jt.design_for("jev-triage-v2") is dz.CURRENT_DESIGN
    with pytest.raises(KeyError, match="jev-triage-v9"):
        jt.design_for("jev-triage-v9")


def test_the_legacy_question_set_constant_is_unchanged_because_the_benchmark_tooling_records_it():
    assert jt.QUESTION_SET_VERSION == "jev-triage-v1"


# ---------- what is sent ----------


def test_every_item_takes_one_request_per_concern_and_only_the_item_request_omits_the_profile(conn, profile):
    add_items(conn, 3, content="Some text.")
    jev = SyntheticJev()
    v2(jev, profile).triage_pending(conn, T0)
    assert len(jev.calls) == 3 * 4
    item_only = [c for c in jev.calls if "reader_profile" not in c["state"]]
    assert len(item_only) == 3 and all(set(c["questions"]) == {"category", "injection", "subject_unclear"} for c in item_only)


def test_no_request_contains_prose_popularity_or_a_section_the_questions_do_not_use(conn, profile):
    store_items(conn, [make_item("hn", 1, T0, content="Text", signals={"points": 999})], now=T0)
    jev = SyntheticJev()
    v2(jev, profile).triage_pending(conn, T0)
    sent = json.dumps(jev.calls)
    assert "999" not in sent and "points" not in sent and "prose that should never be sent" not in sent


def test_a_title_only_item_is_sent_without_a_text_key(conn, profile):
    add_items(conn, 1)
    jev = SyntheticJev()
    v2(jev, profile).triage_pending(conn, T0)
    assert all("text" not in c["state"]["item"] for c in jev.calls)


# ---------- what is stored ----------


def test_the_result_is_one_row_with_every_raw_answer_the_structure_and_the_facts(conn, profile):
    add_items(conn, 2, content="Some text.")
    v2(SyntheticJev(input_tokens=1000), profile).triage_pending(conn, T0)
    assert len(rows(conn)) == 2
    row = rows(conn)[0]
    details = json.loads(row["details"])
    assert (row["model"], row["prompt_version"], row["profile_hash"], row["summary"]) == ("jev-1.13.0", "jev-triage-v2", "p123", "")
    assert details["question_set"] == "jev-triage-v2"
    expected_ids = {qid for r in dz.CURRENT_DESIGN.plan(profile.jev_state()).requests for qid in r.questions}
    assert set(details["answers"]) == expected_ids
    assert details["usage"] == {"input_tokens": 4000, "output_tokens": 40}  # four requests, summed
    assert [r["name"] for r in details["requests"]] == ["item", "interest", "value", "discovery"]
    assert details["plan"]["tiers"] == ["tier_1", "tier_2", "tier_3"] and details["facts"] == {"title_only": False}
    assert {"reach", "low_value", "injection"} <= set(details["features"]) and "evidence" in details


def test_the_stored_row_alone_reproduces_its_score_through_the_version_it_names(conn, profile):
    add_items(conn, 1, content="Text")
    v2(SyntheticJev(choice={"priority_tier": {"tier_2": 0.7, "none": 0.3}}, noul={"signal.": 0.6}), profile).triage_pending(conn, T0)
    row = rows(conn)[0]
    details = json.loads(row["details"])
    again = jt.design_for(details["question_set"]).derive(details["answers"], details["plan"], details["facts"])
    assert again.relevance == pytest.approx(row["relevance"]) and again.importance == row["importance"] and again.category == row["category"]


def test_title_only_is_recorded_as_a_fact_and_does_not_change_the_score(conn, profile):
    store_items(conn, [make_item("hn", 1, T0, content="Text"), make_item("hn", 2, T0)], now=T0)
    v2(SyntheticJev(choice={"priority_tier": {"tier_1": 1.0}}, noul={"signal.": 0.5}), profile).triage_pending(conn, T0)
    by_title = {r["title"]: r for r in rows(conn)}
    facts = {t: json.loads(r["details"])["facts"]["title_only"] for t, r in by_title.items()}
    assert facts == {"hn item 1": False, "hn item 2": True}
    assert by_title["hn item 1"]["relevance"] == pytest.approx(by_title["hn item 2"]["relevance"])


def test_models_that_differ_between_requests_are_both_kept(conn, profile):
    add_items(conn, 1)

    class Rolling(SyntheticJev):
        def system_one(self, state, questions):
            self.model = "jev-1.14.0" if len(self.calls) >= 2 else "jev-1.13.0"  # an alias moved in the middle of an item
            return super().system_one(state, questions)

    v2(Rolling(), profile).triage_pending(conn, T0)
    row = rows(conn)[0]
    assert row["model"] == "jev-1.13.0+jev-1.14.0" and json.loads(row["details"])["models"] == ["jev-1.13.0", "jev-1.14.0"]


# ---------- failure handling ----------


def test_an_item_is_stored_only_when_every_request_succeeded_and_an_outage_is_not_held_against_it(conn, profile):
    add_items(conn, 2)
    report = v2(SyntheticJev(fail=lambda n: LLMUnavailable("down") if n == 3 else None), profile).triage_pending(conn, T0)
    assert (report.enriched, report.failed_batches, report.remaining) == (1, 1, 1)
    assert len(rows(conn)) == 1  # never a half-answered item
    assert conn.execute("SELECT MAX(triage_attempts) FROM items").fetchone()[0] == 0


def test_a_rejected_request_counts_against_that_item_only(conn, profile):
    add_items(conn, 3)  # processed newest first: item 2, then item 1, then item 0; request 5 opens the second one
    report = v2(SyntheticJev(fail=lambda n: LLMBadOutput("rejected") if n == 5 else None), profile).triage_pending(conn, T0)
    assert report.enriched == 2
    attempts = {r["title"]: r["triage_attempts"] for r in conn.execute("SELECT title, triage_attempts FROM items")}
    assert attempts == {"hn item 0": 0, "hn item 1": 1, "hn item 2": 0}


def test_the_circuit_breaker_still_stops_spending_after_consecutive_outages(conn, profile):
    add_items(conn, 20)
    jev = SyntheticJev(fail=lambda n: LLMUnavailable("down"))
    report = v2(jev, profile, max_consecutive_failures=3).triage_pending(conn, T0)
    assert report.stopped_early and report.enriched == 0
    assert len(jev.calls) == 3  # each item fails on its first request; after three in a row nothing more is sent


def test_a_profile_that_cannot_support_the_design_is_refused_at_construction():
    with pytest.raises(dz.DesignError):
        JevTriager(SyntheticJev(), Profile(data={"low_value_information": {"usually_low_value": ["x"]}}, hash="h"), design=dz.CURRENT_DESIGN)
