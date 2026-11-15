"""Read models: the queries behind Home, the feeds, search, item detail and the profile.

Kept apart from the HTTP layer so the worker (which pre-writes briefs for Home) and the tests can use the same
selection logic the app shows.
"""

import json
import re
import sqlite3
from datetime import date, datetime

from pia.store.db import days_ago

FEED_COLUMNS = """i.id, i.title, i.url, i.source_name, i.kind, i.summary, i.published_at, i.acquired_at, i.sort_at,
                  i.importance, i.relevance, i.score, i.rank, i.read_at, i.saved_at, i.dismissed_at, i.signals, i.stage,
                  i.context_boost, i.muted, d.slug AS domain_slug, d.short AS domain_short, d.hue AS domain_hue,
                  (i.content IS NOT NULL AND i.content != '') AS has_text"""
HOME_WINDOW_DAYS = 10
HOME_MAX = 12
HOME_PER_DOMAIN = 3
SORTS = {"latest": "i.sort_at DESC, i.id DESC", "important": "i.rank DESC, i.id DESC"}


def feed_item(row: sqlite3.Row, topics: list[dict] | None = None) -> dict:
    signals = json.loads(row["signals"])
    item = {
        "id": row["id"], "title": row["title"], "url": row["url"], "source": row["source_name"], "kind": row["kind"],
        "summary": row["summary"], "published_at": row["published_at"], "acquired_at": row["acquired_at"], "sort_at": row["sort_at"],
        "importance": row["importance"], "relevance": row["relevance"], "score": row["score"],
        "read": row["read_at"] is not None, "saved": row["saved_at"] is not None, "dismissed": row["dismissed_at"] is not None,
        "domain": row["domain_slug"], "domain_short": row["domain_short"], "hue": row["domain_hue"],
        "assessed": row["stage"] == "assessed", "has_text": bool(row["has_text"]), "resurfaced": row["context_boost"] > 0.02,
        "muted": bool(row["muted"]), "spam": bool(signals.get("clone_swarm")),
        "signals": {key: signals[key] for key in ("points", "comments", "stars", "upvotes", "deadline", "location", "organisation") if signals.get(key) not in (None, "")},
    }
    if topics is not None:
        item["topics"] = topics
    return item


def _attach_topics(conn: sqlite3.Connection, rows: list[sqlite3.Row]) -> list[dict]:
    if not rows:
        return []
    ids = [row["id"] for row in rows]
    by_item: dict[int, list[dict]] = {}
    for row in conn.execute(
        f"""SELECT it.item_id, t.id, t.name, t.level, t.kind FROM item_topics it JOIN topics t ON t.id = it.topic_id
            WHERE it.item_id IN ({','.join('?' * len(ids))}) ORDER BY COALESCE(t.level, 0) DESC, it.weight DESC""", ids):
        if len(by_item.setdefault(row["item_id"], [])) < 4:
            by_item[row["item_id"]].append({"id": row["id"], "name": row["name"], "level": row["level"], "kind": row["kind"]})
    return [feed_item(row, by_item.get(row["id"], [])) for row in rows]


def fts_query(text: str) -> str | None:
    """Turn what a person typed into a safe FTS5 query: quoted terms, all required, the last one a prefix."""
    words = re.findall(r"[\w+#.-]{2,}", text.lower())[:8]
    if not words:
        return None
    terms = [f'"{word}"' for word in words[:-1]] + [f'"{words[-1]}"*']
    return " AND ".join(terms)


def list_items(
    conn: sqlite3.Connection, *, domain: str | None = None, view: str = "latest", state: str = "active", kind: str | None = None,
    topic_id: int | None = None, source: str | None = None, q: str | None = None, days: int | None = None,
    now: datetime | None = None, limit: int = 30, offset: int = 0,
) -> dict:
    """One page of a feed. `state`: active (not dismissed), unread, read, saved, dismissed, all."""
    joins, where, params = ["LEFT JOIN domains d ON d.id = i.domain_id"], [], []
    if domain:
        joins.append("JOIN item_domains idom ON idom.item_id = i.id JOIN domains fd ON fd.id = idom.domain_id AND fd.slug = ?")
        params.append(domain)
    if topic_id is not None:
        joins.append("JOIN item_topics ft ON ft.item_id = i.id AND ft.topic_id = ?")
        params.append(topic_id)
    order = SORTS.get(view, SORTS["latest"])
    match = fts_query(q) if q else None
    if q and not match:
        return {"items": [], "total": 0, "has_more": False}
    if match:
        joins.append("JOIN items_fts f ON f.rowid = i.id")
        where.append("items_fts MATCH ?")
        params.append(match)
        if view == "relevance":
            order = "bm25(items_fts, 8.0, 1.0, 3.0), i.rank DESC"
    state_sql = {
        "active": "i.dismissed_at IS NULL", "unread": "i.dismissed_at IS NULL AND i.read_at IS NULL",
        "read": "i.dismissed_at IS NULL AND i.read_at IS NOT NULL", "saved": "i.saved_at IS NOT NULL",
        "dismissed": "i.dismissed_at IS NOT NULL", "all": "1 = 1",
    }.get(state, "i.dismissed_at IS NULL")
    where.append(state_sql)
    if state == "saved" and view == "latest":
        order = "i.saved_at DESC"
    if kind:
        where.append("i.kind = ?")
        params.append(kind)
    if source:
        where.append("i.source_name = ?")
        params.append(source)
    if days and now:
        where.append("i.sort_at >= ?")
        params.append(days_ago(now, days))
    sql = f"FROM items i {' '.join(joins)} WHERE {' AND '.join(where)}"
    total = conn.execute(f"SELECT COUNT(*) {sql}", params).fetchone()[0]
    rows = conn.execute(f"SELECT {FEED_COLUMNS} {sql} ORDER BY {order} LIMIT ? OFFSET ?", (*params, limit, offset)).fetchall()
    return {"items": _attach_topics(conn, rows), "total": total, "has_more": offset + len(rows) < total}


