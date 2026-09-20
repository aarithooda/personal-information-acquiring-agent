"""Command line interface. Deliberately thin: all logic lives in pipeline/state."""

import logging
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import httpx
import typer
from rich.console import Console
from rich.markdown import Markdown

from pia.briefing.curate import EnrichmentFailed, make_curator
from pia.collect import collect
from pia.config import DEFAULT_DB, DEFAULT_SOURCES, ConfigError, get_groq_api_key
from pia.db import connect
from pia.http import USER_AGENT
from pia.llm.client import LLM, GroqClient
from pia.llm.enrich import enrich_pending
from pia.llm.prompts import PROMPT_VERSION
from pia.pipeline import AllSourcesFailed, run_briefing
from pia.sources.registry import load_sources
from pia.state import enriched_items, get_checkpoint, last_fetch_runs, pending_items

app = typer.Typer(add_completion=False, help="Personal Intelligence Agent: what happened since I last checked?")


@dataclass
class Settings:
    db: Path
    config: Path


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
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show warnings and retries."),
) -> None:
    """With no subcommand: fetch what's new and show your briefing."""
    ensure_utf8_output()
    logging.basicConfig(level=logging.INFO if verbose else logging.ERROR, format="%(levelname)s %(message)s")
    ctx.obj = Settings(db, config)
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
        with console.status("Checking sources and reading what's new..."):
            try:
                result = run_briefing(conn, client, sources, now, prepare=make_curator(llm))
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
        report = enrich_pending(conn, llm, datetime.now(timezone.utc), limit=limit)

    typer.echo(f"Enriched {report.enriched}; {report.remaining} still pending"
               + (" (stopped early after repeated failures)" if report.stopped_early else ""))
    for row in enriched_items(conn, PROMPT_VERSION):
        typer.echo(f"  {row['importance']} {row['category']:<9} {row['summary']}  [{row['source']}: {row['title'][:60]}]")


@app.command()
def status(ctx: typer.Context) -> None:
    """Show the checkpoint, unbriefed items and the health of each source."""
    conn = connect(ctx.obj.db)
    checkpoint = get_checkpoint(conn)
    typer.echo(f"Last checked: {checkpoint:%Y-%m-%d %H:%M UTC}" if checkpoint else "Last checked: never")
    typer.echo(f"Not yet briefed: {len(pending_items(conn))}")
    typer.echo("Sources:")
    for run in last_fetch_runs(conn):
        detail = f"ok, {run['item_count']} items" if run["status"] == "ok" else f"FAILED: {run['error']}"
        typer.echo(f"  {run['source']:<10} {run['started_at'][:16]}  {detail}")


if __name__ == "__main__":
    app()
