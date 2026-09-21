"""The frozen item set every arm and every label refers to.

WHY A SNAPSHOT. The database keeps changing (new items arrive, old ones get briefed). A benchmark needs a fixed
list of items so that "arm A scored these 186 items" and "the human labelled these 186 items" are statements about
the same thing, today and in six months. The snapshot is written once, is identified by a content hash, and every
later file records that hash.

WHAT IS DELIBERATELY MISSING. Only facts about the item are exported: where it came from, its text, its URL, and the
popularity signals (the production ranker needs those). No enrichment, no score, no summary, no briefing decision, no
status. The labelling tool loads only this file, so it cannot leak what it never had.
"""

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from benchmarks.common import BenchmarkError, read_json, write_json_atomic
from pia.history import DatabaseMissing, connect_readonly
from pia.jev.triage import build_state

FORMAT = 1
ITEM_FIELDS = (
    "id",
    "source",
    "external_id",
    "canonical_url",
    "url",
    "title",
    "content_raw",
    "published_at",
    "discovered_at",
    "signals",
)


@dataclass
class Snapshot:
    items: list[dict]
    snapshot_id: str
    exported_at: str

    def by_id(self) -> dict[int, dict]:
        return {item["id"]: item for item in self.items}


def compute_snapshot_id(items: list[dict]) -> str:
    """A short content hash: any change to any item changes it. The export time is not part of it."""
    canonical = json.dumps(items, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]


def export_snapshot(db_path: Path, out_path: Path, *, now: datetime, force: bool = False) -> Snapshot:
    """Copy every item's facts out of the database (opened READ-ONLY) into a frozen file."""
    if out_path.exists() and not force:
        raise BenchmarkError(
            f"{out_path} already exists. The snapshot is frozen on purpose: labels and results refer to it. "
            "Pass --force only if you mean to start the benchmark over."
        )
    try:
        conn = connect_readonly(db_path)
    except DatabaseMissing:
        raise BenchmarkError(f"no database at {db_path}") from None
    try:
        rows = conn.execute(f"SELECT {', '.join(ITEM_FIELDS)} FROM items ORDER BY id").fetchall()
        schema = conn.execute("PRAGMA user_version").fetchone()[0]
    finally:
        conn.close()
    items = [{**{f: row[f] for f in ITEM_FIELDS}, "signals": json.loads(row["signals"])} for row in rows]
    shot = Snapshot(items, compute_snapshot_id(items), now.isoformat())
    write_json_atomic(
        out_path,
        {"format": FORMAT, "snapshot_id": shot.snapshot_id, "exported_at": shot.exported_at, "source_schema": schema, "items": items},
    )
    return shot


def load_snapshot(path: Path) -> Snapshot:
    if not path.is_file():
        raise BenchmarkError(f"no snapshot at {path}. Create it first: python -m benchmarks snapshot")
    data = read_json(path)
    if compute_snapshot_id(data["items"]) != data["snapshot_id"]:
        raise BenchmarkError(f"{path} has changed since it was frozen (its content no longer matches its snapshot id)")
    return Snapshot(data["items"], data["snapshot_id"], data["exported_at"])


def is_title_only(item: dict) -> bool:
    return not (item.get("content_raw") or "").strip()


def human_view(item: dict) -> dict:
    """What the labeller is shown: the same source label, title and (truncated) text that Jev is sent.

    Calling the production function, instead of copying its rules, means the human and the model can never
    silently be given different views of an item if that function changes."""
    return build_state({}, item)["item"]
