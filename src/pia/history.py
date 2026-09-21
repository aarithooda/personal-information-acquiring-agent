"""Read-only access to the database, for commands that only look (status, history, show, doctor).

`db.connect()` creates missing files and runs migrations, which are writes. A command whose
whole purpose is inspecting state must not be able to change it, so these helpers open the
file with SQLite's `mode=ro` (plus `query_only`) and never migrate: an older schema is read as it is.
"""

import sqlite3
from pathlib import Path


class DatabaseMissing(Exception):
    """There is no database file yet (nothing has been run)."""


def connect_readonly(path: str | Path) -> sqlite3.Connection:
    file = Path(path)
    if not file.is_file():
        raise DatabaseMissing(str(file))
    # as_uri() percent-encodes spaces etc., which matters for paths like "AI info scrapper".
    conn = sqlite3.connect(f"{file.resolve().as_uri()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    return conn


def list_briefings(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Every briefing, newest first, with how many items it showed and dropped."""
    return conn.execute(
        """SELECT b.id, b.covers_from, b.covers_until, b.created_at, length(b.rendered_md) AS chars,
                  (SELECT COUNT(*) FROM items i WHERE i.briefing_id = b.id AND i.status = 'briefed') AS shown,
                  (SELECT COUNT(*) FROM items i WHERE i.briefing_id = b.id AND i.status = 'skipped') AS skipped
           FROM briefings b ORDER BY b.id DESC"""
    ).fetchall()


def get_briefing(conn: sqlite3.Connection, briefing_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM briefings WHERE id = ?", (briefing_id,)).fetchone()


def default_briefing_id(conn: sqlite3.Connection) -> int | None:
    """The briefing you most likely want to re-read: the latest one that actually showed something.

    Every check is recorded, including "nothing new" ones, so the newest briefing is often empty.
    """
    row = conn.execute(
        """SELECT b.id FROM briefings b
           WHERE EXISTS (SELECT 1 FROM items i WHERE i.briefing_id = b.id AND i.status = 'briefed')
           ORDER BY b.id DESC LIMIT 1"""
    ).fetchone()
    if row:
        return row["id"]
    return conn.execute("SELECT MAX(id) FROM briefings").fetchone()[0]
