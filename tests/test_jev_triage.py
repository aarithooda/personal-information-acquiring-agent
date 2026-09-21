import json
import threading
import time
from types import SimpleNamespace

import pytest
from fakes import T0, FakeSource, SmartLLM, make_item

from pia.db import connect, store_items
from pia.jev import triage as jt
from pia.jev.client import SystemOneResponse
from pia.jev.triage import JevTriager, build_questions, build_state, derive, make_jev_triage
from pia.llm.client import LLMBadOutput, LLMUnavailable
from pia.profile import load_profile


# ---------- helpers: scripted Jev answers in the documented response shape ----------


def noul_a(p):
    return {"type": "noul", "noul": p}


def score_a(level, confidence=0.9, levels=4):
    probs = {str(i): 0.0 for i in range(levels)}
    probs[str(int(level))] = 1.0
    return {"type": "score", "score": float(level), "probabilities": probs, "confidence": confidence, "legend": {}}


def choice_a(name="ai", confidence=0.95):
    options = {"ai": 0.0, "software": 0.0, "research": 0.0, "other": 0.0}
    options[name] = 1.0
    return {"type": "choice", "choice": name, "probabilities": options, "confidence": confidence}


def answers(match=3, substance=2, low=0.1, exception=0.1, wildcard=0.1, build=0.5, too_little=0.1, category="ai", injection=0.05):
    return {
        "interest_match": score_a(match),
        "substance": score_a(substance),
        "low_value_pattern": noul_a(low),
        "low_value_exception": noul_a(exception),
        "wildcard": noul_a(wildcard),
        "buildable": noul_a(build),
        "too_little_info": noul_a(too_little),
        "category": choice_a(category),
        "injection": noul_a(injection),
    }


def response(**kwargs):
    return SystemOneResponse.model_validate(
        {"model": "jev-1.13.0", "answers": answers(**kwargs), "usage": {"input_tokens": 4400, "output_tokens": 183}}
    )


class FakeJev:
    """Stands in for JevClient. `script(item_title, call_number)` returns kwargs for response(), or raises."""

    def __init__(self, script=lambda title, n: {}, delay=0.0):
        self.script, self.delay = script, delay
        self.calls: list[dict] = []
        self.threads: set[str] = set()
        self._lock = threading.Lock()

    def system_one(self, state, questions):
        with self._lock:
            self.calls.append(state)
            n = len(self.calls)
            self.threads.add(threading.current_thread().name)
        if self.delay:
            time.sleep(self.delay)
        return response(**self.script(state["item"]["title"], n))


PROFILE_TOML = """
[core_interest_areas]
[[core_interest_areas.area]]
name = "AI agents"
priority = "very_high"
specific_subtopics = ["tool use"]
[low_value_information]
usually_low_value = ["Routine announcements"]
"""


@pytest.fixture
def profile(tmp_path):
    path = tmp_path / "interests.toml"
    path.write_text(PROFILE_TOML, encoding="utf-8")
    return load_profile(path)


@pytest.fixture
def conn():
    connection = connect(":memory:")
    yield connection
    connection.close()


def add_items(conn, n, **extra):
    store_items(conn, [make_item("hn", i, T0, **extra) for i in range(n)], now=T0)


def enrichment_rows(conn):
    return conn.execute("SELECT i.title, e.* FROM enrichments e JOIN items i ON i.id = e.item_id ORDER BY i.id").fetchall()


# ---------- combining answers into a relevance: pure, from the STORED form ----------


def stored(**kwargs):
    """The form persisted in enrichments.details: plain dicts, exactly what json round-trips."""
    return json.loads(json.dumps(answers(**kwargs)))


def test_a_strong_on_topic_substantive_item_is_top_ranked():
    d = derive(stored(match=3, substance=3, build=0.9, low=0.05))
    assert d.relevance >= 0.9 and d.importance == 5 and d.category == "ai"


def test_an_unrelated_empty_item_is_noise():
    d = derive(stored(match=0, substance=0, build=0.05, low=0.9, category="other"))
    assert d.relevance < 0.1 and d.importance == 1 and d.category == "other"


def test_relevance_is_monotonic_in_topical_match_and_stays_in_range():
    values = [derive(stored(match=m)).relevance for m in (0, 1, 2, 3)]
    assert values == sorted(values) and all(0 <= v <= 1 for v in values)


def test_a_low_value_pattern_lowers_relevance_unless_an_exception_applies():
    base = derive(stored(low=0.05)).relevance
    penalised = derive(stored(low=0.95, exception=0.05)).relevance
    excused = derive(stored(low=0.95, exception=0.95)).relevance
    assert penalised < base and excused > penalised and excused == pytest.approx(base, abs=0.1)


