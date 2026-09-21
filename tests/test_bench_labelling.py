"""The labelling workflow: a fair queue, a store that survives interruptions, and a screen that stays blind."""

import ast
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from benchmarks import labelling as lab
from benchmarks.common import BenchmarkError
from benchmarks.snapshot import Snapshot, compute_snapshot_id

NOW = datetime(2026, 9, 22, 9, 0, tzinfo=timezone.utc)


def make_snapshot(n=12):
    items = [
        {
            "id": 100 + i,
            "source": "hn" if i % 2 else "arxiv",
            "external_id": str(i),
            "canonical_url": f"https://example.com/{i}",
            "url": f"https://example.com/{i}?utm=x",
            "title": f"Title number {i}",
            "content_raw": None if i % 2 else f"Abstract text for item {i}.",
            "published_at": None,
            "discovered_at": "2026-09-20T00:00:00+00:00",
            "signals": {"hn": {"points": 987654}} if i % 2 else {},
        }
        for i in range(n)
    ]
    return Snapshot(items, compute_snapshot_id(items), NOW.isoformat())


class Keys:
    """Feeds scripted key presses; running out of keys is a test bug, so it raises."""

    def __init__(self, keys):
        self.keys = list(keys)

    def __call__(self):
        if not self.keys:
            raise AssertionError("the session asked for a key the test did not provide")
        return self.keys.pop(0)


def session(shot, queue, store, keys, opened=None, screens=None):
    screens = screens if screens is not None else []
    return lab.run_session(
        shot,
        queue,
        store,
        read_key=Keys(keys),
        out=screens.append,
        open_url=(opened.append if opened is not None else lambda url: None),
        now=lambda: NOW,
    ), screens


# ---------- the queue ----------


@pytest.mark.parametrize("seed", [0, 1, 2, 3, 4])
def test_every_item_is_shown_once_and_repeats_come_back_much_later(seed):
    ids = list(range(60))
    queue = lab.build_queue(ids, seed=seed, repeats=12, min_gap=10)
    first = {e["item_id"]: e["pos"] for e in queue if not e["repeat"]}
    again = {e["item_id"]: e["pos"] for e in queue if e["repeat"]}
    assert sorted(first) == ids and len(queue) == 72 and len(again) == 12
    assert [e["pos"] for e in queue] == list(range(72))
    assert all(again[i] - first[i] >= 10 for i in again), "a repeat too close to its first showing measures memory, not judgment"


def test_the_queue_is_reproducible_and_shuffled():
    ids = list(range(40))
    a = lab.build_queue(ids, seed=5, repeats=8, min_gap=5)
    assert a == lab.build_queue(ids, seed=5, repeats=8, min_gap=5)
    assert a != lab.build_queue(ids, seed=6, repeats=8, min_gap=5)
    assert [e["item_id"] for e in a if not e["repeat"]] != ids  # not the database order: no source or date clustering


def test_more_repeats_than_can_fit_are_clamped_instead_of_breaking_the_gap():
    queue = lab.build_queue(list(range(10)), seed=0, repeats=50, min_gap=6)
    first = {e["item_id"]: e["pos"] for e in queue if not e["repeat"]}
    assert all(e["pos"] - first[e["item_id"]] >= 6 for e in queue if e["repeat"])
    assert len(queue) < 20


def test_init_writes_the_queue_and_refuses_to_replace_it(tmp_path):
    shot = make_snapshot()
    queue = lab.init_labelling(shot, tmp_path, seed=1, repeats=3, min_gap=4)
    assert lab.load_queue(tmp_path, shot) == queue
    with pytest.raises(BenchmarkError, match="already exists"):
        lab.init_labelling(shot, tmp_path, seed=2, repeats=3, min_gap=4)


def test_a_queue_for_a_different_snapshot_is_refused(tmp_path):
    shot = make_snapshot()
    lab.init_labelling(shot, tmp_path, seed=1, repeats=2, min_gap=3)
    other = make_snapshot(13)
    with pytest.raises(BenchmarkError, match="different snapshot"):
        lab.load_queue(tmp_path, other)


def test_load_queue_before_init_says_what_to_run(tmp_path):
    with pytest.raises(BenchmarkError, match="python -m benchmarks init"):
        lab.load_queue(tmp_path, make_snapshot())


