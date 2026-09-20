"""The V1 pipeline: collect -> prepare the briefing -> commit it.

This is deterministic orchestration: the steps and their order are fixed in code. What
goes into the briefing (and whether an LLM is involved) is the injected `prepare` step;
nothing here decides what to do next based on a model's opinion.
"""

import sqlite3
from dataclasses import dataclass
from datetime import datetime

import httpx

from pia.briefing.content import Prepare
from pia.briefing.render import plain_prepare
from pia.collect import SourceResult, collect
from pia.sources.base import Source
from pia.state import commit_briefing, get_checkpoint


class AllSourcesFailed(Exception):
    """Nothing could be fetched, so we refuse to claim the user is 'up to date'."""


@dataclass
class BriefingResult:
    briefing_id: int
    covers_from: datetime | None
    covers_until: datetime
    items: list[sqlite3.Row]  # the items shown to the user
    source_results: list[SourceResult]
    markdown: str


def run_briefing(
    conn: sqlite3.Connection,
    client: httpx.Client | None,
    sources: list[Source],
    now: datetime,
    *,
    prepare: Prepare = plain_prepare,
    **collect_options,
) -> BriefingResult:
    checkpoint = get_checkpoint(conn)

    results = collect(conn, client, sources, now, **collect_options)
    if sources and not any(r.ok for r in results):
        errors = "; ".join(f"{r.name}: {r.error}" for r in results)
        raise AllSourcesFailed(errors)

    content = prepare(conn, checkpoint, now, results)  # if this raises, nothing was committed

    # The last step, and the only one that moves the checkpoint.
    briefing_id = commit_briefing(
        conn,
        covers_from=checkpoint,
        covers_until=now,
        created_at=now,
        rendered_md=content.markdown,
        shown_ids=[row["id"] for row in content.shown],
        skipped_ids=[row["id"] for row in content.skipped],
    )
    return BriefingResult(briefing_id, checkpoint, now, content.shown, results, content.markdown)
