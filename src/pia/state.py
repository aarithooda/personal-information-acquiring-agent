"""Persistent application state: the user's checkpoint and per-source cursors.

Two different questions, deliberately answered by two different tables:
  * "Where should each *source* resume fetching?"  -> fetch_runs (per-source cursor)
  * "What has the *user* already been shown?"      -> briefings (checkpoint) + items.briefing_id
"""

import sqlite3
from datetime import datetime

from pia.models import ensure_utc


def _iso(value: datetime) -> str:
    return ensure_utc(value).isoformat()


def _parse(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def get_checkpoint(conn: sqlite3.Connection) -> datetime | None:
    """When the last completed briefing covered up to; None if the user never checked."""
    row = conn.execute("SELECT MAX(covers_until) FROM briefings").fetchone()
    return _parse(row[0])


def get_cursor(conn: sqlite3.Connection, source: str) -> datetime | None:
    """How far this source was last fetched *successfully*. Failures never move it."""
    row = conn.execute(
        "SELECT MAX(fetched_until) FROM fetch_runs WHERE source = ? AND status = 'ok'", (source,)
    ).fetchone()
    return _parse(row[0])


def record_fetch_run(
    conn: sqlite3.Connection,
    source: str,
    started_at: datetime,
    *,
    fetched_until: datetime | None = None,
    item_count: int = 0,
    error: str | None = None,
) -> None:
    """Log one fetch attempt. Success (no error) is what advances the cursor."""
    conn.execute(
        """INSERT INTO fetch_runs (source, started_at, fetched_until, status, error, item_count)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (
            source,
            _iso(started_at),
            _iso(fetched_until) if fetched_until and not error else None,
            "error" if error else "ok",
            error,
            item_count,
        ),
    )


def last_fetch_runs(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Most recent attempt per source (for `pia status`)."""
    return conn.execute(
        """SELECT * FROM fetch_runs WHERE id IN (SELECT MAX(id) FROM fetch_runs GROUP BY source)
           ORDER BY source"""
    ).fetchall()


def pending_items(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Everything the user has not been briefed on yet.

    Chosen by STATE, not by comparing timestamps to the checkpoint: a paper published
    last week that we only discovered today is still new *to the user*.
    """
    return conn.execute(
        """SELECT * FROM items WHERE briefing_id IS NULL
           ORDER BY COALESCE(published_at, discovered_at) DESC, id DESC"""
    ).fetchall()


def commit_briefing(
    conn: sqlite3.Connection,
    *,
    covers_from: datetime | None,
    covers_until: datetime,
    created_at: datetime,
    rendered_md: str,
    item_ids: list[int],
) -> int:
    """Save the briefing AND mark its items as briefed, or do neither.

    The checkpoint only exists as this row, so moving it and marking items are one
    atomic step: a crash can never advance the checkpoint without the items being marked.
    """
    with conn:  # one transaction: commits on success, rolls back on any exception
        cursor = conn.execute(
            """INSERT INTO briefings (covers_from, covers_until, created_at, rendered_md)
               VALUES (?, ?, ?, ?)""",
            (
                _iso(covers_from) if covers_from else None,
                _iso(covers_until),
                _iso(created_at),
                rendered_md,
            ),
        )
        briefing_id = cursor.lastrowid
        conn.executemany(
            "UPDATE items SET briefing_id = ?, status = 'briefed' WHERE id = ?",
            [(briefing_id, item_id) for item_id in item_ids],
        )
    return briefing_id
