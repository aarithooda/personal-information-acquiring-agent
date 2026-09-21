"""Stage 1 with Jev: for every new item, ask a set of typed questions and combine the answers in code.

Division of labour (the rule this whole project follows): the MODEL supplies calibrated judgments about one
item at a time; CODE decides what they add up to. Jev is asked nine small, single-proposition questions (its docs
say to decompose), never "is this worth showing?". Each answer is stored RAW in `enrichments.details`, and
`derive()` turns the raw answers into the relevance, importance and category the rest of the pipeline already
understands. Because it works from the stored form, the formula can be changed later without calling Jev again.

Two question sets live side by side. `jev-triage-v1` (this file: `build_questions` + `derive`) is the LEGACY design and
is kept exactly as it was, because every stored v1 row and the frozen benchmark measure it. `jev-triage-v2`
(pia.jev.design) is the current design and is what `make_jev_triage` uses. A design decides which requests are made for
an item (v1: one; v2: four, each with only the profile data it needs) and how the answers combine; `design_for` maps the
`question_set` stored with a row back to the code that derives it.

Requests are made per item (Jev evaluates one state at a time), a few items at a time, and each item's result is saved
the moment it is complete. All SQLite writes stay on the calling thread: workers only make HTTP calls.
"""

import logging
import sqlite3
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from pia.jev.client import SystemOneResponse, choice, noul, score
from pia.jev.design import CURRENT_DESIGN, SOURCE_KINDS, Derived, Design, Plan, Request, item_entry  # noqa: F401  (re-exported)
from pia.llm.client import LLM, LLMBadOutput, LLMError
from pia.llm.enrich import MAX_TRIAGE_ATTEMPTS, EnrichReport, enrich_pending
from pia.profile import Profile
from pia.state import items_needing_enrichment, record_triage_failures, save_enrichments

log = logging.getLogger(__name__)

QUESTION_SET_VERSION = "jev-triage-v1"  # bump when the questions change meaning (stored as enrichments.prompt_version)

# ---------- how the answers are combined (version 0: reasoned, NOT yet validated against labelled data) ----------
# relevance = quality x (1 - low-value penalty), where quality is a weighted blend of three independent judgments.
W_MATCH = 0.50  # how well the item fits the reader's interests
W_SUBSTANCE = 0.35  # how much real technical content it carries
W_BUILD = 0.15  # could it become part of something the reader builds
WILDCARD_CREDIT = 0.85  # a strong wildcard can stand in for topical match, discounted (discovery without echo chamber)
LOW_VALUE_PENALTY = 0.60  # at most this fraction is removed for a low-value pattern that no exception excuses
INJECTION_ZERO_AT = 0.90  # only a near-certain injection is dropped; see the note in derive()
SCORE_LEVELS = 4  # levels in each Score question (0..3)


def build_questions() -> dict[str, dict]:
    """The nine typed questions. Instructions point into the profile with backticked paths (per Jev's docs)."""
    return {
        "interest_match": score(
            "How well does the item match the interests described in `reader_profile`?",
            [
                "Unrelated to every interest area, or only shares a keyword without technical substance",
                "Adjacent to a lower-priority interest or a wildcard area, without a strong connection",
                "Clearly within a Tier 2 or Tier 3 area of `reader_profile.priority_hierarchy`, or a moderate match with a Tier 1 area",
                "Directly advances a Tier 1 area of `reader_profile.priority_hierarchy`",
            ],
        ),
        "substance": score(
            "How much genuine technical substance does the item carry?",
            [
                "Marketing, hype, generic news, opinion or personality-driven, with no technical content",
                "Discusses something technical but reports no concrete new result, method or evidence",
                "Reports a concrete technical result, tool, release or technique with some evidence or implementation",
                "A significant new capability, strong empirical or theoretical result, or deep idea that changes technical understanding",
            ],
        ),
        "low_value_pattern": noul("The item matches a pattern in `reader_profile.low_value_information.usually_low_value`."),
        "low_value_exception": noul("One of `reader_profile.low_value_information.exceptions` applies to the item."),
        "wildcard": noul("The item is a strong wildcard as described in `reader_profile.discovery.strong_wildcards`."),
        "buildable": noul(
            "The item could plausibly become a component, inspiration or experiment for something in `reader_profile.building_interests`."
        ),
        "too_little_info": noul("The item text is too short or vague to tell what the item actually says or claims."),
        "category": choice(
            "Which area does the item mainly belong to?",
            {
                "ai": "AI, machine learning, LLMs, agents, or tooling built for AI",
                "software": "Programming, software engineering, systems, developer tools or open-source infrastructure",
                "research": "Mathematics, physics, computer-science theory or other science that is not mainly about AI",
                "other": "None of these: general news, business, politics, lifestyle",
            },
        ),
        # Worded narrowly on purpose: an early wording ("tries to give instructions to whoever processes it") scored
        # 0.74 on the plain imperative title "Exfiltrate Your Weights". The target is text aimed at a MODEL.
        "injection": noul(
            "The item text contains instructions addressed to an AI assistant or language model, "
            "such as a request to ignore its earlier instructions."
        ),
    }


