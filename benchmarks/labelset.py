"""From raw label records to THE yardstick: a frozen, hashed, one-label-per-item file.

Freezing is what makes a benchmark a benchmark. Once `labels_frozen.json` exists, every arm is scored against exactly
these labels, and a later ranking change is judged on the same yardstick as today's. The file carries a content hash
(`labels_hash`) that every report prints, so two reports can be compared only if they used the same labels.

Each item also gets a FOLD (0-4) derived from its URL. Nothing uses folds yet. They exist so that if the ranking
weights are ever tuned on these labels, the tuning can be cross-validated (tune on four folds, measure on the fifth)
instead of being graded on the very items it was fitted to, which would flatter it.
"""

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from benchmarks.common import FROZEN_LABELS_FILE, BenchmarkError, read_json, write_json_atomic
from benchmarks.labelling import LabelStore, load_queue
from benchmarks.metrics import MAYBE, SHOW, SKIP, exact_agreement, weighted_kappa
from benchmarks.snapshot import Snapshot

GRADE = {"SKIP": SKIP, "MAYBE": MAYBE, "SHOW": SHOW}
N_FOLDS = 5


@dataclass
class LabelSet:
    grades: dict[int, int]  # item id -> 2 / 1 / 0
    n_total: int  # items in the snapshot (more than len(grades) when labelling is unfinished)
    source: str  # "frozen" or "live"
    labels_hash: str
    counts: dict[str, int]
    retest: list[dict] = field(default_factory=list)
    folds: dict[int, int] = field(default_factory=dict)

    @property
    def complete(self) -> bool:
        return len(self.grades) == self.n_total


def fold_of(canonical_url: str) -> int:
    """Stable across runs and machines (Python's built-in hash() is randomised per process, so it would not be)."""
    return int.from_bytes(hashlib.sha256(canonical_url.encode("utf-8")).digest()[:4], "big") % N_FOLDS


def _hash(labels: dict[int, str]) -> str:
    canonical = json.dumps(sorted(labels.items()), separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]


def _counts(labels: dict[int, str]) -> dict[str, int]:
    return {name: sum(v == name for v in labels.values()) for name in ("SHOW", "MAYBE", "SKIP")}


def _retest_pairs(first: dict[int, str], repeat: dict[int, str]) -> list[dict]:
    return [{"item_id": i, "first": first[i], "repeat": repeat[i]} for i in sorted(repeat) if i in first]


def freeze_labels(
    snapshot: Snapshot, queue: list[dict], store: LabelStore, data_dir: Path, *, now: datetime, force: bool = False
) -> LabelSet:
    path = data_dir / FROZEN_LABELS_FILE
    if path.exists() and not force:
        raise BenchmarkError(f"{path} is already frozen. It is the yardstick; pass --force only if you mean to relabel.")
    labels = store.first_pass_labels(queue)
    missing = len(snapshot.items) - len(labels)
    if missing:
        raise BenchmarkError(f"{missing} of {len(snapshot.items)} items are still unlabelled; label them all before freezing.")
    by_id = snapshot.by_id()
    retest = _retest_pairs(labels, store.repeat_labels(queue))
    label_hash = _hash(labels)
    write_json_atomic(
        path,
        {
            "format": 1,
            "snapshot_id": snapshot.snapshot_id,
            "frozen_at": now.isoformat(),
            "labels_hash": label_hash,
            "labels": [
                {"item_id": i, "canonical_url": by_id[i]["canonical_url"], "label": labels[i], "fold": fold_of(by_id[i]["canonical_url"])}
                for i in sorted(labels)
            ],
            "retest": retest,
        },
    )
    return _from_frozen(read_json(path))


def _from_frozen(data: dict) -> LabelSet:
    labels = {row["item_id"]: row["label"] for row in data["labels"]}
    return LabelSet(
        grades={i: GRADE[name] for i, name in labels.items()},
        n_total=len(labels),
        source="frozen",
        labels_hash=data["labels_hash"],
        counts=_counts(labels),
        retest=data["retest"],
        folds={row["item_id"]: row["fold"] for row in data["labels"]},
    )


def load_labels(snapshot: Snapshot, data_dir: Path, *, allow_partial: bool = False) -> LabelSet:
    """The frozen labels if they exist; otherwise the live ones, which must be complete unless `allow_partial`."""
    frozen_path = data_dir / FROZEN_LABELS_FILE
    if frozen_path.is_file():
        data = read_json(frozen_path)
        if data["snapshot_id"] != snapshot.snapshot_id:
            raise BenchmarkError(f"{frozen_path} was frozen for a different snapshot ({data['snapshot_id']}, not {snapshot.snapshot_id})")
        if _hash({row["item_id"]: row["label"] for row in data["labels"]}) != data["labels_hash"]:
            raise BenchmarkError(f"{frozen_path} has changed since it was frozen (its labels no longer match its hash)")
        return _from_frozen(data)

    queue = load_queue(data_dir, snapshot)
    store = LabelStore(data_dir)
    labels = store.first_pass_labels(queue)
    if not labels:
        raise BenchmarkError("no labels yet. Label some items first: python -m benchmarks label")
    if len(labels) < len(snapshot.items) and not allow_partial:
        raise BenchmarkError(
            f"only {len(labels)} of {len(snapshot.items)} items are labelled. Finish labelling and run "
            "`python -m benchmarks freeze`, or pass --allow-partial for a PRELIMINARY analysis."
        )
    return LabelSet(
        grades={i: GRADE[name] for i, name in labels.items()},
        n_total=len(snapshot.items),
        source="live",
        labels_hash=_hash(labels),
        counts=_counts(labels),
        retest=_retest_pairs(labels, store.repeat_labels(queue)),
    )


def retest_summary(retest: list[dict]) -> dict:
    """How consistent the human was with themself: the ceiling on how well any ranker can agree with these labels."""
    pairs = [(GRADE[r["first"]], GRADE[r["repeat"]]) for r in retest]
    return {
        "n": len(pairs),
        "agreement": exact_agreement(pairs),
        "kappa": weighted_kappa(pairs),
        "show_skip_flips": sum({a, b} == {SHOW, SKIP} for a, b in pairs),
    }
