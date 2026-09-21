"""Stage 2: the editor. From a ranked shortlist, pick the few developments that truly matter
and explain them.

Why a second, stronger model: stage 1 scores each item on its own and proved generous.
Choosing among candidates side by side (listwise) is a judgment models do much better,
and the shortlist keeps the expensive model's input small.
"""

import logging
import sqlite3
from dataclasses import dataclass

from pydantic import BaseModel, ValidationError, field_validator

from pia.llm.client import LLM, LLMError
from pia.llm.prompts import (
    STAGE2_MODEL,
    STAGE2_SCHEMA,
    STAGE2_SCHEMA_NAME,
    build_stage2_user,
    stage2_system,
)

log = logging.getLogger(__name__)


@dataclass
class Headline:
    row: sqlite3.Row
    explanation: str
    why_it_matters: str | None


class _Pick(BaseModel):
    index: int
    explanation: str
    why_it_matters: str

    @field_validator("explanation")
    @classmethod
    def _explanation_required(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("blank explanation")
        return value


def select_headlines(
    llm: LLM,
    candidates: list[sqlite3.Row],
    max_count: int,
    *,
    model: str = STAGE2_MODEL,
    profile_text: str | None = None,
) -> list[Headline]:
    """Raises LLMError if the model call fails or yields nothing usable; callers fall back."""
    if not candidates:
        return []

    data = llm.complete_json(
        model=model,
        system=stage2_system(profile_text),
        user=build_stage2_user(candidates, max_count),
        schema_name=STAGE2_SCHEMA_NAME,
        schema=STAGE2_SCHEMA,
        max_tokens=8000,
        reasoning_effort="medium",
    )

    headlines: list[Headline] = []
    seen: set[int] = set()
    for raw in data.get("headlines", []):
        try:
            pick = _Pick.model_validate(raw)
        except ValidationError as exc:
            log.warning("dropping invalid editor pick: %s", exc.errors()[0]["msg"])
            continue
        if not 0 <= pick.index < len(candidates) or pick.index in seen:
            log.warning("dropping editor pick with unusable index %s", pick.index)
            continue
        seen.add(pick.index)
        headlines.append(Headline(candidates[pick.index], pick.explanation, pick.why_it_matters.strip() or None))
        if len(headlines) == max_count:
            break  # the budget is a hard ceiling even if the model over-delivers

    if not headlines:
        raise LLMError("editor returned no usable picks")
    return headlines
