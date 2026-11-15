"""Seeding: turns the committed config files into database rows, without ever overwriting the owner's edits.

  config/taxonomy.toml   domains and seed topics       (committed)
  config/catalog.toml    the vetted source catalogue   (committed)
  config/personal.toml   the owner's starting profile  (git-ignored; optional)

Seeding is additive and idempotent: a row that already exists is left exactly as the owner last had it. Only
`apply_personal` changes existing rows, and only where the owner has not rated the topic in the app.
"""

import json
import re
import sqlite3
import tomllib
from datetime import datetime
from pathlib import Path

from pia.config import PROJECT_ROOT
from pia.store.db import iso

TAXONOMY_FILE = PROJECT_ROOT / "config" / "taxonomy.toml"
CATALOG_FILE = PROJECT_ROOT / "config" / "catalog.toml"
PERSONAL_FILE = PROJECT_ROOT / "config" / "personal.toml"

LEVELS = (-2, -1, 1, 2, 3)


def slugify(text: str) -> str:
    text = text.lower().replace("c++", "cpp").replace("&", " and ")
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")[:80] or "x"


def load_toml(path: Path) -> dict:
    if not Path(path).is_file():
        return {}
    with open(path, "rb") as handle:
        return tomllib.load(handle)


def parse_topic(entry: str) -> tuple[str, list[str]]:
    """'Name | alias | alias' -> (name, aliases)."""
    parts = [part.strip() for part in entry.split("|") if part.strip()]
    return parts[0], parts[1:]


def seed_taxonomy(conn: sqlite3.Connection, taxonomy: dict, now: datetime) -> dict[str, int]:
    """Insert domains and their seed topics if missing. Returns how many of each were added."""
    added = {"domains": 0, "topics": 0, "aliases_updated": 0}
    for position, domain in enumerate(taxonomy.get("domain", [])):
        row = conn.execute("SELECT id FROM domains WHERE slug = ?", (domain["slug"],)).fetchone()
        if row:
            domain_id = row["id"]
        else:
            domain_id = conn.execute(
                "INSERT INTO domains (slug, name, short, description, position, hue) VALUES (?, ?, ?, ?, ?, ?)",
                (domain["slug"], domain["name"], domain.get("short", domain["name"]), domain.get("description", ""), position, domain.get("hue", 220)),
            ).lastrowid
            added["domains"] += 1
        for entry in domain.get("topics", []):
            name, aliases = parse_topic(entry)
            cursor = conn.execute(
                "INSERT OR IGNORE INTO topics (slug, name, domain_id, aliases, origin, created_at) VALUES (?, ?, ?, ?, 'seed', ?)",
                (slugify(name), name, domain_id, json.dumps(aliases), iso(now)),
            )
            added["topics"] += cursor.rowcount
            # A seed topic's matching vocabulary is not editable in the app, so this file stays its source of
            # truth: improving an alias list here reaches existing databases. Levels and names are never touched.
            added["aliases_updated"] += conn.execute(
                "UPDATE topics SET aliases = ? WHERE slug = ? AND origin = 'seed' AND aliases != ?",
                (json.dumps(aliases), slugify(name), json.dumps(aliases)),
            ).rowcount
    conn.commit()
    return added


def apply_personal(conn: sqlite3.Connection, personal: dict, now: datetime) -> dict[str, int]:
    """Apply the owner's starting profile. Never overrides a level the owner already set in the app (`rated_at`
    marks those), so re-running it is safe."""
    changed = {"domains": 0, "topics": 0, "unknown_topics": 0}
    university = personal.get("university", {})
    if university.get("name"):
        conn.execute(
            "UPDATE domains SET name = ?, short = ? WHERE slug = 'university'",
            (f"{university['name']} Announcements & Opportunities", university.get("short", university["name"])),
        )
    for slug, weight in personal.get("domains", {}).items():
        changed["domains"] += conn.execute(
            "UPDATE domains SET weight = ? WHERE slug = ?", (max(0.0, min(1.5, float(weight))), slug)
        ).rowcount
    for name, level in personal.get("topics", {}).items():
        if level not in LEVELS:
            continue
        updated = conn.execute(
            "UPDATE topics SET level = ? WHERE slug = ? AND rated_at IS NULL", (level, slugify(name))
        ).rowcount
        if not updated and not conn.execute("SELECT 1 FROM topics WHERE slug = ?", (slugify(name),)).fetchone():
            changed["unknown_topics"] += 1
        changed["topics"] += updated
    if personal.get("me"):
        conn.execute(
            "INSERT INTO settings (key, value) VALUES ('me', ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (json.dumps(personal["me"]),),
        )
    conn.commit()
    return changed


def seed_sources(conn: sqlite3.Connection, catalog: dict, now: datetime) -> int:
    """Insert catalogue sources that are not in the database yet (matched by name). `tier = "core"` sources start
    active; everything else starts as a candidate, i.e. a recommendation the owner can accept."""
    added = 0
    for entry in catalog.get("source", []):
        if conn.execute("SELECT 1 FROM sources WHERE name = ?", (entry["name"],)).fetchone():
            continue
        core = entry.get("tier", "core") == "core"
        conn.execute(
            """INSERT INTO sources (name, kind, url, homepage, description, config, domains, general, status, origin,
                                    purpose, trust, interval_minutes, reason, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'builtin', ?, ?, ?, ?, ?)""",
            (
                entry["name"],
                entry["kind"],
                entry.get("url"),
                entry.get("homepage"),
                entry.get("description", ""),
                json.dumps(entry.get("config", {})),
                json.dumps(entry.get("domains", [])),
                1 if entry.get("general") else 0,
                "active" if core else "candidate",
                entry.get("purpose", "news"),
                float(entry.get("trust", 0.7)),
                int(entry.get("interval", 180)),
                "" if core else entry.get("reason", "From PIA's vetted catalogue; matches one of your spaces."),
                iso(now),
            ),
        )
        added += 1
    conn.commit()
    return added


def seed_all(conn: sqlite3.Connection, now: datetime, *, personal: bool = True) -> dict:
    report = {"taxonomy": seed_taxonomy(conn, load_toml(TAXONOMY_FILE), now)}
    report["sources"] = seed_sources(conn, load_toml(CATALOG_FILE), now)
    if personal and PERSONAL_FILE.is_file():
        report["personal"] = apply_personal(conn, load_toml(PERSONAL_FILE), now)
    return report