# ---------- the store: append-only, resumable ----------


def test_progress_survives_a_restart(tmp_path):
    shot = make_snapshot()
    queue = lab.init_labelling(shot, tmp_path, seed=1, repeats=2, min_gap=4)
    store = lab.LabelStore(tmp_path)
    (state, _) = session(shot, queue, store, ["y", "m", "n", "q"])
    assert lab.LabelStore(tmp_path).next_pos(queue) == 3  # a brand-new object reading the same file
    assert lab.LabelStore(tmp_path).progress(queue).done == 3


def test_the_latest_label_for_a_position_wins_but_history_is_kept(tmp_path):
    shot = make_snapshot()
    queue = lab.init_labelling(shot, tmp_path, seed=1, repeats=0, min_gap=4)
    store = lab.LabelStore(tmp_path)
    first = queue[0]["item_id"]
    store.append(queue[0], "SKIP", opened=False, at=NOW)
    store.append(queue[0], "SHOW", opened=False, at=NOW)
    assert store.first_pass_labels(queue)[first] == "SHOW"
    assert len((tmp_path / "labels.jsonl").read_text(encoding="utf-8").splitlines()) == 2


def test_a_half_written_last_line_is_ignored_not_fatal(tmp_path):
    shot = make_snapshot()
    queue = lab.init_labelling(shot, tmp_path, seed=1, repeats=0, min_gap=4)
    store = lab.LabelStore(tmp_path)
    store.append(queue[0], "SHOW", opened=False, at=NOW)
    with open(tmp_path / "labels.jsonl", "a", encoding="utf-8") as f:
        f.write('{"pos": 1, "item_id": ')  # the process died mid-write
    fresh = lab.LabelStore(tmp_path)
    assert fresh.progress(queue).done == 1 and fresh.next_pos(queue) == 1


def test_a_corrupt_line_in_the_middle_is_an_error_because_it_would_silently_lose_a_label(tmp_path):
    shot = make_snapshot()
    queue = lab.init_labelling(shot, tmp_path, seed=1, repeats=0, min_gap=4)
    store = lab.LabelStore(tmp_path)
    store.append(queue[0], "SHOW", opened=False, at=NOW)
    store.append(queue[1], "SKIP", opened=False, at=NOW)
    lines = (tmp_path / "labels.jsonl").read_text(encoding="utf-8").splitlines()
    (tmp_path / "labels.jsonl").write_text(lines[0][:10] + "\n" + lines[1] + "\n", encoding="utf-8")
    with pytest.raises(BenchmarkError, match="line 1"):
        lab.LabelStore(tmp_path).progress(queue)


def test_labels_recorded_against_a_different_queue_are_rejected(tmp_path):
    shot = make_snapshot()
    queue = lab.init_labelling(shot, tmp_path, seed=1, repeats=0, min_gap=4)
    lab.LabelStore(tmp_path).append(queue[0], "SHOW", opened=False, at=NOW)
    forged = [dict(e) for e in queue]
    forged[0]["item_id"] = 999  # pretend the queue was rebuilt after labelling began
    with pytest.raises(BenchmarkError, match="do not match the queue"):
        lab.LabelStore(tmp_path).progress(forged)


def test_re_initialising_is_refused_once_labels_exist(tmp_path):
    shot = make_snapshot()
    queue = lab.init_labelling(shot, tmp_path, seed=1, repeats=0, min_gap=4)
    lab.LabelStore(tmp_path).append(queue[0], "SHOW", opened=False, at=NOW)
    (tmp_path / "queue.json").unlink()
    with pytest.raises(BenchmarkError, match="labels already exist"):
        lab.init_labelling(shot, tmp_path, seed=1, repeats=0, min_gap=4)


def test_first_pass_and_repeat_labels_are_kept_apart(tmp_path):
    shot = make_snapshot()
    queue = lab.init_labelling(shot, tmp_path, seed=1, repeats=3, min_gap=4)
    store = lab.LabelStore(tmp_path)
    for entry in queue:
        store.append(entry, "SHOW" if entry["repeat"] else "SKIP", opened=False, at=NOW)
    assert set(store.first_pass_labels(queue).values()) == {"SKIP"} and len(store.first_pass_labels(queue)) == 12
    assert set(store.repeat_labels(queue).values()) == {"SHOW"} and len(store.repeat_labels(queue)) == 3


