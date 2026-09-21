"""`python -m benchmark_v2`: build, summarise, freeze, verify, then the blind labelling workflow."""

import itertools
import json
from datetime import datetime, timezone

import pytest
from fakes import make_item
from test_bench2_corpus import Scripted, v1_items, write_v1
from typer.testing import CliRunner

from benchmark_v2 import cli
from benchmark_v2 import common as c
from benchmark_v2 import freeze as fz
from benchmarks import labelling
from pia.db import connect

runner = CliRunner()
UTC = timezone.utc
FP = {"src_tree": "t1", "uncommitted_changes": False, "sources_config_sha256": "s" * 64, "profile_hash": "p1"}


@pytest.fixture
def env(tmp_path, monkeypatch):
    v1 = write_v1(tmp_path, v1_items(30))
    monkeypatch.setattr(c, "V1_ITEMS", v1)
    monkeypatch.setattr(c, "V1_DB", tmp_path / "no-such.db")
    monkeypatch.setattr(c, "N_NEW", 20)
    monkeypatch.setattr(c, "N_REPEAT", 5)
    monkeypatch.setattr(c, "WINDOW_START", datetime(2026, 8, 1, tzinfo=UTC))
    monkeypatch.setattr(c, "WINDOW_END", datetime(2026, 8, 7, tzinfo=UTC))
    monkeypatch.setattr(c, "PAUSE_SECONDS", {})
    per_call = [[make_item("hn", 100 * w + i, datetime(2026, 8, 1, tzinfo=UTC), content=None if i % 2 else "some text").model_copy(update={"url": f"https://new.example/{w}/{i}"}) for i in range(15)] for w in range(2)]
    monkeypatch.setattr(cli, "_sources", lambda: [Scripted("hn", per_call)])
    monkeypatch.setattr(cli, "_http", lambda: None)
    method = tmp_path / "METHODOLOGY.md"
    method.write_text("# methodology\n", encoding="utf-8")
    monkeypatch.setattr(c, "METHODOLOGY", method)
    monkeypatch.setattr(cli, "METHODOLOGY", method)
    monkeypatch.setattr(fz, "production_fingerprint", lambda *a, **k: dict(FP))
    monkeypatch.setattr(cli, "_methodology_committed", lambda: True)
    return {"data": tmp_path / "data", "method": method}


def invoke(env, *args):
    return runner.invoke(cli.app, ["--data-dir", str(env["data"]), *args])


def ok(result):
    assert result.exit_code == 0, result.output
    return result.output


def fail(result, *fragments):
    assert result.exit_code == 1, result.output
    for f in fragments:
        assert f in result.output, f"{f!r} not in {result.output}"
    assert "Traceback" not in result.output


def built(env):
    ok(invoke(env, "build"))


def frozen(env):
    built(env)
    ok(invoke(env, "freeze"))


# ---------- build ----------


def test_build_creates_the_corpus_and_prints_counts_never_titles(env):
    out = ok(invoke(env, "build"))
    items = json.loads((env["data"] / "items.json").read_text(encoding="utf-8"))["items"]
    assert len(items) == 25 and "20 new" in out and "5 repeats" in out
    assert "hn item" not in out and "V1 title" not in out  # nothing that could prime the reader
    assert (env["data"] / "manifest.json").is_file() and (env["data"] / "pool.db").is_file()


def test_build_refuses_to_run_twice_and_before_touching_the_network(env, monkeypatch):
    built(env)
    monkeypatch.setattr(cli, "_sources", lambda: (_ for _ in ()).throw(AssertionError("the network must not be reached")))
    fail(invoke(env, "build"), "already exists")


def test_a_failed_source_stops_the_build_and_leaves_no_corpus(env, monkeypatch):
    monkeypatch.setattr(cli, "_sources", lambda: [Scripted("hn", [[], []], error_on=1)])
    fail(invoke(env, "build"), "window 1", "pool must be complete")
    assert not (env["data"] / "items.json").exists()


