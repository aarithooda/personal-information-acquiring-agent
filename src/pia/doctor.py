"""`pia doctor`: answers "why isn't it working?" without changing anything.

Offline checks (default) read files and the database in read-only mode. `online=True` also makes
small network calls: it tries each source over the last day (storing nothing) and asks Groq to
list its models (this sends no data and costs nothing). The API key is never printed, only
whether it was found, where, and that Groq accepts it.
"""

import os
import sqlite3
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

import httpx

from pia.config import PROJECT_ROOT, ConfigError, find_groq_api_key
from pia.db import MIGRATIONS
from pia.history import DatabaseMissing, connect_readonly
from pia.http import SourceError, request_with_retry
from pia.llm.client import BASE_URL
from pia.sources.base import Source
from pia.sources.registry import load_sources


@dataclass
class Check:
    name: str
    status: Literal["ok", "warn", "fail"]
    detail: str


def _check_python() -> Check:
    version = ".".join(map(str, sys.version_info[:3]))
    if sys.version_info < (3, 11):
        return Check("Python", "fail", f"{version}; PIA needs 3.11 or newer")
    return Check("Python", "ok", version)


def _check_key(root: Path, environ: Mapping[str, str]) -> tuple[Check, str | None]:
    try:
        key, origin = find_groq_api_key(root, environ)
    except ConfigError as exc:
        return Check("API key", "fail", str(exc)), None
    shape = "looks like a Groq key" if key.startswith("gsk_") else "does not start with 'gsk_'; double-check it"
    status = "ok" if key.startswith("gsk_") else "warn"
    return Check("API key", status, f"found in {origin} ({len(key)} characters, {shape})"), key


def _check_sources(config_path: Path) -> tuple[Check, list[Source] | None]:
    try:
        sources = load_sources(config_path)
    except FileNotFoundError:
        return Check("Sources config", "fail", f"{config_path} does not exist"), None
    except Exception as exc:  # noqa: BLE001 - bad TOML, unknown type, bad parameters...
        return Check("Sources config", "fail", str(exc)), None
    return Check("Sources config", "ok", f"{len(sources)} sources: {', '.join(s.name for s in sources)}"), sources


def _check_database(path: Path) -> Check:
    try:
        conn = connect_readonly(path)
    except DatabaseMissing:
        return Check("Database", "ok", f"not created yet ({path}); it is created the first time you run `pia`")
    try:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        integrity = conn.execute("PRAGMA quick_check").fetchone()[0]
        last = None
        if version >= 1:
            last = conn.execute("SELECT MAX(covers_until) FROM briefings").fetchone()[0]
    except sqlite3.DatabaseError as exc:
        return Check("Database", "fail", f"cannot be read ({exc}); {path}")
    finally:
        conn.close()

    latest = len(MIGRATIONS)
    if integrity != "ok":
        return Check("Database", "fail", f"integrity check failed: {integrity}")
    if version > latest:
        return Check("Database", "fail", f"schema v{version} was created by a newer version of PIA (this one knows v{latest})")
    if version < latest:
        return Check("Database", "warn", f"schema v{version}; it will be upgraded to v{latest} automatically the next time you run `pia`")
    return Check("Database", "ok", f"schema v{version}, last checked {last[:16].replace('T', ' ') + ' UTC' if last else 'never'}")


def _check_groq(client: httpx.Client, key: str | None) -> Check:
    if key is None:
        return Check("Groq API", "fail", "skipped: no API key")
    try:
        request_with_retry(
            client, "GET", f"{BASE_URL}/models", headers={"Authorization": f"Bearer {key}"}, retries=1, max_wait=5
        )
    except SourceError as exc:
        hint = "the key was rejected; check it at console.groq.com" if "401" in str(exc) else "could not reach Groq"
        return Check("Groq API", "fail", f"{hint} ({exc})")
    return Check("Groq API", "ok", "reachable and the key is accepted")


def _check_source_online(source: Source, client: httpx.Client, now: datetime) -> Check:
    name = f"Source: {source.name}"
    try:
        items = source.fetch(client, now - timedelta(days=1), now)
    except SourceError as exc:
        return Check(name, "fail", str(exc))
    except Exception as exc:  # noqa: BLE001
        return Check(name, "fail", f"unexpected error: {exc!r}")
    return Check(name, "ok", f"reachable ({len(items)} item{'s' if len(items) != 1 else ''} in the last day; not stored)")


def run_checks(
    *,
    db_path: Path,
    config_path: Path,
    root: Path = PROJECT_ROOT,
    environ: Mapping[str, str] = os.environ,
    online: bool = False,
    client: httpx.Client | None = None,
    sources: list[Source] | None = None,
    now: datetime | None = None,
) -> list[Check]:
    checks = [_check_python()]
    key_check, key = _check_key(root, environ)
    checks.append(key_check)
    sources_check, loaded = _check_sources(config_path)
    checks += [sources_check, _check_database(db_path)]

    if online and client is not None:
        now = now or datetime.now(timezone.utc)
        checks.append(_check_groq(client, key))
        for source in sources if sources is not None else (loaded or []):
            checks.append(_check_source_online(source, client, now))
    return checks
