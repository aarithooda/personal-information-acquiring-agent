"""`python -m benchmarks ...`: the command line for the labelled benchmark. Deliberately thin.

BLINDNESS RULE. While the reader is labelling, no command may show an item next to a model's opinion of it. So
`snapshot`, `status` and `arm` print aggregates only (counts, timings), never titles or scores, and `analyze` withholds
item-level lists until every item is labelled.
"""

import webbrowser
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import httpx
import typer

from benchmarks import labelling
from benchmarks.analysis import Config, analyze, render_markdown
from benchmarks.arms import arm_path, load_arm, rescore_jev_arm, run_jev_arm, run_llm_arm, save_arm
from benchmarks.common import ARMS_DIR, DATA_DIR, FROZEN_LABELS_FILE, REPORTS_DIR, SCRATCH_DIR, SNAPSHOT_FILE, BenchmarkError, read_json, write_json_atomic
from benchmarks.labelset import freeze_labels, load_labels, retest_summary
from benchmarks.snapshot import export_snapshot, is_title_only, load_snapshot
from pia.cli import ensure_utf8_output
from pia.config import DEFAULT_DB, DEFAULT_PROFILE, ConfigError, get_groq_api_key, get_jev_api_key
from pia.http import USER_AGENT
from pia.jev.client import JevClient
from pia.llm.client import GroqClient
from pia.profile import ProfileError, load_profile

app = typer.Typer(add_completion=False, help="Labelled benchmark for PIA's Stage 1. See benchmarks/README.md.")


@app.callback()
def main(ctx: typer.Context, data_dir: Path = typer.Option(DATA_DIR, help="Where the snapshot, labels, arms and reports live (git-ignored).")) -> None:
    ensure_utf8_output()
    ctx.obj = data_dir


@contextmanager
def friendly():
    """A problem the user can fix is a message and exit code 1, not a stack trace."""
    try:
        yield
    except (BenchmarkError, ConfigError) as exc:
        typer.secho(str(exc), fg=typer.colors.RED)
        raise typer.Exit(1)
    except ProfileError as exc:
        typer.secho(f"Interest profile problem: {exc}", fg=typer.colors.RED)
        raise typer.Exit(1)


# Seams for tests: the real clock, the real HTTP client and the real model clients are created here, nowhere else.
def _now() -> datetime:
    return datetime.now(timezone.utc)


def _http() -> httpx.Client:
    return httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=60, follow_redirects=True)


def _jev_client(http: httpx.Client) -> JevClient:
    return JevClient(get_jev_api_key(), http)


def _llm_client(http: httpx.Client) -> GroqClient:
    return GroqClient(get_groq_api_key(), http)


# ---------- step 1: freeze the items ----------


@app.command()
def snapshot(
    ctx: typer.Context,
    db: Path = typer.Option(DEFAULT_DB, help="The PIA database to copy items from (opened read-only)."),
    force: bool = typer.Option(False, "--force", help="Replace an existing snapshot. This starts the benchmark over."),
) -> None:
    """Freeze the items every arm and every label will refer to."""
    with friendly():
        shot = export_snapshot(db, ctx.obj / SNAPSHOT_FILE, now=_now(), force=force)
    by_source: dict[str, int] = {}
    for item in shot.items:
        by_source[item["source"]] = by_source.get(item["source"], 0) + 1
    title_only = sum(is_title_only(item) for item in shot.items)
    typer.echo(f"Froze {len(shot.items)} items (snapshot {shot.snapshot_id}).")
    typer.echo("  " + ", ".join(f"{n} {source}" for source, n in sorted(by_source.items())))
    typer.echo(f"  {title_only} title-only ({100 * title_only / max(1, len(shot.items)):.0f}%)")
    typer.echo("Next: python -m benchmarks init")


# ---------- step 2: label ----------


@app.command()
def init(
    ctx: typer.Context,
    seed: int = typer.Option(labelling.DEFAULT_SEED, help="Seed for the shuffled order (fixed, so it is reproducible)."),
    repeats: int = typer.Option(labelling.DEFAULT_REPEATS, help="How many items are shown a second time, to measure your consistency."),
    min_gap: int = typer.Option(labelling.DEFAULT_MIN_GAP, help="A repeat comes at least this many screens after its first showing."),
) -> None:
    """Create the labelling queue (once)."""
    with friendly():
        shot = load_snapshot(ctx.obj / SNAPSHOT_FILE)
        queue = labelling.init_labelling(shot, ctx.obj, seed=seed, repeats=repeats, min_gap=min_gap)
    typer.echo(f"Queue ready: {len(queue)} screens ({len(shot.items)} items, {len(queue) - len(shot.items)} shown twice on purpose).")
    typer.echo("Next: python -m benchmarks label      (or double-click benchmarks\\label.bat)")


