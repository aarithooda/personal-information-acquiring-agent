"""Arms: the systems under test, each frozen as its raw Stage 1 output for every item in the snapshot.

WHAT AN ARM IS. Not a code path, a FILE: for each item, what Stage 1 said (Jev's raw typed answers, or the LLM's
category and integer importance), plus provenance (model version, prompt or question-set version, profile hash, git
commit). Two consequences:

  * The benchmark measures the REAL thing. To score an arm, the analysis puts its stored output back into a database
    and runs PIA's own `state.enriched_items` and `rank_items` over it, so what is measured is the ranking PIA
    actually uses, popularity and cross-source boost included, not a re-implementation of it.
  * A future change can be judged on the same frozen labels WITHOUT new API calls: change the formula in
    `jev/triage.py::derive` (or `briefing/rank.py`), `rescore` the stored raw answers, and compare.

RUNNING AN ARM. The production Stage 1 code runs unmodified against a scratch database built from the snapshot, never
against data/pia.db. The scratch database persists (benchmarks/data/scratch), so an interrupted run resumes and pays
only for the items that are not done yet.
"""

import json
import re
import sqlite3
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from benchmarks.common import ARMS_DIR, BENCH_ROOT, BenchmarkError, read_json, write_json_atomic
from benchmarks.snapshot import Snapshot
from pia.briefing.rank import _base, rank_items  # _base: the model's judgment on the 1-5 scale, exactly as production uses it
from pia.db import connect
from pia.jev.triage import QUESTION_SET_VERSION, JevTriager, derive
from pia.llm.enrich import enrich_pending
from pia.profile import Profile
from pia.state import enriched_items

FORMAT = 1
NAME_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")


@dataclass
class Arm:
    name: str
    kind: str  # "jev" or "llm"
    meta: dict
    records: dict[int, dict] = field(default_factory=dict)  # item id -> what Stage 1 said


# ---------- provenance ----------


def code_version() -> dict:
    """Which code produced a result. A dirty tree means uncommitted changes, so the commit alone does not identify it."""
    try:
        run = lambda *args: subprocess.run(  # noqa: E731
            ["git", *args], cwd=BENCH_ROOT.parent, capture_output=True, text=True, timeout=15, check=True
        ).stdout.strip()
        return {"commit": run("rev-parse", "--short", "HEAD"), "dirty": bool(run("status", "--porcelain"))}
    except (OSError, subprocess.SubprocessError):
        return {"commit": "unknown", "dirty": None}


# ---------- files ----------


def _check_name(name: str) -> None:
    if not NAME_PATTERN.match(name):
        raise BenchmarkError(f"arm name {name!r} may only contain letters, digits, '.', '_' and '-'")


def arm_path(data_dir: Path, name: str) -> Path:
    _check_name(name)
    return data_dir / ARMS_DIR / f"{name}.json"


def save_arm(arm: Arm, data_dir: Path, *, force: bool = False) -> Path:
    path = arm_path(data_dir, arm.name)
    if path.exists() and not force:
        raise BenchmarkError(f"{path} already exists. Results are frozen once saved; use a new name, or --force to replace it.")
    write_json_atomic(
        path,
        {"format": FORMAT, "name": arm.name, "kind": arm.kind, "meta": arm.meta, "records": {str(i): r for i, r in arm.records.items()}},
    )
    return path


def load_arm(path: Path) -> Arm:
    if not path.is_file():
        available = sorted(p.stem for p in path.parent.glob("*.json")) if path.parent.is_dir() else []
        raise BenchmarkError(f"no arm at {path}. Available arms: {', '.join(available) or 'none yet (run: python -m benchmarks arm)'}")
    data = read_json(path)
    return Arm(data["name"], data["kind"], data["meta"], {int(i): r for i, r in data["records"].items()})


# ---------- running production Stage 1 on the snapshot ----------

_INSERT_ITEM = """INSERT INTO items (id, source, external_id, canonical_url, url, title, content_raw,
                                     published_at, discovered_at, signals, status)
                  VALUES (:id, :source, :external_id, :canonical_url, :url, :title, :content_raw,
                          :published_at, :discovered_at, :signals, :status)"""


def _insert_items(conn: sqlite3.Connection, items: list[dict], status: str) -> None:
    with conn:
        conn.executemany(_INSERT_ITEM, [{**item, "signals": json.dumps(item["signals"]), "status": status} for item in items])


