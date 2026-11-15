"""Connections to the platform database, plus the few helpers every module needs.

One connection per thread (SQLite connections are bound to their creating thread). WAL lets the API read while
the worker writes; `busy_timeout` makes the rare writer collision wait instead of failing.
"""

import json
import shutil
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pia.config import PROJECT_ROOT
from pia.store.schema import MIGRATIONS

DEFAULT_DB = PROJECT_ROOT / "data" / "platform.db"
BACKUP_DIR_NAME = "backups"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat(timespec="seconds")


def parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def connect(path: str | Path = DEFAULT_DB, *, migrate: bool = True) -> sqlite3.Connection:
    in_memory = str(path) == ":memory:"
    if not in_memory:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    if not in_memory:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
    if migrate:
        _migrate(conn)
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    if current > len(MIGRATIONS):
        raise RuntimeError(f"database schema v{current} is newer than this code (v{len(MIGRATIONS)}); update PIA")
    for version, script in enumerate(MIGRATIONS[current:], start=current + 1):
        # One transaction per migration: a crash leaves the previous version intact.
        conn.executescript(f"BEGIN;\n{script}\nPRAGMA user_version = {version};\nCOMMIT;")


def schema_version(conn: sqlite3.Connection) -> int:
    return conn.execute("PRAGMA user_version").fetchone()[0]


# ---------- settings ----------


def get_setting(conn: sqlite3.Connection, key: str, default=None):
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return json.loads(row["value"]) if row else default


def set_setting(conn: sqlite3.Connection, key: str, value) -> None:
    conn.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, json.dumps(value)),
    )


# ---------- job history ----------


def start_run(conn: sqlite3.Connection, kind: str, now: datetime) -> int:
    run_id = conn.execute("INSERT INTO runs (kind, started_at) VALUES (?, ?)", (kind, iso(now))).lastrowid
    conn.commit()
    return run_id


def close_interrupted_runs(conn: sqlite3.Connection, now: datetime) -> int:
    """A run with no end time was cut short (the window was closed, the machine slept). Say so, instead of
    leaving it looking as if it were still in progress. Called when PIA starts, before any new run begins."""
    count = conn.execute(
        "UPDATE runs SET finished_at = ?, ok = 0, error = 'Interrupted: PIA was stopped while this run was in progress.' WHERE finished_at IS NULL",
        (iso(now),),
    ).rowcount
    conn.commit()
    return count


def finish_run(conn: sqlite3.Connection, run_id: int, now: datetime, *, stats: dict | None = None, error: str | None = None) -> None:
    conn.execute(
        "UPDATE runs SET finished_at = ?, ok = ?, stats = ?, error = ? WHERE id = ?",
        (iso(now), 0 if error else 1, json.dumps(stats or {}), error, run_id),
    )
    conn.commit()


# ---------- backups ----------


def backup(conn: sqlite3.Connection, db_path: str | Path, now: datetime, *, keep: int = 14) -> Path:
    """A consistent snapshot using SQLite's online backup API (safe while the app is running)."""
    folder = Path(db_path).parent / BACKUP_DIR_NAME
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"platform-{now.strftime('%Y%m%d-%H%M%S')}.db"
    copy = sqlite3.connect(str(target))
    try:
        conn.backup(copy)
    finally:
        copy.close()
    for old in sorted(folder.glob("platform-*.db"))[:-keep]:
        old.unlink(missing_ok=True)
    return target


def copy_file_backup(db_path: str | Path, suffix: str) -> Path | None:
    """A plain file copy taken BEFORE opening a database, used ahead of a schema migration."""
    source = Path(db_path)
    if not source.is_file():
        return None
    folder = source.parent / BACKUP_DIR_NAME
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{source.stem}-{suffix}{source.suffix}"
    shutil.copy2(source, target)
    return target


def needs_migration(db_path: str | Path) -> bool:
    if not Path(db_path).is_file():
        return False
    probe = sqlite3.connect(f"file:{Path(db_path).as_posix()}?mode=ro", uri=True)
    try:
        return probe.execute("PRAGMA user_version").fetchone()[0] < len(MIGRATIONS)
    finally:
        probe.close()


def days_ago(now: datetime, days: float) -> str:
    return iso(now - timedelta(days=days))
