"""Paths and small helpers shared by the benchmark modules."""

import json
import os
from pathlib import Path

BENCH_ROOT = Path(__file__).resolve().parent
DATA_DIR = BENCH_ROOT / "data"  # git-ignored: it describes the reader's habits and opinions

SNAPSHOT_FILE = "items.json"
QUEUE_FILE = "queue.json"
LABELS_FILE = "labels.jsonl"
FROZEN_LABELS_FILE = "labels_frozen.json"
ARMS_DIR = "arms"
SCRATCH_DIR = "scratch"
REPORTS_DIR = "reports"


class BenchmarkError(Exception):
    """A problem the user can fix (a missing file, a mismatched snapshot). The message says how."""


def write_json_atomic(path: Path, data) -> None:
    """Write a whole file or nothing: a crash mid-write must never leave half a frozen file behind."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))
