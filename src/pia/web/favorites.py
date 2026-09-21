"""Favorites: the one thing the web UI writes.

Both operations are idempotent: repeating either leaves the database in the same state as
doing it once. That is what makes the HTTP layer safe against double clicks, retries and
two open tabs (see PUT / DELETE in app.py).
"""

import sqlite3
from datetime import datetime

from pia.models import ensure_utc


class ItemNotFound(Exception):
    """No item has this id."""


def _require_item(conn: sqlite3.Connection, item_id: int) -> None:
    if conn.execute("SELECT 1 FROM items WHERE id = ?", (item_id,)).fetchone() is None:
        raise ItemNotFound(item_id)


def add_favorite(conn: sqlite3.Connection, item_id: int, now: datetime) -> None:
    _require_item(conn, item_id)
    # OR IGNORE: if it is already a favorite, keep the original timestamp.
    with conn:
        conn.execute(
            "INSERT OR IGNORE INTO favorites (item_id, created_at) VALUES (?, ?)",
            (item_id, ensure_utc(now).isoformat()),
        )


def remove_favorite(conn: sqlite3.Connection, item_id: int) -> None:
    _require_item(conn, item_id)
    with conn:
        conn.execute("DELETE FROM favorites WHERE item_id = ?", (item_id,))


def favorite_ids(conn: sqlite3.Connection) -> set[int]:
    return {row[0] for row in conn.execute("SELECT item_id FROM favorites")}
