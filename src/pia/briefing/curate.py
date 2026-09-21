"""Turns new items into a briefing: triage -> rank -> editor -> render.

The judgment (what is significant, how to explain it) comes from the LLM stages. Everything
about *how much* to show, *which* candidates the editor sees, what happens on failure and
which items are marked shown or skipped is deterministic code.
"""

import logging
import sqlite3
from collections.abc import Callable
from datetime import datetime

from pia.briefing.budget import also_budget, days_since, headline_budget, shortlist_size
from pia.briefing.content import BriefingContent, Prepare
from pia.briefing.rank import rank_items
from pia.briefing.render import SECTIONS, render_briefing
from pia.collect import SourceResult
from pia.llm.client import LLM, LLMError
from pia.llm.enrich import enrich_pending
from pia.llm.headlines import Headline, select_headlines
from pia.llm.prompts import STAGE2_MODEL
from pia.profile import Profile
from pia.state import enriched_items, items_needing_enrichment

log = logging.getLogger(__name__)


class EnrichmentFailed(Exception):
    """New items exist but none could be triaged. We commit nothing rather than brief on nothing."""


def make_curator(
    llm: LLM,
    *,
    editor_model: str = STAGE2_MODEL,
    triage: Callable[[sqlite3.Connection, datetime], object] | None = None,
    profile: Profile | None = None,
) -> Prepare:
    """`triage(conn, now)` is Stage 1. Default: the LLM triage (unchanged). Pass make_jev_triage(...) to use Jev.
    `profile`, if given, is shown to the editor (Stage 2) so its choices and explanations follow the reader's own words."""
    profile_text = profile.llm_text() if profile else None
    run_triage = triage or (lambda conn, now: enrich_pending(conn, llm, now))

    def prepare(
        conn: sqlite3.Connection,
        checkpoint: datetime | None,
        now: datetime,
        results: list[SourceResult],
    ) -> BriefingContent:
        run_triage(conn, now)
        triaged = enriched_items(conn)
        awaiting = len(items_needing_enrichment(conn))
        if not triaged and awaiting:
            raise EnrichmentFailed(f"{awaiting} new items could not be triaged (is the LLM reachable?)")

        # 'other' is irrelevant by definition, and importance 1 means "noise" in the rubric.
        relevant = [r for r in triaged if r["category"] != "other" and r["importance"] > 1]
        ranked = rank_items(relevant)

        budget = headline_budget(days_since(checkpoint, now))
        editor_failed = False
        try:
            headlines = select_headlines(
                llm, ranked[: shortlist_size(budget)], budget, model=editor_model, profile_text=profile_text
            )
        except LLMError as exc:
            log.warning("editor step failed, falling back to plain ranking: %s", exc)
            editor_failed = True
            headlines = [Headline(row, row["summary"], None) for row in ranked[:budget]]

        headline_ids = {h.row["id"] for h in headlines}
        extras = [row for row in ranked if row["id"] not in headline_ids][: also_budget(budget)]
        # Group extras by section order so the rendered list reads category by category.
        section_order = {category: i for i, (category, _) in enumerate(SECTIONS)}
        extras.sort(key=lambda row: section_order.get(row["category"], len(section_order)))

        shown = [h.row for h in headlines] + extras
        shown_ids = {row["id"] for row in shown}
        markdown = render_briefing(
            headlines=headlines,
            extras=extras,
            checkpoint=checkpoint,
            now=now,
            results=results,
            triaged=len(triaged),
            awaiting=awaiting,
            editor_failed=editor_failed,
        )
        return BriefingContent(
            markdown=markdown,
            shown=shown,
            skipped=[row for row in triaged if row["id"] not in shown_ids],
        )

    return prepare
