"""Notifications: levels, caps, one per story, freshness, quiet hours, devices and deadline reminders."""

from datetime import datetime, timedelta, timezone

import pytest

from pia.intel import opportunities
from pia.notify import engine
from pia.store import db
from tests.platform_helpers import NOW, add_item, add_source, item, make_db

AFTERNOON = datetime(2026, 11, 15, 9, 0, tzinfo=timezone.utc)  # 14:30 in the default (India) time zone: not quiet hours
NIGHT = datetime(2026, 11, 15, 19, 30, tzinfo=timezone.utc)  # 01:00 local: quiet hours


def scored(conn, title, *, importance, relevance, score, acquired=AFTERNOON, published=None, stage="assessed", **extra):
    item_id = add_item(conn, title, acquired=acquired, published=published or acquired, **extra)
    conn.execute("UPDATE items SET importance = ?, relevance = ?, score = ?, stage = ?, summary = 'What it is.' WHERE id = ?", (importance, relevance, score, stage, item_id))
    conn.commit()
    return item_id


def levels(conn):
    return {row["title"]: row["level"] for row in conn.execute("SELECT title, level FROM notifications")}


@pytest.mark.parametrize("score, importance, relevance, expected", [
    (0.85, 0.90, 0.80, "critical"), (0.85, 0.70, 0.90, "important"),  # a high score without landmark importance is not critical
    (0.70, 0.65, 0.60, "important"), (0.70, 0.40, 0.95, "interesting"),  # merely on-topic cannot reach "important"
    (0.60, 0.50, 0.50, "interesting"), (0.60, 0.90, 0.30, None),  # merely important-in-general, not relevant: nothing
    (0.40, 0.90, 0.90, None),
])
def test_each_level_needs_the_score_and_a_minimum_of_both_qualities(score, importance, relevance, expected):
    assert engine.level_for(score, importance, relevance, engine.DEFAULTS["thresholds"]) == expected


def test_qualifying_items_notify_once_and_everything_else_is_still_kept():
    conn = make_db()
    scored(conn, "Landmark result", importance=0.9, relevance=0.8, score=0.85)
    scored(conn, "Solid update", importance=0.65, relevance=0.6, score=0.7)
    quiet = scored(conn, "Minor note", importance=0.3, relevance=0.4, score=0.35)
    created = engine.evaluate(conn, AFTERNOON)
    assert len(created) == 2 and levels(conn) == {"Landmark result": "critical", "Solid update": "important"}
    assert engine.evaluate(conn, AFTERNOON) == []  # considered exactly once
    assert item(conn, quiet)["notified"] == 2 and conn.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 3  # not notified is not discarded
    row = conn.execute("SELECT * FROM notifications WHERE level = 'critical'").fetchone()
    assert row["body"] == "What it is." and row["reason"].startswith("AI")


def test_old_muted_read_and_dismissed_items_never_notify():
    conn = make_db()
    old = scored(conn, "From last week", importance=0.9, relevance=0.9, score=0.9, acquired=AFTERNOON - timedelta(days=7))
    for title, column in (("Muted", "muted = 1"), ("Read already", "read_at = '2026-11-15T08:00:00+00:00'"), ("Dismissed", "dismissed_at = '2026-11-15T08:00:00+00:00'")):
        item_id = scored(conn, title, importance=0.9, relevance=0.9, score=0.9)
        conn.execute(f"UPDATE items SET {column} WHERE id = ?", (item_id,))
    conn.commit()
    assert engine.evaluate(conn, AFTERNOON) == [] and item(conn, old)["notified"] == 2  # a backfill never produces a burst


def test_newly_acquired_but_long_published_items_do_not_notify():
    """A first run, or a newly followed source, brings in weeks of older items. New to PIA is not news."""
    conn = make_db()
    backfilled = scored(conn, "Published ten days ago", importance=0.9, relevance=0.9, score=0.9, published=AFTERNOON - timedelta(days=10))
    recent = scored(conn, "Published yesterday", importance=0.9, relevance=0.9, score=0.9, published=AFTERNOON - timedelta(days=1))
    engine.evaluate(conn, AFTERNOON)
    assert levels(conn) == {"Published yesterday": "critical"} and item(conn, backfilled)["notified"] == 2 and item(conn, recent)["notified"] == 1


