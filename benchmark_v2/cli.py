"""`python -m benchmark_v2 ...`: build the corpus, freeze it, and run the blind labelling workflow.

BLINDNESS. Nothing here shows an item next to any model information, and this phase produces none: no arm, no Jev call, no LLM
call. `build` and `summary` print counts only (never titles), and the labelling commands reuse Benchmark v1's labelling code
unchanged, which shows the reader only what Jev would be sent: source label, title, text. The 40 repeats are ordinary corpus
items; their identities live in the internal manifest and never appear on the reader's screen or in the queue.
"""

import subprocess
import webbrowser
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import httpx
import typer

from benchmark_v2 import common as c
from benchmark_v2 import corpus as co
from benchmark_v2 import freeze as fz
from benchmark_v2.common import METHODOLOGY
from benchmarks import labelling
from benchmarks.common import BenchmarkError, read_json
from benchmarks.labelset import freeze_labels
from benchmarks.snapshot import is_title_only, load_snapshot
from pia.config import DEFAULT_SOURCES
from pia.db import connect
from pia.http import USER_AGENT
from pia.sources.registry import load_sources

app = typer.Typer(add_completion=False, help="Benchmark v2: a fresh, blind evaluation corpus for the current production Jev layer. See benchmark_v2/METHODOLOGY.md.")


@app.callback()
def main(ctx: typer.Context, data_dir: Path = typer.Option(c.DATA_DIR, help="Where the corpus, labels and (later) results live (git-ignored).")) -> None:
    from pia.cli import ensure_utf8_output

    ensure_utf8_output()
    ctx.obj = data_dir


@contextmanager
def friendly():
    try:
        yield
    except BenchmarkError as exc:
        typer.secho(str(exc), fg=typer.colors.RED)
        raise typer.Exit(1)


# Seams for tests: the real network, sources, clock and git are created here and nowhere else.
def _sources():
    return co.replayable(load_sources(DEFAULT_SOURCES))


def _http() -> httpx.Client:
    return httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=60, follow_redirects=True)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _methodology_committed() -> bool:
    """The methodology and the corpus code must be committed before freezing, so git history proves they predate the labels."""
    paths = ["benchmark_v2/METHODOLOGY.md", "benchmark_v2/common.py", "benchmark_v2/corpus.py", "benchmark_v2/freeze.py", "benchmark_v2/cli.py"]
    try:
        dirty = subprocess.run(["git", "status", "--porcelain", "--", *paths], cwd=c.PROJECT_ROOT, capture_output=True, text=True, timeout=30, check=True).stdout.strip()
        tracked = subprocess.run(["git", "ls-files", "--error-unmatch", "--", *paths], cwd=c.PROJECT_ROOT, capture_output=True, text=True, timeout=30).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False
    return tracked and not dirty


# ---------- aggregates (never items) ----------


def native_category(item: dict) -> str:
    """A category that comes from the SOURCE, not from any model: arXiv's primary category, GitHub's language, or the RSS feed."""
    signals = item["signals"]
    if "arxiv" in signals:
        categories = signals["arxiv"].get("categories") or []
        return f"arxiv:{categories[0]}" if categories else "arxiv:(none)"
    if "github" in signals:
        return f"github:{signals['github'].get('language') or 'no language'}"
    if item["source"] not in ("hn", "hf_papers"):
        return f"rss:{item['source']}"
    return "(none: Hacker News and HF papers have no native category)"


def summarize(items: list[dict], manifest: dict) -> dict:
    origin = manifest["origin_by_id"]
    groups = {"all": items, "new": [i for i in items if origin[str(i["id"])] == "new"], "repeat": [i for i in items if origin[str(i["id"])] == "repeat"]}
    week = lambda i: (lambda d: f"{d.isocalendar().year}-W{d.isocalendar().week:02d}")(datetime.fromisoformat(i["published_at"])) if i["published_at"] else "undated"  # noqa: E731
    published = sorted(i["published_at"] for i in items if i["published_at"])
    windows_seen = Counter(m["first_window"] for m in manifest["new"] if m.get("first_window") is not None)
    return {
        "n_items": len(items),
        "by_origin": {k: len(v) for k, v in groups.items() if k != "all"},
        "sources": {k: dict(sorted(Counter(i["source"] for i in v).items())) for k, v in groups.items()},
        "title_only": {k: {"title_only": sum(is_title_only(i) for i in v), "has_text": sum(not is_title_only(i) for i in v)} for k, v in groups.items()},
        "native_categories": {k: dict(sorted(Counter(native_category(i) for i in groups[k]).items())) for k in ("new", "repeat")},
        "published": {"by_iso_week": {k: dict(sorted(Counter(week(i) for i in v).items())) for k, v in groups.items() if k != "all"}, "earliest": published[0] if published else None, "latest": published[-1] if published else None,
                      "undated": sum(1 for i in items if not i["published_at"])},
        "windows": {"windows_in_period": manifest["period"]["windows"], "windows_represented_by_new_items": len(windows_seen), "new_items_per_window": dict(sorted(windows_seen.items()))},
        "pool": {**manifest["pool"], **manifest["collected"]},
        "absence_check": manifest["absence_check"],
    }


