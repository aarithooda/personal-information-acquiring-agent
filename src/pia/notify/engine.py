"""Notifications: the server decides, clients deliver (ARCHITECTURE.md, A12).

A `notifications` row is the fact that something was worth telling the owner about. The web app shows it in the
inbox; each registered device (the Windows companion) pulls what it has not delivered yet and acknowledges it.

Rules that keep this useful rather than noisy:
  * three levels with configurable thresholds; a level's daily cap stops a busy day from flooding the desktop;
  * one notification per story (a development reported by several sources notifies once);
  * only recently acquired items notify, so a backfill or a first run never produces a burst;
  * quiet hours hold back desktop delivery (not the fact), except for Critical;
  * whether an item notifies has no bearing on whether it is kept: every item stays in its feed.
"""

import hashlib
import secrets
import sqlite3
from datetime import date, datetime, timedelta, timezone

from pia.intel.relate import story_ids
from pia.store.db import get_setting, iso, parse_ts, set_setting

LEVELS = ("interesting", "important", "critical")
PUBLISHED_WITHIN_HOURS = 72
DEFAULTS = {
    "enabled": True,
    "thresholds": {"interesting": 0.56, "important": 0.66, "critical": 0.80},
    "daily_caps": {"interesting": 6, "important": 10, "critical": 30},
    "desktop_min_level": "interesting",
    "quiet_hours": {"enabled": True, "start": "23:00", "end": "07:30"},
    "utc_offset_minutes": 330,  # the owner's local time, for quiet hours and "today" (default: India)
    "max_age_hours": 36,
    "opportunity_reminder_days": 3,
}
# Beyond the blended score, each level asks for a minimum of BOTH qualities, so neither a merely popular item
# nor a merely on-topic one can reach a high level alone.
# Critical is reserved for what a model judged a landmark in its field (5 of 5) that also matches the owner.
GATES = {"critical": (0.80, 0.55), "important": (0.62, 0.50), "interesting": (0.0, 0.45)}  # (importance, relevance)


def settings(conn: sqlite3.Connection) -> dict:
    stored = get_setting(conn, "notifications", {}) or {}
    merged = {**DEFAULTS, **stored}
    for key in ("thresholds", "daily_caps", "quiet_hours"):
        merged[key] = {**DEFAULTS[key], **(stored.get(key) or {})}
    return merged


def update_settings(conn: sqlite3.Connection, changes: dict) -> dict:
    current = settings(conn)
    for key, value in changes.items():
        if key not in DEFAULTS:
            continue
        if isinstance(DEFAULTS[key], dict) and isinstance(value, dict):
            current[key] = {**current[key], **{k: v for k, v in value.items() if k in DEFAULTS[key]}}
        elif type(value) is type(DEFAULTS[key]) or (isinstance(DEFAULTS[key], (int, float)) and isinstance(value, (int, float)) and not isinstance(value, bool)):
            current[key] = value
    thresholds = current["thresholds"]
    for level in LEVELS:  # keep thresholds sane and ordered
        thresholds[level] = max(0.05, min(0.99, float(thresholds[level])))
    thresholds["important"] = max(thresholds["important"], thresholds["interesting"])
    thresholds["critical"] = max(thresholds["critical"], thresholds["important"])
    if current["desktop_min_level"] not in LEVELS:
        current["desktop_min_level"] = "interesting"
    set_setting(conn, "notifications", current)
    conn.commit()
    return current


def level_for(score: float, importance: float, relevance: float, thresholds: dict) -> str | None:
    for level in ("critical", "important", "interesting"):
        min_importance, min_relevance = GATES[level]
        if score >= thresholds[level] and importance >= min_importance and relevance >= min_relevance:
            return level
    return None


def local_now(now: datetime, config: dict) -> datetime:
    return now.astimezone(timezone(timedelta(minutes=int(config["utc_offset_minutes"]))))


def in_quiet_hours(now: datetime, config: dict) -> bool:
    quiet = config["quiet_hours"]
    if not quiet.get("enabled"):
        return False
    try:
        start = datetime.strptime(quiet["start"], "%H:%M").time()
        end = datetime.strptime(quiet["end"], "%H:%M").time()
    except (ValueError, KeyError):
        return False
    current = local_now(now, config).time()
    return start <= current < end if start <= end else current >= start or current < end


def _reason(conn: sqlite3.Connection, item: sqlite3.Row) -> str:
    topics = [row["name"] for row in conn.execute(
        "SELECT t.name FROM item_topics it JOIN topics t ON t.id = it.topic_id WHERE it.item_id = ? AND t.level >= 1 ORDER BY t.level DESC, it.weight DESC LIMIT 3",
        (item["id"],))]
    parts = [item["domain_short"] or "PIA"]
    if topics:
        parts.append("matches " + ", ".join(topics))
    elif item["importance"] >= 0.7:
        parts.append("a significant development")
    return ": ".join(parts)