# ---------- the session ----------


def test_keys_label_items_in_queue_order_and_q_saves_and_quits(tmp_path):
    shot = make_snapshot()
    queue = lab.init_labelling(shot, tmp_path, seed=1, repeats=0, min_gap=4)
    store = lab.LabelStore(tmp_path)
    session(shot, queue, store, ["y", "m", "n", "q"])
    got = store.first_pass_labels(queue)
    assert [got[e["item_id"]] for e in queue[:3]] == ["SHOW", "MAYBE", "SKIP"]
    assert len(got) == 3


def test_a_second_session_resumes_where_the_first_stopped(tmp_path):
    shot = make_snapshot()
    queue = lab.init_labelling(shot, tmp_path, seed=1, repeats=0, min_gap=4)
    store = lab.LabelStore(tmp_path)
    session(shot, queue, store, ["y", "y", "q"])
    _, screens = session(shot, queue, lab.LabelStore(tmp_path), ["n", "q"])
    assert "3 of 12" in screens[0]  # the first screen of the second session is the third item


def test_labelling_everything_finishes_cleanly(tmp_path):
    shot = make_snapshot(4)
    queue = lab.init_labelling(shot, tmp_path, seed=1, repeats=0, min_gap=2)
    store = lab.LabelStore(tmp_path)
    _, screens = session(shot, queue, store, ["1", "2", "3", "y"])  # digits work as well as letters
    assert lab.LabelStore(tmp_path).progress(queue).complete
    assert "All done" in screens[-1]


def test_b_goes_back_to_change_a_label_and_then_continues_where_you_were(tmp_path):
    shot = make_snapshot()
    queue = lab.init_labelling(shot, tmp_path, seed=1, repeats=0, min_gap=4)
    store = lab.LabelStore(tmp_path)
    _, screens = session(shot, queue, store, ["y", "y", "b", "n", "m", "q"])
    got = store.first_pass_labels(queue)
    assert got[queue[1]["item_id"]] == "SKIP"  # relabelled
    assert got[queue[2]["item_id"]] == "MAYBE"  # and the session carried on to the item after it
    assert any("Current label: SHOW" in s for s in screens)  # going back shows only THAT item's own label


def test_b_on_the_first_item_does_nothing(tmp_path):
    shot = make_snapshot()
    queue = lab.init_labelling(shot, tmp_path, seed=1, repeats=0, min_gap=4)
    _, screens = session(shot, queue, lab.LabelStore(tmp_path), ["b", "q"])
    assert "Item 1 of 12" in screens[0] and "Item 1 of 12" in screens[1]  # 'b' redisplayed the same first item


