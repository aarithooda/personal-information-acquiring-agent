"""The scenarios from the project brief, simulated by injecting the clock."""

from datetime import timedelta

import pytest
from fakes import T0, FakeSource, make_item

from pia.db import connect, store_items
from pia.http import SourceError
from pia.pipeline import AllSourcesFailed, run_briefing
from pia.state import get_checkpoint, get_cursor

HOUR = timedelta(hours=1)


def day(n: int):
    return T0 + timedelta(days=n)


@pytest.fixture
def conn():
    connection = connect(":memory:")
    yield connection
    connection.close()


def briefing_count(conn) -> int:
    return conn.execute("SELECT COUNT(*) FROM briefings").fetchone()[0]


def titles(result) -> set[str]:
    return {item["title"] for item in result.items}


def test_first_run_has_no_previous_checkpoint_and_sets_one(conn):
    source = FakeSource("a", [make_item("a", 1, T0 - HOUR)])
    result = run_briefing(conn, None, [source], now=T0)
    assert result.covers_from is None
    assert titles(result) == {"a item 1"}
    assert get_checkpoint(conn) == T0


def test_returning_after_three_days_shows_only_what_appeared_since(conn):
    source = FakeSource("a", [make_item("a", 1, T0 - HOUR)])
    run_briefing(conn, None, [source], now=T0)

    source.items += [make_item("a", 2, day(1)), make_item("a", 3, day(2))]
    result = run_briefing(conn, None, [source], now=day(3))

    assert titles(result) == {"a item 2", "a item 3"}  # not item 1: already seen
    assert result.covers_from == T0
    assert get_checkpoint(conn) == day(3)


def test_returning_five_days_after_that_continues_from_the_new_checkpoint(conn):
    source = FakeSource("a", [make_item("a", 1, T0 - HOUR)])
    run_briefing(conn, None, [source], now=T0)
    source.items.append(make_item("a", 2, day(2)))
    run_briefing(conn, None, [source], now=day(3))

    source.items.append(make_item("a", 3, day(6)))
    result = run_briefing(conn, None, [source], now=day(8))

    assert titles(result) == {"a item 3"}
    assert result.covers_from == day(3)


def test_opening_again_immediately_shows_nothing_new(conn):
    source = FakeSource("a", [make_item("a", 1, T0 - HOUR)])
    run_briefing(conn, None, [source], now=T0)
    result = run_briefing(conn, None, [source], now=T0 + timedelta(minutes=5))
    assert result.items == []
    assert briefing_count(conn) == 2  # the check itself is still recorded


def test_new_means_new_to_the_user_not_recently_published(conn):
    run_briefing(conn, None, [], now=T0)
    # We only discover a month-old item today: it must still be shown.
    store_items(conn, [make_item("a", 1, T0 - timedelta(days=30))], now=day(1))
    result = run_briefing(conn, None, [], now=day(1))
    assert titles(result) == {"a item 1"}


def test_briefed_items_are_linked_to_their_briefing(conn):
    source = FakeSource("a", [make_item("a", 1, T0 - HOUR)])
    result = run_briefing(conn, None, [source], now=T0)
    row = conn.execute("SELECT status, briefing_id FROM items").fetchone()
    assert (row["status"], row["briefing_id"]) == ("briefed", result.briefing_id)


def test_a_crash_before_the_briefing_is_saved_loses_nothing(conn):
    source = FakeSource("a", [make_item("a", 1, T0 - HOUR)])

    def exploding_prepare(*args):
        raise RuntimeError("render crashed")

    with pytest.raises(RuntimeError):
        run_briefing(conn, None, [source], now=T0, prepare=exploding_prepare)
    assert briefing_count(conn) == 0
    assert get_checkpoint(conn) is None  # checkpoint did not move

    result = run_briefing(conn, None, [source], now=T0 + HOUR)  # "restart the app"
    assert titles(result) == {"a item 1"}


def test_if_every_source_fails_nothing_is_committed(conn):
    sources = [FakeSource("a", error=SourceError("down")), FakeSource("b", error=SourceError("down"))]
    with pytest.raises(AllSourcesFailed):
        run_briefing(conn, None, sources, now=T0)
    assert briefing_count(conn) == 0
    assert get_checkpoint(conn) is None


def test_if_some_sources_fail_the_briefing_still_happens_and_names_them(conn):
    good = FakeSource("good", [make_item("good", 1, T0 - HOUR)])
    bad = FakeSource("bad", error=SourceError("service down"))
    result = run_briefing(conn, None, [good, bad], now=T0)

    assert titles(result) == {"good item 1"}
    assert "bad" in result.markdown and "service down" in result.markdown
    assert get_checkpoint(conn) == T0
    assert get_cursor(conn, "bad") is None  # the failed source will catch up later


def test_the_rendered_briefing_lists_new_items_grouped_by_source(conn):
    source = FakeSource("a", [make_item("a", 1, T0 - HOUR)])
    result = run_briefing(conn, None, [source], now=T0)
    assert "a item 1" in result.markdown
    assert "https://example.com/a/1" in result.markdown