def build_scratch_db(snapshot: Snapshot, path: Path) -> sqlite3.Connection:
    """A database holding exactly the snapshot's items (original ids), all waiting for Stage 1. Created on first use;
    an existing one is reused (that is how an interrupted run resumes) after checking it matches the snapshot."""
    existed = path.exists()
    conn = connect(path)
    wanted = {item["id"] for item in snapshot.items}
    if existed:
        if {r[0] for r in conn.execute("SELECT id FROM items")} != wanted:
            conn.close()
            raise BenchmarkError(f"{path} was built from a different snapshot. Delete it to start this arm over.")
    else:
        _insert_items(conn, snapshot.items, "discovered")
    return conn


def _export(conn: sqlite3.Connection, snapshot: Snapshot, *, name: str, kind: str, now: datetime, code: dict, started: float, report) -> Arm:
    rows = conn.execute(
        """SELECT e.* FROM enrichments e WHERE e.rowid = (SELECT MAX(rowid) FROM enrichments WHERE item_id = e.item_id)
           ORDER BY e.item_id"""
    ).fetchall()
    records = {
        row["item_id"]: {
            "model": row["model"],
            "prompt_version": row["prompt_version"],
            "category": row["category"],
            "importance": row["importance"],
            "summary": row["summary"],
            "relevance": row["relevance"],
            "details": json.loads(row["details"]) if row["details"] else None,
            "profile_hash": row["profile_hash"],
        }
        for row in rows
    }
    versions = sorted({r["prompt_version"] for r in records.values()})
    meta = {
        "kind": kind,
        "snapshot_id": snapshot.snapshot_id,
        "created_at": now.isoformat(),
        "code": code,
        "n_items": len(snapshot.items),
        "n_scored": len(records),
        "complete": len(records) == len(snapshot.items),
        "models": sorted({r["model"] for r in records.values()}),
        "prompt_version": versions[0] if len(versions) == 1 else versions,
        "elapsed_seconds": round(time.monotonic() - started, 1),  # this call only; a resumed arm reports its last leg
        "report": {
            "enriched": report.enriched,
            "failed_items": report.failed_items,
            "failed_batches": report.failed_batches,
            "stopped_early": report.stopped_early,
            "remaining": report.remaining,
        },
    }
    if kind == "jev":
        meta["question_set"] = QUESTION_SET_VERSION
        hashes = {r["profile_hash"] for r in records.values()}
        meta["profile_hash"] = hashes.pop() if len(hashes) == 1 else sorted(h for h in hashes if h)
        meta["input_tokens"] = sum(r["details"]["usage"]["input_tokens"] for r in records.values() if r["details"])
    return Arm(name, kind, meta, records)


def run_jev_arm(
    snapshot: Snapshot,
    profile: Profile,
    client,
    scratch_dir: Path,
    *,
    name: str,
    now: datetime,
    workers: int = 4,
    code: Callable[[], dict] = code_version,
) -> Arm:
    """Production Jev Stage 1, alone: NO LLM fallback, so every score in this arm really is Jev's."""
    _check_name(name)
    started = time.monotonic()
    conn = build_scratch_db(snapshot, scratch_dir / f"{name}.db")
    try:
        report = JevTriager(client, profile, workers=workers).triage_pending(conn, now)
        return _export(conn, snapshot, name=name, kind="jev", now=now, code=code(), started=started, report=report)
    finally:
        conn.close()


def run_llm_arm(snapshot: Snapshot, llm, scratch_dir: Path, *, name: str, now: datetime, code: Callable[[], dict] = code_version) -> Arm:
    """Production LLM Stage 1 (gpt-oss-20b, batched, no interest profile)."""
    _check_name(name)
    started = time.monotonic()
    conn = build_scratch_db(snapshot, scratch_dir / f"{name}.db")
    try:
        report = enrich_pending(conn, llm, now)
        return _export(conn, snapshot, name=name, kind="llm", now=now, code=code(), started=started, report=report)
    finally:
        conn.close()


