"""Import v1's data into the platform database.

The v1 file (`data/pia.db`) is opened READ-ONLY and is never modified: v1 keeps working, and nothing can be lost
by importing. Importing is idempotent (items are matched by canonical URL), so it can be run again safely.

What comes across: every item with its text, dates and signals; the latest triage result per item (the model's
significance or Jev's relevance, and any summary) as a stored judgement; and favourites, which become Saved.
Briefings are not imported: they are rendered documents of the v1 product, and stay readable with `pia show`.
"""

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from pia.intel.classify import classify_new
from pia.store.db import iso

# v1 source ids -> the platform's source names (config/catalog.toml)
SOURCE_NAMES = {
    "hn": "Hacker News", "arxiv": "arXiv: AI", "hf_papers": "Hugging Face Daily Papers", "github": "GitHub: new AI repositories",
    "quanta": "Quanta Magazine", "deepmind": "Google DeepMind Blog", "openai": "OpenAI News", "hf_blog": "Hugging Face Blog",
    "google_ai": "Google AI (The Keyword)",
}
KINDS = {"arxiv": "paper", "hf_papers": "paper", "github": "repo"}


def import_legacy(conn: sqlite3.Connection, legacy_path: str | Path, now: datetime) -> dict:
    path = Path(legacy_path)
    if not path.is_file():
        return {"found": False}
    old = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    old.row_factory = sqlite3.Row
    report = {"found": True, "items": 0, "already_present": 0, "judgements": 0, "saved": 0}
    try:
        tables = {row[0] for row in old.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        favourites = {row["item_id"]: row["created_at"] for row in old.execute("SELECT item_id, created_at FROM favorites")} if "favorites" in tables else {}
        sources = {row["name"]: row["id"] for row in conn.execute("SELECT id, name FROM sources")}
        assessed_ids = []
        for item in old.execute("SELECT * FROM items ORDER BY id"):
            if conn.execute("SELECT 1 FROM items WHERE canonical_url = ?", (item["canonical_url"],)).fetchone():
                report["already_present"] += 1
                continue
            nested = json.loads(item["signals"] or "{}")
            signals: dict = {}
            for values in nested.values():  # v1 nested signals by source; the platform keeps one flat set
                if isinstance(values, dict):
                    signals.update({key: value for key, value in values.items() if value not in (None, "", [])})
            name = SOURCE_NAMES.get(item["source"], item["source"])
            acquired = item["discovered_at"]
            item_id = conn.execute(
                """INSERT INTO items (canonical_url, url, title, content, published_at, acquired_at, sort_at, source_id, source_name, kind, signals, notified)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 2)""",
                (item["canonical_url"], item["url"], item["title"], item["content_raw"], item["published_at"], acquired,
                 item["published_at"] or acquired, sources.get(name), name, KINDS.get(item["source"], "article"), json.dumps(signals)),
            ).lastrowid
            for source in nested:
                conn.execute(
                    "INSERT OR IGNORE INTO item_sources (item_id, source_name, source_id, external_id, url, seen_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (item_id, SOURCE_NAMES.get(source, source), sources.get(SOURCE_NAMES.get(source, source)), item["external_id"] if source == item["source"] else None, item["url"], acquired),
                )
            report["items"] += 1
            triage = old.execute("SELECT * FROM enrichments WHERE item_id = ? ORDER BY rowid DESC LIMIT 1", (item["id"],)).fetchone()
            if triage:
                keys = triage.keys()
                relevance = triage["relevance"] if "relevance" in keys else None
                payload = {"category": triage["category"], "importance": triage["importance"], "summary": triage["summary"], "relevance": relevance,
                           "profile_hash": triage["profile_hash"] if "profile_hash" in keys else None}
                conn.execute(
                    "INSERT INTO judgements (item_id, task, provider, model, version, payload, created_at) VALUES (?, 'v1-triage', ?, ?, ?, ?, ?)",
                    (item_id, "typesafe" if triage["model"].startswith("jev") else "groq", triage["model"], triage["prompt_version"], json.dumps(payload), triage["created_at"]),
                )
                # Jev's number was personal relevance; the LLM's was importance. Each goes where it belongs.
                if relevance is not None:
                    conn.execute("UPDATE items SET fit = ?, summary = ? WHERE id = ?", (relevance, triage["summary"] or "", item_id))
                else:
                    conn.execute("UPDATE items SET model_importance = ?, summary = ? WHERE id = ?", ((triage["importance"] - 1) / 4, triage["summary"] or "", item_id))
                    assessed_ids.append(item_id)
                report["judgements"] += 1
            if item["id"] in favourites:
                conn.execute("UPDATE items SET saved_at = ?, read_at = ? WHERE id = ?", (favourites[item["id"]], favourites[item["id"]], item_id))
                report["saved"] += 1
        conn.commit()
    finally:
        old.close()
    classify_new(conn)
    if assessed_ids:  # already judged by v1's model: do not pay to assess them again
        conn.executemany("UPDATE items SET stage = 'assessed' WHERE id = ?", [(item_id,) for item_id in assessed_ids])
        conn.commit()
    report["imported_at"] = iso(now)
    return report
