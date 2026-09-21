"""Question set v2: what Jev is asked, of whom, and how the answers become a relevance.

Every rule here comes from TypeSafe's documentation (docs.typesafe.ai: primitives, confidence, composite scoring,
and the jev-1.13 "known limitations" page), not from tuning against any labelled data:

  * ONE CONDITION PER NOUL. "If a question has two conditions ... the value means less. Ask two Nouls and combine them
    in code." So a profile list ("matches a pattern in usually_low_value") is not one question about the whole list;
    each ENTRY of the reader's own list becomes its own Noul, pointing at it with the documented backtick path.
  * SCORE LEVELS ARE ONE DIMENSION, and a Score's decimal has "weak numerical calibration". v2 asks no Score at all:
    priority is a Choice over the reader's own tiers (plus `none`), whose PROBABILITIES are used, not just its winner.
  * ONLY THE STATE A QUESTION NEEDS. "Accuracy falls as the state grows with content unrelated to the decision." Each
    request carries only the profile sections its questions point at; questions that need no profile (category,
    prompt injection, unclear subject) send none.
  * CONFIDENCE TELLS YOU WHETHER TO ACT, NOT WHAT THE ANSWER IS. It is stored as metadata beside the score and never
    multiplied into it.
  * ARITHMETIC IN CODE ("Jev is not a calculator"), with every raw answer preserved so the formula can change later
    without calling Jev again.

The taste comes from the reader's profile (their own signals, low-value patterns, wildcards, tiers); the code only
aggregates probabilities. The few constants below are named PRIORS with a stated reason, not fitted numbers.
"""

import json
import re
from dataclasses import dataclass, field
from typing import Protocol

from pia.llm.prompts import CONTENT_CHARS
from pia.profile import ProfileError, approx_tokens

VERSION = "jev-triage-v2"

# ---------- constants (priors, not fitted; see docs/design.md, "Jev layer v2") ----------
VALUE_FLOOR = 0.5  # substance evidence may at most HALVE relevance (never gate it): the evidence a title cannot carry must not erase a strong fit
INJECTION_ZERO_AT = 0.90  # only a near-certain injection is dropped (unchanged from v1; see the note in the v1 derive)
OTHER_REMAP_RELEVANCE = 0.5  # unchanged from v1: the curator drops category "other", so a relevant one is filed under its closest real area

SOURCE_KINDS = {
    "hn": "Hacker News link",
    "arxiv": "arXiv paper",
    "hf_papers": "Hugging Face daily paper",
    "github": "GitHub repository",
}


class DesignError(ProfileError):
    """The profile has nothing this design can build questions from."""


def item_entry(item: dict) -> dict:
    """What Jev sees of an item: a readable source label, the title, and up to CONTENT_CHARS of text (key omitted when
    there is none). Popularity signals are deliberately absent: the ranker handles them, and keeping them out avoids
    rewarding "popular but unrelated"."""
    entry = {"source": SOURCE_KINDS.get(item["source"], item["source"]), "title": item["title"]}
    text = (item["content_raw"] or "").strip()[:CONTENT_CHARS]
    if text:
        entry["text"] = text
    return entry


# ---------- the shapes shared by every design ----------


@dataclass
class Derived:
    relevance: float
    importance: int
    category: str
    features: dict[str, float]
    flags: list[str] = field(default_factory=list)
    extra: dict = field(default_factory=dict)  # stored in enrichments.details beside the raw answers


@dataclass(frozen=True)
class Request:
    """One Jev request for one item: a slice of the profile (None = send none of it) and the questions asked of it."""

    name: str
    profile: dict | None
    questions: dict[str, dict]


@dataclass(frozen=True)
class Plan:
    version: str
    requests: tuple[Request, ...]
    structure: dict  # JSON-safe description of what the question ids mean; stored with the answers so they can be re-derived

    def state_for(self, request: Request, entry: dict) -> dict:
        state = {"reader_profile": request.profile} if request.profile is not None else {}
        state["item"] = entry
        return state

    def token_estimates(self) -> dict[str, int]:
        """Rough input tokens per request for an average item (about 100 tokens): a planning aid, not a measurement."""
        return {
            r.name: approx_tokens(json.dumps(r.profile or {}, ensure_ascii=False)) + approx_tokens(json.dumps(r.questions, ensure_ascii=False)) + 100
            for r in self.requests
        }


class Design(Protocol):
    version: str

    def plan(self, profile_state: dict) -> Plan: ...

    def derive(self, answers: dict[str, dict], structure: dict, facts: dict) -> Derived: ...


# ---------- helpers ----------


