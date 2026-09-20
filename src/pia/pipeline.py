"""The V1 pipeline: collect -> select what is new -> render -> commit the briefing.

This is deterministic orchestration: the steps and their order are fixed in code.
Nothing here decides what to do next based on an LLM's opinion.
"""

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

import httpx

from pia.briefing.render import render_plain
from pia.collect import SourceResult, collect
from pia.sources.base import Source
from pia.state import commit_briefing, get_checkpoint, pending_items


class AllSourcesFailed(Exception):
    """Nothing could be fetched, so we refuse to claim the user is 'up to date'."""


@dataclass
class BriefingResult:
    briefing_id: int
    covers_from: datetime | None
    covers_until: datetime
    items: list[sqlite3.Row]
    source_results: list[SourceResult]
    markdown: str


def run_briefing(
    conn: sqlite3.Connection,
    client: httpx.Client | None,
    sources: list[Source],
    now: datetime,
    *,
    render: Callable = render_plain,
    **collect_options,
) -> BriefingResult:
    checkpoint = get_checkpoint(conn)

    results = collect(conn, client, sources, now, **collect_options)
    if sources and not any(r.ok for r in results):
        errors = "; ".join(f"{r.name}: {r.error}" for r in results)
        raise AllSourcesFailed(errors)

    items = pending_items(conn)
    markdown = render(items, checkpoint, now, results)  # if this raises, nothing was committed

    # The last step, and the only one that moves the checkpoint.
    briefing_id = commit_briefing(
        conn,
        covers_from=checkpoint,
        covers_until=now,
        created_at=now,
        rendered_md=markdown,
        item_ids=[row["id"] for row in items],
    )
    return BriefingResult(briefing_id, checkpoint, now, items, results, markdown)
