"""A fake Jev that answers WHATEVER questions it is asked, so tests do not depend on one question set.

`SyntheticJev` inspects each request: a Noul gets a base probability (overridable per question id or by id prefix), a
Choice gets a distribution (overridable), a Score a level. It records every request (state and questions), which lets
tests check what was sent, to whom, and how often.
"""

import threading

from pia.jev.client import SystemOneResponse


# A profile the current design can build questions from (tiers, signals, low-value patterns, exceptions, wildcards).
V2_PROFILE_TOML = """
[core_interest_areas]
[[core_interest_areas.area]]
name = "AI agents"
priority = "very_high"
specific_subtopics = ["tool use"]
[priority_hierarchy]
tier_1 = ["AI engineering"]
tier_2 = ["Research"]
tier_3 = ["Broader technology"]
[high_value_information]
strongest_signals = ["Genuinely new technical capability"]
[low_value_information]
usually_low_value = ["Routine announcements"]
exceptions = ["A routine release that changes architecture"]
[discovery]
strong_wildcards = ["New computing paradigms"]
"""


def _noul(p):
    return {"type": "noul", "noul": p}


def _choice(options, winner, confidence=0.9):
    probs = {o: 0.0 for o in options}
    probs[winner] = 1.0
    return {"type": "choice", "choice": winner, "probabilities": probs, "confidence": confidence}


def _score(levels, level, confidence=0.9):
    probs = {str(i): 0.0 for i in range(levels)}
    probs[str(level)] = 1.0
    return {"type": "score", "score": float(level), "probabilities": probs, "confidence": confidence, "legend": {}}


class SyntheticJev:
    """noul:     {question id or id-prefix: probability}; the LONGEST matching prefix wins; default `base`
    choice:   {question id: {option: probability}} (winner = the largest); default: all mass on the first option
    fail:     callable(request_number) -> exception to raise, or None
    script:   callable(item title) -> {"noul": {...}, "choice": {...}} overrides for that item
    calls:    every REQUEST received (an item needs several under question set v2)
    """

    def __init__(self, noul=None, choice=None, base=0.1, fail=None, model="jev-1.13.0", input_tokens=1000, script=None):
        self.noul, self.choice, self.base, self.fail, self.script = noul or {}, choice or {}, base, fail, script
        self.model, self.input_tokens = model, input_tokens
        self.calls: list[dict] = []  # one entry per REQUEST (an item takes several): {"state": ..., "questions": ...}
        self._lock = threading.Lock()

    def _p(self, qid, noul):
        matches = [k for k in noul if qid == k or qid.startswith(k)]
        return noul[max(matches, key=len)] if matches else self.base

    def system_one(self, state, questions):
        with self._lock:
            self.calls.append({"state": state, "questions": questions})
            n = len(self.calls)
        if self.fail:
            exc = self.fail(n)
            if exc is not None:
                raise exc
        # Per-item behaviour: script(title) -> {"noul": {...}, "choice": {...}}, layered over the defaults.
        extra = self.script(state["item"]["title"]) if self.script else {}
        noul, choice = {**self.noul, **extra.get("noul", {})}, {**self.choice, **extra.get("choice", {})}
        answers = {}
        for qid, q in questions.items():
            if q["type"] == "noul":
                answers[qid] = _noul(self._p(qid, noul))
            elif q["type"] == "choice":
                options = list(q["criteria"])
                dist = choice.get(qid)
                if dist:
                    winner = max(dist, key=dist.get)
                    answers[qid] = {"type": "choice", "choice": winner, "probabilities": {o: dist.get(o, 0.0) for o in options}, "confidence": 0.8}
                else:
                    answers[qid] = _choice(options, options[0])
            else:
                answers[qid] = _score(len(q["criteria"]), 0)
        return SystemOneResponse.model_validate(
            {"model": self.model, "answers": answers, "usage": {"input_tokens": self.input_tokens, "output_tokens": 10}}
        )