def build_state(profile_state: dict, item: dict) -> dict:
    """What Jev sees: the trimmed profile and the item. Popularity signals are deliberately NOT included: the ranker
    already accounts for them, and keeping them out avoids rewarding "popular but unrelated" items."""
    return {"reader_profile": profile_state, "item": item_entry(item)}


# ---------- answers -> relevance (pure, works from the stored form) ----------


def derive(answers: dict[str, dict]) -> Derived:
    """Combine raw answers (plain dicts, as stored in enrichments.details) into the pipeline's vocabulary."""
    p = lambda name: answers[name]["noul"]  # noqa: E731
    level = lambda name: answers[name]["score"] / (SCORE_LEVELS - 1)  # noqa: E731

    match, substance = level("interest_match"), level("substance")
    wildcard, build = p("wildcard"), p("buildable")
    low_value = p("low_value_pattern") * (1 - p("low_value_exception"))  # a pattern only counts if no exception excuses it

    topical = max(match, WILDCARD_CREDIT * wildcard)
    quality = W_MATCH * topical + W_SUBSTANCE * substance + W_BUILD * build
    relevance = quality * (1 - LOW_VALUE_PENALTY * low_value)

    flags: list[str] = []
    if p("injection") >= INJECTION_ZERO_AT:
        flags.append("possible_injection")
        relevance = 0.0
    relevance = min(1.0, max(0.0, relevance))

    probabilities = answers["category"]["probabilities"]
    category = max(probabilities, key=probabilities.get)
    if category == "other" and relevance >= 0.5:
        # A wildcard is by definition outside the listed areas, but the briefing has no "other" section and the
        # curator drops "other" items. File it under its closest real area instead of silently discarding it.
        category = max((c for c in probabilities if c != "other"), key=probabilities.get)

    features = {
        "interest_match": match,
        "substance": substance,
        "wildcard": wildcard,
        "buildable": build,
        "low_value": low_value,
        "too_little_info": p("too_little_info"),
        "injection": p("injection"),
    }
    return Derived(relevance, 1 + round(4 * relevance), category, features, flags)


# ---------- designs: which requests an item needs and how the answers combine ----------


class LegacyV1:
    """Question set v1, unchanged: ONE request per item carrying the whole trimmed profile and all nine questions."""

    version = QUESTION_SET_VERSION

    def plan(self, profile_state: dict) -> Plan:
        return Plan(self.version, (Request("all", profile_state, build_questions()),), {})

    def derive(self, answers: dict[str, dict], structure: dict, facts: dict) -> Derived:
        return derive(answers)


LEGACY_V1 = LegacyV1()
DESIGNS: dict[str, Design] = {LEGACY_V1.version: LEGACY_V1, CURRENT_DESIGN.version: CURRENT_DESIGN}


def design_for(question_set: str) -> Design:
    """The code that derives a stored row, looked up by the `question_set` stored in its details."""
    try:
        return DESIGNS[question_set]
    except KeyError:
        raise KeyError(f"unknown Jev question set {question_set!r}; known: {', '.join(DESIGNS)}") from None


# ---------- the triager ----------


@dataclass
class TriageResult:
    """The attributes state.save_enrichments reads. summary is empty: Jev does not generate text."""

    category: str
    importance: int
    relevance: float
    details: dict
    summary: str = ""


class _Jev(Protocol):
    def system_one(self, state: dict, questions: dict) -> SystemOneResponse: ...


@dataclass
class Judgement:
    """Everything Jev said about one item, across all of its requests."""

    answers: dict[str, dict]  # question id -> raw answer (plain dicts, as stored)
    usage: dict[str, int]  # summed over the item's requests
    models: list[str]  # the versioned ids that answered, sorted; more than one only if an alias moved mid-item
    requests: list[dict]  # per request: name, question count, input tokens


