"""Source management: what the Sources page does. Adding, approving, pausing, removing, and organisations.

Adding always goes through a look first: PIA fetches the address, works out what kind of source it is (a feed, a
known job-board API, or a page to watch), and for feeds measures it, so the owner sees what they are about to
follow before it is switched on.
"""

import json
import re
import sqlite3
from datetime import datetime, timedelta

import httpx

from pia.acquire.adapters import JOB_BOARDS, OPPORTUNITY_PATTERN, build_adapter, detect_job_board
from pia.acquire.discovery import evaluate_preview
from pia.acquire.feeds import find_feed
from pia.acquire.runner import source_health
from pia.acquire.safefetch import FetchFailed, FetchRefused, guarded_get
from pia.acquire.textextract import extract
from pia.http import SourceError
from pia.store.db import iso

STATUSES = ("candidate", "active", "paused", "rejected")


def source_dict(row: sqlite3.Row) -> dict:
    data = dict(row)
    for key in ("config", "domains", "evaluation"):
        data[key] = json.loads(data[key]) if data.get(key) else ({} if key == "config" else [] if key == "domains" else None)
    data.pop("state", None)
    data["health"] = source_health(row)
    data["general"] = bool(data["general"])
    return data


def list_sources(conn: sqlite3.Connection, now: datetime | None = None) -> list[dict]:
    week_ago = iso(now - timedelta(days=7)) if now else "9999"
    stats = {row["source_id"]: row for row in conn.execute(
        """SELECT source_id, COUNT(*) AS item_count, SUM(acquired_at >= ?) AS week_count, ROUND(AVG(score), 3) AS avg_score,
                  SUM(saved_at IS NOT NULL) AS saved_count
           FROM items WHERE source_id IS NOT NULL GROUP BY source_id""", (week_ago,))}
    rows = conn.execute("SELECT s.*, o.name AS organisation FROM sources s LEFT JOIN organisations o ON o.id = s.org_id ORDER BY s.status, s.name COLLATE NOCASE").fetchall()
    out = []
    for row in rows:
        data = source_dict(row)
        found = stats.get(row["id"])
        data.update(item_count=found["item_count"] if found else 0, week_count=(found["week_count"] or 0) if found else 0,
                    avg_score=found["avg_score"] if found else None, saved_count=(found["saved_count"] or 0) if found else 0)
        out.append(data)
    return out


def inspect(conn: sqlite3.Connection, client: httpx.Client, url: str, now: datetime, *, resolver=None) -> dict:
    """Look at an address and say what PIA could do with it. Never stores anything."""
    url = url.strip()
    if "://" not in url:
        url = "https://" + url
    extra = {"resolver": resolver} if resolver else {}
    board = detect_job_board(url)
    if board:
        preset, board_id = board
        adapter = build_adapter({"name": "preview", "kind": "json_api", "url": "", "config": json.dumps({"preset": preset, "board": board_id}), "state": "{}"})
        items = adapter.fetch(client, now, now)
        return {"kind": "json_api", "preset": preset, "board": board_id, "url": url, "title": f"{board_id} ({preset} job board)",
                "summary": f"A {preset.title()} job board. {len(items)} current postings look like early-career or student opportunities.",
                "samples": [item.title for item in items[:6]], "purpose": "opportunities"}
    try:
        preview = find_feed(client, url, now, **extra)
    except SourceError as feed_error:
        try:
            fetched = guarded_get(client, url, **extra)
        except (FetchRefused, FetchFailed) as exc:
            raise SourceError(str(exc)) from exc
        page = extract(fetched.text, fetched.url)
        if len(page.links) < 3:
            raise SourceError(f"{feed_error}; and the page has no readable links to watch (it may be rendered by JavaScript)") from feed_error
        pattern = re.compile(OPPORTUNITY_PATTERN, re.I)
        matching = [text for _, text in page.links if 6 <= len(text) <= 220 and pattern.search(text)]
        return {"kind": "page", "url": fetched.url, "title": page.title or fetched.url,
                "summary": f"No feed found. PIA can watch this page for new links instead ({len(page.links)} links now, {len(matching)} look like opportunities).",
                "samples": matching[:6] or [text for _, text in page.links[:6]], "purpose": "opportunities" if matching else "news"}
    evaluation = evaluate_preview(conn, preview, now)
    return {"kind": "rss", "url": preview.url, "homepage": preview.homepage, "title": preview.title or preview.url,
            "summary": " ".join(evaluation["notes"]), "samples": preview.samples, "evaluation": evaluation, "purpose": "news",
            "already_followed": bool(conn.execute("SELECT 1 FROM sources WHERE url = ?", (preview.url,)).fetchone())}


def _unique_name(conn: sqlite3.Connection, name: str) -> str:
    base, candidate, suffix = name.strip()[:80] or "Source", name.strip()[:80] or "Source", 2
    while conn.execute("SELECT 1 FROM sources WHERE name = ?", (candidate,)).fetchone():
        candidate, suffix = f"{base} ({suffix})", suffix + 1
    return candidate