def _counts_today(conn: sqlite3.Connection, now: datetime, config: dict) -> dict[str, int]:
    midnight_local = local_now(now, config).replace(hour=0, minute=0, second=0, microsecond=0)
    rows = conn.execute("SELECT level, COUNT(*) AS n FROM notifications WHERE created_at >= ? GROUP BY level", (iso(midnight_local),)).fetchall()
    return {row["level"]: row["n"] for row in rows}


def evaluate(conn: sqlite3.Connection, now: datetime) -> list[int]:
    """Create notifications for items that qualify. Every item is considered exactly once (`notified`):
    1 = notified, 2 = considered and passed over. Returns the new notification ids."""
    config = settings(conn)
    created: list[int] = []
    rows = conn.execute(
        """SELECT i.*, d.short AS domain_short, o.deadline, o.first_year FROM items i
           LEFT JOIN domains d ON d.id = i.domain_id LEFT JOIN opportunities o ON o.item_id = i.id
           WHERE i.notified = 0 AND i.stage != 'new' ORDER BY i.score DESC""").fetchall()
    if not config["enabled"]:
        conn.execute("UPDATE items SET notified = 2 WHERE notified = 0 AND stage != 'new'")
        conn.commit()
        return created
    counts = _counts_today(conn, now, config)
    cutoff = now - timedelta(hours=float(config["max_age_hours"]))
    published_cutoff = now - timedelta(hours=max(float(config["max_age_hours"]), PUBLISHED_WITHIN_HOURS))
    today = local_now(now, config).date()
    for item in rows:
        mark = 2
        # Fresh means newly acquired AND recently published: a first run or a newly followed source brings in
        # weeks of older items, and "new to PIA" is not a reason to interrupt anyone.
        fresh = parse_ts(item["acquired_at"]) >= cutoff and parse_ts(item["sort_at"]) >= published_cutoff
        # Wait for the model's view before deciding, while the item is still fresh enough to matter.
        if item["stage"] == "classified" and item["assess_attempts"] < 3 and fresh and item["kind"] != "opportunity":
            continue
        expired = bool(item["deadline"]) and date.fromisoformat(item["deadline"]) < today
        if fresh and not item["muted"] and not item["dismissed_at"] and not item["read_at"] and not expired:
            level = level_for(item["score"], item["importance"], item["relevance"], config["thresholds"])
            # An opportunity the owner can actually apply to (stated first-year eligibility) and that fits their
            # interests is Important even if its "significance" is modest. Routine listings take the ordinary route.
            if item["kind"] == "opportunity" and item["first_year"] == "yes" and item["relevance"] >= 0.5 and level in (None, "interesting"):
                level = "important"
            already = any(conn.execute("SELECT 1 FROM notifications WHERE item_id = ?", (sibling,)).fetchone() for sibling in story_ids(conn, item["id"]))
            if level and not already and counts.get(level, 0) < int(config["daily_caps"][level]):
                body = item["summary"] or (item["content"] or "")[:180]
                created.append(conn.execute(
                    "INSERT INTO notifications (item_id, level, title, body, reason, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (item["id"], level, item["title"][:200], " ".join(body.split())[:240], _reason(conn, item), iso(now)),
                ).lastrowid)
                counts[level] = counts.get(level, 0) + 1
                mark = 1
        conn.execute("UPDATE items SET notified = ? WHERE id = ?", (mark, item["id"]))
    created += _deadline_reminders(conn, now, config, today)
    conn.commit()
    return created


def _deadline_reminders(conn: sqlite3.Connection, now: datetime, config: dict, today: date) -> list[int]:
    """One reminder for each opportunity the owner is tracking whose deadline is close."""
    created = []
    horizon = (today + timedelta(days=int(config["opportunity_reminder_days"]))).isoformat()
    rows = conn.execute(
        """SELECT o.item_id, o.deadline, o.programme, o.organisation, i.title FROM opportunities o JOIN items i ON i.id = o.item_id
           WHERE o.status IN ('interested', 'planning') AND o.deadline IS NOT NULL AND o.deadline >= ? AND o.deadline <= ?""",
        (today.isoformat(), horizon)).fetchall()
    for row in rows:
        ref = f"{row['item_id']}:{row['deadline']}"
        if conn.execute("SELECT 1 FROM events WHERE kind = 'deadline_reminder' AND ref = ?", (ref,)).fetchone():
            continue
        days = (date.fromisoformat(row["deadline"]) - today).days
        when = "today" if days == 0 else "tomorrow" if days == 1 else f"in {days} days"
        created.append(conn.execute(
            "INSERT INTO notifications (item_id, level, title, body, reason, created_at) VALUES (?, 'important', ?, ?, ?, ?)",
            (row["item_id"], f"Deadline {when}: {row['programme'] or row['title']}"[:200],
             f"{row['organisation']}, closes {row['deadline']}".strip(" ,"), "An opportunity you are tracking", iso(now)),
        ).lastrowid)
        conn.execute("INSERT INTO events (item_id, kind, ref, created_at) VALUES (?, 'deadline_reminder', ?, ?)", (row["item_id"], ref, iso(now)))
    return created


