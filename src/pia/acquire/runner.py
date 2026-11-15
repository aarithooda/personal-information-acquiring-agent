"""Acquisition: fetch every source that is due, store what it returns, keep each source's cursor and health.

Same rules v1 proved under fault injection, generalised to a database-backed registry:
  * one source failing never affects another;
  * items are stored first and the cursor advances second, so a crash only causes a harmless re-fetch;
  * the window starts a little before the cursor, because sources index items late; dedup absorbs the overlap;
  * a failing source backs off exponentially and is shown as unhealthy; it is never removed automatically.

Network work runs in a small thread pool; all database writes happen on the calling thread.
"""

import json
import logging
import sqlite3
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta

import httpx

from pia.acquire.adapters import FIRST_LOOKBACK_DAYS, UnknownSourceKind, build_adapter
from pia.http import SourceError
from pia.models import RawItem
from pia.normalize import canonicalize_url
from pia.store.db import iso, parse_ts

log = logging.getLogger(__name__)

OVERLAP = timedelta(hours=6)
MAX_LOOKBACK = timedelta(days=30)
MAX_BACKOFF = timedelta(hours=24)
NETWORK_RETRY = timedelta(minutes=15)  # how soon to retry after a failure that never reached the source
UNHEALTHY_AFTER = 5  # consecutive failures before the UI calls a source unhealthy
ARXIV_GAP_SECONDS = 3.0  # arXiv asks API clients to wait three seconds between requests


@dataclass
class SourceResult:
    source_id: int
    name: str
    ok: bool
    fetched: int = 0
    inserted: int = 0
    merged: int = 0
    rejected: int = 0
    error: str | None = None


def due_sources(conn: sqlite3.Connection, now: datetime, *, only: list[int] | None = None, force: bool = False) -> list[sqlite3.Row]:
    rows = conn.execute("SELECT * FROM sources WHERE status = 'active' ORDER BY id").fetchall()
    if only is not None:
        rows = [row for row in rows if row["id"] in only]
    if force:
        return rows
    return [row for row in rows if not row["next_fetch_at"] or parse_ts(row["next_fetch_at"]) <= now]


def window_for(conn: sqlite3.Connection, row: sqlite3.Row, now: datetime) -> tuple[datetime, datetime]:
    cursor = conn.execute(
        "SELECT MAX(fetched_until) FROM fetch_runs WHERE source_id = ? AND ok = 1", (row["id"],)
    ).fetchone()[0]
    if cursor:
        since = parse_ts(cursor) - OVERLAP
    else:
        # No success yet: look back from the first ATTEMPT, so a source that was down at first still catches up.
        first = conn.execute("SELECT MIN(started_at) FROM fetch_runs WHERE source_id = ?", (row["id"],)).fetchone()[0]
        since = (parse_ts(first) if first else now) - timedelta(days=FIRST_LOOKBACK_DAYS.get(row["kind"], 14))
    return max(since, now - MAX_LOOKBACK), now