def rescore_jev_arm(
    arm: Arm, name: str, *, derive_fn: Callable = derive, code: Callable[[], dict] = code_version, now: datetime | None = None
) -> Arm:
    """A new arm from an old one's stored RAW answers and a (possibly new) combining formula. No API calls.

    This is what makes "did my change to derive() help?" answerable on the frozen labels: same raw answers, same
    items, new relevance. Everything else in the record (model, answers, usage, profile hash) is carried over."""
    _check_name(name)
    if arm.kind != "jev" or any(not (r.get("details") or {}).get("answers") for r in arm.records.values()):
        raise BenchmarkError("only an arm with raw Jev answers can be rescored")
    records = {}
    for item_id, record in arm.records.items():
        derived = derive_fn(record["details"]["answers"])
        details = {**record["details"], "features": derived.features, "flags": derived.flags}
        records[item_id] = {
            **record,
            "category": derived.category,
            "importance": derived.importance,
            "relevance": derived.relevance,
            "details": details,
        }
    meta = {
        **arm.meta,
        "rescored_from": arm.name,
        "created_at": (now or datetime.now(timezone.utc)).isoformat(),
        "code": code(),
        "derive": getattr(derive_fn, "__qualname__", repr(derive_fn)),
    }
    return Arm(name, "jev", meta, records)


# ---------- ordering: what PIA would surface ----------


def build_ranked_db(snapshot: Snapshot, arm: Arm) -> sqlite3.Connection:
    """The state PIA is in after Stage 1 has run: scored items are 'enriched' and not yet briefed."""
    conn = connect(":memory:")
    scored = [item for item in snapshot.items if item["id"] in arm.records]
    _insert_items(conn, scored, "enriched")
    with conn:
        conn.executemany(
            """INSERT INTO enrichments (item_id, model, prompt_version, category, importance, summary, created_at,
                                        relevance, details, profile_hash)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                (
                    item["id"],
                    r["model"],
                    r["prompt_version"],
                    r["category"],
                    r["importance"],
                    r["summary"],
                    snapshot.exported_at,
                    r["relevance"],
                    json.dumps(r["details"]) if r["details"] is not None else None,
                    r["profile_hash"],
                )
                for item in scored
                for r in [arm.records[item["id"]]]
            ],
        )
    return conn


def _why_never_shown(row: sqlite3.Row) -> str | None:
    """The curator's inline filter (briefing/curate.py, `relevant = ...`): 'other' is off-topic by definition and
    importance 1 means noise, so neither can reach the editor. Kept identical by a contract test."""
    if row["category"] == "other":
        return "category is 'other'"
    if row["importance"] <= 1:
        return "importance is 1"
    return None


@dataclass
class Ordering:
    ranked: list[int]  # every item, best first: what PIA would surface, then what it never would, then unscored
    model_only: list[int]  # the same, but ordered by the model's score alone (no popularity, no cross-source boost)
    filtered: dict[int, str]  # item id -> why the curator would never show it
    unscored: list[int]  # items Stage 1 produced nothing for; placed last

    def positions(self) -> dict[int, int]:
        return {item: pos for pos, item in enumerate(self.ranked)}

    def model_only_positions(self) -> dict[int, int]:
        return {item: pos for pos, item in enumerate(self.model_only)}


def production_order(snapshot: Snapshot, arm: Arm) -> Ordering:
    conn = build_ranked_db(snapshot, arm)
    try:
        rows = enriched_items(conn)  # the same query the curator uses
        eligible = [r for r in rows if _why_never_shown(r) is None]
        never_shown = [r for r in rows if _why_never_shown(r) is not None]
        recency = lambda r: r["published_at"] or r["discovered_at"]  # noqa: E731

        def by_model_score(rows_: list[sqlite3.Row]) -> list[int]:
            # Two stable sorts, like rank_items: newest first among equal scores.
            ordered = sorted(rows_, key=recency, reverse=True)
            ordered.sort(key=_base, reverse=True)
            return [r["id"] for r in ordered]

        shown_by_production = [r["id"] for r in rank_items(eligible)]
        shown_by_model = by_model_score(eligible)
        never_shown_ids = by_model_score(never_shown)  # they cannot be surfaced; among themselves, higher score first
        filtered = {r["id"]: _why_never_shown(r) for r in never_shown}
    finally:
        conn.close()
    unscored = sorted(item["id"] for item in snapshot.items if item["id"] not in arm.records)
    return Ordering(
        ranked=shown_by_production + never_shown_ids + unscored,
        model_only=shown_by_model + never_shown_ids + unscored,
        filtered=filtered,
        unscored=unscored,
    )
