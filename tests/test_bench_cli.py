"""The `python -m benchmarks` command line, end to end on a small throwaway database."""

import itertools
import json
from datetime import datetime, timezone

import pytest
from fakes import T0, SmartLLM, make_item
from test_jev_triage import PROFILE_TOML, FakeJev
from typer.testing import CliRunner

from benchmarks import cli, labelling
from benchmarks.arms import Arm, load_arm, save_arm
from benchmarks.snapshot import load_snapshot
from pia.db import connect, store_items
from pia.llm.client import LLMUnavailable

runner = CliRunner()
N = 30


@pytest.fixture
def env(tmp_path):
    db = tmp_path / "pia.db"
    conn = connect(db)
    sources = ["hn", "arxiv", "hf_papers", "github"]
    store_items(conn, [make_item(sources[n % 4], n, T0, content=None if n % 4 == 0 else f"text {n}", signals={}) for n in range(N)], now=T0)
    conn.close()
    profile = tmp_path / "interests.toml"
    profile.write_text(PROFILE_TOML, encoding="utf-8")
    return {"db": db, "data": tmp_path / "data", "profile": profile}


def invoke(env, *args, input=None):
    result = runner.invoke(cli.app, ["--data-dir", str(env["data"]), *args], input=input)
    return result


def ok(result):
    assert result.exit_code == 0, result.output
    return result.output


def fail(result, *fragments):
    assert result.exit_code == 1, result.output
    for fragment in fragments:
        assert fragment in result.output, f"{fragment!r} not in: {result.output}"
    assert "Traceback" not in result.output  # a problem the user can fix is a message, not a stack trace


def snapshot_and_init(env, repeats=4):
    ok(invoke(env, "snapshot", "--db", str(env["db"])))
    ok(invoke(env, "init", "--repeats", str(repeats), "--min-gap", "5"))


def label_everything(env, monkeypatch, pattern="y m n n"):
    keys = itertools.cycle(pattern.split())
    monkeypatch.setattr(labelling, "read_key", lambda: next(keys))
    ok(invoke(env, "label"))


# ---------- snapshot / init / status ----------


def test_snapshot_freezes_the_items_and_reports_only_aggregates(env):
    out = ok(invoke(env, "snapshot", "--db", str(env["db"])))
    shot = load_snapshot(env["data"] / "items.json")
    assert len(shot.items) == N and shot.snapshot_id in out and "30 items" in out
    assert "title-only" in out and "hn" in out
    assert "item 0" not in out  # no titles: nothing here should prime the labeller


def test_a_second_snapshot_is_refused_and_a_missing_database_is_a_message(env, tmp_path):
    ok(invoke(env, "snapshot", "--db", str(env["db"])))
    fail(invoke(env, "snapshot", "--db", str(env["db"])), "already exists")
    fail(invoke(env, "--data-dir", str(tmp_path / "elsewhere"), "snapshot", "--db", str(tmp_path / "nope.db")), "no database")


def test_snapshot_never_modifies_the_database_it_reads(env):
    import hashlib

    before = hashlib.sha256(env["db"].read_bytes()).hexdigest()
    ok(invoke(env, "snapshot", "--db", str(env["db"])))
    assert hashlib.sha256(env["db"].read_bytes()).hexdigest() == before


def test_init_creates_the_queue_once_and_status_shows_the_starting_point(env):
    snapshot_and_init(env)
    out = ok(invoke(env, "status"))
    assert "0 of 34" in out  # 30 items + 4 repeats
    fail(invoke(env, "init"), "already exists")


def test_commands_that_need_earlier_steps_say_which(env):
    fail(invoke(env, "init"), "python -m benchmarks snapshot")
    fail(invoke(env, "label"), "python -m benchmarks snapshot")
    fail(invoke(env, "analyze"), "python -m benchmarks snapshot")


# ---------- labelling ----------


def test_label_runs_a_session_and_progress_is_visible_in_status(env, monkeypatch):
    snapshot_and_init(env)
    keys = iter(["y", "n", "m", "q"])
    monkeypatch.setattr(labelling, "read_key", lambda: next(keys))
    out = ok(invoke(env, "label"))
    assert "Saved. 3 of 34" in out
    assert "3 of 34" in ok(invoke(env, "status"))


def test_freeze_needs_every_item_and_then_writes_the_yardstick(env, monkeypatch):
    snapshot_and_init(env)
    keys = iter(["y", "q"])
    monkeypatch.setattr(labelling, "read_key", lambda: next(keys))
    ok(invoke(env, "label"))
    fail(invoke(env, "freeze"), "unlabelled")
    label_everything(env, monkeypatch)
    out = ok(invoke(env, "freeze"))
    assert (env["data"] / "labels_frozen.json").is_file() and "SHOW" in out
    fail(invoke(env, "freeze"), "already frozen")


# ---------- arms ----------


