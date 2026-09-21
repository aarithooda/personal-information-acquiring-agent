"""The freeze record: what makes "we did not change anything after seeing results" checkable instead of promised.

`FREEZE.json` binds together (1) the corpus (its content id and the manifest's hash), (2) the methodology document's hash (line endings
normalised), and (3) a fingerprint of the production system under test: the git tree hash of `src/pia`, whether anything there or in
the sources config is uncommitted, the sources config's hash, and the interest profile's hash. `verify` recomputes all of it. An
evaluation run whose `verify` reports a problem is void (METHODOLOGY.md, section 15).
"""

import hashlib
import json
import subprocess
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from benchmark_v2 import common as c
from benchmark_v2.corpus import compute_corpus_id
from benchmarks.common import BenchmarkError, read_json, write_json_atomic

MODEL_OUTPUT_DIRS = ("arms", "scratch", "reports")  # any of these appearing means model output exists for this corpus


def sha256_text_file(path: Path) -> str:
    """Hash of a text file with line endings normalised, so a git checkout on Windows does not look like an edit."""
    text = path.read_bytes().replace(b"\r\n", b"\n")
    return hashlib.sha256(text).hexdigest()


def _git(root: Path) -> Callable[[list[str]], str]:
    def run(args: list[str]) -> str:
        try:
            return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, timeout=30, check=True).stdout
        except (OSError, subprocess.SubprocessError) as exc:
            raise BenchmarkError(f"could not read the production code's state from git ({exc}); the freeze needs it") from exc

    return run


def production_fingerprint(root: Path = c.PROJECT_ROOT, run: Callable[[list[str]], str] | None = None) -> dict:
    """What identifies the system under test. `run(git_args) -> stdout` is injectable for tests."""
    run = run or _git(root)
    src_tree = run(["rev-parse", "HEAD:src/pia"]).strip()
    dirty = bool(run(["status", "--porcelain", "--", "src/pia", "config/sources.toml", "pyproject.toml"]).strip())
    sources = root / "config" / "sources.toml"
    profile_hash = None
    profile = root / "config" / "interests.toml"
    if profile.is_file():
        from pia.profile import load_profile  # the profile's own content hash, as stored with every triage result

        profile_hash = load_profile(profile).hash
    return {
        "src_tree": src_tree,
        "uncommitted_changes": dirty,
        "sources_config_sha256": sha256_text_file(sources) if sources.is_file() else None,
        "profile_hash": profile_hash,
    }


def _manifest_sha(data_dir: Path) -> str:
    return sha256_text_file(data_dir / c.MANIFEST_FILE)


def write_freeze(data_dir: Path, *, now: datetime, methodology_path: Path, fingerprint: dict) -> dict:
    path = data_dir / c.FREEZE_FILE
    if path.exists():
        raise BenchmarkError(f"{path} already frozen. The freeze is the record that nothing changed afterwards; it is written once.")
    if not (data_dir / c.ITEMS_FILE).is_file() or not (data_dir / c.MANIFEST_FILE).is_file():
        raise BenchmarkError(f"no corpus in {data_dir}. Build it first: python -m benchmark_v2 build")
    items = read_json(data_dir / c.ITEMS_FILE)["items"]
    record = {
        "benchmark_version": c.BENCHMARK_VERSION,
        "frozen_at": now.isoformat(),
        "corpus_id": compute_corpus_id(items),
        "n_items": len(items),
        "manifest_sha256": _manifest_sha(data_dir),
        "methodology_sha256": sha256_text_file(methodology_path),
        "production": fingerprint,
        "jev_model_planned": "jev-1.13.0",
        "model_output_exists": False,  # asserted at freeze time; verify() checks it stays true until the labels are frozen
    }
    write_json_atomic(path, record)
    return record


def verify(data_dir: Path, *, methodology_path: Path = c.METHODOLOGY, fingerprint: dict | None = None, expect_no_model_output: bool = True) -> list[str]:
    """Every way the frozen state no longer matches. An empty list means intact."""
    path = data_dir / c.FREEZE_FILE
    if not path.is_file():
        return ["not frozen yet: no FREEZE.json"]
    frozen = json.loads(path.read_text(encoding="utf-8"))
    problems: list[str] = []
    if compute_corpus_id(read_json(data_dir / c.ITEMS_FILE)["items"]) != frozen["corpus_id"]:
        problems.append("the corpus has changed since it was frozen (its content no longer matches the frozen corpus id)")
    if _manifest_sha(data_dir) != frozen["manifest_sha256"]:
        problems.append("the manifest has changed since it was frozen")
    if sha256_text_file(methodology_path) != frozen["methodology_sha256"]:
        problems.append("the methodology has changed since it was frozen; a change after freezing creates a new benchmark version")
    now = fingerprint if fingerprint is not None else production_fingerprint()
    before = frozen["production"]
    if now["src_tree"] != before["src_tree"]:
        problems.append("production code (src/pia) differs from the frozen tree: the evaluation would be of a different system")
    if now["uncommitted_changes"]:
        problems.append("uncommitted changes in src/pia, config/sources.toml or pyproject.toml: production is not the frozen system")
    if now["sources_config_sha256"] != before["sources_config_sha256"]:
        problems.append("config/sources.toml differs from the frozen one")
    if now["profile_hash"] != before["profile_hash"]:
        problems.append(f"the interest profile differs from the frozen one (was {before['profile_hash']}, now {now['profile_hash']})")
    if expect_no_model_output and any((data_dir / d).exists() for d in MODEL_OUTPUT_DIRS):
        problems.append("model output exists for this corpus, but the labels are not frozen yet")
    return problems