def test_daily_caps_stop_a_busy_day_from_flooding():
    conn = make_db()
    engine.update_settings(conn, {"daily_caps": {"interesting": 2}})
    for n in range(5):
        scored(conn, f"Interesting {n}", importance=0.5, relevance=0.5, score=0.6)
    assert len(engine.evaluate(conn, AFTERNOON)) == 2
    scored(conn, "Critical one", importance=0.9, relevance=0.8, score=0.9)
    assert len(engine.evaluate(conn, AFTERNOON)) == 1  # a higher level has its own allowance


def test_a_story_reported_twice_notifies_once():
    conn = make_db()
    first = scored(conn, "Acme releases Falcon", importance=0.7, relevance=0.7, score=0.75)
    engine.evaluate(conn, AFTERNOON)
    second = scored(conn, "Acme releases the Falcon model", importance=0.7, relevance=0.7, score=0.75, source=add_source(conn, "Other", url="https://o.example/f"))
    conn.execute("INSERT INTO relations (item_id, other_id, kind, score, created_at) VALUES (?, ?, 'same_story', 0.8, ?)", (second, first, db.iso(AFTERNOON)))
    conn.commit()
    assert engine.evaluate(conn, AFTERNOON) == [] and conn.execute("SELECT COUNT(*) FROM notifications").fetchone()[0] == 1


def test_an_item_waits_for_its_assessment_before_the_decision_is_made():
    conn = make_db()
    waiting = scored(conn, "Not assessed yet", importance=0.7, relevance=0.7, score=0.75, stage="classified")
    assert engine.evaluate(conn, AFTERNOON) == [] and item(conn, waiting)["notified"] == 0  # undecided, not passed over
    conn.execute("UPDATE items SET stage = 'assessed' WHERE id = ?", (waiting,))
    assert len(engine.evaluate(conn, AFTERNOON)) == 1


def test_turning_notifications_off_creates_none_and_settings_stay_sane():
    conn = make_db()
    scored(conn, "Landmark", importance=0.9, relevance=0.9, score=0.9)
    settings = engine.update_settings(conn, {"enabled": False, "thresholds": {"interesting": 0.9, "important": 0.2, "critical": 5}, "desktop_min_level": "loud", "unknown": 1, "daily_caps": "many"})
    assert settings["thresholds"] == {"interesting": 0.9, "important": 0.9, "critical": 0.99}  # ordered and clamped
    assert settings["desktop_min_level"] == "interesting" and "unknown" not in settings and settings["daily_caps"]["important"] == 10
    assert engine.evaluate(conn, AFTERNOON) == []


def test_opportunities_open_to_first_years_are_important_and_expired_ones_are_silent():
    conn = make_db()
    engine.update_settings(conn, {"thresholds": {"interesting": 0.9, "important": 0.9, "critical": 0.95}})
    open_one = scored(conn, "Fellowship for first-years", importance=0.5, relevance=0.6, score=0.55, kind="opportunity", signals={"deadline": "2026-12-20"})
    expired = scored(conn, "Closed programme", importance=0.5, relevance=0.9, score=0.7, kind="opportunity", signals={"deadline": "2026-11-01"})
    for item_id in (open_one, expired):
        opportunities.extract_opportunity(conn, item_id, AFTERNOON)
    conn.execute("UPDATE opportunities SET first_year = 'yes' WHERE item_id = ?", (open_one,))
    conn.commit()
    engine.evaluate(conn, AFTERNOON)
    assert levels(conn) == {"Fellowship for first-years": "important"}


