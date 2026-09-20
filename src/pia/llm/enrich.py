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

from pia.llm.client import LLM, LLMError
from pia.llm.prompts import (
    PROMPT_VERSION,
    STAGE1_MODEL,
    STAGE1_SCHEMA,
    STAGE1_SCHEMA_NAME,
    STAGE1_SYSTEM,
    build_stage1_user,
)
from pia.state import items_needing_enrichment, save_enrichments

log = logging.getLogger(__name__)

MAX_SUMMARY_CHARS = 400


class ItemTriage(BaseModel):
    """One validated model answer. Strict schemas make malformed output rare, but a
    model can still return a duplicate index or a blank summary, so we check anyway."""

    index: int
    category: Literal["ai", "software", "research", "other"]
    importance: int = Field(ge=1, le=5)
    summary: str

    @field_validator("summary")
    @classmethod
    def _clean_summary(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("blank summary")
        return value[:MAX_SUMMARY_CHARS]


@dataclass
class EnrichReport:
    enriched: int = 0
    remaining: int = 0  # still pending; the next run will try them again
    failed_batches: int = 0
    stopped_early: bool = False


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
        valid[triage.index] = triage
    if not valid:
        raise LLMError("response contained no usable results")
    return {rows[index]["id"]: triage for index, triage in valid.items()}


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
            results = _triage_batch(llm, model, batch)
        except LLMError as exc:
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
        # Saved per batch, not at the end: a crash or a later failure never costs us
        # (or re-bills us for) work that already succeeded.
        save_enrichments(conn, results, model=model, prompt_version=PROMPT_VERSION, now=now)
        report.enriched += len(results)

    report.remaining = len(rows) - report.enriched
    return report
