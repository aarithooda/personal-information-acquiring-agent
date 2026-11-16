"""Deeper exploration: fetching the text behind an item, legitimately.

  * arXiv papers: arXiv's own HTML rendering of the paper (open access), when one exists.
  * anything else: the linked page, through the guarded fetcher, honouring robots.txt.

Fetched text is a private analysis cache. It is what briefs and opportunity extraction read; the app shows the
full text only for open-access papers and otherwise a short excerpt beside a link to the original. Failures are
cached too, so a dead or forbidden URL is not retried on every open.
"""

import re
import sqlite3
from datetime import datetime, timedelta

import httpx

from pia.acquire.safefetch import FetchFailed, FetchRefused, RobotsCache, guarded_get
from pia.acquire.textextract import extract
from pia.store.db import iso, parse_ts

MAX_STORED_CHARS = 80_000
EXCERPT_CHARS = 1400
RETRY_FAILED_AFTER = timedelta(days=7)
_ARXIV = re.compile(r"^https://arxiv\.org/abs/(\d{4}\.\d{4,5})$")
_robots = RobotsCache()


def is_open_access(canonical_url: str) -> bool:
    return bool(_ARXIV.match(canonical_url))


def fetch_text(conn: sqlite3.Connection, client: httpx.Client, item_id: int, now: datetime, *, resolver=None, force: bool = False) -> dict:
    """Fetch and cache the item's text. Returns {ok, chars, error}. Never raises for a failed fetch."""
    item = conn.execute("SELECT id, url, canonical_url FROM items WHERE id = ?", (item_id,)).fetchone()
    if item is None:
        return {"ok": False, "chars": 0, "error": "no such item"}
    cached = conn.execute("SELECT ok, text, fetched_at, error FROM item_texts WHERE item_id = ?", (item_id,)).fetchone()
    if cached and not force and (cached["ok"] or now - parse_ts(cached["fetched_at"]) < RETRY_FAILED_AFTER):
        return {"ok": bool(cached["ok"]), "chars": len(cached["text"] or ""), "error": cached["error"]}

    arxiv = _ARXIV.match(item["canonical_url"])
    target = f"https://arxiv.org/html/{arxiv.group(1)}" if arxiv else item["url"]
    text, final_url, error, extractor = "", None, None, "arxiv-html" if arxiv else "page"
    try:
        fetched = guarded_get(client, target, resolver=resolver, robots=None if arxiv else _robots, max_bytes=3_000_000)
        final_url = fetched.url
        text = extract(fetched.text, fetched.url).text[:MAX_STORED_CHARS]
        if len(text) < 400:
            error, text = "the page has too little readable text (it may need JavaScript or a login)", ""
    except FetchRefused as exc:
        error = f"not fetched: {exc}"
    except FetchFailed as exc:
        error = f"fetch failed: {exc}"

    conn.execute(
        "INSERT OR REPLACE INTO item_texts (item_id, final_url, fetched_at, ok, text, extractor, error) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (item_id, final_url, iso(now), 0 if error else 1, text or None, extractor, error),
    )
    conn.commit()
    return {"ok": not error, "chars": len(text), "error": error}


def reader_view(conn: sqlite3.Connection, item_id: int) -> dict | None:
    """What the app may display: full text for open-access papers, an excerpt for everything else."""
    row = conn.execute(
        "SELECT t.ok, t.text, t.error, t.final_url, t.fetched_at, i.canonical_url, i.url FROM items i LEFT JOIN item_texts t ON t.item_id = i.id WHERE i.id = ?",
        (item_id,),
    ).fetchone()
    if row is None:
        return None
    if row["ok"] is None:
        return {"status": "not_fetched", "original": row["url"]}
    if not row["ok"]:
        return {"status": "unavailable", "reason": row["error"], "original": row["url"]}
    text = row["text"] or ""
    full = is_open_access(row["canonical_url"])
    shown = text if full else text[:EXCERPT_CHARS]
    return {
        "status": "ok", "mode": "full" if full else "excerpt", "paragraphs": [p for p in shown.split("\n\n") if p.strip()],
        "truncated": len(text) > len(shown), "chars": len(text), "source": row["final_url"] or row["url"], "original": row["url"],
        "fetched_at": row["fetched_at"],
    }
