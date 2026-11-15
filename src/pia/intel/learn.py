"""Personalisation: what the owner's behaviour teaches PIA, and the profile operations the app exposes.

The model is small on purpose so that it stays inspectable (PRD 4):
  * every topic and space has a learned `affinity` in [-1, 1], separate from the level the owner set by hand;
  * each signal moves the affinity of the item's topics (and, more gently, its space) by a fixed amount, with
    diminishing returns near the ends, so no single habit can run away;
  * every signal is logged in `events`, so the profile page can show what a learned value is made of, and
    resetting a topic simply sets it back to zero.

What is NOT learned from: an item being unread or unopened. Not opening something only means the owner was busy.
"""

import json
import sqlite3
from datetime import datetime

from pia.intel.score import rescore
from pia.store.db import iso
from pia.store.seed import LEVELS, slugify

# Signal -> change in topic affinity. Explicit feedback outweighs implicit behaviour.
SIGNALS = {
    "open": 0.04,
    "read": 0.02,
    "save": 0.22,
    "unsave": -0.10,
    "like": 0.35,
    "useful": 0.28,
    "source_click": 0.06,
    "dismiss": -0.06,
    "not_relevant": -0.30,
    "not_interested_topic": -0.55,
    "low_quality": 0.0,  # about the source, not the subject: handled separately
}
DOMAIN_SHARE = 0.35  # how much of a topic-level signal also reaches the item's space
SOURCE_QUALITY_STEP = 0.12
FEEDBACK_KINDS = ("like", "useful", "not_relevant", "low_quality", "not_interested_topic")
MONTHLY_DECAY = 0.92  # learned affinity fades slowly, so an old phase does not define the profile for ever


def _nudge(current: float, delta: float) -> float:
    """Move toward +1 or -1 with diminishing returns: the closer to the end, the smaller the step."""
    room = (1 - current) if delta > 0 else (1 + current)
    return round(max(-1.0, min(1.0, current + delta * room)), 4)


def record_signal(conn: sqlite3.Connection, item_id: int, kind: str, now: datetime, *, ref: str = "") -> None:
    """Log a behaviour signal and apply it to the item's topics and space. Does not commit or rescore."""
    conn.execute("INSERT INTO events (item_id, kind, ref, created_at) VALUES (?, ?, ?, ?)", (item_id, kind, ref[:200], iso(now)))
    delta = SIGNALS.get(kind, 0.0)
    if delta:
        for row in conn.execute("SELECT t.id, t.affinity, it.weight FROM item_topics it JOIN topics t ON t.id = it.topic_id WHERE it.item_id = ?", (item_id,)).fetchall():
            conn.execute("UPDATE topics SET affinity = ? WHERE id = ?", (_nudge(row["affinity"], delta * row["weight"]), row["id"]))
        domain = conn.execute("SELECT d.id, d.affinity FROM items i JOIN domains d ON d.id = i.domain_id WHERE i.id = ?", (item_id,)).fetchone()
        if domain:
            conn.execute("UPDATE domains SET affinity = ? WHERE id = ?", (_nudge(domain["affinity"], delta * DOMAIN_SHARE), domain["id"]))
    if kind == "low_quality":
        conn.execute(
            "UPDATE sources SET quality = MAX(-1.0, quality - ?) WHERE id = (SELECT source_id FROM items WHERE id = ?)",
            (SOURCE_QUALITY_STEP, item_id),
        )


def set_item_state(conn: sqlite3.Connection, item_id: int, now: datetime, *, read: bool | None = None, saved: bool | None = None, dismissed: bool | None = None) -> dict | None:
    """Idempotent state changes: setting a state that already holds changes nothing and teaches nothing twice."""
    row = conn.execute("SELECT read_at, saved_at, dismissed_at FROM items WHERE id = ?", (item_id,)).fetchone()
    if row is None:
        return None
    stamp = iso(now)
    for column, wanted, on_signal, off_signal in (
        ("read_at", read, "read", None), ("saved_at", saved, "save", "unsave"), ("dismissed_at", dismissed, "dismiss", None),
    ):
        if wanted is None or wanted == (row[column] is not None):
            continue
        conn.execute(f"UPDATE items SET {column} = ? WHERE id = ?", (stamp if wanted else None, item_id))
        signal = on_signal if wanted else off_signal
        if signal:
            record_signal(conn, item_id, signal, now)
    if saved or dismissed:  # saving or dismissing something means it has been seen
        conn.execute("UPDATE items SET read_at = COALESCE(read_at, ?) WHERE id = ?", (stamp, item_id))
    conn.commit()
    return dict(conn.execute("SELECT read_at, saved_at, dismissed_at FROM items WHERE id = ?", (item_id,)).fetchone())


def add_feedback(conn: sqlite3.Connection, item_id: int, kind: str, now: datetime, reason: str = "") -> bool:
    if kind not in FEEDBACK_KINDS:
        raise ValueError(f"unknown feedback kind {kind!r}")
    if conn.execute("SELECT 1 FROM items WHERE id = ?", (item_id,)).fetchone() is None:
        return False
    cursor = conn.execute(
        "INSERT INTO feedback (item_id, kind, reason, created_at) VALUES (?, ?, ?, ?) ON CONFLICT(item_id, kind) DO UPDATE SET reason = excluded.reason",
        (item_id, kind, reason[:1000], iso(now)),
    )
    already = conn.execute("SELECT COUNT(*) FROM events WHERE item_id = ? AND kind = ?", (item_id, kind)).fetchone()[0]
    if cursor.rowcount and not already:  # the same feedback given twice teaches once
        record_signal(conn, item_id, kind, now, ref=reason)
    conn.commit()
    return True