def featured(conn: sqlite3.Connection, now: datetime, *, limit: int = HOME_MAX) -> list[dict]:
    """Home's featured items: the best unread, recent, unmuted items, with a per-space cap so that one busy space
    cannot fill the page, and one item per story. Spaces the owner hid (weight 0) are left out."""
    today = now.date().isoformat()
    rows = conn.execute(
        f"""SELECT {FEED_COLUMNS} FROM items i LEFT JOIN domains d ON d.id = i.domain_id
            LEFT JOIN opportunities o ON o.item_id = i.id
            WHERE i.read_at IS NULL AND i.dismissed_at IS NULL AND i.muted = 0 AND COALESCE(d.weight, 1) > 0
              AND (i.sort_at >= ? OR i.context_boost > 0.02) AND (o.deadline IS NULL OR o.deadline >= ?)
            ORDER BY i.rank DESC LIMIT 120""", (days_ago(now, HOME_WINDOW_DAYS), today)).fetchall()
    chosen, per_domain, seen_stories = [], {}, set()
    for row in rows:
        if row["id"] in seen_stories or per_domain.get(row["domain_slug"], 0) >= HOME_PER_DOMAIN:
            continue
        chosen.append(row)
        per_domain[row["domain_slug"]] = per_domain.get(row["domain_slug"], 0) + 1
        for relation in conn.execute("SELECT item_id, other_id FROM relations WHERE kind = 'same_story' AND (item_id = ? OR other_id = ?)", (row["id"], row["id"])):
            seen_stories.update((relation["item_id"], relation["other_id"]))
        if len(chosen) >= limit:
            break
    return _attach_topics(conn, chosen)


def domain_overview(conn: sqlite3.Connection, now: datetime) -> list[dict]:
    rows = conn.execute(
        """SELECT d.id, d.slug, d.name, d.short, d.description, d.hue, d.weight, d.affinity, d.position,
                  COUNT(i.id) AS total,
                  SUM(CASE WHEN i.read_at IS NULL AND i.dismissed_at IS NULL THEN 1 ELSE 0 END) AS unread,
                  SUM(CASE WHEN i.acquired_at >= ? THEN 1 ELSE 0 END) AS new_today,
                  MAX(i.sort_at) AS latest
           FROM domains d LEFT JOIN item_domains x ON x.domain_id = d.id LEFT JOIN items i ON i.id = x.item_id
           WHERE d.enabled = 1 GROUP BY d.id ORDER BY d.position""", (days_ago(now, 1),)).fetchall()
    return [{**dict(row), "unread": row["unread"] or 0, "new_today": row["new_today"] or 0} for row in rows]


def item_detail(conn: sqlite3.Connection, item_id: int) -> dict | None:
    row = conn.execute(f"SELECT {FEED_COLUMNS}, i.content, i.author, i.canonical_url, i.model_importance, i.fit, i.popularity FROM items i LEFT JOIN domains d ON d.id = i.domain_id WHERE i.id = ?", (item_id,)).fetchone()
    if row is None:
        return None
    item = _attach_topics(conn, [row])[0]
    item["topics"] = [dict(topic) for topic in conn.execute(
        "SELECT t.id, t.name, t.level, t.kind FROM item_topics it JOIN topics t ON t.id = it.topic_id WHERE it.item_id = ? ORDER BY COALESCE(t.level, 0) DESC, it.weight DESC LIMIT 12", (item_id,))]
    item.update(
        content=row["content"], author=row["author"], popularity=row["popularity"], model_importance=row["model_importance"], fit=row["fit"],
        domains=[dict(d) for d in conn.execute("SELECT d.slug, d.short, d.hue FROM item_domains x JOIN domains d ON d.id = x.domain_id WHERE x.item_id = ? ORDER BY x.weight DESC", (item_id,))],
        sources=[dict(s) for s in conn.execute("SELECT source_name AS name, url, seen_at FROM item_sources WHERE item_id = ? ORDER BY seen_at", (item_id,))],
        feedback=[dict(f) for f in conn.execute("SELECT kind, reason FROM feedback WHERE item_id = ?", (item_id,))],
    )
    opportunity = conn.execute("SELECT * FROM opportunities WHERE item_id = ?", (item_id,)).fetchone()
    item["opportunity"] = dict(opportunity) if opportunity else None
    return item