def test_a_missing_v1_snapshot_is_a_message(env, monkeypatch, tmp_path):
    monkeypatch.setattr(c, "V1_ITEMS", tmp_path / "missing.json")
    fail(invoke(env, "build"), "v1")


# ---------- summary ----------


def test_the_summary_reports_every_distribution_without_naming_a_single_item(env):
    built(env)
    out = ok(invoke(env, "summary"))
    for heading in ("Corpus 25 items", "Sources", "Native categories", "Title-only", "Published", "Absence check", "Repeats"):
        assert heading in out, heading
    assert "hn item" not in out and "V1 title" not in out and "https://" not in out


def test_the_summary_function_counts_origin_source_and_title_only(env):
    built(env)
    items = json.loads((env["data"] / "items.json").read_text(encoding="utf-8"))["items"]
    manifest = json.loads((env["data"] / "manifest.json").read_text(encoding="utf-8"))
    s = cli.summarize(items, manifest)
    assert s["n_items"] == 25 and s["by_origin"] == {"new": 20, "repeat": 5}
    assert sum(s["sources"]["all"].values()) == 25 and s["title_only"]["all"]["title_only"] + s["title_only"]["all"]["has_text"] == 25
    assert s["absence_check"]["passed"] is True


# ---------- freeze and verify ----------


def test_freeze_records_the_state_and_verify_confirms_it(env):
    built(env)
    assert "Frozen" in ok(invoke(env, "freeze"))
    assert "intact" in ok(invoke(env, "verify"))
    fail(invoke(env, "freeze"), "already frozen")


def test_freeze_refuses_when_the_methodology_or_production_code_is_not_committed(env, monkeypatch):
    built(env)
    monkeypatch.setattr(cli, "_methodology_committed", lambda: False)
    fail(invoke(env, "freeze"), "commit")
    monkeypatch.setattr(cli, "_methodology_committed", lambda: True)
    monkeypatch.setattr(fz, "production_fingerprint", lambda *a, **k: {**FP, "uncommitted_changes": True})
    fail(invoke(env, "freeze"), "uncommitted")


def test_verify_exits_nonzero_and_names_each_problem(env):
    frozen(env)
    env["method"].write_text("# methodology, edited\n", encoding="utf-8")
    fail(invoke(env, "verify"), "methodology has changed")


# ---------- the blind labelling workflow ----------


def keys(pattern="y m n n n"):
    it = itertools.cycle(pattern.split())
    return lambda: next(it)


def test_init_needs_a_frozen_corpus_and_makes_one_screen_per_item_with_no_within_session_repeats(env):
    built(env)
    fail(invoke(env, "init"), "freeze")
    ok(invoke(env, "freeze"))
    out = ok(invoke(env, "init"))
    queue = json.loads((env["data"] / "queue.json").read_text(encoding="utf-8"))
    assert len(queue["entries"]) == 25 and not any(e["repeat"] for e in queue["entries"]) and queue["seed"] == c.QUEUE_SEED
    assert "25 screens" in out and "repeat" not in out.lower().replace("repeats", "")  # the reader is not told which items are repeats


def test_the_queue_order_does_not_reveal_which_items_are_repeats(env):
    frozen(env)
    ok(invoke(env, "init"))
    queue = json.loads((env["data"] / "queue.json").read_text(encoding="utf-8"))["entries"]
    manifest = json.loads((env["data"] / "manifest.json").read_text(encoding="utf-8"))
    origin = [manifest["origin_by_id"][str(e["item_id"])] for e in queue]
    assert origin != sorted(origin) and origin != sorted(origin, reverse=True)


def test_the_reader_sees_only_source_title_and_text_and_no_origin_or_model_information(env, monkeypatch):
    frozen(env)
    ok(invoke(env, "init"))
    monkeypatch.setattr(labelling, "read_key", lambda: "q")
    out = ok(invoke(env, "label"))
    for leak in ("repeat", "new item", "origin", "v1", "score", "rank", "relevance", "points", "stars", "upvotes", "jev", "manifest"):
        assert leak not in out.lower(), leak
    assert "Item 1 of 25" in out and "SHOW" in out