def upsert_item(conn: sqlite3.Connection, source: sqlite3.Row, raw: RawItem, now: datetime) -> str:
    """Insert a new item or merge a repeat sighting. Identity is the canonical URL. Does not commit."""
    canonical = canonicalize_url(raw.url)  # raises ValueError for an unusable URL
    signals = {key: value for key, value in (raw.signals or {}).items() if value not in (None, "", [])}
    existing = conn.execute("SELECT id, signals, content FROM items WHERE canonical_url = ?", (canonical,)).fetchone()
    if existing:
        merged = {**json.loads(existing["signals"]), **signals}
        conn.execute("UPDATE items SET signals = ? WHERE id = ?", (json.dumps(merged), existing["id"]))
        if raw.content and not (existing["content"] or "").strip():
            conn.execute("UPDATE items SET content = ? WHERE id = ?", (raw.content, existing["id"]))
        conn.execute(
            "INSERT OR IGNORE INTO item_sources (item_id, source_name, source_id, external_id, url, seen_at) VALUES (?, ?, ?, ?, ?, ?)",
            (existing["id"], source["name"], source["id"], raw.external_id, raw.url, iso(now)),
        )
        return "merged"

    published = iso(raw.published_at) if raw.published_at else None
    item_id = conn.execute(
        """INSERT INTO items (canonical_url, url, title, content, author, published_at, acquired_at, sort_at,
                              source_id, source_name, kind, signals)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            canonical, raw.url, raw.title[:500], raw.content, raw.author, published, iso(now),
            # A source can claim a future date; never let that pin an item to the top of Latest.
            min(published, iso(now)) if published else iso(now),
            source["id"], source["name"], raw.kind or "article", json.dumps(signals),
        ),
    ).lastrowid
    conn.execute(
        "INSERT INTO item_sources (item_id, source_name, source_id, external_id, url, seen_at) VALUES (?, ?, ?, ?, ?, ?)",
        (item_id, source["name"], source["id"], raw.external_id, raw.url, iso(now)),
    )
    return "inserted"


def _record(conn: sqlite3.Connection, source: sqlite3.Row, now: datetime, result: SourceResult, state: dict | None) -> None:
    interval = timedelta(minutes=source["interval_minutes"])
    if result.ok:
        conn.execute(
            "INSERT INTO fetch_runs (source_id, started_at, fetched_until, ok, item_count, new_count) VALUES (?, ?, ?, 1, ?, ?)",
            (source["id"], iso(now), iso(now), result.fetched, result.inserted),
        )
        conn.execute(
            "UPDATE sources SET last_ok_at = ?, last_error = NULL, failures = 0, next_fetch_at = ? WHERE id = ?",
            (iso(now), iso(now + interval), source["id"]),
        )
        if state is not None:
            conn.execute("UPDATE sources SET state = ? WHERE id = ?", (json.dumps(state), source["id"]))
    else:
        failures = source["failures"] + 1
        if "network error" in (result.error or ""):
            # No answer at all (DNS, connection): that is this machine's network, not the source. Try again soon
            # instead of backing a healthy source off for hours because the laptop's resolver hiccupped.
            backoff = min(interval, NETWORK_RETRY)
        else:
            backoff = min(interval * (2 ** min(failures, 6)), MAX_BACKOFF)
        conn.execute(
            "INSERT INTO fetch_runs (source_id, started_at, ok, error) VALUES (?, ?, 0, ?)", (source["id"], iso(now), result.error)
        )
        conn.execute(
            "UPDATE sources SET last_error = ?, failures = ?, next_fetch_at = ? WHERE id = ?",
            (result.error, failures, iso(now + backoff), source["id"]),
        )
    conn.commit()


def _fetch_lane(client: httpx.Client, lane: list[tuple], sleep: Callable[[float], None]) -> list[tuple]:
    """One lane = sources that must not be fetched concurrently (same polite-rate host). Returns
    (source row, items | None, error | None, adapter state | None) per source. Runs in a worker thread: no database access."""
    out = []
    for index, (row, since, until) in enumerate(lane):
        if index:
            sleep(ARXIV_GAP_SECONDS)
        try:
            adapter = build_adapter(row)
            items = adapter.fetch(client, since, until)
            out.append((row, items, None, getattr(adapter, "state", None)))
        except (SourceError, UnknownSourceKind) as exc:
            out.append((row, None, str(exc)[:500], None))
        except Exception as exc:  # noqa: BLE001 - a buggy adapter must not kill the run
            log.exception("source %s crashed", row["name"])
            out.append((row, None, f"unexpected error: {exc!r}"[:500], None))
    return out


def acquire(
    conn: sqlite3.Connection,
    client: httpx.Client,
    now: datetime,
    *,
    only: list[int] | None = None,
    force: bool = False,
    workers: int = 4,
    sleep: Callable[[float], None] = time.sleep,
) -> list[SourceResult]:
    rows = due_sources(conn, now, only=only, force=force)
    lanes: dict[object, list[tuple]] = {}
    for row in rows:
        row = dict(row)  # plain data for the worker threads: they must never touch sqlite objects
        since, until = window_for(conn, row, now)
        lanes.setdefault("arxiv" if row["kind"] == "arxiv" else row["id"], []).append((row, since, until))

    results: list[SourceResult] = []
    if not lanes:
        return results
    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(lanes)))) as pool:
        for fetched_lane in pool.map(lambda lane: _fetch_lane(client, lane, sleep), lanes.values()):
            for row, items, error, state in fetched_lane:
                result = SourceResult(row["id"], row["name"], ok=error is None, error=error)
                if items is not None:
                    result.fetched = len(items)
                    for raw in items:
                        try:
                            outcome = upsert_item(conn, row, raw, now)
                        except ValueError as exc:
                            log.warning("rejected item from %s: %s", row["name"], exc)
                            result.rejected += 1
                        else:
                            result.inserted += outcome == "inserted"
                            result.merged += outcome == "merged"
                else:
                    log.warning("source %s failed: %s", row["name"], error)
                _record(conn, row, now, result, state)  # items first, cursor second (commits both)
                results.append(result)
    return results


def source_health(row: sqlite3.Row) -> str:
    if row["status"] != "active":
        return row["status"]
    if row["failures"] >= UNHEALTHY_AFTER:
        return "unhealthy"
    if row["failures"]:
        return "retrying"
    return "ok" if row["last_ok_at"] else "pending"