def opportunities(conn: sqlite3.Connection, today: date, *, show: str = "open", status: str | None = None, first_year: bool = False, kind: str | None = None, limit: int = 200) -> list[dict]:
    """`show`: open (not past deadline, undated last), tracked (anything with a status), expired, all."""
    where, params = ["i.dismissed_at IS NULL"], []
    if show == "open":
        where.append("(o.deadline IS NULL OR o.deadline >= ?)")
        params.append(today.isoformat())
    elif show == "expired":
        where.append("o.deadline < ?")
        params.append(today.isoformat())
    elif show == "tracked":
        where.append("o.status != ''")
    if status:
        where.append("o.status = ?")
        params.append(status)
    if first_year:
        where.append("o.first_year = 'yes'")
    if kind:
        where.append("o.type = ?")
        params.append(kind)
    rows = conn.execute(
        f"""SELECT o.*, i.title, i.url, i.source_name, i.summary, i.acquired_at, i.published_at, i.relevance, i.read_at, i.saved_at,
                   d.slug AS domain_slug, d.short AS domain_short, d.hue AS domain_hue
            FROM opportunities o JOIN items i ON i.id = o.item_id LEFT JOIN domains d ON d.id = i.domain_id
            WHERE {' AND '.join(where)}
            ORDER BY (o.deadline IS NULL), o.deadline ASC, i.relevance DESC LIMIT ?""", (*params, limit)).fetchall()
    out = []
    for row in rows:
        entry = dict(row)
        days = (date.fromisoformat(row["deadline"]) - today).days if row["deadline"] else None
        entry["days_left"] = days
        entry["state"] = "undated" if days is None else "expired" if days < 0 else "closing" if days <= 7 else "open"
        entry["read"], entry["saved"] = row["read_at"] is not None, row["saved_at"] is not None
        out.append(entry)
    return out


def profile(conn: sqlite3.Connection) -> dict:
    domains = [dict(row) for row in conn.execute("SELECT id, slug, name, short, hue, weight, affinity FROM domains WHERE enabled = 1 ORDER BY position")]
    topics = [dict(row) for row in conn.execute(
        """SELECT t.id, t.name, t.kind, t.level, t.affinity, t.origin, t.item_count, d.slug AS domain
           FROM topics t LEFT JOIN domains d ON d.id = t.domain_id
           WHERE t.level IS NOT NULL OR t.origin != 'discovered' OR ABS(t.affinity) >= 0.05
           ORDER BY COALESCE(t.level, 0) DESC, t.affinity DESC, t.name""")]
    learned = [dict(row) for row in conn.execute(
        """SELECT e.kind, COUNT(*) AS n FROM events e WHERE e.kind IN ('open','save','like','useful','dismiss','not_relevant','not_interested_topic','low_quality')
           GROUP BY e.kind ORDER BY n DESC""")]
    rated = conn.execute("SELECT COUNT(*) FROM topics WHERE rated_at IS NOT NULL").fetchone()[0]
    return {"domains": domains, "topics": topics, "signals": learned, "rated_in_app": rated}


def onboarding(conn: sqlite3.Connection) -> list[dict]:
    """Seed topics grouped by space, with how many the owner has rated: the questionnaire, a space at a time."""
    groups = []
    for domain in conn.execute("SELECT id, slug, name, short, hue FROM domains WHERE enabled = 1 ORDER BY position"):
        topics = [dict(row) for row in conn.execute(
            "SELECT id, name, level, rated_at IS NOT NULL AS rated FROM topics WHERE domain_id = ? AND origin != 'discovered' ORDER BY id", (domain["id"],))]
        groups.append({**dict(domain), "topics": topics, "rated": sum(1 for topic in topics if topic["rated"]), "total": len(topics)})
    return groups


def topic_timeline(conn: sqlite3.Connection, topic_id: int, limit: int = 100) -> dict | None:
    topic = conn.execute("SELECT t.id, t.name, t.kind, t.level, t.affinity, t.item_count, d.short AS domain FROM topics t LEFT JOIN domains d ON d.id = t.domain_id WHERE t.id = ?", (topic_id,)).fetchone()
    if topic is None:
        return None
    rows = conn.execute(
        f"SELECT {FEED_COLUMNS} FROM items i JOIN item_topics it ON it.item_id = i.id AND it.topic_id = ? LEFT JOIN domains d ON d.id = i.domain_id ORDER BY i.sort_at DESC LIMIT ?",
        (topic_id, limit)).fetchall()
    return {"topic": dict(topic), "items": _attach_topics(conn, rows)}