@app.command()
def label(ctx: typer.Context) -> None:
    """Label items with one key press each. Stop any time (q or Ctrl+C); run it again to continue."""
    with friendly():
        shot = load_snapshot(ctx.obj / SNAPSHOT_FILE)
        queue = labelling.load_queue(ctx.obj, shot)
        store = labelling.LabelStore(ctx.obj)
        labelling.run_session(shot, queue, store, read_key=labelling.read_key, out=typer.echo, open_url=webbrowser.open)


@app.command()
def status(ctx: typer.Context) -> None:
    """Where things stand: labelling progress, frozen labels, arms, reports. Aggregates only."""
    data = ctx.obj
    with friendly():
        shot = load_snapshot(data / SNAPSHOT_FILE)
        typer.echo(f"Snapshot: {len(shot.items)} items ({shot.snapshot_id})")
        try:
            queue = labelling.load_queue(data, shot)
        except BenchmarkError:
            typer.echo("Labelling: not started (python -m benchmarks init)")
        else:
            store = labelling.LabelStore(data)
            progress = store.progress(queue)
            typer.echo(f"Labelling: {progress.done} of {progress.total} screens labelled (items: {progress.first_pass_done} of {progress.first_pass_total})")
            counts = {name: sum(v == name for v in store.first_pass_labels(queue).values()) for name in labelling.LABELS}
            typer.echo("  so far: " + ", ".join(f"{n} {name}" for name, n in counts.items()))
        frozen = data / FROZEN_LABELS_FILE
        typer.echo(f"Frozen labels: {'yes (' + read_json(frozen)['labels_hash'] + ')' if frozen.is_file() else 'not yet (python -m benchmarks freeze, when everything is labelled)'}")
        arms = sorted((data / ARMS_DIR).glob("*.json")) if (data / ARMS_DIR).is_dir() else []
        for path in arms:
            arm = load_arm(path)
            typer.echo(f"Arm {arm.name}: {arm.kind}, {arm.meta.get('n_scored')} of {arm.meta.get('n_items')} items scored")
        if not arms:
            typer.echo("Arms: none yet (python -m benchmarks arm jev)")
        reports = list((data / REPORTS_DIR).glob("*.md")) if (data / REPORTS_DIR).is_dir() else []
        typer.echo(f"Reports: {len(reports)}")


@app.command()
def freeze(ctx: typer.Context, force: bool = typer.Option(False, "--force", help="Replace frozen labels (only if you mean to relabel).")) -> None:
    """Turn your labels into the frozen yardstick. Needs every item labelled."""
    with friendly():
        shot = load_snapshot(ctx.obj / SNAPSHOT_FILE)
        queue = labelling.load_queue(ctx.obj, shot)
        frozen = freeze_labels(shot, queue, labelling.LabelStore(ctx.obj), ctx.obj, now=_now(), force=force)
    typer.echo(f"Frozen {len(frozen.grades)} labels ({frozen.labels_hash}): " + ", ".join(f"{n} {name}" for name, n in frozen.counts.items()))
    retest = retest_summary(frozen.retest)
    if retest["n"]:
        typer.echo(f"Consistency on the {retest['n']} items you saw twice: same label {round(retest['agreement'] * retest['n'])} times; SHOW<->SKIP flips: {retest['show_skip_flips']}")
    typer.echo("Next: python -m benchmarks arm jev; python -m benchmarks arm llm; python -m benchmarks analyze")


# ---------- step 3: arms ----------


