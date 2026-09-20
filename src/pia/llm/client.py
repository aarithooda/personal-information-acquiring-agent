"""A thin client for Groq's OpenAI-compatible chat API.

An LLM call is just an authenticated HTTP POST, so this reuses our retry layer instead
of pulling in a vendor SDK. The rest of the app depends on the small `LLM` protocol, so
tests use a fake and switching providers later means writing one new class.

The contract: `complete_json` returns a parsed JSON object or raises LLMError. Callers
never see raw HTTP problems, truncated output or non-JSON text.
"""

import json
import time
from collections.abc import Callable
from typing import Protocol

import httpx

from pia.http import SourceError, request_with_retry

BASE_URL = "https://api.groq.com/openai/v1"


class LLMError(Exception):
    """The model call failed or returned something unusable. Safe to retry or skip."""


class LLM(Protocol):
    def complete_json(
        self,
        *,
        model: str,
        system: str,
        user: str,
        schema_name: str,
        schema: dict,
        max_tokens: int = 4000,
        reasoning_effort: str | None = None,
    ) -> dict: ...


class GroqClient:
    def __init__(
        self,
        api_key: str,
        http: httpx.Client,
        *,
        base_url: str = BASE_URL,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self._api_key = api_key
        self._http = http
        self._url = f"{base_url}/chat/completions"
        self._sleep = sleep

    def complete_json(
        self,
        *,
        model: str,
        system: str,
        user: str,
        schema_name: str,
        schema: dict,
        max_tokens: int = 4000,
        reasoning_effort: str | None = None,
    ) -> dict:
        body = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            # Strict mode = constrained decoding: the model can only emit tokens that fit
            # the schema, which is much stronger than asking nicely for JSON in the prompt.
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": schema_name, "strict": True, "schema": schema},
            },
            "max_completion_tokens": max_tokens,
            "temperature": 0,  # classification should be as repeatable as possible
        }
        if reasoning_effort:
            body["reasoning_effort"] = reasoning_effort

        try:
            response = request_with_retry(
                self._http,
                "POST",
                self._url,
                json=body,
                headers={"Authorization": f"Bearer {self._api_key}"},
                retries=4,
                max_wait=60,
                sleep=self._sleep,
            )
        except SourceError as exc:
            raise LLMError(f"Groq request failed: {exc}") from exc
        return self._extract_json(response)

    @staticmethod
    def _extract_json(response: httpx.Response) -> dict:
        try:
            choice = response.json()["choices"][0]
            content = choice["message"]["content"]
            finish_reason = choice.get("finish_reason")
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"unexpected response shape from Groq: {exc!r}") from exc

        if finish_reason == "length":
            raise LLMError("model output was cut off (hit the token limit)")
        if not content or not content.strip():
            raise LLMError("model returned empty content")
        try:
            data = json.loads(content)
        except json.JSONDecodeError as exc:
            raise LLMError(f"model returned invalid JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise LLMError("model returned JSON that is not an object")
        return data
