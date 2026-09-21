"""A thin client for the Jev HTTP API, in the exact structure the documentation specifies.

    POST https://api.typesafe.ai/v1/systemone      Authorization: Bearer <key>
    request : {"state": <string|object|array>, "model": "...", "questions": {<id>: <question>}}
    question: {"type": "noul"|"choice"|"score", "instructions": ..., "criteria": ...}
    response: {"model": "jev-1.13.0", "answers": {<id>: <answer>}, "usage": {"input_tokens": n, "output_tokens": n}}

Nothing crosses this boundary as free text. Questions are built by `noul()`, `choice()` and `score()`, which
enforce the documented limits locally (a Score has 2-10 levels, a Choice at most 255 options), and every response
is validated into typed models. The point is the same as our strict-JSON rule for the LLM stage: downstream code
only ever sees values it can trust the SHAPE of. (Shape, not correctness: a well-formed answer can still be wrong.)

Like the Groq client this is plain httpx over our shared retry layer rather than the vendor SDK: the whole API is
one JSON endpoint, so the SDK would add a dependency without adding capability.

Errors reuse the LLM taxonomy so the triage code can treat both providers the same way:
  LLMUnavailable  no answer (network, 401/403, 429/5xx after retries): never the item's fault
  LLMBadOutput    the request was rejected (400/422) or the answer is unusable: possibly the item's fault
"""

import time
from collections.abc import Callable, Mapping
from typing import Annotated, Any, Literal, Union

import httpx
from pydantic import BaseModel, Field, ValidationError

from pia.http import SourceError, request_with_retry
from pia.llm.client import LLMBadOutput, LLMUnavailable

BASE_URL = "https://api.typesafe.ai"
DEFAULT_MODEL = "jev-latest"  # an alias that moves with releases; pin e.g. "jev-1.13.0" for reproducibility
MAX_SCORE_LEVELS = 10
MAX_CHOICE_OPTIONS = 255


# ---------- questions (request side) ----------


def _instructions(text: str) -> str:
    if not text or not text.strip():
        raise ValueError("a question needs instructions")
    return text


def noul(instructions: str, criteria: Mapping[str, Any] | None = None) -> dict:
    """A yes/no question. The answer is P(yes) in [0, 1]; it carries no separate confidence."""
    question: dict = {"type": "noul", "instructions": _instructions(instructions)}
    if criteria:
        question["criteria"] = dict(criteria)  # {"true": "...", "false": "..."}
    return question


def choice(instructions: str, criteria: Mapping[str, Any]) -> dict:
    """Pick one of N options. The answer has the winner, per-option probabilities and a confidence."""
    if not 2 <= len(criteria) <= MAX_CHOICE_OPTIONS:
        raise ValueError(f"a choice needs 2 to {MAX_CHOICE_OPTIONS} options, got {len(criteria)}")
    return {"type": "choice", "instructions": _instructions(instructions), "criteria": dict(criteria)}


def score(instructions: str, criteria: list) -> dict:
    """An ordered scale: `criteria` lists the levels from lowest to highest (2 to 10). Describe situations,
    not degrees. The answer is the probability-weighted level plus probabilities and a confidence."""
    if not 2 <= len(criteria) <= MAX_SCORE_LEVELS:
        raise ValueError(f"a score needs 2 to {MAX_SCORE_LEVELS} levels, got {len(criteria)}")
    return {"type": "score", "instructions": _instructions(instructions), "criteria": list(criteria)}


# ---------- answers (response side) ----------


class NoulAnswer(BaseModel):
    type: Literal["noul"]
    noul: float = Field(ge=0, le=1)


class ScoreAnswer(BaseModel):
    type: Literal["score"]
    score: float = Field(ge=0)
    probabilities: dict[str, float]
    confidence: float = Field(ge=0, le=1)
    legend: dict[str, str] = {}


class ChoiceAnswer(BaseModel):
    type: Literal["choice"]
    choice: str
    probabilities: dict[str, float]
    confidence: float = Field(ge=0, le=1)


Answer = Annotated[Union[NoulAnswer, ScoreAnswer, ChoiceAnswer], Field(discriminator="type")]


class Usage(BaseModel):
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)


class SystemOneResponse(BaseModel):
    model: str  # the VERSIONED id that answered, e.g. "jev-1.13.0", even when we asked for "jev-latest"
    answers: dict[str, Answer]
    usage: Usage

    def _typed(self, name: str, kind: type):
        answer = self.answers[name]
        if not isinstance(answer, kind):
            raise TypeError(f"answer {name!r} is a {answer.type}, not a {kind.__name__}")
        return answer

    def noul(self, name: str) -> NoulAnswer:
        return self._typed(name, NoulAnswer)

    def score(self, name: str) -> ScoreAnswer:
        return self._typed(name, ScoreAnswer)

    def choice(self, name: str) -> ChoiceAnswer:
        return self._typed(name, ChoiceAnswer)


# ---------- the client ----------


class JevClient:
    def __init__(
        self,
        api_key: str,
        http: httpx.Client,
        *,
        base_url: str = BASE_URL,
        model: str = DEFAULT_MODEL,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self._api_key = api_key
        self._http = http
        self._base = base_url.rstrip("/")
        self._model = model
        self._sleep = sleep

    def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        try:
            return request_with_retry(
                self._http,
                method,
                f"{self._base}{path}",
                headers={"Authorization": f"Bearer {self._api_key}"},
                retries=3,
                max_wait=30,
                sleep=self._sleep,
                **kwargs,
            )
        except SourceError as exc:
            # 400/422 mean "this request was refused", which may be caused by this item; everything else means
            # "no answer". The message holds the URL and status only, never headers, so the key cannot leak.
            if exc.status in (400, 422):
                raise LLMBadOutput(f"Jev rejected the request: {exc}") from exc
            raise LLMUnavailable(f"Jev request failed: {exc}") from exc

    def system_one(self, state: Any, questions: Mapping[str, dict]) -> SystemOneResponse:
        if not questions:
            raise ValueError("at least one question is required")
        response = self._request(
            "POST", "/v1/systemone", json={"state": state, "model": self._model, "questions": dict(questions)}
        )
        try:
            parsed = SystemOneResponse.model_validate(response.json())
        except (ValueError, ValidationError) as exc:  # not JSON, or not the documented shape
            raise LLMBadOutput(f"unexpected response from Jev: {str(exc)[:200]}") from exc

        for name, question in questions.items():
            answer = parsed.answers.get(name)
            if answer is None:
                raise LLMBadOutput(f"Jev did not answer question {name!r}")
            if answer.type != question["type"]:
                raise LLMBadOutput(f"question {name!r} is a {question['type']} but the answer is a {answer.type}")
        return parsed

    def list_models(self) -> list[str]:
        """The model names this key can use. Sends no item data, so it is a safe connectivity and key check."""
        response = self._request("GET", "/v1/models")
        try:
            return [entry["name"] for entry in response.json()["models"]]
        except (ValueError, KeyError, TypeError) as exc:
            raise LLMBadOutput(f"unexpected response from Jev: {exc!r}") from exc