def remove_feedback(conn: sqlite3.Connection, item_id: int, kind: str, now: datetime) -> None:
    """Undo: remove the feedback and apply the opposite nudge, so a mis-click leaves no trace in the profile."""
    removed = conn.execute("DELETE FROM feedback WHERE item_id = ? AND kind = ?", (item_id, kind)).rowcount
    if removed:
        delta = SIGNALS.get(kind, 0.0)
        for row in conn.execute("SELECT t.id, t.affinity, it.weight FROM item_topics it JOIN topics t ON t.id = it.topic_id WHERE it.item_id = ?", (item_id,)).fetchall():
            conn.execute("UPDATE topics SET affinity = ? WHERE id = ?", (_nudge(row["affinity"], -delta * row["weight"]), row["id"]))
        if kind == "low_quality":
            conn.execute("UPDATE sources SET quality = MIN(0.0, quality + ?) WHERE id = (SELECT source_id FROM items WHERE id = ?)", (SOURCE_QUALITY_STEP, item_id))
        conn.execute("DELETE FROM events WHERE item_id = ? AND kind = ?", (item_id, kind))
    conn.commit()


# ---------- the profile ----------


def set_topic_level(conn: sqlite3.Connection, topic_id: int, level: int | None, now: datetime) -> bool:
    if level is not None and level not in LEVELS:
        raise ValueError("level must be one of -2, -1, 1, 2, 3 or null")
    changed = conn.execute("UPDATE topics SET level = ?, rated_at = ? WHERE id = ?", (level, iso(now), topic_id)).rowcount
    conn.execute("INSERT INTO events (kind, ref, created_at) VALUES ('topic_level', ?, ?)", (f"{topic_id}:{level}", iso(now)))
    conn.commit()
    return bool(changed)


def create_topic(conn: sqlite3.Connection, name: str, now: datetime, *, domain_id: int | None = None, level: int | None = 2, aliases: list[str] | None = None) -> int:
    name = " ".join(name.split())
    if not 2 <= len(name) <= 80:
        raise ValueError("a topic name needs 2 to 80 characters")
    existing = conn.execute("SELECT id FROM topics WHERE slug = ?", (slugify(name),)).fetchone()
    if existing:
        set_topic_level(conn, existing["id"], level, now)
        return existing["id"]
    topic_id = conn.execute(
        "INSERT INTO topics (slug, name, domain_id, aliases, level, origin, created_at, rated_at) VALUES (?, ?, ?, ?, ?, 'user', ?, ?)",
        (slugify(name), name, domain_id, json.dumps(aliases or []), level, iso(now), iso(now)),
    ).lastrowid
    conn.commit()
    return topic_id


def reset_topic_learning(conn: sqlite3.Connection, topic_id: int) -> None:
    conn.execute("UPDATE topics SET affinity = 0 WHERE id = ?", (topic_id,))
    conn.commit()


def set_domain_weight(conn: sqlite3.Connection, domain_id: int, weight: float) -> bool:
    changed = conn.execute("UPDATE domains SET weight = ? WHERE id = ?", (max(0.0, min(1.5, weight)), domain_id)).rowcount
    conn.commit()
    return bool(changed)


def decay_learning(conn: sqlite3.Connection, now: datetime) -> bool:
    """Once a month, fade learned affinities a little. Returns True if it ran."""
    month = now.strftime("%Y-%m")
    row = conn.execute("SELECT value FROM settings WHERE key = 'learning_decay_month'").fetchone()
    if row and json.loads(row["value"]) == month:
        return False
    if row:  # not on the very first run: there is nothing to fade yet
        conn.execute("UPDATE topics SET affinity = ROUND(affinity * ?, 4) WHERE affinity != 0", (MONTHLY_DECAY,))
        conn.execute("UPDATE domains SET affinity = ROUND(affinity * ?, 4) WHERE affinity != 0", (MONTHLY_DECAY,))
    conn.execute("INSERT INTO settings (key, value) VALUES ('learning_decay_month', ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value", (json.dumps(month),))
    conn.commit()
    return True


def suggested_topics(conn: sqlite3.Connection, limit: int = 12) -> list[dict]:
    """Subjects PIA has noticed that the owner has not rated: discovered by assessment, appearing on several
    items, with behaviour suggesting interest ranked first."""
    rows = conn.execute(
        """SELECT t.id, t.name, t.kind, t.item_count, t.affinity, d.short AS domain
           FROM topics t LEFT JOIN domains d ON d.id = t.domain_id
           WHERE t.level IS NULL AND t.origin = 'discovered' AND t.item_count >= 3
           ORDER BY (t.affinity * 4 + MIN(t.item_count, 20) / 20.0) DESC, t.item_count DESC LIMIT ?""", (limit,)).fetchall()
    return [dict(row) for row in rows]


def apply_profile_change(conn: sqlite3.Connection, now: datetime) -> int:
    """Call after any edit to levels or weights: relevance is derived, so it is recomputed for the library."""
    return rescore(conn, now)
