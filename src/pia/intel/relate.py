"""Connected information: which earlier items a new item relates to.

Deliberately simple and deterministic (PRD 7: useful relationships first, no knowledge graph as a prerequisite):
  references   the new item's text or URL cites the earlier item's URL (or the same arXiv paper)
  same_story   near-identical titles within a few days: the same development seen through another source
  related      shared entities or specific topics, plus overlapping title words

Candidates come from the topic index and the full-text index, so each item is compared with tens of items, not
with the whole library. When a new item relates to an older one, the older one gets a context boost: that is
what lets a three-week-old paper resurface in Important when something builds on it.
"""

import re
import sqlite3
from datetime import datetime

from pia.intel.score import add_context_boost
from pia.store.db import days_ago, iso, parse_ts

WINDOW_DAYS = 180
MAX_RELATED = 6
MIN_SCORE = 0.3
SAME_STORY_TITLE = 0.6
SAME_STORY_DAYS = 10
RARE_TOPIC_ITEMS = 250  # topics on more items than this are too broad to be evidence of a relationship

_STOP = frozenset(
    "a an and are as at be but by for from has have how in into is it its new not of on or over that the their this to using via was "
    "what when where which who why will with without your you we our can more than up out about after all also any been do does".split())
_WORD = re.compile(r"[a-z0-9][a-z0-9+.#-]{1,}")
_ARXIV_ID = re.compile(r"arxiv\.org/(?:abs|pdf)/(\d{4}\.\d{4,5})")
_URL = re.compile(r"https?://[^\s\"'<>)\]]+")


def title_tokens(title: str) -> set[str]:
    return {word.strip(".-") for word in _WORD.findall(title.lower()) if word not in _STOP and len(word) > 2}


def jaccard(a: set[str], b: set[str]) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def _fts_query(tokens: set[str]) -> str | None:
    words = sorted((token for token in tokens if token.isalnum() and len(token) > 3), key=len, reverse=True)[:6]
    return " OR ".join(f'"{word}"' for word in words) if len(words) >= 2 else None


def _candidates(conn: sqlite3.Connection, item: sqlite3.Row, tokens: set[str], since: str) -> dict[int, sqlite3.Row]:
    found: dict[int, sqlite3.Row] = {}
    by_topic = conn.execute(
        """SELECT DISTINCT o.id, o.title, o.canonical_url, o.sort_at FROM item_topics mine
           JOIN topics t ON t.id = mine.topic_id AND t.item_count <= ?
           JOIN item_topics theirs ON theirs.topic_id = mine.topic_id AND theirs.item_id != mine.item_id
           JOIN items o ON o.id = theirs.item_id
           WHERE mine.item_id = ? AND o.sort_at >= ? AND o.id < ? ORDER BY o.sort_at DESC LIMIT 80""",
        (RARE_TOPIC_ITEMS, item["id"], since, item["id"]),
    ).fetchall()
    query = _fts_query(tokens)
    by_text = conn.execute(
        """SELECT o.id, o.title, o.canonical_url, o.sort_at FROM items_fts f JOIN items o ON o.id = f.rowid
           WHERE items_fts MATCH ? AND o.sort_at >= ? AND o.id < ? ORDER BY bm25(items_fts, 8.0, 1.0, 2.0) LIMIT 40""",
        (f"title : ({query})", since, item["id"]),
    ).fetchall() if query else []
    for row in [*by_topic, *by_text]:
        found.setdefault(row["id"], row)
    return found