def add_source(conn: sqlite3.Connection, found: dict, now: datetime, *, name: str | None = None, domains: list[str] | None = None,
               org_id: int | None = None, purpose: str | None = None, interval: int = 240) -> int:
    """Store what `inspect` found as an ACTIVE source the owner chose (origin 'user')."""
    kind = found["kind"]
    config: dict = {}
    organisation = ""
    if org_id is not None:
        row = conn.execute("SELECT name FROM organisations WHERE id = ?", (org_id,)).fetchone()
        organisation = row["name"] if row else ""
    if kind == "json_api":
        if found["preset"] not in JOB_BOARDS:
            raise ValueError("unsupported job board")
        config = {"preset": found["preset"], "board": found["board"], "organisation": organisation or found["board"]}
    elif kind == "page":
        config = {"organisation": organisation}
        if (purpose or found.get("purpose")) != "opportunities":
            config.update(match="", kind_hint="announcement")
    elif kind != "rss":
        raise ValueError(f"cannot add a source of kind {kind!r}")
    existing = conn.execute("SELECT id FROM sources WHERE url = ? AND kind = ?", (found["url"], kind)).fetchone()
    if existing:
        conn.execute("UPDATE sources SET status = 'active', decided_at = ?, org_id = COALESCE(?, org_id) WHERE id = ?", (iso(now), org_id, existing["id"]))
        conn.commit()
        return existing["id"]
    evaluation = found.get("evaluation")
    source_id = conn.execute(
        """INSERT INTO sources (name, kind, url, homepage, description, config, domains, status, origin, purpose, trust,
                                interval_minutes, evaluation, org_id, created_at, decided_at)
           VALUES (?, ?, ?, ?, '', ?, ?, 'active', 'user', ?, 0.7, ?, ?, ?, ?, ?)""",
        (_unique_name(conn, name or found.get("title") or found["url"]), kind, found["url"], found.get("homepage"), json.dumps(config),
         json.dumps(domains or (evaluation or {}).get("domains") or []), purpose or found.get("purpose", "news"), interval,
         json.dumps(evaluation) if evaluation else None, org_id, iso(now), iso(now)),
    ).lastrowid
    conn.commit()
    return source_id


def set_status(conn: sqlite3.Connection, source_id: int, status: str, now: datetime) -> bool:
    if status not in STATUSES:
        raise ValueError(f"unknown status {status!r}")
    cursor = conn.execute(
        "UPDATE sources SET status = ?, decided_at = ?, next_fetch_at = CASE WHEN ? = 'active' THEN NULL ELSE next_fetch_at END, failures = 0 WHERE id = ?",
        (status, iso(now), status, source_id))
    conn.commit()
    return cursor.rowcount > 0


def update_source(conn: sqlite3.Connection, source_id: int, *, interval: int | None = None, domains: list[str] | None = None, trust: float | None = None, name: str | None = None) -> bool:
    row = conn.execute("SELECT id FROM sources WHERE id = ?", (source_id,)).fetchone()
    if row is None:
        return False
    if interval is not None:
        conn.execute("UPDATE sources SET interval_minutes = ? WHERE id = ?", (max(15, min(10080, int(interval))), source_id))
    if domains is not None:
        known = {r["slug"] for r in conn.execute("SELECT slug FROM domains")}
        conn.execute("UPDATE sources SET domains = ? WHERE id = ?", (json.dumps([slug for slug in domains if slug in known]), source_id))
    if trust is not None:
        conn.execute("UPDATE sources SET trust = ? WHERE id = ?", (max(0.0, min(1.0, float(trust))), source_id))
    if name:
        conn.execute("UPDATE sources SET name = ? WHERE id = ?", (_unique_name(conn, name), source_id))
    conn.commit()
    return True


def delete_source(conn: sqlite3.Connection, source_id: int) -> bool:
    """Remove a source. Items it brought in are kept (their attribution survives by name)."""
    cursor = conn.execute("DELETE FROM sources WHERE id = ?", (source_id,))
    conn.commit()
    return cursor.rowcount > 0


# ---------- organisations (the opportunities watch list) ----------


def list_organisations(conn: sqlite3.Connection) -> list[dict]:
    organisations = [dict(row) for row in conn.execute("SELECT * FROM organisations ORDER BY name COLLATE NOCASE")]
    for organisation in organisations:
        organisation["sources"] = [source_dict(row) for row in conn.execute("SELECT * FROM sources WHERE org_id = ? ORDER BY name", (organisation["id"],))]
        organisation["open_count"] = conn.execute(
            """SELECT COUNT(*) FROM sources s JOIN items i ON i.source_id = s.id JOIN opportunities o ON o.item_id = i.id
               WHERE s.org_id = ? AND (o.deadline IS NULL OR o.deadline >= date('now'))""", (organisation["id"],)).fetchone()[0]
    return organisations


def add_organisation(conn: sqlite3.Connection, name: str, now: datetime, *, homepage: str | None = None, notes: str = "") -> int:
    name = " ".join(name.split())
    if not 2 <= len(name) <= 100:
        raise ValueError("an organisation name needs 2 to 100 characters")
    existing = conn.execute("SELECT id FROM organisations WHERE name = ? COLLATE NOCASE", (name,)).fetchone()
    if existing:
        return existing["id"]
    org_id = conn.execute("INSERT INTO organisations (name, homepage, notes, created_at) VALUES (?, ?, ?, ?)", (name, homepage or None, notes[:2000], iso(now))).lastrowid
    conn.commit()
    return org_id


def delete_organisation(conn: sqlite3.Connection, org_id: int) -> bool:
    """Removes the organisation and the pages watched for it (ON DELETE CASCADE). Opportunities already found stay."""
    cursor = conn.execute("DELETE FROM organisations WHERE id = ?", (org_id,))
    conn.commit()
    return cursor.rowcount > 0
