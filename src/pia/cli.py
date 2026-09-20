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

from pia.config import DEFAULT_DB, DEFAULT_SOURCES
from pia.db import connect
from pia.http import USER_AGENT
from pia.pipeline import AllSourcesFailed, run_briefing
from pia.sources.registry import load_sources
from pia.state import get_checkpoint, last_fetch_runs, pending_items

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
    with httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=20, follow_redirects=True) as client:
        with console.status("Checking sources..."):
            try:
                result = run_briefing(conn, client, sources, now)
            except AllSourcesFailed as exc:
                console.print(f"[red]Could not reach any source, so nothing was recorded.[/red]\n{exc}")
                raise typer.Exit(1)
    console.print(Markdown(result.markdown))


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
