"""Jev as Stage 1 and the profile in Stage 2, wired through the UNCHANGED pipeline."""

from datetime import timedelta

import pytest
from fakes import T0, FakeLLM, FakeSource, SmartLLM, make_item, triaged_row
from jevfakes import V2_PROFILE_TOML, SyntheticJev

from pia.briefing.curate import make_curator
from pia.db import connect
from pia.jev.triage import make_jev_triage
from pia.llm.headlines import select_headlines
from pia.llm.prompts import STAGE2_SYSTEM, stage2_system
from pia.pipeline import run_briefing
from pia.profile import load_profile

HOUR = timedelta(hours=1)
STRONG = {7, 13, 21, 22}  # the four items Jev should love


@pytest.fixture
def profile(tmp_path):
    path = tmp_path / "interests.toml"
    path.write_text(V2_PROFILE_TOML, encoding="utf-8")
    return load_profile(path)


@pytest.fixture
def conn():
    connection = connect(":memory:")
    yield connection
    connection.close()


def script(title):
    n = int(title.split()[-1])
    if n in STRONG:
        return {"choice": {"priority_tier": {"tier_1": 1.0}}, "noul": {"signal.": 0.9}}
    return {"choice": {"priority_tier": {"tier_3": 1.0}}}  # a weak but not worthless fit: kept out of the top, not dropped as noise


def briefing(conn, profile, llm, count=30):
    source = FakeSource("hn", [make_item("hn", n, T0 - HOUR, content=f"Text {n}") for n in range(count)])
    triage = make_jev_triage(SyntheticJev(script=script), profile, workers=2)
    return run_briefing(conn, None, [source], T0, prepare=make_curator(llm, triage=triage, profile=profile))


# ---------- the editor prompt ----------


def test_without_a_profile_the_editor_prompt_is_exactly_what_it_was():
    assert stage2_system(None) == STAGE2_SYSTEM
    assert "READER PROFILE" not in STAGE2_SYSTEM


def test_with_a_profile_the_editor_reads_it_and_is_told_to_tie_the_explanation_to_it(profile):
    system = stage2_system(profile.llm_text())
    assert "READER PROFILE" in system and "AI agents" in system and "Routine announcements" in system
    assert "untrusted" in system.lower()  # the injection stance is kept
    assert "which of the reader's interests" in system
    assert "You receive CANDIDATES" in system  # the rest of the instructions are still there


def test_select_headlines_sends_the_profile_only_when_given_one(profile):
    llm = FakeLLM({"headlines": [{"index": 0, "explanation": "E.", "why_it_matters": "W."}]})
    select_headlines(llm, [triaged_row(1)], 1, profile_text=profile.llm_text())
    assert "READER PROFILE" in llm.calls[0]["system"]
    llm2 = FakeLLM({"headlines": [{"index": 0, "explanation": "E.", "why_it_matters": "W."}]})
    select_headlines(llm2, [triaged_row(1)], 1)
    assert llm2.calls[0]["system"] == STAGE2_SYSTEM


# ---------- the pipeline with Jev at Stage 1 ----------


def test_stage_one_is_jev_and_the_llm_is_only_asked_to_edit(conn, profile):
    llm = SmartLLM()
    briefing(conn, profile, llm)
    assert {c["schema_name"] for c in llm.calls} == {"editor_picks"}  # no LLM triage call at all


def test_the_shortlist_is_ranked_by_jevs_relevance_so_the_editor_sees_the_best_first(conn, profile):
    llm = SmartLLM()  # its editor picks the candidates in the order it is shown them
    result = briefing(conn, profile, llm)
    headlines = {row["title"] for row in result.items[:4]}
    assert headlines == {f"hn item {n}" for n in STRONG}


def test_the_editor_receives_the_profile_in_the_pipeline(conn, profile):
    llm = SmartLLM()
    briefing(conn, profile, llm)
    assert "READER PROFILE" in llm.calls[-1]["system"] and "AI agents" in llm.calls[-1]["system"]


def test_extras_are_bare_links_because_jev_writes_no_summaries(conn, profile):
    result = briefing(conn, profile, SmartLLM(editor_take=1))
    extra_lines = [line for line in result.markdown.splitlines() if line.startswith("- [")]
    assert extra_lines and all(": " not in line.split("](", 1)[1] for line in extra_lines)  # no description after the link


def test_the_briefing_still_respects_the_time_based_budget_and_settles_every_item(conn, profile):
    result = briefing(conn, profile, SmartLLM())
    headlines = [line for line in result.markdown.splitlines() if line.startswith("### ")]
    assert len(headlines) == 4  # first run counts as 3 days -> floor(1.5 x 3) slots, unchanged
    statuses = {r[0]: r[1] for r in conn.execute("SELECT status, COUNT(*) FROM items GROUP BY status")}
    assert set(statuses) == {"briefed", "skipped"} and sum(statuses.values()) == 30


def test_the_old_llm_triage_path_is_untouched_when_no_triage_or_profile_is_given(conn):
    llm = SmartLLM()
    source = FakeSource("hn", [make_item("hn", n, T0 - HOUR, content="Text") for n in range(6)])
    run_briefing(conn, None, [source], T0, prepare=make_curator(llm))
    assert {c["schema_name"] for c in llm.calls} == {"triage_results", "editor_picks"}
    assert "READER PROFILE" not in [c for c in llm.calls if c["schema_name"] == "editor_picks"][0]["system"]
