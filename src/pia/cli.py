"""Command line interface. Deliberately thin: all logic lives in pipeline/state."""

import logging
import sys
import threading
import webbrowser
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import httpx
import typer
from rich.console import Console
from rich.markdown import Markdown

from pia.briefing.curate import EnrichmentFailed, make_curator
from pia.collect import collect
from pia.config import DEFAULT_DB, DEFAULT_PROFILE, DEFAULT_SOURCES, ConfigError, get_groq_api_key, get_jev_api_key, get_jev_model
from pia.db import connect
from pia.doctor import run_checks
from pia.history import DatabaseMissing, connect_readonly, default_briefing_id, get_briefing, list_briefings
from pia.http import USER_AGENT
from pia.jev.client import DEFAULT_MODEL, JevClient
from pia.jev.design import CURRENT_DESIGN
from pia.jev.triage import make_jev_triage
from pia.llm.client import LLM, GroqClient
from pia.llm.enrich import enrich_pending
from pia.pipeline import AllSourcesFailed, run_briefing
from pia.profile import Profile, ProfileError, load_profile
from pia.sources.registry import load_sources
from pia.state import enriched_items, get_checkpoint, last_fetch_runs, pending_items

app = typer.Typer(add_completion=False, help="Personal Intelligence Agent: what happened since I last checked?")


TRIAGE_MODES = ("auto", "jev", "llm")


@dataclass
class Settings:
    db: Path
    config: Path
    profile: Path
    triage: str


def ensure_utf8_output() -> None:
    """Titles from the web contain any Unicode. Windows may hand us a cp1252 stream
    (notably when output is redirected), which would crash on the first 'ā'."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    db: Path = typer.Option(DEFAULT_DB, help="SQLite database file."),
    config: Path = typer.Option(DEFAULT_SOURCES, help="Sources config file."),
    profile: Path = typer.Option(DEFAULT_PROFILE, help="Your interest profile (TOML)."),
    triage: str = typer.Option(
        "auto",
        "--triage",
        envvar="PIA_TRIAGE",
        help="Stage 1: jev (decision model + your profile), llm (gpt-oss), or auto (Jev if a key and a profile exist).",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show warnings and retries."),
) -> None:
    """With no subcommand: fetch what's new and show your briefing."""
    ensure_utf8_output()
    logging.basicConfig(level=logging.INFO if verbose else logging.ERROR, format="%(levelname)s %(message)s")
    if triage not in TRIAGE_MODES:
        raise typer.BadParameter(f"must be one of {', '.join(TRIAGE_MODES)}", param_hint="--triage")
    ctx.obj = Settings(db, config, profile, triage)
    if ctx.invoked_subcommand is None:
        _briefing(ctx.obj)


def _briefing(settings: Settings) -> None:
    console = Console()
    conn = connect(settings.db)
    sources = load_sources(settings.config)
    now = datetime.now(timezone.utc)
    with _http_client() as client:
        try:
            llm = make_llm(client)
        except ConfigError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(1)
        try:
            triage, profile, note = _stage1(settings, client, llm)
        except (ConfigError, ProfileError) as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(1)
        console.print(f"[dim]{note}[/dim]")
        with console.status("Checking sources and reading what's new..."):
            try:
                result = run_briefing(conn, client, sources, now, prepare=make_curator(llm, triage=triage, profile=profile))
            except AllSourcesFailed as exc:
                console.print(f"[red]Could not reach any source, so nothing was recorded.[/red]\n{exc}")
                raise typer.Exit(1)
            except EnrichmentFailed as exc:
                console.print(f"[red]Nothing was recorded: {exc}[/red]")
                raise typer.Exit(1)
    console.print(Markdown(result.markdown))


def _http_client() -> httpx.Client:
    return httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=60, follow_redirects=True)


def make_llm(client: httpx.Client) -> LLM:
    return GroqClient(get_groq_api_key(), client)


def make_jev(client: httpx.Client) -> JevClient:
    return JevClient(get_jev_api_key(), client, model=get_jev_model() or DEFAULT_MODEL)


