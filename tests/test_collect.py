from datetime import timedelta

import pytest
from fakes import T0, FakeSource, make_item

from pia.collect import collect
from pia.db import connect
from pia.http import SourceError
from pia.state import get_cursor

HOUR, DAY = timedelta(hours=1), timedelta(days=1)


@pytest.fixture
def conn():
    connection = connect(":memory:")
    yield connection
    connection.close()


def item_count(conn) -> int:
    return conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]


def test_first_run_looks_back_a_default_period_and_records_a_cursor(conn):
    source = FakeSource("a", [make_item("a", 1, T0 - HOUR)])
    (result,) = collect(conn, None, [source], now=T0, first_run_lookback=3 * DAY)
    assert source.calls == [(T0 - 3 * DAY, T0)]
    assert result.ok and result.inserted == 1
    assert get_cursor(conn, "a") == T0


def test_later_runs_start_at_the_cursor_minus_a_safety_overlap(conn):
    source = FakeSource("a")
    collect(conn, None, [source], now=T0, overlap=6 * HOUR)
    collect(conn, None, [source], now=T0 + 2 * DAY, overlap=6 * HOUR)
    assert source.calls[1] == (T0 - 6 * HOUR, T0 + 2 * DAY)


def test_overlap_refetches_are_absorbed_by_dedup(conn):
    source = FakeSource("a", [make_item("a", 1, T0 - HOUR)])
    collect(conn, None, [source], now=T0, overlap=6 * HOUR)
    (second,) = collect(conn, None, [source], now=T0 + HOUR, overlap=6 * HOUR)
    assert (second.inserted, second.merged) == (0, 1)
    assert item_count(conn) == 1


def test_a_very_old_cursor_is_capped_by_max_lookback(conn):
    source = FakeSource("a")
    collect(conn, None, [source], now=T0)
    late = T0 + 30 * DAY
    collect(conn, None, [source], now=late, max_lookback=14 * DAY)
    assert source.calls[1][0] == late - 14 * DAY


def test_a_failing_source_is_recorded_and_does_not_stop_the_others(conn):
    bad = FakeSource("bad", error=SourceError("service down"))
    good = FakeSource("good", [make_item("good", 1, T0 - HOUR)])
    bad_result, good_result = collect(conn, None, [bad, good], now=T0)
    assert not bad_result.ok and "service down" in bad_result.error
    assert good_result.ok and good_result.inserted == 1
    assert get_cursor(conn, "bad") is None
    assert get_cursor(conn, "good") == T0


def test_a_failed_source_catches_up_from_its_last_good_cursor(conn):
    source = FakeSource("a")
    collect(conn, None, [source], now=T0, overlap=HOUR)
    source.error = SourceError("down")
    collect(conn, None, [source], now=T0 + DAY, overlap=HOUR)
    assert get_cursor(conn, "a") == T0  # unchanged by the failure
    source.error = None
    collect(conn, None, [source], now=T0 + 2 * DAY, overlap=HOUR)
    assert source.calls[-1] == (T0 - HOUR, T0 + 2 * DAY)  # window still reaches back to T0


def test_a_source_that_failed_on_its_first_ever_run_still_looks_back_from_that_first_attempt(conn):
    """Found by the fault-injection test: without a cursor, a late first success used to look back
    only from *its own* time, silently losing everything published while the source was down."""
    source = FakeSource("a", error=SourceError("down"))
    collect(conn, None, [source], now=T0, first_run_lookback=3 * DAY)
    source.error = None
    collect(conn, None, [source], now=T0 + 5 * DAY, first_run_lookback=3 * DAY)
    assert source.calls[-1] == (T0 - 3 * DAY, T0 + 5 * DAY)


def test_the_first_attempt_anchor_is_still_capped_by_max_lookback(conn):
    source = FakeSource("a", error=SourceError("down"))
    collect(conn, None, [source], now=T0)
    source.error = None
    late = T0 + 40 * DAY
    collect(conn, None, [source], now=late, max_lookback=14 * DAY)
    assert source.calls[-1][0] == late - 14 * DAY


def test_an_unexpected_adapter_bug_is_contained(conn):
    buggy = FakeSource("buggy", error=RuntimeError("adapter bug"))
    good = FakeSource("good", [make_item("good", 1, T0 - HOUR)])
    buggy_result, good_result = collect(conn, None, [buggy, good], now=T0)
    assert not buggy_result.ok and "adapter bug" in buggy_result.error
    assert good_result.ok
