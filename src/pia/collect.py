"""Fetch every source and store what it returns.

Deterministic orchestration: for each source, work out its window, fetch, store,
then advance that source's cursor. One source failing never affects the others.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

import httpx

from pia.db import store_items
from pia.http import SourceError
from pia.sources.base import Source
from pia.state import get_cursor, get_first_attempt, record_fetch_run

log = logging.getLogger(__name__)

FIRST_RUN_LOOKBACK = timedelta(days=3)  # how far back a brand-new install looks
MAX_LOOKBACK = timedelta(days=14)  # never fetch further back than this, however long away
OVERLAP = timedelta(hours=6)  # re-fetch a little before the cursor (see collect())


@dataclass
class SourceResult:
    name: str
    ok: bool
    inserted: int = 0
    merged: int = 0
    rejected: int = 0
    error: str | None = None


def collect(
    conn,
    client: httpx.Client | None,
    sources: list[Source],
    now: datetime,
    *,
    first_run_lookback: timedelta = FIRST_RUN_LOOKBACK,
    max_lookback: timedelta = MAX_LOOKBACK,
    overlap: timedelta = OVERLAP,
) -> list[SourceResult]:
    results = []
    for source in sources:
        cursor = get_cursor(conn, source.name)
        # Start slightly BEFORE the cursor: sources index items late (an arXiv paper can
        # appear hours after its submit time). Re-fetched items are absorbed by dedup, so
        # overlap costs nothing and prevents gaps.
        # No successful fetch yet: look back from the FIRST ATTEMPT (not from now), so a source
        # that was down when the app was first opened still catches up on what it missed.
        anchor = get_first_attempt(conn, source.name) or now
        since = cursor - overlap if cursor else anchor - first_run_lookback
        since = max(since, now - max_lookback)

        try:
            items = source.fetch(client, since, now)
        except SourceError as exc:
            error = str(exc)
        except Exception as exc:  # noqa: BLE001 - a buggy adapter must not kill the run
            log.exception("source %s crashed", source.name)
            error = f"unexpected error: {exc!r}"
        else:
            # Order matters: items are stored first, the cursor advances second. A crash
            # in between just re-fetches the same window next time, which is harmless.
            report = store_items(conn, items, now)
            record_fetch_run(conn, source.name, now, fetched_until=now, item_count=len(items))
            conn.commit()
            results.append(
                SourceResult(source.name, True, report.inserted, report.merged, report.rejected)
            )
            continue

        log.warning("source %s failed: %s", source.name, error)
        record_fetch_run(conn, source.name, now, error=error)
        conn.commit()
        results.append(SourceResult(source.name, False, error=error))
    return results
