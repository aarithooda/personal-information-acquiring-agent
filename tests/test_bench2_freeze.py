"""The freeze: a record that binds the corpus, the methodology and the production code, so any later change is visible."""

import json
from datetime import datetime, timezone

import pytest

from benchmark_v2 import common as c
from benchmark_v2 import freeze as fz
from benchmarks.common import BenchmarkError, write_json_atomic

NOW = datetime(2026, 9, 22, 9, 0, tzinfo=timezone.utc)
FP = {"src_tree": "abc123", "uncommitted_changes": False, "sources_config_sha256": "s" * 64, "profile_hash": "p123"}


def make_data(tmp_path):
    from benchmark_v2.corpus import compute_corpus_id

    items = [{"id": 1, "title": "T"}, {"id": 2, "title": "U"}]
    data = tmp_path / "data"
    write_json_atomic(data / c.ITEMS_FILE, {"snapshot_id": compute_corpus_id(items), "items": items})
    write_json_atomic(data / c.MANIFEST_FILE, {"repeats": []})
    method = tmp_path / "METHODOLOGY.md"
    method.write_text("# Methodology\r\nline two\r\n", encoding="utf-8", newline="")
    return data, method


def frozen(tmp_path):
    data, method = make_data(tmp_path)
    fz.write_freeze(data, now=NOW, methodology_path=method, fingerprint=FP)
    return data, method


def test_freezing_records_what_the_corpus_the_methodology_and_the_production_code_were(tmp_path):
    data, method = make_data(tmp_path)
    record = fz.write_freeze(data, now=NOW, methodology_path=method, fingerprint=FP)
    saved = json.loads((data / c.FREEZE_FILE).read_text(encoding="utf-8"))
    assert saved == record
    assert saved["benchmark_version"] == "v2" and saved["production"] == FP
    assert saved["corpus_id"] and len(saved["manifest_sha256"]) == 64 and len(saved["methodology_sha256"]) == 64
    assert saved["model_output_exists"] is False and saved["jev_model_planned"] == "jev-1.13.0"


def test_a_freeze_is_written_once_and_needs_the_corpus_and_methodology(tmp_path):
    data, method = frozen(tmp_path)
    with pytest.raises(BenchmarkError, match="already frozen"):
        fz.write_freeze(data, now=NOW, methodology_path=method, fingerprint=FP)
    with pytest.raises(BenchmarkError, match="no corpus"):
        fz.write_freeze(tmp_path / "empty", now=NOW, methodology_path=method, fingerprint=FP)


def test_a_pristine_freeze_verifies(tmp_path):
    data, method = frozen(tmp_path)
    assert fz.verify(data, methodology_path=method, fingerprint=FP) == []


def test_line_endings_do_not_change_the_methodology_hash(tmp_path):
    data, method = frozen(tmp_path)
    method.write_text("# Methodology\nline two\n", encoding="utf-8", newline="")  # what git checkout may do
    assert fz.verify(data, methodology_path=method, fingerprint=FP) == []


@pytest.mark.parametrize("edit,expected", [
    (lambda d, m: m.write_text("# Methodology, quietly edited\nline two\n", encoding="utf-8"), "methodology"),
    (lambda d, m: write_json_atomic(d / c.MANIFEST_FILE, {"repeats": [1]}), "manifest"),
])
def test_editing_the_methodology_or_the_manifest_is_detected(tmp_path, edit, expected):
    data, method = frozen(tmp_path)
    edit(data, method)
    assert any(expected in p for p in fz.verify(data, methodology_path=method, fingerprint=FP))


def test_editing_an_item_is_detected(tmp_path):
    data, method = frozen(tmp_path)
    raw = json.loads((data / c.ITEMS_FILE).read_text(encoding="utf-8"))
    raw["items"][0]["title"] = "edited"
    (data / c.ITEMS_FILE).write_text(json.dumps(raw), encoding="utf-8")
    assert any("corpus" in p for p in fz.verify(data, methodology_path=method, fingerprint=FP))


@pytest.mark.parametrize("change", [{"src_tree": "different"}, {"profile_hash": "other"}, {"sources_config_sha256": "x" * 64}, {"uncommitted_changes": True}])
def test_any_change_to_the_production_code_profile_or_config_makes_the_evaluation_void(tmp_path, change):
    data, method = frozen(tmp_path)
    problems = fz.verify(data, methodology_path=method, fingerprint={**FP, **change})
    assert problems and all("production" in p or "profile" in p or "config" in p or "uncommitted" in p for p in problems)


def test_model_output_appearing_before_the_labels_are_frozen_is_flagged(tmp_path):
    data, method = frozen(tmp_path)
    (data / "arms").mkdir()
    assert any("model output" in p for p in fz.verify(data, methodology_path=method, fingerprint=FP))
    assert fz.verify(data, methodology_path=method, fingerprint=FP, expect_no_model_output=False) == []  # allowed once the evaluation phase has begun


def test_the_fingerprint_reads_the_production_tree_and_any_uncommitted_change_through_git(tmp_path):
    calls = []

    def fake_git(args):
        calls.append(args)
        return {"rev-parse": "deadbeef\n", "status": " M src/pia/jev/design.py\n"}[args[0]]

    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "sources.toml").write_text("[[source]]\n", encoding="utf-8")
    fp = fz.production_fingerprint(tmp_path, run=fake_git)
    assert fp["src_tree"] == "deadbeef" and fp["uncommitted_changes"] is True and fp["profile_hash"] is None
    assert calls[0] == ["rev-parse", "HEAD:src/pia"] and "src/pia" in calls[1]
    assert len(fp["sources_config_sha256"]) == 64