def test_the_jev_arm_is_saved_and_the_command_prints_no_item_content(env, monkeypatch):
    snapshot_and_init(env)
    monkeypatch.setattr(cli, "_jev_client", lambda http: FakeJev(lambda t, n: dict(match=n % 4)))
    out = ok(invoke(env, "arm", "jev", "--profile", str(env["profile"])))
    arm = load_arm(env["data"] / "arms" / "jev.json")
    assert arm.kind == "jev" and len(arm.records) == N and arm.meta["complete"]
    assert "30 of 30" in out and "item" not in out.lower().replace("items", "")  # counts, never titles or scores of items
    fail(invoke(env, "arm", "jev", "--profile", str(env["profile"])), "already exists")


def test_the_llm_arm_uses_the_production_stage1(env, monkeypatch):
    snapshot_and_init(env)
    monkeypatch.setattr(cli, "_llm_client", lambda http: SmartLLM())
    ok(invoke(env, "arm", "llm"))
    arm = load_arm(env["data"] / "arms" / "llm.json")
    assert arm.kind == "llm" and len(arm.records) == N


def test_an_incomplete_arm_is_not_saved_unless_asked_and_can_be_resumed(env, monkeypatch):
    snapshot_and_init(env)

    def dies_after_five(title, n):
        if n > 5:
            raise LLMUnavailable("down")
        return {}

    monkeypatch.setattr(cli, "_jev_client", lambda http: FakeJev(dies_after_five))
    fail(invoke(env, "arm", "jev", "--profile", str(env["profile"]), "--workers", "1"), "5 of 30", "run the same command again")
    assert not (env["data"] / "arms" / "jev.json").exists()

    monkeypatch.setattr(cli, "_jev_client", lambda http: FakeJev())
    ok(invoke(env, "arm", "jev", "--profile", str(env["profile"]), "--workers", "1"))  # resumes: only the missing 25 are requested
    assert len(load_arm(env["data"] / "arms" / "jev.json").records) == N


def test_a_missing_profile_or_key_is_a_message(env, monkeypatch):
    snapshot_and_init(env)
    fail(invoke(env, "arm", "jev", "--profile", str(env["data"] / "nope.toml")), "profile")


def test_rescore_makes_a_new_arm_from_stored_answers(env, monkeypatch):
    snapshot_and_init(env)
    monkeypatch.setattr(cli, "_jev_client", lambda http: FakeJev(lambda t, n: dict(match=n % 4)))
    ok(invoke(env, "arm", "jev", "--profile", str(env["profile"])))
    ok(invoke(env, "rescore", "jev", "--name", "jev-again"))
    assert load_arm(env["data"] / "arms" / "jev-again.json").meta["rescored_from"] == "jev"
    fail(invoke(env, "rescore", "jev", "--name", "jev-again"), "already exists")
    fail(invoke(env, "rescore", "nope", "--name", "x"), "nope")


# ---------- analysis ----------


def make_arms(env):
    shot = load_snapshot(env["data"] / "items.json")
    for name, direction in (("a", 1), ("b", -1)):
        records = {
            item["id"]: {"model": "m", "prompt_version": "pv", "category": "ai", "importance": 3, "summary": "", "relevance": 0.5 + direction * (item["id"] % 7) / 20, "details": None, "profile_hash": None}
            for item in shot.items
        }
        save_arm(Arm(name, "jev", {"snapshot_id": shot.snapshot_id, "kind": "jev"}, records), env["data"])


def test_analyze_writes_a_markdown_and_a_json_report_and_shows_the_markdown(env, monkeypatch):
    snapshot_and_init(env)
    make_arms(env)
    label_everything(env, monkeypatch)
    ok(invoke(env, "freeze"))
    out = ok(invoke(env, "analyze", "--n-boot", "20"))
    reports = sorted((env["data"] / "reports").iterdir())
    assert {p.suffix for p in reports} == {".md", ".json"}
    data = json.loads(next(p for p in reports if p.suffix == ".json").read_text(encoding="utf-8"))
    assert data["meta"]["labels_source"] == "frozen" and set(data["arms"]) == {"a", "b"}
    assert "Headline metrics" in out and "PRELIMINARY" not in out


def test_analyze_picks_arms_by_name_and_baseline(env, monkeypatch):
    snapshot_and_init(env)
    make_arms(env)
    label_everything(env, monkeypatch)
    ok(invoke(env, "freeze"))
    out = ok(invoke(env, "analyze", "--arms", "b,a", "--baseline", "b", "--n-boot", "10"))
    assert "Baseline for comparisons: `b`" in out
    fail(invoke(env, "analyze", "--arms", "zzz"), "zzz")