def _stage1(settings: Settings, client: httpx.Client, llm: LLM) -> tuple[Callable | None, Profile | None, str]:
    """Choose Stage 1. Returns (triage step, profile, one-line description). A step of None means the default LLM triage.

    `jev` is strict: a missing key or profile is an error, because silently doing something else after being asked
    for Jev would be misleading. `auto` uses Jev only when both exist and says why when it does not. In every mode an
    INVALID profile is an error: quietly ignoring a file the reader wrote would hide their mistake."""
    if settings.triage == "llm":
        return None, None, "Triage: LLM (gpt-oss-20b), no profile."
    try:
        jev = make_jev(client)
    except ConfigError:
        if settings.triage == "jev":
            raise
        return None, None, "Triage: LLM (gpt-oss-20b). Jev is not configured (add TYPESAFE_API_KEY to use it)."
    if settings.triage == "auto" and not settings.profile.is_file():
        return None, None, f"Triage: LLM (gpt-oss-20b). Jev has a key but there is no interest profile at {settings.profile}."
    profile = load_profile(settings.profile)  # ProfileError: missing (in jev mode) or invalid
    step = make_jev_triage(jev, profile, fallback_llm=llm)
    return step, profile, f"Triage: Jev ({CURRENT_DESIGN.version}) + your interest profile ({profile.hash}); editor: gpt-oss-120b with the same profile."


@app.command("collect")
def collect_command(ctx: typer.Context) -> None:
    """Fetch every source and store new items. Does not create a briefing."""
    conn = connect(ctx.obj.db)
    with _http_client() as client:
        results = collect(conn, client, load_sources(ctx.obj.config), datetime.now(timezone.utc))
    for r in results:
        detail = f"{r.inserted} new, {r.merged} already known" if r.ok else f"FAILED: {r.error}"
        typer.echo(f"  {r.name:<10} {detail}")


@app.command()
def enrich(
    ctx: typer.Context,
    limit: int = typer.Option(None, help="Only triage this many items (handy while testing)."),
) -> None:
    """Have the LLM triage new items (category, importance, summary) and show the results."""
    conn = connect(ctx.obj.db)
    with _http_client() as client:
        try:
            llm = make_llm(client)
        except ConfigError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(1)
        try:
            triage, _, note = _stage1(ctx.obj, client, llm)
        except (ConfigError, ProfileError) as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(1)
        typer.echo(note)
        now = datetime.now(timezone.utc)
        report = triage(conn, now, limit=limit) if triage else enrich_pending(conn, llm, now, limit=limit)

    typer.echo(f"Enriched {report.enriched}; {report.remaining} still pending"
               + (" (stopped early after repeated failures)" if report.stopped_early else ""))
    for row in enriched_items(conn):
        relevance = f"rel {row['relevance']:.2f}" if row["relevance"] is not None else " " * 8
        typer.echo(f"  {row['importance']} {row['category']:<9} {relevance}  {row['summary']}  [{row['source']}: {row['title'][:60]}]")


@app.command()
def status(ctx: typer.Context) -> None:
    """Show the checkpoint, unbriefed items and the health of each source. Read-only."""
    try:
        conn = connect_readonly(ctx.obj.db)
    except DatabaseMissing:
        typer.echo("Last checked: never")
        typer.echo("(no database yet; run `pia` to create it)")
        return
    checkpoint = get_checkpoint(conn)
    typer.echo(f"Last checked: {checkpoint:%Y-%m-%d %H:%M UTC}" if checkpoint else "Last checked: never")
    typer.echo(f"Not yet briefed: {len(pending_items(conn))}")
    failed = conn.execute("SELECT COUNT(*) FROM items WHERE status = 'failed'").fetchone()[0]
    typer.echo(f"Could not be processed (given up on): {failed}")
    typer.echo("Sources:")
    for run in last_fetch_runs(conn):
        detail = f"ok, {run['item_count']} items" if run["status"] == "ok" else f"FAILED: {run['error']}"
        typer.echo(f"  {run['source']:<10} {run['started_at'][:16]}  {detail}")


@app.command()
def history(ctx: typer.Context) -> None:
    """List every past check, newest first. Read-only."""
    try:
        rows = list_briefings(connect_readonly(ctx.obj.db))
    except DatabaseMissing:
        rows = []
    if not rows:
        typer.echo("No briefings yet. Run `pia` to create the first one.")
        return
    for r in rows:
        typer.echo(f"  #{r['id']:<3} {r['created_at'][:16]} UTC   showed {r['shown']:<3} skipped {r['skipped']}")
    typer.echo("\nRead one with: pia show <number>")