class JevTriager:
    """`design` defaults to the LEGACY v1 design so existing callers (notably the benchmark tooling, which measures the
    design that was frozen) keep exactly their behaviour. The production wiring, `make_jev_triage`, opts into the
    current design."""

    def __init__(self, client: _Jev, profile: Profile, *, workers: int = 4, max_consecutive_failures: int = 3, design: Design | None = None):
        self._client = client
        self._profile = profile
        self._design = design or LEGACY_V1
        self._plan = self._design.plan(profile.jev_state())  # compiled once, not per item (raises DesignError for an unusable profile)
        self._workers = workers
        self._max_failures = max_consecutive_failures

    def _ask(self, item: dict) -> Judgement:
        """All of one item's requests, in order. If any fails the whole item fails: a half-answered item is never stored."""
        entry = item_entry(item)
        answers: dict[str, dict] = {}
        models: set[str] = set()
        usage = {"input_tokens": 0, "output_tokens": 0}
        requests = []
        for request in self._plan.requests:
            response = self._client.system_one(self._plan.state_for(request, entry), request.questions)
            answers.update({name: answer.model_dump() for name, answer in response.answers.items()})
            models.add(response.model)
            usage["input_tokens"] += response.usage.input_tokens
            usage["output_tokens"] += response.usage.output_tokens
            requests.append({"name": request.name, "questions": len(request.questions), "input_tokens": response.usage.input_tokens})
        return Judgement(answers, usage, sorted(models), requests)

    def triage_pending(self, conn: sqlite3.Connection, now: datetime, *, limit: int | None = None) -> EnrichReport:
        rows = items_needing_enrichment(conn, limit)
        report = EnrichReport()
        if not rows:
            return report
        # Plain dicts for the workers: they must never touch the sqlite connection or its rows.
        items = {row["id"]: {"source": row["source"], "title": row["title"], "content_raw": row["content_raw"]} for row in rows}

        consecutive_outages = 0
        queue = iter(items)
        in_flight: dict[Future, int] = {}

        with ThreadPoolExecutor(max_workers=self._workers) as pool:

            def submit_next() -> None:
                # Backpressure: never more than `workers` requests in flight. Submitting everything up front would
                # let the pool run the whole backlog before we could react to an outage.
                item_id = next(queue, None)
                if item_id is not None:
                    in_flight[pool.submit(self._ask, items[item_id])] = item_id

            for _ in range(self._workers):
                submit_next()
            try:
                while in_flight:
                    done, _ = wait(in_flight, return_when=FIRST_COMPLETED)
                    for future in done:
                        item_id = in_flight.pop(future)
                        try:
                            response = future.result()
                        except LLMBadOutput as exc:  # this request was refused or unusable: may be this item's fault
                            log.warning("Jev could not score item %s: %s", item_id, exc)
                            report.failed_items += record_triage_failures(conn, [item_id], MAX_TRIAGE_ATTEMPTS)
                            consecutive_outages = 0
                        except LLMError as exc:  # unavailable: says nothing about the item
                            log.warning("Jev unavailable: %s", exc)
                            report.failed_batches += 1
                            consecutive_outages += 1
                            if consecutive_outages >= self._max_failures:
                                report.stopped_early = True  # circuit breaker: submit nothing more
                        else:
                            consecutive_outages = 0
                            self._save(conn, item_id, response, items[item_id], now)
                            report.enriched += 1
                        if not report.stopped_early:
                            submit_next()
            finally:
                for pending in in_flight:  # an unexpected error must not keep spending on the rest
                    pending.cancel()

        report.remaining = len(rows) - report.enriched - report.failed_items
        return report

    def _save(self, conn: sqlite3.Connection, item_id: int, judgement: Judgement, item: dict, now: datetime) -> None:
        facts = {"title_only": not (item["content_raw"] or "").strip()}  # a fact the code knows; recorded, never asked
        derived = self._design.derive(judgement.answers, self._plan.structure, facts)
        details = {
            "question_set": self._design.version,
            "answers": judgement.answers,
            "usage": judgement.usage,
            "features": derived.features,
            "flags": derived.flags,
        }
        if self._plan.structure:  # v2: enough to re-derive from the stored row alone, and to see how the item was asked about
            details["plan"] = self._plan.structure
            details["requests"] = judgement.requests
            details["models"] = judgement.models
        details.update(derived.extra)
        result = TriageResult(derived.category, derived.importance, derived.relevance, details)
        save_enrichments(
            conn,
            {item_id: result},
            model="+".join(judgement.models),  # the VERSIONED id(s) that answered (e.g. jev-1.13.0), not the alias we asked for
            prompt_version=self._design.version,
            now=now,
            profile_hash=self._profile.hash,
        )


def make_jev_triage(
    client: _Jev,
    profile: Profile,
    *,
    fallback_llm: LLM | None = None,
    workers: int = 4,
    max_consecutive_failures: int = 3,
    design: Design | None = None,
) -> Callable[..., EnrichReport]:
    """The Stage 1 step for the curator. If Jev stops responding and a fallback LLM is given, the existing LLM triage
    finishes the remaining items (graceful degradation, as everywhere else in PIA); otherwise they wait for next run."""
    triager = JevTriager(client, profile, workers=workers, max_consecutive_failures=max_consecutive_failures, design=design or CURRENT_DESIGN)

    def triage(conn: sqlite3.Connection, now: datetime, limit: int | None = None) -> EnrichReport:
        report = triager.triage_pending(conn, now, limit=limit)
        if report.stopped_early and fallback_llm is not None:
            log.warning("Jev stopped responding; the LLM triage will finish the remaining items")
            fallback = enrich_pending(conn, fallback_llm, now, limit=limit)
            report.enriched += fallback.enriched
            report.failed_items += fallback.failed_items
            report.failed_batches += fallback.failed_batches
            report.remaining = fallback.remaining
            report.stopped_early = fallback.stopped_early
        return report

    return triage