def test_o_opens_the_link_records_that_it_happened_and_only_for_web_urls(tmp_path):
    shot = make_snapshot()
    queue = lab.init_labelling(shot, tmp_path, seed=1, repeats=0, min_gap=4)
    store = lab.LabelStore(tmp_path)
    opened = []
    session(shot, queue, store, ["o", "y", "n", "q"], opened=opened)
    records = [json.loads(line) for line in (tmp_path / "labels.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [r["opened"] for r in records] == [True, False]
    assert len(opened) == 1 and opened[0].startswith("https://example.com/")

    hostile = make_snapshot(4)
    q2 = lab.init_labelling(hostile, tmp_path / "other", seed=1, repeats=0, min_gap=2)
    hostile.by_id()[q2[0]["item_id"]]["url"] = "javascript:alert(1)"  # the item shown first
    opened2 = []
    _, screens = session(hostile, q2, lab.LabelStore(tmp_path / "other"), ["o", "q"], opened=opened2)
    assert opened2 == []  # never handed to the browser
    assert "not a web link" in screens[-1] or "not a web link" in " ".join(screens)


def test_unknown_keys_are_ignored(tmp_path):
    shot = make_snapshot()
    queue = lab.init_labelling(shot, tmp_path, seed=1, repeats=0, min_gap=4)
    store = lab.LabelStore(tmp_path)
    session(shot, queue, store, ["x", " ", "\r", "y", "q"])
    assert store.progress(queue).done == 1


# ---------- blindness ----------


def test_the_screen_shows_the_item_as_the_model_sees_it_and_nothing_else(tmp_path):
    shot = make_snapshot()
    queue = lab.init_labelling(shot, tmp_path, seed=1, repeats=0, min_gap=4)
    _, screens = session(shot, queue, lab.LabelStore(tmp_path), ["q"])
    screen = screens[0]
    item = shot.by_id()[queue[0]["item_id"]]
    assert item["title"] in screen
    assert "987654" not in screen and "points" not in screen  # popularity is not shown (Jev never sees it either)
    assert item["url"] not in screen and str(item["id"]) not in screen  # no URL/host cue, no database id
    for cue in ("relevance", "importance", "score", "shortlist", "rank"):
        assert cue not in screen.lower(), f"{cue!r} on the labelling screen"


def test_a_repeated_item_is_indistinguishable_and_does_not_show_the_first_label(tmp_path):
    shot = make_snapshot(8)
    queue = lab.init_labelling(shot, tmp_path, seed=3, repeats=2, min_gap=3)
    store = lab.LabelStore(tmp_path)
    repeat_positions = [e["pos"] for e in queue if e["repeat"]]
    _, screens = session(shot, queue, store, ["y"] * repeat_positions[0] + ["q"])
    last = screens[-2]  # the screen for the first repeat (screens[-1] is the "saved" message)
    assert f"Item {repeat_positions[0] + 1} of {len(queue)}" in last, "not the screen this test means to inspect"
    assert "Current label" not in last and "repeat" not in last.lower() and "again" not in last.lower()


def test_the_labelling_module_cannot_see_model_results():
    """Blindness by construction: the module that draws the screen does not import anything that can read
    enrichments, arm results or briefings."""
    source = (Path(lab.__file__)).read_text(encoding="utf-8")
    imported = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
    forbidden = {"sqlite3", "pia.db", "pia.state", "pia.history", "benchmarks.arms", "benchmarks.analysis", "benchmarks.labelset"}
    assert not (imported & forbidden), f"labelling.py imports {imported & forbidden}"


def test_the_legend_defines_the_three_labels_in_the_readers_words(tmp_path):
    shot = make_snapshot()
    queue = lab.init_labelling(shot, tmp_path, seed=1, repeats=0, min_gap=4)
    _, screens = session(shot, queue, lab.LabelStore(tmp_path), ["q"])
    for phrase in ("SHOW", "MAYBE", "SKIP", "surfaced in my briefing", "not clearly worth a headline"):
        assert phrase in screens[0]


# ---------- the real keyboard ----------


def test_the_windows_keyboard_reader_swallows_special_keys_and_turns_ctrl_c_into_an_interrupt(monkeypatch):
    import sys
    import types

    presses = iter(["à", "H", "y", ""])  # an arrow key arrives as two characters
    monkeypatch.setitem(sys.modules, "msvcrt", types.SimpleNamespace(getwch=lambda: next(presses)))
    assert lab.read_key() == ""  # the arrow key: both halves consumed, nothing returned
    assert lab.read_key() == "y"
    with pytest.raises(KeyboardInterrupt):
        lab.read_key()


def test_ctrl_c_during_a_session_saves_what_was_done_and_exits_quietly(tmp_path):
    shot = make_snapshot()
    queue = lab.init_labelling(shot, tmp_path, seed=1, repeats=0, min_gap=4)
    store = lab.LabelStore(tmp_path)
    presses = iter(["y", KeyboardInterrupt()])

    def read_key():
        press = next(presses)
        if isinstance(press, BaseException):
            raise press
        return press

    lab.run_session(shot, queue, store, read_key=read_key, out=lambda text: None, open_url=lambda url: None, now=lambda: NOW)
    assert lab.LabelStore(tmp_path).progress(queue).done == 1


def test_text_cut_at_the_models_limit_is_marked_so_it_is_not_mistaken_for_the_whole_item():
    from pia.llm.prompts import CONTENT_CHARS

    cut = lab.render_screen({"source": "arXiv paper", "title": "T", "text": "x" * CONTENT_CHARS}, 0, 5)
    whole = lab.render_screen({"source": "arXiv paper", "title": "T", "text": "a short abstract"}, 0, 5)
    assert f"cut off at {CONTENT_CHARS} characters" in cut and "cut off" not in whole
