"""The human labelling workflow: a queue, an append-only label file, and a keyboard-driven terminal screen.

BLINDNESS BY CONSTRUCTION. This module imports nothing that can read enrichments, model scores, arm results or
briefing decisions (a test parses this file's imports to keep it so). It is given the frozen snapshot, which holds
item facts only, and it shows each item exactly as the model sees it (`snapshot.human_view`): source label, title,
truncated text. It shows no popularity numbers, no URL, no database id and no rank.

THE QUEUE. Items are shown in a seeded RANDOM order, not database order. Two reasons: (1) fatigue and drift then
fall evenly across sources and topics instead of piling onto whatever happens to be last, and (2) any labelled
PREFIX is a random sample of all items, so a half-finished labelling session can still give an honest, if noisy,
preview of the results. A number of items are shown a SECOND time, far from the first (the reader is not told which):
comparing the two labels measures how consistent the human is, which caps how well ANY ranker can score.

THE FILE. `labels.jsonl` is append-only: one JSON line per key press that assigned a label. The latest record for a
queue position wins, so going back to change a label appends a new line and history is never lost. A process killed
mid-write leaves at most a broken LAST line, which is ignored on read (a broken line anywhere else is an error,
because skipping it would silently lose a label).
"""

import json
import os
import random
import textwrap
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from benchmarks.common import LABELS_FILE, QUEUE_FILE, BenchmarkError, read_json, write_json_atomic
from benchmarks.snapshot import Snapshot, human_view
from pia.llm.prompts import CONTENT_CHARS

LABELS = ("SHOW", "MAYBE", "SKIP")
KEYS = {"y": "SHOW", "1": "SHOW", "m": "MAYBE", "2": "MAYBE", "n": "SKIP", "3": "SKIP"}
DEFAULT_SEED = 20260921
DEFAULT_REPEATS = 40  # items shown twice, for test-retest agreement
DEFAULT_MIN_GAP = 30  # a repeat comes at least this many screens after its first showing
WIDTH = 90


# ---------- the queue ----------


def build_queue(item_ids, *, seed: int, repeats: int, min_gap: int) -> list[dict]:
    """A seeded shuffle of every item, with `repeats` of them inserted a second time at least `min_gap` later.

    Only items shown early enough can be repeated (so the gap always fits); `repeats` is clamped to that."""
    rng = random.Random(seed)
    order = list(item_ids)
    rng.shuffle(order)
    eligible = order[: max(0, len(order) - min_gap + 1)]
    chosen = rng.sample(eligible, min(repeats, len(eligible)))
    sequence = [(item, False) for item in order]
    for item in chosen:
        first = sequence.index((item, False))
        sequence.insert(rng.randint(first + min_gap, len(sequence)), (item, True))
    return [{"pos": pos, "item_id": item, "repeat": repeat} for pos, (item, repeat) in enumerate(sequence)]


def init_labelling(snapshot: Snapshot, data_dir: Path, *, seed: int, repeats: int, min_gap: int) -> list[dict]:
    """Create the queue once. Replacing it would re-order the items under labels that already exist."""
    if (data_dir / QUEUE_FILE).exists():
        raise BenchmarkError(f"{data_dir / QUEUE_FILE} already exists. The queue is fixed once created so that labels stay attached to it.")
    if (data_dir / LABELS_FILE).exists() and (data_dir / LABELS_FILE).stat().st_size:
        raise BenchmarkError(f"labels already exist in {data_dir / LABELS_FILE}; move them aside before creating a new queue")
    queue = build_queue([item["id"] for item in snapshot.items], seed=seed, repeats=repeats, min_gap=min_gap)
    write_json_atomic(
        data_dir / QUEUE_FILE,
        {"format": 1, "snapshot_id": snapshot.snapshot_id, "seed": seed, "repeats": repeats, "min_gap": min_gap, "entries": queue},
    )
    return queue


def load_queue(data_dir: Path, snapshot: Snapshot) -> list[dict]:
    path = data_dir / QUEUE_FILE
    if not path.is_file():
        raise BenchmarkError(f"no labelling queue at {path}. Create it first: python -m benchmarks init")
    data = read_json(path)
    if data["snapshot_id"] != snapshot.snapshot_id:
        raise BenchmarkError(f"{path} was made for a different snapshot ({data['snapshot_id']}, not {snapshot.snapshot_id})")
    return data["entries"]


# ---------- the label file ----------


@dataclass
class Progress:
    done: int
    total: int
    first_pass_done: int
    first_pass_total: int

    @property
    def complete(self) -> bool:
        return self.done == self.total

    @property
    def first_pass_complete(self) -> bool:
        return self.first_pass_done == self.first_pass_total


