import json
from datetime import datetime, timedelta, timezone

import pytest

from pia.db import connect, store_items, upsert_item
from pia.models import RawItem

T0 = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)


def raw(**overrides) -> RawItem:
    fields = dict(
        source="hn",
        external_id="1",
        url="https://example.com/post",
        title="A post",
        content="body",
        published_at=T0 - timedelta(hours=3),
        signals={"points": 100},
    )
    fields.update(overrides)
    return RawItem(**fields)


@pytest.fixture
def conn():
    connection = connect(":memory:")
    yield connection
    connection.close()


def all_items(conn):
    return conn.execute("SELECT * FROM items ORDER BY id").fetchall()


def test_connect_applies_migrations_once(tmp_path):
    path = tmp_path / "pia.db"
    first = connect(path)
    version = first.execute("PRAGMA user_version").fetchone()[0]
    first.close()
    assert version >= 1
    second = connect(path)  # re-opening must not re-run or fail
    assert second.execute("PRAGMA user_version").fetchone()[0] == version
    second.close()


def test_connect_creates_missing_parent_directories(tmp_path):
    path = tmp_path / "does" / "not" / "exist" / "pia.db"
    connect(path).close()
    assert path.exists()


def test_new_item_is_inserted_with_utc_timestamps_and_discovered_status(conn):
    assert upsert_item(conn, raw(), now=T0) == "inserted"
    (row,) = all_items(conn)
    assert row["canonical_url"] == "https://example.com/post"
    assert row["status"] == "discovered"
    assert row["discovered_at"] == "2026-09-20T12:00:00+00:00"
    assert row["published_at"] == "2026-09-20T09:00:00+00:00"
    assert row["briefing_id"] is None
    assert json.loads(row["signals"]) == {"hn": {"points": 100}}


def test_same_url_from_another_source_merges_instead_of_duplicating(conn):
    upsert_item(conn, raw(url="https://arxiv.org/abs/2401.12345v1", source="arxiv"), now=T0)
    result = upsert_item(
        conn,
        raw(url="http://www.arxiv.org/pdf/2401.12345v2.pdf?utm_source=hn", signals={"points": 250}),
        now=T0 + timedelta(hours=1),
    )
    assert result == "merged"
    (row,) = all_items(conn)
    assert row["source"] == "arxiv"  # first sighting stays primary
    assert json.loads(row["signals"])["hn"] == {"points": 250}


def test_resighting_updates_signals_but_not_discovery_time(conn):
    upsert_item(conn, raw(), now=T0)
    upsert_item(conn, raw(signals={"points": 400}), now=T0 + timedelta(days=1))
    (row,) = all_items(conn)
    assert row["discovered_at"] == "2026-09-20T12:00:00+00:00"
    assert json.loads(row["signals"]) == {"hn": {"points": 400}}


def test_resighting_does_not_reset_an_already_briefed_item(conn):
    upsert_item(conn, raw(), now=T0)
    conn.execute("UPDATE items SET status = 'briefed'")
    upsert_item(conn, raw(signals={"points": 999}), now=T0 + timedelta(days=1))
    assert all_items(conn)[0]["status"] == "briefed"


def test_missing_published_at_is_stored_as_null_not_invented(conn):
    upsert_item(conn, raw(published_at=None), now=T0)
    assert all_items(conn)[0]["published_at"] is None


def test_naive_datetimes_are_treated_as_utc(conn):
    upsert_item(conn, raw(published_at=datetime(2026, 9, 20, 9, 0)), now=T0)
    assert all_items(conn)[0]["published_at"] == "2026-09-20T09:00:00+00:00"


def test_unusable_url_raises(conn):
    with pytest.raises(ValueError):
        upsert_item(conn, raw(url="not a url"), now=T0)


def test_blank_title_is_rejected_by_the_model():
    with pytest.raises(ValueError):
        raw(title="   ")


def test_store_items_reports_counts_and_survives_bad_items(conn):
    items = [
        raw(external_id="1", url="https://example.com/a"),
        raw(external_id="2", url="https://example.com/a?utm_source=x"),  # duplicate of 1
        raw(external_id="3", url="garbage"),  # unusable
        raw(external_id="4", url="https://example.com/b"),
    ]
    report = store_items(conn, items, now=T0)
    assert (report.inserted, report.merged, report.rejected) == (2, 1, 1)
    assert len(all_items(conn)) == 2


def test_data_survives_restart(tmp_path):
    path = tmp_path / "pia.db"
    first = connect(path)
    upsert_item(first, raw(), now=T0)
    first.commit()
    first.close()
    second = connect(path)
    assert len(all_items(second)) == 1
    second.close()
