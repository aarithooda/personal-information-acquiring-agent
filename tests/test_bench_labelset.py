"""Freezing the labels into the yardstick, and loading whichever version exists."""

import json
from datetime import datetime, timezone

import pytest
from test_bench_labelling import make_snapshot

from benchmarks import labelling as lab
from benchmarks import labelset as ls
from benchmarks.common import BenchmarkError

NOW = datetime(2026, 9, 23, 9, 0, tzinfo=timezone.utc)


def label_first_pass(shot, tmp_path, pattern="SHOW MAYBE SKIP SKIP", repeats=0, upto=None):
    queue = lab.init_labelling(shot, tmp_path, seed=1, repeats=repeats, min_gap=4)
    store = lab.LabelStore(tmp_path)
    cycle = pattern.split()
    entries = [e for e in queue if not e["repeat"]]
    for i, entry in enumerate(entries[:upto]):
        store.append(entry, cycle[i % len(cycle)], opened=False, at=NOW)
    return queue, store


def test_freezing_needs_every_item_labelled_and_says_how_many_are_missing(tmp_path):
    shot = make_snapshot()
    queue, store = label_first_pass(shot, tmp_path, upto=9)
    with pytest.raises(BenchmarkError, match="3 of 12 items are still unlabelled"):
        ls.freeze_labels(shot, queue, store, tmp_path, now=NOW)


def test_the_frozen_file_holds_labels_folds_and_a_hash(tmp_path):
    shot = make_snapshot()
    queue, store = label_first_pass(shot, tmp_path)
    frozen = ls.freeze_labels(shot, queue, store, tmp_path, now=NOW)
    assert frozen.complete and frozen.n_total == 12 and frozen.source == "frozen"
    assert frozen.counts == {"SHOW": 3, "MAYBE": 3, "SKIP": 6}
    assert set(frozen.grades.values()) == {2, 1, 0}
    data = json.loads((tmp_path / "labels_frozen.json").read_text(encoding="utf-8"))
    assert data["snapshot_id"] == shot.snapshot_id and data["labels_hash"] == frozen.labels_hash
    assert {row["item_id"] for row in data["labels"]} == {i["id"] for i in shot.items}
    assert all(row["fold"] in range(5) for row in data["labels"])
    assert data["labels"][0]["canonical_url"].startswith("https://example.com/")  # joinable without the database


def test_a_frozen_label_set_is_not_replaced_by_accident(tmp_path):
    shot = make_snapshot()
    queue, store = label_first_pass(shot, tmp_path)
    ls.freeze_labels(shot, queue, store, tmp_path, now=NOW)
    with pytest.raises(BenchmarkError, match="already frozen"):
        ls.freeze_labels(shot, queue, store, tmp_path, now=NOW)
    ls.freeze_labels(shot, queue, store, tmp_path, now=NOW, force=True)


def test_folds_are_deterministic_and_do_not_depend_on_python_hash_randomisation():
    assert ls.fold_of("https://example.com/a") == ls.fold_of("https://example.com/a")
    assert ls.fold_of("https://example.com/a") == int.from_bytes(__import__("hashlib").sha256(b"https://example.com/a").digest()[:4], "big") % 5
    folds = {ls.fold_of(f"https://example.com/{i}") for i in range(100)}
    assert folds == {0, 1, 2, 3, 4}


def test_frozen_labels_are_preferred_and_checked_against_tampering(tmp_path):
    shot = make_snapshot()
    queue, store = label_first_pass(shot, tmp_path)
    frozen = ls.freeze_labels(shot, queue, store, tmp_path, now=NOW)
    loaded = ls.load_labels(shot, tmp_path)
    assert loaded.source == "frozen" and loaded.grades == frozen.grades and loaded.labels_hash == frozen.labels_hash

    path = tmp_path / "labels_frozen.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["labels"][0]["label"] = "SHOW" if data["labels"][0]["label"] != "SHOW" else "SKIP"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(BenchmarkError, match="changed since it was frozen"):
        ls.load_labels(shot, tmp_path)


def test_frozen_labels_for_another_snapshot_are_refused(tmp_path):
    shot = make_snapshot()
    queue, store = label_first_pass(shot, tmp_path)
    ls.freeze_labels(shot, queue, store, tmp_path, now=NOW)
    with pytest.raises(BenchmarkError, match="different snapshot"):
        ls.load_labels(make_snapshot(13), tmp_path)


def test_without_a_frozen_file_a_partial_label_set_needs_explicit_permission(tmp_path):
    shot = make_snapshot()
    label_first_pass(shot, tmp_path, upto=5)
    with pytest.raises(BenchmarkError, match="5 of 12"):
        ls.load_labels(shot, tmp_path)
    partial = ls.load_labels(shot, tmp_path, allow_partial=True)
    assert partial.source == "live" and not partial.complete
    assert len(partial.grades) == 5 and partial.n_total == 12


def test_a_complete_live_label_set_needs_no_permission(tmp_path):
    shot = make_snapshot()
    label_first_pass(shot, tmp_path)
    assert ls.load_labels(shot, tmp_path).complete


def test_nothing_labelled_at_all_is_an_error_even_when_partial_is_allowed(tmp_path):
    shot = make_snapshot()
    lab.init_labelling(shot, tmp_path, seed=1, repeats=0, min_gap=4)
    with pytest.raises(BenchmarkError, match="no labels yet"):
        ls.load_labels(shot, tmp_path, allow_partial=True)


def test_the_hash_identifies_the_labels_not_when_or_how_they_were_made(tmp_path):
    shot = make_snapshot()
    queue, store = label_first_pass(shot, tmp_path)
    a = ls.freeze_labels(shot, queue, store, tmp_path, now=NOW)
    b = ls.freeze_labels(shot, queue, store, tmp_path, now=datetime(2030, 1, 1, tzinfo=timezone.utc), force=True)
    assert a.labels_hash == b.labels_hash


# ---------- test-retest ----------


def test_retest_pairs_compare_each_repeated_item_with_its_first_label(tmp_path):
    shot = make_snapshot()
    queue = lab.init_labelling(shot, tmp_path, seed=1, repeats=3, min_gap=4)
    store = lab.LabelStore(tmp_path)
    for entry in queue:
        store.append(entry, "SKIP" if not entry["repeat"] else "SHOW", opened=False, at=NOW)
    frozen = ls.freeze_labels(shot, queue, store, tmp_path, now=NOW)
    assert len(frozen.retest) == 3
    assert all(r["first"] == "SKIP" and r["repeat"] == "SHOW" for r in frozen.retest)
    summary = ls.retest_summary(frozen.retest)
    assert summary["n"] == 3 and summary["agreement"] == 0.0 and summary["show_skip_flips"] == 3


def test_retest_summary_with_no_repeats_says_so():
    assert ls.retest_summary([]) == {"n": 0, "agreement": None, "kappa": None, "show_skip_flips": 0}