def test_labelling_is_refused_if_the_corpus_was_altered_after_the_freeze(env, monkeypatch):
    frozen(env)
    ok(invoke(env, "init"))
    raw = json.loads((env["data"] / "items.json").read_text(encoding="utf-8"))
    raw["items"][0]["title"] += "!"
    (env["data"] / "items.json").write_text(json.dumps(raw), encoding="utf-8")
    monkeypatch.setattr(labelling, "read_key", lambda: "q")
    fail(invoke(env, "label"), "corpus")


def test_labelling_still_works_after_unrelated_production_changes_because_only_evaluation_needs_that_check(env, monkeypatch):
    frozen(env)
    ok(invoke(env, "init"))
    monkeypatch.setattr(fz, "production_fingerprint", lambda *a, **k: {**FP, "src_tree": "changed"})
    monkeypatch.setattr(labelling, "read_key", lambda: "q")
    ok(invoke(env, "label"))


def test_full_flow_label_status_and_freeze_labels(env, monkeypatch):
    frozen(env)
    ok(invoke(env, "init"))
    fail(invoke(env, "freeze-labels"), "unlabelled")
    monkeypatch.setattr(labelling, "read_key", keys())
    ok(invoke(env, "label"))
    assert "25 of 25" in ok(invoke(env, "status"))
    out = ok(invoke(env, "freeze-labels"))
    assert (env["data"] / "labels_frozen.json").is_file() and "SHOW" in out
    labels = json.loads((env["data"] / "labels_frozen.json").read_text(encoding="utf-8"))
    assert labels["retest"] == [] and len(labels["labels"]) == 25  # no within-session repeats; repeat consistency is v1-vs-v2, computed later


def test_labels_cannot_be_frozen_once_model_output_exists_for_the_corpus(env, monkeypatch):
    frozen(env)
    ok(invoke(env, "init"))
    monkeypatch.setattr(labelling, "read_key", keys())
    ok(invoke(env, "label"))
    (env["data"] / "arms").mkdir()
    fail(invoke(env, "freeze-labels"), "model output")


def test_status_prints_aggregates_only(env):
    frozen(env)
    ok(invoke(env, "init"))
    out = ok(invoke(env, "status"))
    assert "0 of 25" in out and "hn item" not in out


def test_the_launcher_is_windows_crlf_and_starts_this_benchmark():
    from pathlib import Path

    raw = (Path(cli.__file__).parent / "label.bat").read_bytes()
    assert b"\r\n" in raw and b"\n" not in raw.replace(b"\r\n", b"")
    assert b"-m benchmark_v2 label" in raw and b"-m benchmarks" not in raw.replace(b"-m benchmark_v2", b"")


# ---------- the reader's instructions stay true ----------


def test_the_readme_documents_every_command_and_the_rules_that_keep_the_labels_blind():
    from pathlib import Path

    from typer.main import get_command

    text = (Path(cli.__file__).parent / "README.md").read_text(encoding="utf-8")
    missing = [name for name in get_command(cli.app).commands if f"benchmark_v2 {name}" not in text]
    assert not missing, f"README does not mention: {missing}"
    for phrase in ("200 screens", "each item appears **once**", "Do not open any file in `benchmark_v2\\data`", "You will not be told which items are repeats", "no model has\nseen this corpus"):
        assert phrase in text, phrase
    assert "Benchmark version: **v2**" in text


def test_the_launcher_passes_arguments_through_and_does_not_wait_for_a_key(tmp_path):
    import subprocess
    import sys
    from pathlib import Path

    if sys.platform != "win32":
        pytest.skip("Windows launcher")
    bat = Path(cli.__file__).parent / "label.bat"
    result = subprocess.run(["cmd", "/c", str(bat), "--data-dir", str(tmp_path / "empty"), "status"], capture_output=True, text=True, encoding="utf-8", timeout=60, stdin=subprocess.DEVNULL)
    assert result.returncode == 1 and "not frozen yet" in result.stdout  # a hang would hit the timeout
