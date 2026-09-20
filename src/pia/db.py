"""SQLite persistence: schema migrations and the item store.

All timestamps are stored as UTC ISO-8601 text. `now` is always passed in
rather than read from the clock, so behaviour is deterministic and testable.
"""

import json
import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

from pia.models import RawItem, ensure_utc
from pia.normalize import canonicalize_url

log = logging.getLogger(__name__)

# Each entry upgrades the schema by one version (tracked in PRAGMA user_version).
# Never edit a released migration; append a new one.
MIGRATIONS = [
    """
    CREATE TABLE briefings (
        id            INTEGER PRIMARY KEY,
        covers_from   TEXT,               -- NULL on the very first briefing
        covers_until  TEXT NOT NULL,      -- this is the user's checkpoint
        created_at    TEXT NOT NULL,
        rendered_md   TEXT NOT NULL
    );

    CREATE TABLE items (
        id            INTEGER PRIMARY KEY,
        source        TEXT NOT NULL,      -- first source that reported it
        external_id   TEXT NOT NULL,
        canonical_url TEXT NOT NULL UNIQUE,
        url           TEXT NOT NULL,
        title         TEXT NOT NULL,
        content_raw   TEXT,
        published_at  TEXT,               -- NULL when the source gave none
        discovered_at TEXT NOT NULL,
        signals       TEXT NOT NULL DEFAULT '{}',   -- JSON: {source: {...}}
        briefing_id   INTEGER REFERENCES briefings(id),
        status        TEXT NOT NULL DEFAULT 'discovered'
                      CHECK (status IN ('discovered','enriched','briefed','skipped','failed'))
    );
    CREATE INDEX idx_items_status ON items(status);

    CREATE TABLE enrichments (
        item_id        INTEGER NOT NULL REFERENCES items(id),
        model          TEXT NOT NULL,
        prompt_version TEXT NOT NULL,
        category       TEXT NOT NULL,
        importance     INTEGER NOT NULL CHECK (importance BETWEEN 1 AND 5),
        summary        TEXT NOT NULL,
        why_it_matters TEXT,
        created_at     TEXT NOT NULL,
        PRIMARY KEY (item_id, model, prompt_version)
    );

    CREATE TABLE fetch_runs (
        id            INTEGER PRIMARY KEY,
        source        TEXT NOT NULL,
        started_at    TEXT NOT NULL,
        fetched_until TEXT,               -- per-source cursor, set on success only
        status        TEXT NOT NULL CHECK (status IN ('ok','error')),
        error         TEXT,
        item_count    INTEGER NOT NULL DEFAULT 0
    );
    """,
    # v2: how many times triage has failed to produce a result for this item, so an item that
    # the model can never handle is eventually given up on instead of retried forever.
    """
    ALTER TABLE items ADD COLUMN triage_attempts INTEGER NOT NULL DEFAULT 0;
    """,
]


def connect(path: str | Path) -> sqlite3.Connection:
    in_memory = str(path) == ":memory:"
    if not in_memory:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    if not in_memory:
        conn.execute("PRAGMA journal_mode = WAL")
    _migrate(conn)
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    for version, script in enumerate(MIGRATIONS[current:], start=current + 1):
        # One transaction per migration: a crash leaves the old version intact.
        conn.executescript(f"BEGIN;\n{script}\nPRAGMA user_version = {version};\nCOMMIT;")


def _iso(value: datetime) -> str:
    return ensure_utc(value).isoformat()


def upsert_item(
    conn: sqlite3.Connection, item: RawItem, now: datetime
) -> Literal["inserted", "merged"]:
    """Insert a new item, or merge a repeat sighting into the existing one.

    Identity is the canonical URL. Raises ValueError if the URL is unusable.
    Does not commit; the caller owns the transaction.
    """
    canonical = canonicalize_url(item.url)
    existing = conn.execute(
        "SELECT id, signals FROM items WHERE canonical_url = ?", (canonical,)
    ).fetchone()

    if existing:
        signals = json.loads(existing["signals"])
        signals[item.source] = item.signals
        conn.execute(
            "UPDATE items SET signals = ? WHERE id = ?", (json.dumps(signals), existing["id"])
        )
        return "merged"

    conn.execute(
        """INSERT INTO items (source, external_id, canonical_url, url, title, content_raw,
                              published_at, discovered_at, signals)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            item.source,
            item.external_id,
            canonical,
            item.url,
            item.title,
            item.content,
            _iso(item.published_at) if item.published_at else None,
            _iso(now),
            json.dumps({item.source: item.signals}),
        ),
    )
    return "inserted"


@dataclass
class StoreReport:
    inserted: int = 0
    merged: int = 0
    rejected: int = 0


def store_items(conn: sqlite3.Connection, items: list[RawItem], now: datetime) -> StoreReport:
    """Store a batch. One bad item is logged and counted, never fatal to the batch."""
    report = StoreReport()
    for item in items:
        try:
            outcome = upsert_item(conn, item, now)
        except ValueError as exc:
            log.warning("rejected item %s/%s: %s", item.source, item.external_id, exc)
            report.rejected += 1
        else:
            if outcome == "inserted":
                report.inserted += 1
            else:
                report.merged += 1
    conn.commit()
    return report