# ---------- inbox ----------


def inbox(conn: sqlite3.Connection, limit: int = 60) -> list[dict]:
    rows = conn.execute(
        """SELECT n.*, i.kind AS item_kind, d.slug AS domain_slug, d.short AS domain_short FROM notifications n
           LEFT JOIN items i ON i.id = n.item_id LEFT JOIN domains d ON d.id = i.domain_id
           ORDER BY n.id DESC LIMIT ?""", (limit,)).fetchall()
    return [dict(row) for row in rows]


def unseen_count(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) FROM notifications WHERE seen_at IS NULL").fetchone()[0]


def mark_seen(conn: sqlite3.Connection, now: datetime, ids: list[int] | None = None) -> int:
    if ids:
        cursor = conn.execute(f"UPDATE notifications SET seen_at = ? WHERE seen_at IS NULL AND id IN ({','.join('?' * len(ids))})", (iso(now), *ids))
    else:
        cursor = conn.execute("UPDATE notifications SET seen_at = ? WHERE seen_at IS NULL", (iso(now),))
    conn.commit()
    return cursor.rowcount


# ---------- devices ----------


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def register_device(conn: sqlite3.Connection, name: str, now: datetime) -> tuple[int, str]:
    """Returns (device id, token). The token is shown once; only its hash is stored."""
    token = "pia_dev_" + secrets.token_urlsafe(32)
    device_id = conn.execute("INSERT INTO devices (name, token_hash, created_at) VALUES (?, ?, ?)", (name.strip()[:80] or "device", _hash(token), iso(now))).lastrowid
    conn.commit()
    return device_id, token


def device_for_token(conn: sqlite3.Connection, token: str, now: datetime) -> sqlite3.Row | None:
    row = conn.execute("SELECT * FROM devices WHERE token_hash = ? AND revoked_at IS NULL", (_hash(token),)).fetchone()
    if row:
        conn.execute("UPDATE devices SET last_seen_at = ? WHERE id = ?", (iso(now), row["id"]))
        conn.commit()
    return row


def revoke_device(conn: sqlite3.Connection, device_id: int, now: datetime) -> bool:
    cursor = conn.execute("UPDATE devices SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL", (iso(now), device_id))
    conn.commit()
    return cursor.rowcount > 0


def pending_for_device(conn: sqlite3.Connection, device: sqlite3.Row, now: datetime, limit: int = 5) -> list[dict]:
    """Notifications this device has not delivered: at or above the desktop level, created after the device was
    registered and within the last day (a laptop opened after a week must not replay the week), oldest first.
    During quiet hours only Critical ones are released; the rest wait."""
    config = settings(conn)
    minimum = LEVELS.index(config["desktop_min_level"])
    allowed = [level for level in LEVELS[minimum:] if level == "critical" or not in_quiet_hours(now, config)]
    if not allowed:
        return []
    since = max(device["created_at"], iso(now - timedelta(hours=24)))
    rows = conn.execute(
        f"""SELECT n.id, n.item_id, n.level, n.title, n.body, n.reason, n.created_at FROM notifications n
            WHERE n.created_at >= ? AND n.seen_at IS NULL AND n.level IN ({','.join('?' * len(allowed))})
              AND NOT EXISTS (SELECT 1 FROM deliveries d WHERE d.notification_id = n.id AND d.device_id = ?)
            ORDER BY n.id LIMIT ?""", (since, *allowed, device["id"], limit)).fetchall()
    return [dict(row) for row in rows]


def acknowledge(conn: sqlite3.Connection, device_id: int, notification_ids: list[int], now: datetime) -> int:
    conn.executemany(
        "INSERT OR IGNORE INTO deliveries (notification_id, device_id, delivered_at) VALUES (?, ?, ?)",
        [(notification_id, device_id, iso(now)) for notification_id in notification_ids],
    )
    conn.commit()
    return len(notification_ids)

