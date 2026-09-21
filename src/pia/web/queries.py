"""Read-model queries for the UI. Everything here takes a (read-only) connection and only SELECTs."""

import json
import sqlite3

from pia.briefing.render import SECTIONS
from pia.history import get_briefing
from pia.normalize import canonicalize_url
from pia.state import get_checkpoint
from pia.web.briefing_view import parse_briefing

_SECTION_LABEL = {category: label.split(" ", 1)[1] for category, label in SECTIONS}

# An item may have been triaged more than once; the latest enrichment wins (same rule as the core).
_FROM_ITEMS = """
    FROM items i
    LEFT JOIN briefings b ON b.id = i.briefing_id
    LEFT JOIN enrichments e ON e.item_id = i.id
         AND e.rowid = (SELECT MAX(rowid) FROM enrichments WHERE item_id = i.id)
    LEFT JOIN favorites f ON f.item_id = i.id
"""
_ITEM_COLUMNS = """
    i.id, i.title, i.url, i.source, i.signals, i.published_at, i.discovered_at, i.status, i.briefing_id,
    b.created_at AS briefed_at, e.category, e.summary, e.importance,
    (f.item_id IS NOT NULL) AS favorite, f.created_at AS favorited_at
"""


def _item(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "title": row["title"],
        "url": row["url"],
        "source": row["source"],
        "seen_on": sorted(json.loads(row["signals"])),
        "category": row["category"],
        "summary": row["summary"] or None,
        "importance": row["importance"],
        "published_at": row["published_at"],
        "discovered_at": row["discovered_at"],
        "briefing_id": row["briefing_id"],
        "briefed_at": row["briefed_at"],
        "status": row["status"],
        "favorite": bool(row["favorite"]),
        "favorited_at": row["favorited_at"],
    }


def _like(text: str) -> str:
    """Search text as a literal: '%' and '_' must not act as wildcards."""
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def library_page(
    conn: sqlite3.Connection,
    *,
    q: str | None = None,
    category: str | None = None,
    source: str | None = None,
    favorite: bool = False,
    include_skipped: bool = False,
    limit: int = 30,
    offset: int = 0,
) -> tuple[list[dict], int]:
    """Library and Favorites are the same query with a different filter.

    Favorites ignore an item's status (you can favorite something the briefing skipped) and are
    ordered by when you favorited them; the library is newest briefing first, then importance.
    """
    where: list[str] = []
    params: list = []
    if favorite:
        where.append("f.item_id IS NOT NULL")
        order = "f.created_at DESC, i.id DESC"
    else:
        statuses = ["briefed", "skipped"] if include_skipped else ["briefed"]
        where.append(f"i.status IN ({','.join('?' * len(statuses))})")
        params += statuses
        order = "COALESCE(i.briefing_id, 0) DESC, e.importance DESC, COALESCE(i.published_at, i.discovered_at) DESC, i.id DESC"
    if q:
        where.append("(i.title LIKE ? ESCAPE '\\' OR e.summary LIKE ? ESCAPE '\\')")
        params += [_like(q), _like(q)]
    if category:
        where.append("e.category = ?")
        params.append(category)
    if source:
        where.append("i.source = ?")
        params.append(source)

    clause = " AND ".join(where)
    total = conn.execute(f"SELECT COUNT(*) {_FROM_ITEMS} WHERE {clause}", params).fetchone()[0]
    rows = conn.execute(
        f"SELECT {_ITEM_COLUMNS} {_FROM_ITEMS} WHERE {clause} ORDER BY {order} LIMIT ? OFFSET ?",
        [*params, limit, offset],
    ).fetchall()
    return [_item(row) for row in rows], total


def status(conn: sqlite3.Connection) -> dict:
    checkpoint = get_checkpoint(conn)
    count = lambda sql: conn.execute(sql).fetchone()[0]  # noqa: E731
    return {
        "last_checked": checkpoint.isoformat() if checkpoint else None,
        "latest_briefing_id": conn.execute("SELECT MAX(id) FROM briefings").fetchone()[0],
        "counts": {
            "library": count("SELECT COUNT(*) FROM items WHERE status = 'briefed'"),
            "skipped": count("SELECT COUNT(*) FROM items WHERE status = 'skipped'"),
            "favorites": count("SELECT COUNT(*) FROM favorites"),
        },
        "sources": [
            r[0] for r in conn.execute("SELECT DISTINCT source FROM items WHERE status IN ('briefed', 'skipped') ORDER BY source")
        ],
    }


def _lookup(conn: sqlite3.Connection, url: str) -> dict:
    """Find the stored item a briefing entry points at (briefings link to the item's url)."""
    row = conn.execute(
        f"SELECT i.id, i.source, e.category, (f.item_id IS NOT NULL) AS favorite {_FROM_ITEMS} WHERE i.url = ?", (url,)
    ).fetchone()
    if row is None:
        try:
            canonical = canonicalize_url(url)
        except ValueError:
            canonical = None
        if canonical:
            row = conn.execute(
                f"SELECT i.id, i.source, e.category, (f.item_id IS NOT NULL) AS favorite {_FROM_ITEMS} "
                "WHERE i.canonical_url = ?",
                (canonical,),
            ).fetchone()
    if row is None:
        return {"item_id": None, "favorite": False, "source": None, "category": None}
    return {"item_id": row["id"], "favorite": bool(row["favorite"]), "source": row["source"], "category": row["category"]}


def briefing_detail(conn: sqlite3.Connection, briefing_id: int) -> dict | None:
    row = get_briefing(conn, briefing_id)
    if row is None:
        return None
    counts = conn.execute(
        "SELECT SUM(status = 'briefed'), SUM(status = 'skipped') FROM items WHERE briefing_id = ?", (briefing_id,)
    ).fetchone()
    detail = {
        "id": row["id"],
        "created_at": row["created_at"],
        "covers_from": row["covers_from"],
        "covers_until": row["covers_until"],
        "shown": counts[0] or 0,
        "skipped": counts[1] or 0,
        "markdown": row["rendered_md"],
        "parsed": False,
        "intro": None,
        "notes": [],
        "headlines": [],
        "sections": [],
        "hidden": 0,
        "empty_message": None,
    }
    parsed = parse_briefing(row["rendered_md"])
    if parsed is None:
        return detail

    detail.update(parsed=True, intro=parsed.intro or None, notes=parsed.notes, hidden=parsed.hidden, empty_message=parsed.empty_message)
    detail["headlines"] = [
        {
            **_lookup(conn, h.url),
            "title": h.title,
            "url": h.url,
            "explanation": h.explanation,
            "why_it_matters": h.why_it_matters,
            "via": h.via,
        }
        for h in parsed.headlines
    ]
    detail["sections"] = [
        {
            "category": category,
            "label": _SECTION_LABEL[category],
            "items": [{**_lookup(conn, e.url), "title": e.title, "url": e.url, "summary": e.summary} for e in entries],
        }
        for category, entries in parsed.sections
        if entries
    ]
    return detail
