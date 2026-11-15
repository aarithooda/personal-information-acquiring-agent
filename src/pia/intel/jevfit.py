"""Jev as an optional personal-fit evaluator on the shortlist.

Role and why (ARCHITECTURE.md, "Model routing"; evidence in docs/evaluation.md): Jev answers typed yes/no and
choice questions against the owner's own written taste (config/interests.toml) and returns calibrated
probabilities, with raw answers that can be re-derived offline. The benchmark showed it ranks above chance and
could NOT show it is better than the LLM triage, so it is used as a second opinion blended into relevance for
the items most likely to reach the owner's attention, not as the sole judge of everything, and never as a claim
that it is superior. It runs only when a Jev key and the profile file both exist.

The question set is v1's `jev-triage-v2` design, reused unchanged.
"""

import json
import logging
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from pia.intel.usage import Meter, UsageRecord
from pia.jev.design import CURRENT_DESIGN, item_entry
from pia.llm.client import LLMError
from pia.profile import Profile
from pia.store.db import days_ago, iso

log = logging.getLogger(__name__)
MAX_AGE_DAYS = 10


def shortlist(conn: sqlite3.Connection, now: datetime, limit: int) -> list[sqlite3.Row]:
    """Recent, unread, not yet judged, most promising first."""
    return conn.execute(
        """SELECT id, source_name, title, content FROM items
           WHERE fit IS NULL AND stage = 'assessed' AND dismissed_at IS NULL AND read_at IS NULL AND muted = 0
             AND acquired_at >= ? AND kind != 'opportunity'
           ORDER BY score DESC LIMIT ?""", (days_ago(now, MAX_AGE_DAYS), limit)).fetchall()


def judge_shortlist(conn: sqlite3.Connection, jev, profile: Profile, meter: Meter | None, now: datetime, *, limit: int = 30, workers: int = 4) -> int:
    """Ask Jev about the shortlist and store `fit` plus the raw answers. Returns how many items were judged.
    Any Jev failure leaves the item without a fit; relevance then rests on the interest profile alone."""
    rows = shortlist(conn, now, limit)
    if not rows:
        return 0
    plan = CURRENT_DESIGN.plan(profile.jev_state())
    items = {row["id"]: {"source": row["source_name"], "title": row["title"], "content_raw": row["content"]} for row in rows}

    def ask(item: dict) -> dict:
        entry = item_entry(item)
        answers, models, tokens = {}, set(), 0
        for request in plan.requests:  # all of an item's requests or none: a half-answered item is never stored
            response = jev.system_one(plan.state_for(request, entry), request.questions)
            answers.update({name: answer.model_dump() for name, answer in response.answers.items()})
            models.add(response.model)
            tokens += response.usage.input_tokens
        return {"answers": answers, "models": sorted(models), "input_tokens": tokens}

    judged = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {item_id: pool.submit(ask, item) for item_id, item in items.items()}
        for item_id, future in futures.items():
            try:
                result = future.result()
            except LLMError as exc:
                log.warning("Jev could not judge item %s: %s", item_id, exc)
                continue
            facts = {"title_only": not (items[item_id]["content_raw"] or "").strip()}
            derived = CURRENT_DESIGN.derive(result["answers"], plan.structure, facts)
            model = "+".join(result["models"])
            payload = {"answers": result["answers"], "plan": plan.structure, "features": derived.features, "flags": derived.flags, "profile_hash": profile.hash}
            conn.execute(
                "INSERT INTO judgements (item_id, task, provider, model, version, payload, created_at) VALUES (?, 'fit', 'typesafe', ?, ?, ?, ?)",
                (item_id, model, CURRENT_DESIGN.version, json.dumps(payload, sort_keys=True), iso(now)),
            )
            conn.execute("UPDATE items SET fit = ? WHERE id = ?", (round(derived.relevance, 4), item_id))
            if meter is not None:
                meter.add(UsageRecord(at=now, provider="typesafe", model=model, task="fit", input_tokens=result["input_tokens"],
                                      cost_usd=meter.config.cost(model, result["input_tokens"], 0), items=1))
            judged += 1
    conn.commit()
    return judged