def _print_summary(s: dict) -> None:
    say = typer.echo
    say(f"Corpus {s['n_items']} items ({s['by_origin']['new']} new + {s['by_origin']['repeat']} repeats). Corpus id in FREEZE.json.")
    say("\nSources (all / new / repeats):")
    for name in sorted({n for g in s["sources"].values() for n in g}):
        say(f"  {name:<10}{s['sources']['all'].get(name, 0):>5}{s['sources']['new'].get(name, 0):>6}{s['sources']['repeat'].get(name, 0):>6}")
    say("\nNative categories, new items (from the source, not a model):")
    for name, n in sorted(s["native_categories"]["new"].items(), key=lambda kv: -kv[1]):
        say(f"  {n:>4}  {name}")
    say("\nTitle-only vs has text (all / new / repeats):")
    for k in ("all", "new", "repeat"):
        t = s["title_only"][k]
        say(f"  {k:<7} title-only {t['title_only']:>3}   has text {t['has_text']:>3}")
    p = s["published"]
    say(f"\nPublished: earliest {p['earliest']}, latest {p['latest']}, undated {p['undated']}")
    for k in ("new", "repeat"):
        say(f"  {k}: " + ", ".join(f"{w} {n}" for w, n in p["by_iso_week"][k].items()))
    w = s["windows"]
    say(f"  windows represented by new items: {w['windows_represented_by_new_items']} of {w['windows_in_period']}")
    say(f"\nPool: {s['pool']['eligible']} eligible; excluded {s['pool']['excluded']}; returned per source {s['pool'].get('returned_per_source')}; rejected by PIA's store {s['pool'].get('rejected_by_store')}")
    a = s["absence_check"]
    say(f"Absence check: {a['new_items']} new items, canonical-URL overlap with v1 = {a['canonical_url_overlap']}, source+external-id overlap = {a['source_external_id_overlap']}, passed = {a['passed']}")
    say(f"Repeats: {s['by_origin']['repeat']} (identities are recorded in the internal manifest and are not shown to the reader)")


# ---------- corpus ----------


@app.command()
def build(ctx: typer.Context) -> None:
    """Collect the historical pool through PIA's sources, sample the corpus, and write it (once)."""
    data: Path = ctx.obj
    with friendly():
        if (data / c.ITEMS_FILE).exists():
            raise BenchmarkError(f"{data / c.ITEMS_FILE} already exists. The corpus is frozen once written.")
        if not Path(c.V1_ITEMS).is_file():
            raise BenchmarkError(f"the v1 item snapshot is missing at {c.V1_ITEMS}; it is needed for the absence check and the repeats")
        if (data / c.POOL_DB).exists():
            raise BenchmarkError(f"{data / c.POOL_DB} exists from an unfinished build; delete it to start the build again")
        data.mkdir(parents=True, exist_ok=True)
        period = co.windows(c.WINDOW_START, c.WINDOW_END, c.WINDOW_DAYS)
        now = _now()
        pool = connect(data / c.POOL_DB)
        client = _http()
        try:
            typer.echo(f"Collecting {len(period)} windows of {c.WINDOW_DAYS} days ({c.WINDOW_START:%Y-%m-%d} to {c.WINDOW_END:%Y-%m-%d}); this takes several minutes because sources are asked politely.")
            report = co.collect_pool(pool, client, _sources(), period, now)
            built = co.build_corpus(pool, c.V1_ITEMS, first_window=report.first_window, n_new=c.N_NEW, n_repeat=c.N_REPEAT, now=now, collected=report.summary(), v1_db_path=c.V1_DB)
        finally:
            pool.close()
            if client is not None:
                client.close()
        co.write_corpus(built, data)
    typer.echo(f"Built corpus {built.corpus_id}: {c.N_NEW} new + {c.N_REPEAT} repeats = {len(built.items)} items ({built.manifest['pool']['eligible']} eligible in the pool).")
    typer.echo("Next: python -m benchmark_v2 summary   then   python -m benchmark_v2 freeze")


@app.command()
def summary(ctx: typer.Context) -> None:
    """Distributions of the corpus (sources, native categories, title-only, dates). Counts only; no item is named."""
    with friendly():
        items = read_json(ctx.obj / c.ITEMS_FILE)["items"] if (ctx.obj / c.ITEMS_FILE).is_file() else None
        if items is None:
            raise BenchmarkError("no corpus yet. Build it first: python -m benchmark_v2 build")
        _print_summary(summarize(items, read_json(ctx.obj / c.MANIFEST_FILE)))


