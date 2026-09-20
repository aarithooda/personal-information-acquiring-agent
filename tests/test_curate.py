"""End to end: collect -> triage -> rank -> editor -> render -> commit, with fake sources and a fake LLM."""

from datetime import timedelta

import pytest
from fakes import T0, FakeSource, SmartLLM, make_item

from pia.briefing.curate import EnrichmentFailed, make_curator
from pia.db import connect
from pia.llm.client import LLMError
from pia.pipeline import run_briefing
from pia.state import get_checkpoint

HOUR = timedelta(hours=1)


def day(n):
    return T0 + timedelta(days=n)


@pytest.fixture
def conn():
    connection = connect(":memory:")
    yield connection
    connection.close()


def items(count, published, source="a", start=0, **extra):
    return [make_item(source, start + n, published, content=f"Content {n}", **extra) for n in range(count)]


def brief(conn, source, llm, now):
    return run_briefing(conn, None, [source], now, prepare=make_curator(llm))


def start_at_day_zero(conn, source, llm=None):
    """Establish a checkpoint at T0 so the next briefing covers a known number of days."""
    return brief(conn, source, llm or SmartLLM(), T0)


def statuses(conn) -> dict[str, int]:
    rows = conn.execute("SELECT status, COUNT(*) FROM items GROUP BY status").fetchall()
    return {r[0]: r[1] for r in rows}


def headline_count(markdown: str) -> int:
    return sum(line.startswith("### ") for line in markdown.splitlines())


def test_after_one_day_only_one_development_is_a_headline(conn):
    source = FakeSource("a")
    start_at_day_zero(conn, source)
    source.items = items(20, day(0) + HOUR)
    result = brief(conn, source, SmartLLM(), day(1))
    assert headline_count(result.markdown) == 1


def test_after_ten_days_up_to_fifteen_developments_are_headlines(conn):
    source = FakeSource("a")
    start_at_day_zero(conn, source)
    source.items = items(60, day(5))
    llm = SmartLLM()
    result = brief(conn, source, llm, day(10))
    assert headline_count(result.markdown) == 15
    assert "at most 15" in llm.calls[-1]["user"]


def test_the_budget_is_a_ceiling_not_a_quota(conn):
    """A quiet period: the editor finds only 2 worthwhile developments out of 15 slots."""
    source = FakeSource("a")
    start_at_day_zero(conn, source)
    source.items = items(30, day(5))
    result = brief(conn, source, SmartLLM(editor_take=2), day(10))
    assert headline_count(result.markdown) == 2


def test_the_list_of_extras_scales_with_time_too_and_never_repeats_a_headline(conn):
    source = FakeSource("a")
    start_at_day_zero(conn, source)
    source.items = items(100, day(5))
    result = brief(conn, source, SmartLLM(), day(10))
    titles = [row["title"] for row in result.items]
    assert len(titles) == len(set(titles))
    assert len(result.items) == 15 + 2 * 15  # headlines + one-line extras


def test_irrelevant_and_noise_items_are_never_shown_and_become_skipped(conn):
    source = FakeSource("a", items(4, T0 - HOUR))
    scores = {
        "a item 0": ("ai", 4),
        "a item 1": ("other", 5),  # irrelevant however 'important' it claims to be
        "a item 2": ("software", 1),  # noise by definition of the rubric
        "a item 3": ("research", 3),
    }
    result = brief(conn, source, SmartLLM(scores=scores), T0)
    shown = {row["title"] for row in result.items}
    assert shown == {"a item 0", "a item 3"}
    assert statuses(conn) == {"briefed": 2, "skipped": 2}


def test_shown_items_are_briefed_and_everything_else_considered_is_skipped(conn):
    source = FakeSource("a", items(50, T0 - HOUR))
    result = brief(conn, source, SmartLLM(), T0)  # first run: 3 days -> 4 headlines + 8 extras
    assert len(result.items) == 12
    assert statuses(conn) == {"briefed": 12, "skipped": 38}


def test_opening_again_shows_nothing_and_asks_the_llm_nothing(conn):
    source = FakeSource("a", items(5, T0 - HOUR))
    brief(conn, source, SmartLLM(), T0)
    llm = SmartLLM()
    result = brief(conn, source, llm, T0 + HOUR)
    assert result.items == [] and llm.calls == []
    assert "nothing new" in result.markdown.lower()


def test_extras_are_grouped_under_the_category_sections_from_the_brief(conn):
    source = FakeSource("a", items(6, T0 - HOUR))
    scores = {
        "a item 0": ("ai", 5), "a item 1": ("ai", 4), "a item 2": ("ai", 3),
        "a item 3": ("software", 3), "a item 4": ("research", 3), "a item 5": ("research", 2),
    }  # fmt: skip
    md = brief(conn, source, SmartLLM(scores=scores, editor_take=1), T0).markdown
    assert "## 🔥 Worth knowing" in md
    assert "## 🤖 AI & Agents" in md and "## 💻 Software" in md and "## 🔬 Research" in md
    assert md.index("AI & Agents") < md.index("Software") < md.index("Research")
    assert "**Why it matters:**" in md
    assert "https://example.com/a/" in md


def test_if_the_editor_fails_we_still_ship_a_ranked_briefing_and_say_so(conn):
    source = FakeSource("a", items(10, T0 - HOUR))
    result = brief(conn, source, SmartLLM(editor_error=LLMError("editor down")), T0)
    assert headline_count(result.markdown) == 4  # first run counts as 3 days -> 4 slots
    assert "automatic" in result.markdown.lower()
    assert get_checkpoint(conn) == T0  # the briefing was still committed


def test_if_triage_fails_completely_nothing_is_committed(conn):
    source = FakeSource("a", items(5, T0 - HOUR))
    with pytest.raises(EnrichmentFailed):
        brief(conn, source, SmartLLM(triage_error=LLMError("groq down")), T0)
    assert get_checkpoint(conn) is None
    assert conn.execute("SELECT COUNT(*) FROM briefings").fetchone()[0] == 0
    assert statuses(conn) == {"discovered": 5}


def test_a_partially_failed_triage_still_briefs_and_leaves_the_rest_for_next_time(conn):
    source = FakeSource("a", items(15, T0 - HOUR))
    llm = SmartLLM(triage_error=LLMError("rate limited"), triage_ok_calls=1)  # first batch of 10 only
    result = brief(conn, source, llm, T0)
    assert "5 item" in result.markdown and "next time" in result.markdown.lower()
    assert statuses(conn)["discovered"] == 5

    later = brief(conn, source, SmartLLM(), T0 + HOUR)  # the leftovers are picked up next run
    assert statuses(conn).get("discovered", 0) == 0
    assert len(later.items) > 0