@app.command()
def arm(
    ctx: typer.Context,
    kind: str = typer.Argument(..., help="jev (the current Jev Stage 1 + your profile) or llm (the current gpt-oss-20b Stage 1)."),
    name: str = typer.Option(None, help="Name for this arm's results file (default: the kind)."),
    profile: Path = typer.Option(DEFAULT_PROFILE, help="Your interest profile (jev only)."),
    workers: int = typer.Option(4, help="Parallel Jev requests."),
    force: bool = typer.Option(False, "--force", help="Replace an existing arm of this name."),
    allow_incomplete: bool = typer.Option(False, "--allow-incomplete", help="Save even if some items got no score."),
) -> None:
    """Run the CURRENT production Stage 1 on the snapshot (never on your real database) and freeze its output.

    Prints counts and timings only, never items or scores, so it is safe to run while you are still labelling."""
    if kind not in ("jev", "llm"):
        raise typer.BadParameter("must be jev or llm", param_hint="KIND")
    name = name or kind
    with friendly():
        if arm_path(ctx.obj, name).exists() and not force:
            raise BenchmarkError(f"{arm_path(ctx.obj, name)} already exists. Results are frozen once saved; use --name for a new one, or --force to replace it.")
        shot = load_snapshot(ctx.obj / SNAPSHOT_FILE)
        scratch = ctx.obj / SCRATCH_DIR
        with _http() as http:
            if kind == "jev":
                result = run_jev_arm(shot, load_profile(profile), _jev_client(http), scratch, name=name, now=_now(), workers=workers)
            else:
                result = run_llm_arm(shot, _llm_client(http), scratch, name=name, now=_now())
        scored, total = result.meta["n_scored"], result.meta["n_items"]
        if not result.meta["complete"] and not allow_incomplete:
            raise BenchmarkError(
                f"{name}: only {scored} of {total} items were scored, so nothing was saved. Finished items are kept: "
                "run the same command again to resume (they are not paid for twice), or pass --allow-incomplete."
            )
        path = save_arm(result, ctx.obj, force=force)
    tokens = f", {result.meta['input_tokens']:,} input tokens" if "input_tokens" in result.meta else ""
    typer.echo(f"{name}: {scored} of {total} items scored in {result.meta['elapsed_seconds']} s{tokens}. Saved {path}.")


@app.command()
def rescore(
    ctx: typer.Context,
    source: str = typer.Argument(..., help="An existing jev arm."),
    name: str = typer.Option(..., help="Name for the new arm."),
    force: bool = typer.Option(False, "--force"),
) -> None:
    """Apply the CURRENT combining formula (jev/triage.py derive) to a Jev arm's stored raw answers. No API calls."""
    with friendly():
        if arm_path(ctx.obj, name).exists() and not force:
            raise BenchmarkError(f"{arm_path(ctx.obj, name)} already exists. Use another --name, or --force.")
        original = load_arm(arm_path(ctx.obj, source))
        path = save_arm(rescore_jev_arm(original, name), ctx.obj, force=force)
    typer.echo(f"Rescored {source} with the current formula -> {path}. Compare: python -m benchmarks analyze --arms {source},{name}")


# ---------- step 4: results ----------


@app.command("analyze")
def analyze_command(
    ctx: typer.Context,
    arms: str = typer.Option(None, "--arms", help="Comma-separated arm names (default: all). The first is the baseline unless --baseline says otherwise."),
    baseline: str = typer.Option(None, help="The arm the others are compared against."),
    allow_partial: bool = typer.Option(False, "--allow-partial", help="Analyse unfinished labelling: PRELIMINARY, and item lists are withheld."),
    n_boot: int = typer.Option(1000, help="Bootstrap resamples for the intervals (0 = none)."),
    seed: int = typer.Option(0, help="Bootstrap seed."),
    fn_k: int = typer.Option(16, help="A SHOW item ranked below this counts as missed."),
) -> None:
    """Score the arms against your labels and write a report (Markdown + JSON) to benchmarks/data/reports."""
    with friendly():
        shot = load_snapshot(ctx.obj / SNAPSHOT_FILE)
        labels = load_labels(shot, ctx.obj, allow_partial=allow_partial)
        arms_dir = ctx.obj / ARMS_DIR
        names = [n.strip() for n in arms.split(",")] if arms else sorted(p.stem for p in arms_dir.glob("*.json")) if arms_dir.is_dir() else []
        if not names:
            raise BenchmarkError("no arms yet. Run one first: python -m benchmarks arm jev")
        loaded = {n: load_arm(arm_path(ctx.obj, n)) for n in names}
        now = _now()
        result = analyze(shot, labels, loaded, config=Config(n_boot=n_boot, seed=seed, baseline=baseline, fn_k=fn_k), now=now)
        stem = f"report-{labels.labels_hash}-{now:%Y%m%d-%H%M%S}"
        markdown = render_markdown(result)
        (ctx.obj / REPORTS_DIR).mkdir(parents=True, exist_ok=True)
        (ctx.obj / REPORTS_DIR / f"{stem}.md").write_text(markdown, encoding="utf-8")
        write_json_atomic(ctx.obj / REPORTS_DIR / f"{stem}.json", result)
    typer.echo(markdown)
    typer.echo(f"\nSaved {ctx.obj / REPORTS_DIR / (stem + '.md')} and .json")