def _lists(section) -> dict[str, list[str]]:
    """The ordered lists of plain strings in a profile section, in file order (prose fields are ignored)."""
    if not isinstance(section, dict):
        return {}
    return {k: v for k, v in section.items() if isinstance(v, list) and v and all(isinstance(x, str) for x in v)}


def rank_weights(n: int) -> list[float]:
    """Neutral weights for n ORDERED groups: (n - rank) / n, so 1 for the first and 1/n for the last. The profile says
    which tier or list outranks which but gives no magnitudes; linear-by-rank is the assumption that invents none."""
    return [(n - j) / n for j in range(n)]


def _noul(instructions: str) -> dict:
    return {"type": "noul", "instructions": instructions}


def _path(*parts: str | int) -> str:
    out = ""
    for part in parts:
        out += f"[{part}]" if isinstance(part, int) else ("." if out else "") + part
    return out


# ---------- v2 ----------


class DesignV2:
    version = VERSION

    def plan(self, profile_state: dict) -> Plan:
        tiers = sorted((k for k, v in _lists(profile_state.get("priority_hierarchy")).items() if re.fullmatch(r"tier_\d+", k)), key=lambda k: int(k[5:]))
        tier_lists = _lists(profile_state.get("priority_hierarchy"))
        build = _lists(profile_state.get("building_interests"))
        signals = _lists(profile_state.get("high_value_information"))
        low = _lists(profile_state.get("low_value_information"))
        patterns, exceptions = low.get("usually_low_value", []), low.get("exceptions", [])
        wildcards = _lists(profile_state.get("discovery")).get("strong_wildcards", [])
        if not (tiers or build or wildcards):
            raise DesignError(
                "the interest profile has no priority tiers, building interests or wildcards, so Jev has nothing to judge relevance "
                "against. See config/interests.example.toml."
            )

        requests = [
            Request(
                "item",
                None,
                {
                    "category": {
                        "type": "choice",
                        "instructions": "Which area does the item mainly belong to?",
                        "criteria": {
                            "ai": "AI, machine learning, LLMs, agents, or tooling built for AI",
                            "software": "Programming, software engineering, systems, developer tools or open-source infrastructure",
                            "research": "Mathematics, physics, computer-science theory or other science that is not mainly about AI",
                            "other": "None of these: general news, business, politics, lifestyle",
                        },
                    },
                    # Worded narrowly on purpose (see the v1 note): the target is text aimed at a MODEL.
                    "injection": _noul(
                        "The item text contains instructions addressed to an AI assistant or language model, "
                        "such as a request to ignore its earlier instructions."
                    ),
                    # An evidence signal, not a relevance factor. Whether an item HAS text is a fact the code knows and does not ask.
                    "subject_unclear": _noul("The item does not make clear what its subject is."),
                },
            )
        ]

        interest_q: dict[str, dict] = {}
        interest_slice: dict = {}
        if tiers:
            for key in ("interest_areas", "technical_interests", "research_interests"):
                if key in profile_state:
                    interest_slice[key] = profile_state[key]
            interest_slice["priority_hierarchy"] = {t: tier_lists[t] for t in tiers}
            criteria = {t: f"The item's subject belongs to an area listed in `{_path('reader_profile', 'priority_hierarchy', t)}`." for t in tiers}
            criteria["none"] = "The item's subject is not an area in any priority tier, even if the item shares a word with one."
            interest_q["priority_tier"] = {
                "type": "choice",
                "instructions": "Which priority tier contains the area that the item is mainly about?",
                "criteria": criteria,
            }
        if build:
            interest_slice["building_interests"] = build
            for key, entries in build.items():
                for i in range(len(entries)):
                    interest_q[f"build.{key}.{i}"] = _noul(
                        f"The item is a tool, method or result that could be used directly in the project described by "
                        f"`{_path('reader_profile', 'building_interests', key, i)}`."
                    )
        if interest_q:
            requests.append(Request("interest", interest_slice, interest_q))

        value_q: dict[str, dict] = {}
        value_slice: dict = {}
        if signals:
            value_slice["high_value_information"] = signals
            for key, entries in signals.items():
                for i in range(len(entries)):
                    value_q[f"signal.{key}.{i}"] = _noul(f"The item shows the signal described in `{_path('reader_profile', 'high_value_information', key, i)}`.")
        if patterns or exceptions:
            value_slice["low_value_information"] = {k: v for k, v in (("usually_low_value", patterns), ("exceptions", exceptions)) if v}
            for i in range(len(patterns)):
                value_q[f"low.pattern.{i}"] = _noul(f"The item is an example of the pattern described in `{_path('reader_profile', 'low_value_information', 'usually_low_value', i)}`.")
            for i in range(len(exceptions)):
                value_q[f"low.exception.{i}"] = _noul(f"The exception described in `{_path('reader_profile', 'low_value_information', 'exceptions', i)}` applies to the item.")
        if value_q:
            requests.append(Request("value", value_slice, value_q))

        if wildcards:
            requests.append(
                Request(
                    "discovery",
                    {"discovery": {"strong_wildcards": wildcards}},
                    {f"wildcard.{i}": _noul(f"The item is an example of the wildcard described in `{_path('reader_profile', 'discovery', 'strong_wildcards', i)}`.") for i in range(len(wildcards))},
                )
            )

        structure = {
            "tiers": tiers,
            "build_lists": {k: len(v) for k, v in build.items()},
            "signal_lists": {k: len(v) for k, v in signals.items()},
            "n_patterns": len(patterns),
            "n_exceptions": len(exceptions),
            "n_wildcards": len(wildcards),
        }
        return Plan(VERSION, tuple(requests), structure)

    # ---------- answers -> relevance (pure; works from the stored form) ----------

    def derive(self, answers: dict[str, dict], structure: dict, facts: dict) -> Derived:
        noul = lambda qid: answers[qid]["noul"]  # noqa: E731

        def best_of_lists(prefix: str, lists: dict[str, int]) -> tuple[float | None, str | None]:
            """max over every entry of every list, each list weighted by its rank. (None, None) if there are no such questions."""
            best, by = None, None
            weights = rank_weights(len(lists))
            for weight, (key, n) in zip(weights, lists.items()):
                for i in range(n):
                    qid = f"{prefix}.{key}.{i}"
                    value = weight * noul(qid)
                    if best is None or value > best:
                        best, by = value, qid
            return best, by

        routes: dict[str, tuple[float, str]] = {}
        tiers = structure["tiers"]
        if tiers:
            probabilities = answers["priority_tier"]["probabilities"]
            expected = sum(probabilities.get(t, 0.0) * w for t, w in zip(tiers, rank_weights(len(tiers))))  # `none` carries weight 0
            routes["interest"] = (expected, "priority_tier")
        build, build_by = best_of_lists("build", structure["build_lists"])
        if build is not None:
            routes["build"] = (build, build_by)
        wild = [(noul(f"wildcard.{i}"), f"wildcard.{i}") for i in range(structure["n_wildcards"])]
        if wild:
            routes["discovery"] = max(wild, key=lambda pair: pair[0])
        reach, reach_by = max(routes.values(), key=lambda pair: pair[0])  # any ONE route to relevance suffices: the profile's "or"

        value, value_by = best_of_lists("signal", structure["signal_lists"])
        value_factor = 1.0 if value is None else VALUE_FLOOR + (1 - VALUE_FLOOR) * value  # no signals in the profile: no modulation

        patterns = [(noul(f"low.pattern.{i}"), f"low.pattern.{i}") for i in range(structure["n_patterns"])]
        excuse = max((noul(f"low.exception.{i}") for i in range(structure["n_exceptions"])), default=0.0)
        strongest, penalty_by = max(patterns, key=lambda pair: pair[0], default=(0.0, None))
        low_value = strongest * (1 - excuse)  # a pattern only counts if no exception excuses it

        relevance = reach * (1 - low_value) * value_factor
        flags: list[str] = []
        if noul("injection") >= INJECTION_ZERO_AT:
            flags.append("possible_injection")
            relevance = 0.0
        relevance = min(1.0, max(0.0, relevance))

        category_answer = answers["category"]
        category = max(category_answer["probabilities"], key=category_answer["probabilities"].get)
        if category == "other" and relevance >= OTHER_REMAP_RELEVANCE:
            category = max((c for c in category_answer["probabilities"] if c != "other"), key=category_answer["probabilities"].get)

        features = {name: route[0] for name, route in routes.items()}
        features.update({"reach": reach, "low_value": low_value, "injection": noul("injection"), "subject_unclear": noul("subject_unclear"), "category_confidence": category_answer["confidence"]})
        if value is not None:
            features["value"] = value
        if tiers:
            features["tier_confidence"] = answers["priority_tier"]["confidence"]
        extra = {
            "evidence": {"reach_by": reach_by if reach > 0 else None, "value_by": value_by, "penalty_by": penalty_by if low_value > 0 else None},
            "facts": dict(facts),  # recorded beside the score, never inside it: whether the item had text does not change relevance
        }
        return Derived(relevance, 1 + round(4 * relevance), category, features, flags, extra)


CURRENT_DESIGN = DesignV2()