def test_analyze_refuses_partial_labels_unless_told_and_then_withholds_item_detail(env, monkeypatch):
    snapshot_and_init(env)
    make_arms(env)
    keys = iter(["y", "n", "m", "y", "n", "m", "y", "q"])
    monkeypatch.setattr(labelling, "read_key", lambda: next(keys))
    ok(invoke(env, "label"))
    fail(invoke(env, "analyze"), "7 of 30", "--allow-partial")
    out = ok(invoke(env, "analyze", "--allow-partial", "--n-boot", "10"))
    assert "PRELIMINARY" in out and "item 3" not in out and "hn item" not in out and "arxiv item" not in out


def test_analyze_with_no_arms_says_how_to_make_one(env, monkeypatch):
    snapshot_and_init(env)
    label_everything(env, monkeypatch)
    ok(invoke(env, "freeze"))
    fail(invoke(env, "analyze"), "python -m benchmarks arm")


# ---------- the double-click launcher ----------


def test_the_launcher_passes_arguments_through_and_does_not_wait_for_a_key(tmp_path):
    """Only the with-arguments path is run: the no-argument path starts an interactive labelling session."""
    import subprocess
    import sys
    from pathlib import Path

    if sys.platform != "win32":
        pytest.skip("Windows launcher")
    bat = Path(cli.__file__).parent / "label.bat"
    result = subprocess.run(
        ["cmd", "/c", str(bat), "--data-dir", str(tmp_path / "empty"), "status"],
        capture_output=True, text=True, encoding="utf-8", timeout=60, stdin=subprocess.DEVNULL,
    )
    assert result.returncode == 1 and "python -m benchmarks snapshot" in result.stdout  # a hang would hit the timeout


def test_the_launcher_pauses_only_when_started_without_arguments_and_has_windows_line_endings():
    from pathlib import Path

    raw = (Path(cli.__file__).parent / "label.bat").read_bytes()
    assert b"\r\n" in raw and b"\n" not in raw.replace(b"\r\n", b"")  # every line ends CRLF
    text = raw.decode()
    assert 'if "%~1"==""' in text and "-m benchmarks label" in text


# ---------- the docs stay true ----------


def test_the_benchmark_readme_documents_every_command_and_the_hard_rules():
    from pathlib import Path

    from typer.main import get_command

    text = (Path(cli.__file__).parent / "README.md").read_text(encoding="utf-8")
    missing = [name for name in get_command(cli.app).commands if f"benchmarks {name}" not in text]
    assert not missing, f"benchmarks/README.md does not mention: {missing}"
    for phrase in ("Why human labels are necessary", "circular", "What 186 labelled items can and cannot support", "Threats to validity", "label.bat"):
        assert phrase in text, phrase


def test_the_readme_metric_glossary_covers_every_metric_the_report_prints():
    """Every metric family `summarize` produces must be listed here with the phrase the README explains it by, so a
    new metric cannot be added without documenting it (and a renamed one cannot leave the README stale)."""
    from pathlib import Path

    from benchmarks.metrics import summarize

    explained_as = {
        "precision": "precision@k", "recall": "recall@k", "capture": "capture@k", "ndcg": "ndcg@k",
        "precision_lenient": "_lenient", "recall_lenient": "_lenient", "ap": "average precision",
        "auc_show_vs_skip": "show vs skip", "auc_show_vs_rest": "show vs rest", "ap_lenient": "_lenient", "spearman": "spearman",
    }
    families = {name.split("@")[0] for name in summarize([0, 1, 2]) if not name.startswith("hits")}
    assert families <= set(explained_as), f"add these to the map (and to the README): {families - set(explained_as)}"
    text = (Path(cli.__file__).parent / "README.md").read_text(encoding="utf-8").lower()
    assert [f for f in families if explained_as[f] not in text] == []


def test_the_decision_rules_say_what_the_owner_fixed_before_labelling():
    """The rules are registered before any label exists. If someone edits them, this fails and forces the edit to be
    deliberate: change the version (and the version history) too, as rule 7 requires."""
    import re
    from pathlib import Path

    from benchmarks.common import BENCHMARK_VERSION

    text = (Path(cli.__file__).parent / "README.md").read_text(encoding="utf-8")
    assert f"Benchmark version: **{BENCHMARK_VERSION}**" in text
    section = text[text.index("## Decision rules") :]
    section = section[: section.index("## Comparing a future change")]
    flat = re.sub(r"\s+", " ", section)
    for required in (
        "SHOW = 2",  # the label mapping, defined explicitly
        "MAYBE = 1",
        "SKIP = 0",
        "AP treats SHOW as relevant and MAYBE and SKIP as non-relevant",
        "new title_only recall@16 >= current Jev title_only recall@16",
        "development/evaluation benchmark",
        "not definitive evidence of generalization",
        "must not be changed after seeing results",
        "creates a new benchmark version",
    ):
        assert required in flat, f"decision rules no longer say: {required!r}"
    assert "**Primary metrics:** capture@16 and nDCG@16" in flat  # the unchanged rules are still there
    assert "fold" in flat and "No tuning on the same labels you test on" in flat
    assert re.search(r"### Version history.*v1", section, re.S)