def relate_item(conn: sqlite3.Connection, item: sqlite3.Row, now: datetime) -> int:
    """Find and store relations from `item` to EARLIER items (lower id). Returns how many were stored."""
    tokens = title_tokens(item["title"])
    since = days_ago(now, WINDOW_DAYS)
    candidates = _candidates(conn, item, tokens, since)

    text = f"{item['url']} {item['content'] or ''}"
    cited_urls = {url.rstrip(".,;") for url in _URL.findall(text)}
    cited_arxiv = set(_ARXIV_ID.findall(text))
    if cited_arxiv:
        for row in conn.execute(
            f"SELECT id, title, canonical_url, sort_at FROM items WHERE id != ? AND canonical_url IN ({','.join('?' * len(cited_arxiv))})",
            (item["id"], *[f"https://arxiv.org/abs/{paper}" for paper in cited_arxiv]),
        ):
            candidates.setdefault(row["id"], row)

    my_topics = {row["topic_id"]: row["kind"] for row in conn.execute(
        "SELECT it.topic_id, t.kind FROM item_topics it JOIN topics t ON t.id = it.topic_id WHERE it.item_id = ? AND t.item_count <= ?",
        (item["id"], RARE_TOPIC_ITEMS))}
    scored = []
    for other in candidates.values():
        if other["id"] == item["id"] or other["canonical_url"] == item["canonical_url"]:
            continue
        overlap = jaccard(tokens, title_tokens(other["title"]))
        shared = conn.execute(
            f"SELECT topic_id FROM item_topics WHERE item_id = ? AND topic_id IN ({','.join('?' * len(my_topics)) or 'NULL'})",
            (other["id"], *my_topics),
        ).fetchall() if my_topics else []
        entities = sum(1 for row in shared if my_topics[row["topic_id"]] != "topic")
        topical = min(1.0, 0.25 * (len(shared) - entities) + 0.4 * entities)
        references = other["canonical_url"] in cited_urls or any(other["canonical_url"].endswith(f"/abs/{paper}") for paper in cited_arxiv)
        days_apart = abs((parse_ts(item["sort_at"]) - parse_ts(other["sort_at"])).total_seconds()) / 86400
        if references and other["id"] != item["id"]:
            kind, score = "references", max(0.8, overlap)
        elif overlap >= SAME_STORY_TITLE and days_apart <= SAME_STORY_DAYS:
            kind, score = "same_story", overlap
        else:
            kind, score = "related", 0.6 * overlap + 0.4 * topical
            if overlap < 0.12 and entities == 0:  # shared broad topics alone are not a relationship
                continue
        if score >= MIN_SCORE:
            scored.append((score, kind, other["id"]))

    scored.sort(reverse=True)
    for score, kind, other_id in scored[:MAX_RELATED]:
        conn.execute(
            "INSERT OR REPLACE INTO relations (item_id, other_id, kind, score, created_at) VALUES (?, ?, ?, ?, ?)",
            (item["id"], other_id, kind, round(score, 3), iso(now)),
        )
        if kind != "same_story":  # the same story twice is not a new development
            add_context_boost(conn, other_id, round(0.08 * score, 4), now)
    conn.execute("UPDATE items SET related_done = 1 WHERE id = ?", (item["id"],))
    return min(len(scored), MAX_RELATED)


def relate_pending(conn: sqlite3.Connection, now: datetime, *, limit: int = 400) -> int:
    rows = conn.execute("SELECT * FROM items WHERE related_done = 0 AND stage != 'new' ORDER BY id LIMIT ?", (limit,)).fetchall()
    total = sum(relate_item(conn, row, now) for row in rows)
    conn.commit()
    return total


def related_for(conn: sqlite3.Connection, item_id: int, limit: int = 8) -> list[dict]:
    """Relations in both directions, newest first: what this item builds on and what later built on it."""
    rows = conn.execute(
        """SELECT r.kind, r.score, 'earlier' AS direction, o.id, o.title, o.source_name, o.sort_at, o.kind AS item_kind
           FROM relations r JOIN items o ON o.id = r.other_id WHERE r.item_id = ?
           UNION ALL
           SELECT r.kind, r.score, 'later' AS direction, o.id, o.title, o.source_name, o.sort_at, o.kind AS item_kind
           FROM relations r JOIN items o ON o.id = r.item_id WHERE r.other_id = ?
           ORDER BY sort_at DESC LIMIT ?""", (item_id, item_id, limit)).fetchall()
    return [dict(row) for row in rows]


def story_ids(conn: sqlite3.Connection, item_id: int) -> set[int]:
    """The item plus everything recorded as the same story (used to notify once per story)."""
    ids = {item_id}
    for row in conn.execute("SELECT item_id, other_id FROM relations WHERE kind = 'same_story' AND (item_id = ? OR other_id = ?)", (item_id, item_id)):
        ids.update((row["item_id"], row["other_id"]))
    return ids