@app.command()
def freeze(ctx: typer.Context) -> None:
    """Write FREEZE.json: the corpus, the methodology and the production code are now fixed."""
    with friendly():
        if not _methodology_committed():
            raise BenchmarkError("commit benchmark_v2/ (the methodology and the builder) first, so that git history proves they predate the labels")
        fingerprint = fz.production_fingerprint()
        if fingerprint["uncommitted_changes"]:
            raise BenchmarkError("there are uncommitted changes in src/pia, config/sources.toml or pyproject.toml; commit them so the system under test is fixed")
        record = fz.write_freeze(ctx.obj, now=_now(), methodology_path=METHODOLOGY, fingerprint=fingerprint)
    typer.echo(f"Frozen: corpus {record['corpus_id']} ({record['n_items']} items), methodology {record['methodology_sha256'][:12]}, production tree {record['production']['src_tree'][:12]}, profile {record['production']['profile_hash']}.")
    typer.echo("No model output exists for this corpus. Next: python -m benchmark_v2 init")


@app.command()
def verify(ctx: typer.Context, evaluation: bool = typer.Option(False, "--evaluation", help="Evaluation phase: model output is expected to exist.")) -> None:
    """Check that the corpus, methodology and production code still match the freeze. Exit code 1 lists every problem."""
    problems = fz.verify(ctx.obj, methodology_path=METHODOLOGY, expect_no_model_output=not evaluation)
    if problems:
        for p in problems:
            typer.secho(f"PROBLEM: {p}", fg=typer.colors.RED)
        raise typer.Exit(1)
    typer.echo("Freeze intact: corpus, methodology and production code match FREEZE.json.")


# ---------- the blind labelling workflow ----------


def _ready_for_labelling(data: Path, *, forbid_model_output: bool = True):
    """The corpus must be frozen and untouched, and no model output may exist. Production code is NOT checked here: labelling does not depend on it."""
    if not (data / c.FREEZE_FILE).is_file():
        raise BenchmarkError("the corpus is not frozen yet. Freeze it first: python -m benchmark_v2 freeze")
    problems = fz.verify(data, methodology_path=METHODOLOGY, fingerprint=_frozen_fingerprint(data), expect_no_model_output=forbid_model_output)
    if problems:
        raise BenchmarkError("cannot proceed: " + "; ".join(problems))
    return load_snapshot(data / c.ITEMS_FILE)


def _frozen_fingerprint(data: Path) -> dict:
    """Labelling and label-freezing do not depend on production code, so compare the freeze with itself on that axis."""
    return read_json(data / c.FREEZE_FILE)["production"]


@app.command()
def init(ctx: typer.Context) -> None:
    """Create the shuffled labelling queue (once): one screen per item, no repeats within the session."""
    with friendly():
        shot = _ready_for_labelling(ctx.obj)
        queue = labelling.init_labelling(shot, ctx.obj, seed=c.QUEUE_SEED, repeats=0, min_gap=labelling.DEFAULT_MIN_GAP)
    typer.echo(f"Queue ready: {len(queue)} screens.")
    typer.echo("Next: python -m benchmark_v2 label      (or double-click benchmark_v2\\label.bat)")


@app.command()
def label(ctx: typer.Context) -> None:
    """Label items with one key press each. Stop any time (q or Ctrl+C); run it again to continue."""
    with friendly():
        shot = _ready_for_labelling(ctx.obj)
        queue = labelling.load_queue(ctx.obj, shot)
        labelling.run_session(shot, queue, labelling.LabelStore(ctx.obj), read_key=labelling.read_key, out=typer.echo, open_url=webbrowser.open)


@app.command()
def status(ctx: typer.Context) -> None:
    """Labelling progress. Aggregates only."""
    with friendly():
        shot = _ready_for_labelling(ctx.obj)
        queue = labelling.load_queue(ctx.obj, shot)
        store = labelling.LabelStore(ctx.obj)
        progress = store.progress(queue)
        counts = Counter(store.first_pass_labels(queue).values())
    typer.echo(f"Labelling: {progress.done} of {progress.total} screens labelled.")
    typer.echo("  so far: " + ", ".join(f"{counts.get(n, 0)} {n}" for n in labelling.LABELS))
    typer.echo(f"Labels frozen: {'yes' if (ctx.obj / 'labels_frozen.json').is_file() else 'not yet (python -m benchmark_v2 freeze-labels, when all are labelled)'}")


@app.command("freeze-labels")
def freeze_labels_command(ctx: typer.Context) -> None:
    """Turn the labels into the frozen yardstick. Needs every item labelled and no model output to exist."""
    with friendly():
        shot = _ready_for_labelling(ctx.obj)
        queue = labelling.load_queue(ctx.obj, shot)
        frozen = freeze_labels(shot, queue, labelling.LabelStore(ctx.obj), ctx.obj, now=_now())
    typer.echo(f"Frozen {len(frozen.grades)} labels ({frozen.labels_hash}): " + ", ".join(f"{n} {name}" for name, n in frozen.counts.items()))
    typer.echo("Labels are now fixed. The models have still not seen this corpus. Tell your assistant that the labels are frozen.")
