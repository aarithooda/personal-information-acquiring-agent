"""Stage 1: triage every new item (category, importance, one-line summary).

The LLM supplies judgment; everything around it stays deterministic: which items are
sent, batching, validation, persistence and what happens on failure.
"""

import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, ValidationError, field_validator

from pia.llm.client import LLM, LLMBadOutput, LLMError
from pia.llm.prompts import (
    PROMPT_VERSION,
    STAGE1_MODEL,
    STAGE1_SCHEMA,
    STAGE1_SCHEMA_NAME,
    STAGE1_SYSTEM,
    build_stage1_user,
)
from pia.state import items_needing_enrichment, record_triage_failures, save_enrichments

log = logging.getLogger(__name__)

MAX_SUMMARY_CHARS = 400
MAX_TRIAGE_ATTEMPTS = 3  # after this many failed attempts an item is given up on


class ItemTriage(BaseModel):
    """One validated model answer. Strict schemas make malformed output rare, but a
    model can still return a duplicate index or an out-of-range score, so we check anyway."""

    index: int
    category: Literal["ai", "software", "research", "other"]
    importance: int = Field(ge=1, le=5)
    summary: str

    @field_validator("summary")
    @classmethod
    def _clean_summary(cls, value: str) -> str:
        return value.strip()[:MAX_SUMMARY_CHARS]


@dataclass
class EnrichReport:
    enriched: int = 0
    remaining: int = 0  # still pending; the next run will try them again
    failed_batches: int = 0
    failed_items: int = 0  # given up on after repeated failures
    stopped_early: bool = False


def _has_text(row: sqlite3.Row) -> bool:
    return bool((row["content_raw"] or "").strip())


def _triage_batch(llm: LLM, model: str, rows: list[sqlite3.Row]) -> dict[int, ItemTriage]:
    data = llm.complete_json(
        model=model,
        system=STAGE1_SYSTEM,
        user=build_stage1_user(rows),
        schema_name=STAGE1_SCHEMA_NAME,
        schema=STAGE1_SCHEMA,
        max_tokens=4000,
        reasoning_effort="low",
    )
    valid: dict[int, ItemTriage] = {}
    for entry in data.get("results", []):
        try:
            triage = ItemTriage.model_validate(entry)
        except ValidationError as exc:
            log.warning("dropping invalid triage entry: %s", exc.errors()[0]["msg"])
            continue
        if not 0 <= triage.index < len(rows) or triage.index in valid:
            log.warning("dropping triage entry with unusable index %s", triage.index)
            continue
        if not _has_text(rows[triage.index]):
            # With only a title to go on, any summary is the model guessing. Never keep one.
            triage.summary = ""
        elif not triage.summary:
            log.warning("dropping triage entry with a blank summary")
            continue
        valid[triage.index] = triage
    if not valid:
        raise LLMBadOutput("response contained no usable results")
    return {rows[index]["id"]: triage for index, triage in valid.items()}


def _run_batch(conn, llm: LLM, model: str, batch: list[sqlite3.Row], now: datetime, report: EnrichReport) -> None:
    """Triage one batch. Raises LLMError only when the LLM is unavailable (not the items' fault).

    If the model returns unusable output, split the batch in half and try each half: a single
    poison item then costs about log2(batch size) extra calls instead of blocking every
    batch behind it forever.
    """
    try:
        results = _triage_batch(llm, model, batch)
    except LLMBadOutput as exc:
        if len(batch) == 1:
            log.warning("giving one attempt against item %s: %s", batch[0]["id"], exc)
            report.failed_items += record_triage_failures(conn, [batch[0]["id"]], MAX_TRIAGE_ATTEMPTS)
            return
        middle = len(batch) // 2
        _run_batch(conn, llm, model, batch[:middle], now, report)
        _run_batch(conn, llm, model, batch[middle:], now, report)
        return

    # Saved per batch, not at the end: a crash or a later failure never costs us
    # (or re-bills us for) work that already succeeded.
    save_enrichments(conn, results, model=model, prompt_version=PROMPT_VERSION, now=now)
    report.enriched += len(results)
    unanswered = [row["id"] for row in batch if row["id"] not in results]
    if unanswered:  # the model skipped or botched these; count it so they cannot loop forever
        report.failed_items += record_triage_failures(conn, unanswered, MAX_TRIAGE_ATTEMPTS)


def enrich_pending(
    conn: sqlite3.Connection,
    llm: LLM,
    now: datetime,
    *,
    model: str = STAGE1_MODEL,
    batch_size: int = 10,
    max_consecutive_failures: int = 2,
    limit: int | None = None,
) -> EnrichReport:
    rows = items_needing_enrichment(conn, limit)
    report = EnrichReport()
    consecutive_failures = 0

    for start in range(0, len(rows), batch_size):
        batch = rows[start : start + batch_size]
        try:
            _run_batch(conn, llm, model, batch, now, report)
        except LLMError as exc:  # unavailable: outage, rate limit, bad key
            log.warning("triage batch failed: %s", exc)
            report.failed_batches += 1
            consecutive_failures += 1
            if consecutive_failures >= max_consecutive_failures:
                # Circuit breaker: an outage or exhausted rate limit will not fix itself
                # in the next few seconds, so stop and let the next run pick up the rest.
                report.stopped_early = True
                break
            continue
        consecutive_failures = 0

    report.remaining = len(rows) - report.enriched - report.failed_items
    return report
