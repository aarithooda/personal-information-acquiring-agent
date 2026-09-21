"""Contract tests: the parser must understand what the REAL renderer produces.

If someone changes render_briefing's layout, these fail (instead of the web page silently breaking).
"""

from datetime import timedelta

import pytest
from fakes import T0, triaged_row

from pia.briefing.render import SECTIONS, render_briefing
from pia.collect import SourceResult
from pia.llm.headlines import Headline
from pia.web.briefing_view import parse_briefing


def headline(id_, why="It matters.", explanation=None, signals=None, title=None):
    row = triaged_row(id_, signals=signals if signals is not None else {"hn": {"points": 5}})
    return Headline(row, explanation or f"Explanation {id_}.", why)


def extra(id_, category, summary=True):
    row = triaged_row(id_, category=category)
    return row if summary else _blank_summary(row)


def _blank_summary(row):
    import sqlite3

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    return conn.execute(
        "SELECT ? AS id, ? AS source, ? AS title, ? AS url, ? AS category, '' AS summary, ? AS signals",
        (row["id"], row["source"], row["title"], row["url"], row["category"], row["signals"]),
    ).fetchone()


def render(headlines=(), extras=(), *, checkpoint=None, results=(), triaged=10, awaiting=0, editor_failed=False):
    return render_briefing(
        headlines=list(headlines),
        extras=list(extras),
        checkpoint=checkpoint,
        now=T0,
        results=list(results),
        triaged=triaged,
        awaiting=awaiting,
        editor_failed=editor_failed,
    )


def test_headlines_round_trip_with_all_their_fields():
    md = render(
        [
            headline(1, signals={"arxiv": {}, "hf_papers": {}, "hn": {}}),
            headline(2, why=None, explanation="Only an explanation."),
        ],
        checkpoint=T0 - timedelta(days=3),
    )
    parsed = parse_briefing(md)
    assert [h.title for h in parsed.headlines] == ["Title 1", "Title 2"]
    first, second = parsed.headlines
    assert first.url == "https://example.com/1"
    assert first.explanation == "Explanation 1."
    assert first.why_it_matters == "It matters."
    assert first.via == ["arxiv", "hf_papers", "hn"]
    assert second.why_it_matters is None and second.explanation == "Only an explanation."


def test_extras_round_trip_grouped_by_category_in_section_order():
    md = render(
        [headline(1)],
        [extra(2, "ai"), extra(3, "software"), extra(4, "research"), extra(5, "ai", summary=False)],
    )
    parsed = parse_briefing(md)
    assert [category for category, _ in parsed.sections] == ["ai", "software", "research"]
    ai = dict(parsed.sections)["ai"]
    assert [(e.title, e.summary) for e in ai] == [("Title 2", "Summary 2."), ("Title 5", None)]  # blank -> None
    assert dict(parsed.sections)["software"][0].url == "https://example.com/3"


def test_every_section_the_renderer_can_emit_is_understood():
    md = render([], [extra(i, category) for i, (category, _) in enumerate(SECTIONS, start=1)])
    assert [c for c, _ in parse_briefing(md).sections] == [c for c, _ in SECTIONS]


@pytest.mark.parametrize(
    "title",
    ["[Show HN] Foo [bar]", 'Quotes "and" colons: here', "Trailing paren (like this)", "Emoji 🚀 and Māori"],
)
def test_awkward_titles_survive(title):
    row = triaged_row(7)
    import sqlite3

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    awkward = conn.execute(
        "SELECT 7 AS id, 'hn' AS source, ? AS title, 'https://example.com/7' AS url, 'ai' AS category, "
        "'Summary.' AS summary, ? AS signals, ? AS content_raw",
        (title, row["signals"], "x"),
    ).fetchone()
    md = render([Headline(awkward, "Explained.", "Why.")], [awkward])
    parsed = parse_briefing(md)
    assert parsed.headlines[0].title == title and parsed.headlines[0].url == "https://example.com/7"
    assert dict(parsed.sections)["ai"][0].title == title


def test_notes_and_the_skipped_count_are_captured():
    md = render(
        [headline(1)],
        [extra(2, "ai")],
        results=[SourceResult("arxiv", False, error="HTTP 503")],
        awaiting=3,
        editor_failed=True,
        triaged=20,
    )
    parsed = parse_briefing(md)
    joined = " ".join(parsed.notes)
    assert "arxiv" in joined and "HTTP 503" in joined
    assert "editor" in joined.lower() and "3 items" in joined
    assert parsed.hidden == 18  # 20 triaged - 1 headline - 1 extra


def test_the_intro_line_is_kept_for_display():
    md = render([headline(1)], checkpoint=T0 - timedelta(days=3))
    assert "Last checked" in parse_briefing(md).intro and "Skimmed" in parse_briefing(md).intro


def test_first_run_intro():
    assert parse_briefing(render([headline(1)])).intro.startswith("First run")


def test_an_empty_briefing_is_understood_as_empty_with_its_message():
    nothing_new = parse_briefing(render([], [], triaged=0, checkpoint=T0 - timedelta(hours=5)))
    assert nothing_new.headlines == [] and nothing_new.sections == []
    assert nothing_new.empty_message == "Nothing new since your last check."
    assert parse_briefing(render([], [], triaged=12)).empty_message == "Nothing stood out in the new items."


@pytest.mark.parametrize(
    "markdown",
    [
        "",
        "just some text",
        "# Something else entirely\n\n## Weird heading\n",
        "# Since you last checked\n\n## 🔮 A section we do not know\n\n- [x](https://a.test)\n",
        "# Since you last checked\n\nLast checked now.\n\n**3 new item(s).**\n\n## arxiv (3)\n\n- [a](https://a.test) (2026-09-18)\n",  # plain M3 format
    ],
)
def test_anything_unrecognised_returns_none_so_the_ui_can_fall_back_to_raw_text(markdown):
    assert parse_briefing(markdown) is None
