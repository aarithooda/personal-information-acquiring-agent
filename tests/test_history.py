import hashlib
import sqlite3
from datetime import timedelta

import pytest
from fakes import T0, FakeSource, make_item

from pia.db import MIGRATIONS, connect
from pia.history import (
    DatabaseMissing,
    connect_readonly,
    default_briefing_id,
    get_briefing,
    list_briefings,
)
from pia.pipeline import run_briefing


@pytest.fixture
def db(tmp_path):
    """A real database file with two briefings: one with 2 items, then an empty 'nothing new' one."""
    path = tmp_path / "pia.db"
    conn = connect(path)
    source = FakeSource("a", [make_item("a", 1, T0), make_item("a", 2, T0)])
    run_briefing(conn, None, [source], now=T0)
    run_briefing(conn, None, [source], now=T0 + timedelta(days=1))
    conn.close()
    return path


def digest(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_a_missing_database_is_reported_and_not_created(tmp_path):
    path = tmp_path / "nope" / "pia.db"
    with pytest.raises(DatabaseMissing):
        connect_readonly(path)
    assert not path.exists() and not path.parent.exists()


def test_the_connection_cannot_write(db):
    conn = connect_readonly(db)
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        conn.execute("DELETE FROM briefings")
    conn.close()


def test_reading_leaves_the_database_file_byte_for_byte_unchanged(db):
    before = digest(db)
    conn = connect_readonly(db)
    list_briefings(conn)
    get_briefing(conn, 1)
    default_briefing_id(conn)
    conn.close()
    assert digest(db) == before


def test_an_old_schema_database_is_read_as_is_and_never_migrated(tmp_path):
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.executescript(f"BEGIN;\n{MIGRATIONS[0]}\nPRAGMA user_version = 1;\nCOMMIT;")
    old.execute(
        "INSERT INTO briefings (covers_from, covers_until, created_at, rendered_md) "
        "VALUES (NULL, '2026-09-20T17:25:15+00:00', '2026-09-20T17:25:15+00:00', '# Old briefing')"
    )
    old.commit()
    old.close()
    before = digest(path)

    conn = connect_readonly(path)
    assert get_briefing(conn, 1)["rendered_md"] == "# Old briefing"
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
    conn.close()
    assert digest(path) == before


def test_history_lists_newest_first_with_counts(db):
    rows = list_briefings(connect_readonly(db))
    assert [r["id"] for r in rows] == [2, 1]
    assert (rows[1]["shown"], rows[1]["skipped"]) == (2, 0)
    assert (rows[0]["shown"], rows[0]["skipped"]) == (0, 0)  # the 'nothing new' check


def test_get_briefing_returns_the_stored_markdown_or_none(db):
    conn = connect_readonly(db)
    assert "a item 1" in get_briefing(conn, 1)["rendered_md"]
    assert get_briefing(conn, 99) is None


def test_default_is_the_latest_briefing_that_actually_had_content(db):
    # #2 is the newest but empty; what the user wants to re-read is #1.
    assert default_briefing_id(connect_readonly(db)) == 1


def test_default_falls_back_to_the_latest_when_none_had_content(tmp_path):
    path = tmp_path / "pia.db"
    conn = connect(path)
    run_briefing(conn, None, [FakeSource("a")], now=T0)
    conn.close()
    assert default_briefing_id(connect_readonly(path)) == 1


def test_default_is_none_when_there_are_no_briefings(tmp_path):
    path = tmp_path / "pia.db"
    connect(path).close()
    assert default_briefing_id(connect_readonly(path)) is None