def test_a_strong_wildcard_can_stand_in_for_a_missing_topical_match():
    plain = derive(stored(match=0, substance=3, wildcard=0.05)).relevance
    wild = derive(stored(match=0, substance=3, wildcard=0.95)).relevance
    assert wild > plain + 0.2


def test_a_wildcard_is_not_filed_under_other_so_it_can_still_appear_in_a_section():
    d = derive(stored(match=0, substance=3, wildcard=0.95, build=0.6, category="other"))
    assert d.relevance >= 0.5 and d.category != "other"
    boring = derive(stored(match=0, substance=0, category="other"))
    assert boring.category == "other"


def test_only_a_near_certain_injection_is_zeroed_because_imperative_titles_look_like_instructions():
    """Found live: a plain imperative title ('Exfiltrate Your Weights') scored 0.74 on an early wording."""
    innocent = derive(stored(match=3, substance=3, injection=0.74))
    assert innocent.relevance > 0.8 and not innocent.flags
    hostile = derive(stored(match=3, substance=3, injection=0.97))
    assert hostile.relevance == 0 and hostile.importance == 1 and "possible_injection" in hostile.flags


def test_the_weights_add_up_to_one_so_relevance_stays_on_a_0_to_1_scale():
    assert jt.W_MATCH + jt.W_SUBSTANCE + jt.W_BUILD == pytest.approx(1.0)


def test_the_raw_answers_are_kept_as_features_for_later_analysis():
    d = derive(stored(too_little=0.92))
    assert d.features["too_little_info"] == pytest.approx(0.92)
    assert d.features["interest_match"] == pytest.approx(1.0)  # normalised to 0-1


# ---------- what is sent ----------


def test_the_question_set_is_the_documented_typed_shape():
    q = build_questions()
    assert list(q) == [
        "interest_match", "substance", "low_value_pattern", "low_value_exception", "wildcard",
        "buildable", "too_little_info", "category", "injection",
    ]  # fmt: skip
    assert q["interest_match"]["type"] == "score" and len(q["interest_match"]["criteria"]) == 4
    assert q["category"]["type"] == "choice" and set(q["category"]["criteria"]) == {"ai", "software", "research", "other"}
    assert all(q[k]["type"] == "noul" for k in ("low_value_pattern", "low_value_exception", "wildcard", "buildable", "too_little_info", "injection"))
    assert "AI assistant" in q["injection"]["instructions"]  # aimed at a model, not merely imperative


def test_the_state_holds_the_trimmed_profile_and_the_item_but_never_popularity_signals(profile):
    row = {"source": "hn", "title": "A title", "content_raw": None, "signals": '{"hn": {"points": 999}}'}
    state = build_state(profile.jev_state(), row)
    assert state["reader_profile"] == profile.jev_state()
    assert state["item"] == {"source": "Hacker News link", "title": "A title"}  # no text key when there is no text
    assert "999" not in json.dumps(state)  # popularity is the ranker's job, not the model's (avoids "popular but unrelated" bias)


def test_item_text_is_included_but_capped_and_known_sources_get_descriptive_labels(profile):
    row = {"source": "arxiv", "title": "T", "content_raw": "x" * 5000, "signals": "{}"}
    item = build_state(profile.jev_state(), row)["item"]
    assert item["source"] == "arXiv paper" and len(item["text"]) == 500
    assert build_state(profile.jev_state(), {**row, "source": "my_blog", "content_raw": "  "})["item"] == {"source": "my_blog", "title": "T"}


# ---------- the triager ----------


def test_each_item_is_scored_once_and_stored_with_provenance_and_raw_answers(conn, profile):
    add_items(conn, 3, content="Some text.")
    jev = FakeJev()
    report = JevTriager(jev, profile, workers=1).triage_pending(conn, T0)
    assert (report.enriched, report.remaining, report.failed_items) == (3, 0, 0)
    assert len(jev.calls) == 3  # one request per item: no batch to poison

    rows = enrichment_rows(conn)
    assert len(rows) == 3
    row = rows[0]
    assert (row["model"], row["prompt_version"], row["profile_hash"]) == ("jev-1.13.0", jt.QUESTION_SET_VERSION, profile.hash)
    assert row["summary"] == "" and row["category"] == "ai" and 1 <= row["importance"] <= 5
    assert 0 <= row["relevance"] <= 1
    details = json.loads(row["details"])
    assert details["usage"] == {"input_tokens": 4400, "output_tokens": 183}
    assert derive(details["answers"]).relevance == pytest.approx(row["relevance"])  # re-derivable offline, no API call
    assert {r["status"] for r in conn.execute("SELECT status FROM items")} == {"enriched"}