class LabelStore:
    """Append-only label records; everything else is derived by reading the file."""

    def __init__(self, data_dir: Path):
        self.path = data_dir / LABELS_FILE

    def append(self, entry: dict, label: str, *, opened: bool, at: datetime) -> None:
        if label not in LABELS:
            raise ValueError(f"label must be one of {LABELS}, not {label!r}")
        record = {
            "pos": entry["pos"],
            "item_id": entry["item_id"],
            "repeat": entry["repeat"],
            "label": label,
            "opened": opened,
            "at": at.isoformat(),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
            f.flush()
            os.fsync(f.fileno())  # the label is on disk before the next item is shown

    def _records(self) -> list[dict]:
        if not self.path.is_file():
            return []
        lines = self.path.read_text(encoding="utf-8").splitlines()
        records = []
        for number, line in enumerate(lines, start=1):
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                if number == len(lines):
                    continue  # a write that was interrupted: the label was never confirmed to the reader either
                raise BenchmarkError(f"{self.path}: line {number} is not valid JSON, so a label may be lost. Fix or remove it.") from None
        return records

    def latest(self, queue: list[dict]) -> dict[int, dict]:
        """Queue position -> its most recent record, checked against the queue."""
        latest: dict[int, dict] = {}
        for record in self._records():
            pos = record["pos"]
            if not 0 <= pos < len(queue) or queue[pos]["item_id"] != record["item_id"]:
                raise BenchmarkError(
                    f"{self.path}: the recorded labels do not match the queue (position {pos} was labelled as item "
                    f"{record['item_id']}). Was the queue rebuilt after labelling began?"
                )
            latest[pos] = record
        return latest

    def next_pos(self, queue: list[dict]) -> int | None:
        done = self.latest(queue)
        return next((e["pos"] for e in queue if e["pos"] not in done), None)

    def progress(self, queue: list[dict]) -> Progress:
        done = self.latest(queue)
        first = [e for e in queue if not e["repeat"]]
        return Progress(len(done), len(queue), sum(e["pos"] in done for e in first), len(first))

    def _labels(self, queue: list[dict], repeat: bool) -> dict[int, str]:
        latest = self.latest(queue)
        return {e["item_id"]: latest[e["pos"]]["label"] for e in queue if e["repeat"] == repeat and e["pos"] in latest}

    def first_pass_labels(self, queue: list[dict]) -> dict[int, str]:
        """item id -> label from the first showing (the one that counts as the item's label)."""
        return self._labels(queue, repeat=False)

    def repeat_labels(self, queue: list[dict]) -> dict[int, str]:
        """item id -> label from the second showing (used only to measure consistency)."""
        return self._labels(queue, repeat=True)


# ---------- the screen ----------

LEGEND = f"""{'-' * WIDTH}
 Would you want this in your briefing?

   y   SHOW    I would want this surfaced in my briefing.
   m   MAYBE   Interesting enough that I might want it, but not clearly worth a headline.
   n   SKIP    I would not want this surfaced.

   o open the link   b back   q save and quit          (1 / 2 / 3 also work)
{'-' * WIDTH}"""


def render_screen(view: dict, pos: int, total: int, current_label: str | None = None) -> str:
    """One item, as the model sees it. `current_label` is shown only when going back to a position that was
    already labelled, and it is that position's OWN label: a repeat never reveals the label of its first showing."""
    lines = [
        "",
        "=" * WIDTH,
        f" Item {pos + 1} of {total}",
        "=" * WIDTH,
        f" [{view['source']}]",
        "",
        textwrap.fill(view["title"], WIDTH - 2, initial_indent=" ", subsequent_indent=" "),
        "",
    ]
    if view.get("text"):
        lines.append(textwrap.fill(view["text"], WIDTH - 2, initial_indent=" ", subsequent_indent=" "))
        if len(view["text"]) >= CONTENT_CHARS:
            lines.append(f" [text cut off at {CONTENT_CHARS} characters, exactly as the model sees it]")
    else:
        lines.append(" (title only: this item has no text)")
    if current_label:
        lines += ["", f" Current label: {current_label}   (press y / m / n to change it)"]
    lines.append(LEGEND)
    return "\n".join(lines)


def read_key() -> str:
    """One key press, no Enter needed on Windows; elsewhere, the first character of a typed line."""
    try:
        import msvcrt
    except ImportError:
        return input("> ").strip()[:1]
    char = msvcrt.getwch()
    if char in ("\x00", "\xe0"):  # an arrow or function key arrives as two characters: swallow both
        msvcrt.getwch()
        return ""
    if char == "\x03":  # Ctrl+C is not delivered as a signal by getwch
        raise KeyboardInterrupt
    return char


def _is_web_url(url: str) -> bool:
    return url.lower().startswith(("http://", "https://"))


def run_session(
    snapshot: Snapshot,
    queue: list[dict],
    store: LabelStore,
    *,
    read_key: Callable[[], str],
    out: Callable[[str], None],
    open_url: Callable[[str], object],
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> Progress:
    """Show items until everything is labelled or the reader quits. Returns the progress."""
    items = snapshot.by_id()
    pos = store.next_pos(queue)
    opened = False
    try:
        while pos is not None:
            entry = queue[pos]
            current = store.latest(queue).get(pos)
            out(render_screen(human_view(items[entry["item_id"]]), pos, len(queue), current["label"] if current else None))
            key = read_key().lower()
            if key == "q":
                break
            if key == "b":
                pos, opened = max(0, pos - 1), False
            elif key == "o":
                url = items[entry["item_id"]]["url"]
                if _is_web_url(url):
                    open_url(url)
                    opened = True
                else:
                    out(" (not a web link, so it was not opened)")
            elif key in KEYS:
                store.append(entry, KEYS[key], opened=opened, at=now())
                pos, opened = store.next_pos(queue), False
    except KeyboardInterrupt:
        pass  # every label is already on disk
    progress = store.progress(queue)
    if progress.complete:
        out(f"\nAll done: {progress.done} of {progress.total} screens labelled. Thank you!")
    else:
        out(f"\nSaved. {progress.done} of {progress.total} screens labelled; run the same command to continue.")
    return progress