@app.command()
def show(
    ctx: typer.Context,
    briefing_id: int = typer.Argument(None, help="Which briefing (see `pia history`). Default: the latest one that had content."),
    raw: bool = typer.Option(False, "--raw", help="Print the plain markdown only (for piping or saving)."),
) -> None:
    """Re-read a past briefing exactly as it was saved. Read-only: fetches nothing, changes nothing."""
    try:
        conn = connect_readonly(ctx.obj.db)
    except DatabaseMissing:
        typer.echo("No briefings yet. Run `pia` to create the first one.", err=True)
        raise typer.Exit(1)
    if briefing_id is None:
        briefing_id = default_briefing_id(conn)
        if briefing_id is None:
            typer.echo("No briefings yet. Run `pia` to create the first one.", err=True)
            raise typer.Exit(1)
    briefing = get_briefing(conn, briefing_id)
    if briefing is None:
        typer.echo(f"No briefing #{briefing_id}. Run `pia history` to see what exists.", err=True)
        raise typer.Exit(1)
    if raw:
        typer.echo(briefing["rendered_md"])
        return
    typer.echo(f"Briefing #{briefing['id']}, saved {briefing['created_at'][:16]} UTC\n")
    Console().print(Markdown(briefing["rendered_md"]))


@app.command()
def doctor(
    ctx: typer.Context,
    online: bool = typer.Option(False, "--online", help="Also test the network: each source and your Groq key."),
) -> None:
    """Check that everything is set up correctly. Changes nothing; never prints your API key."""
    kwargs = dict(db_path=ctx.obj.db, config_path=ctx.obj.config, online=online, profile_path=ctx.obj.profile)
    if online:
        with _http_client() as client:
            checks = run_checks(**kwargs, client=client)
    else:
        checks = run_checks(**kwargs, client=None)

    labels = {"ok": "[ ok ]", "warn": "[warn]", "fail": "[FAIL]"}
    for c in checks:
        typer.echo(f"{labels[c.status]} {c.name}: {c.detail}")
    problems = sum(c.status == "fail" for c in checks)
    if problems:
        typer.echo(f"\n{problems} problem{'s' if problems != 1 else ''} found.")
        raise typer.Exit(1)
    typer.echo("\nAll good." + ("" if online else " (Add --online to also test the network.)"))


LOCAL_HOSTS = ("127.0.0.1", "localhost")


def _load_web():
    """Import the optional web dependencies only when `pia web` runs, so the core needs none of them."""
    import uvicorn

    from pia.web.app import create_app

    return uvicorn, create_app


def _open_later(url: str) -> None:
    threading.Timer(1.0, webbrowser.open, [url]).start()  # give the server a moment to start


@app.command()
def web(
    ctx: typer.Context,
    host: str = typer.Option("127.0.0.1", help="Address to listen on. Local addresses only."),
    port: int = typer.Option(8765, help="Port to listen on."),
    open_browser: bool = typer.Option(False, "--open", help="Open the page in your browser."),
) -> None:
    """Start the local web UI: New, Library and Favorites. Press Ctrl+C to stop."""
    if host not in LOCAL_HOSTS:
        typer.echo(
            f"Refusing to listen on {host!r}: the web UI is local-only, because it shows your personal "
            "reading history. Use 127.0.0.1 or localhost.",
            err=True,
        )
        raise typer.Exit(1)
    try:
        uvicorn, create_app = _load_web()
    except ImportError:
        typer.echo('The web UI needs extra packages. Install them with:  pip install -e ".[web]"', err=True)
        raise typer.Exit(1)

    web_app = create_app(ctx.obj.db)  # also upgrades the database schema if needed, like `pia` does
    url = f"http://{host}:{port}"
    typer.echo(f"PIA web UI: {url}   (Ctrl+C to stop)")
    if open_browser:
        _open_later(url)
    uvicorn.run(web_app, host=host, port=port, log_level="warning")


if __name__ == "__main__":
    app()