def test_the_stored_answers_alone_reproduce_the_score_so_the_formula_can_change_without_new_calls(conn, profile):
    add_items(conn, 1)
    JevTriager(FakeJev(lambda t, n: dict(match=2, substance=1, build=0.3)), profile, workers=1).triage_pending(conn, T0)
    row = enrichment_rows(conn)[0]
    assert derive(json.loads(row["details"])["answers"]).relevance == pytest.approx(row["relevance"])


def test_a_bad_answer_counts_against_that_item_only_and_the_others_still_finish(conn, profile):
    add_items(conn, 4)

    class Flaky(FakeJev):
        def system_one(self, state, questions):
            if state["item"]["title"] == "hn item 2":
                raise LLMBadOutput("rejected")
            return super().system_one(state, questions)

    report = JevTriager(Flaky(), profile, workers=1).triage_pending(conn, T0)
    assert report.enriched == 3 and not report.stopped_early
    attempts = {r["title"]: r["triage_attempts"] for r in conn.execute("SELECT title, triage_attempts FROM items")}
    assert attempts["hn item 2"] == 1 and sum(attempts.values()) == 1


def test_an_item_that_keeps_failing_is_given_up_on(conn, profile):
    add_items(conn, 1)

    class Broken(FakeJev):
        def system_one(self, state, questions):
            raise LLMBadOutput("always")

    for _ in range(3):
        report = JevTriager(Broken(), profile, workers=1).triage_pending(conn, T0)
    assert conn.execute("SELECT status FROM items").fetchone()[0] == "failed" and report.failed_items == 1


def test_repeated_outages_stop_the_run_and_are_never_held_against_items(conn, profile):
    add_items(conn, 10)

    class Down(FakeJev):
        def system_one(self, state, questions):
            with self._lock:
                self.calls.append(state)
            raise LLMUnavailable("Jev is down")

    jev = Down()
    report = JevTriager(jev, profile, workers=1, max_consecutive_failures=3).triage_pending(conn, T0)
    assert report.stopped_early and report.enriched == 0 and report.remaining == 10
    assert len(jev.calls) == 3  # gave up instead of hammering a service that is down
    assert conn.execute("SELECT SUM(triage_attempts) FROM items").fetchone()[0] == 0


def test_results_are_saved_as_they_arrive_so_a_crash_keeps_earlier_work(conn, profile):
    add_items(conn, 5)

    class Crashing(FakeJev):
        def system_one(self, state, questions):
            if len(self.calls) == 3:
                raise RuntimeError("process killed")
            return super().system_one(state, questions)

    with pytest.raises(RuntimeError):
        JevTriager(Crashing(), profile, workers=1).triage_pending(conn, T0)
    assert len(enrichment_rows(conn)) == 3  # the three finished before the crash are already stored


def test_concurrent_requests_all_finish_and_every_database_write_happens_on_one_thread(conn, profile):
    add_items(conn, 12)
    jev = FakeJev(delay=0.03)
    report = JevTriager(jev, profile, workers=4).triage_pending(conn, T0)  # sqlite would raise if used from another thread
    assert report.enriched == 12 and len(enrichment_rows(conn)) == 12
    assert len(jev.threads) > 1  # the requests really did overlap


def test_limit_caps_how_many_items_are_scored(conn, profile):
    add_items(conn, 6)
    assert JevTriager(FakeJev(), profile, workers=1).triage_pending(conn, T0, limit=2).enriched == 2


def test_nothing_pending_means_no_requests(conn, profile):
    jev = FakeJev()
    assert JevTriager(jev, profile, workers=1).triage_pending(conn, T0).enriched == 0 and jev.calls == []


# ---------- the fallback chain ----------


def test_when_jev_is_down_the_existing_llm_triage_takes_over(conn, profile):
    add_items(conn, 4, content="text")

    class Down(FakeJev):
        def system_one(self, state, questions):
            raise LLMUnavailable("down")

    triage = make_jev_triage(Down(), profile, fallback_llm=SmartLLM(), workers=1, max_consecutive_failures=2)
    report = triage(conn, T0)
    assert report.enriched == 4 and report.remaining == 0
    assert {r["model"] for r in enrichment_rows(conn)} == {"openai/gpt-oss-20b"}  # the legacy path did the work


def test_the_fallback_is_not_used_when_jev_works(conn, profile):
    add_items(conn, 3)
    llm = SmartLLM()
    make_jev_triage(FakeJev(), profile, fallback_llm=llm, workers=1)(conn, T0)
    assert llm.calls == []


def test_without_a_fallback_an_outage_leaves_the_items_pending_for_the_next_run(conn, profile):
    add_items(conn, 3)

    class Down(FakeJev):
        def system_one(self, state, questions):
            raise LLMUnavailable("down")

    report = make_jev_triage(Down(), profile, fallback_llm=None, workers=1, max_consecutive_failures=2)(conn, T0)
    assert report.stopped_early and report.remaining == 3