def test_a_tracked_opportunity_gets_one_reminder_as_its_deadline_nears():
    conn = make_db()
    tracked = scored(conn, "Summer school", importance=0.3, relevance=0.3, score=0.3, kind="opportunity", signals={"deadline": "2026-11-17"})
    untracked = scored(conn, "Other school", importance=0.3, relevance=0.3, score=0.3, kind="opportunity", signals={"deadline": "2026-11-17"})
    for item_id in (tracked, untracked):
        opportunities.extract_opportunity(conn, item_id, AFTERNOON)
    opportunities.set_status(conn, tracked, "planning", AFTERNOON)
    engine.evaluate(conn, AFTERNOON)
    reminders = conn.execute("SELECT title, body FROM notifications").fetchall()
    assert [r["title"] for r in reminders] == ["Deadline in 2 days: Summer school"] and "2026-11-17" in reminders[0]["body"]
    engine.evaluate(conn, AFTERNOON + timedelta(hours=5))
    assert conn.execute("SELECT COUNT(*) FROM notifications").fetchone()[0] == 1  # reminded once, not every cycle


# ---------- devices ----------


def test_a_device_receives_each_notification_once_and_only_with_a_valid_token():
    conn = make_db()
    device_id, token = engine.register_device(conn, "Laptop", AFTERNOON - timedelta(hours=1))
    assert token.startswith("pia_dev_") and token not in str(conn.execute("SELECT * FROM devices").fetchone()[:])  # only a hash is stored
    scored(conn, "Solid update", importance=0.65, relevance=0.6, score=0.7)
    engine.evaluate(conn, AFTERNOON)
    device = engine.device_for_token(conn, token, AFTERNOON)
    pending = engine.pending_for_device(conn, device, AFTERNOON)
    assert [n["title"] for n in pending] == ["Solid update"] and pending[0]["item_id"]
    engine.acknowledge(conn, device_id, [pending[0]["id"]], AFTERNOON)
    assert engine.pending_for_device(conn, device, AFTERNOON) == []
    assert engine.device_for_token(conn, "pia_dev_wrong", AFTERNOON) is None
    assert engine.revoke_device(conn, device_id, AFTERNOON) and engine.device_for_token(conn, token, AFTERNOON) is None


def test_quiet_hours_hold_back_everything_but_critical_and_old_or_seen_ones_are_not_replayed():
    conn = make_db()
    _, token = engine.register_device(conn, "Laptop", AFTERNOON - timedelta(days=3))
    scored(conn, "Important thing", importance=0.65, relevance=0.6, score=0.7, acquired=NIGHT)
    scored(conn, "Critical thing", importance=0.9, relevance=0.8, score=0.9, acquired=NIGHT)
    engine.evaluate(conn, NIGHT)
    device = engine.device_for_token(conn, token, NIGHT)
    assert engine.in_quiet_hours(NIGHT, engine.settings(conn)) and not engine.in_quiet_hours(AFTERNOON, engine.settings(conn))
    assert [n["title"] for n in engine.pending_for_device(conn, device, NIGHT)] == ["Critical thing"]
    morning = NIGHT + timedelta(hours=8)
    assert {n["title"] for n in engine.pending_for_device(conn, device, morning)} == {"Important thing", "Critical thing"}  # the fact was kept; delivery waited
    engine.mark_seen(conn, morning)
    assert engine.pending_for_device(conn, device, morning) == []  # already seen in the app
    conn.execute("UPDATE notifications SET seen_at = NULL")
    assert engine.pending_for_device(conn, device, NIGHT + timedelta(days=2, hours=8)) == []  # a laptop opened days later does not replay
    engine.update_settings(conn, {"desktop_min_level": "critical"})
    assert [n["title"] for n in engine.pending_for_device(conn, device, morning)] == ["Critical thing"]


def test_a_new_device_does_not_receive_what_happened_before_it_existed():
    conn = make_db()
    scored(conn, "Earlier", importance=0.65, relevance=0.6, score=0.7)
    engine.evaluate(conn, AFTERNOON)
    _, token = engine.register_device(conn, "New laptop", AFTERNOON + timedelta(minutes=5))
    assert engine.pending_for_device(conn, engine.device_for_token(conn, token, AFTERNOON), AFTERNOON + timedelta(minutes=6)) == []
    assert engine.unseen_count(conn) == 1 and len(engine.inbox(conn)) == 1
