import json

import httpx
import pytest

from pia.llm.client import GroqClient, LLMError

SCHEMA = {
    "type": "object",
    "properties": {"answer": {"type": "string"}},
    "required": ["answer"],
    "additionalProperties": False,
}


def completion(content, finish_reason="stop") -> httpx.Response:
    body = {"choices": [{"message": {"content": content}, "finish_reason": finish_reason}]}
    return httpx.Response(200, json=body)


def client_for(handler, **kwargs) -> tuple[GroqClient, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def recording(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    http = httpx.Client(transport=httpx.MockTransport(recording))
    return GroqClient("gsk_TESTKEY", http, sleep=lambda s: None, **kwargs), seen


def call(client: GroqClient, **overrides):
    args = dict(model="m", system="be brief", user="hi", schema_name="answer", schema=SCHEMA)
    args.update(overrides)
    return client.complete_json(**args)


def test_sends_a_strict_json_schema_request_with_bearer_auth():
    client, seen = client_for(lambda r: completion('{"answer": "ok"}'))
    call(client, reasoning_effort="low", max_tokens=123)
    request = seen[0]
    body = json.loads(request.content)
    assert str(request.url) == "https://api.groq.com/openai/v1/chat/completions"
    assert request.headers["authorization"] == "Bearer gsk_TESTKEY"
    assert body["model"] == "m"
    assert body["messages"] == [
        {"role": "system", "content": "be brief"},
        {"role": "user", "content": "hi"},
    ]
    assert body["response_format"] == {
        "type": "json_schema",
        "json_schema": {"name": "answer", "strict": True, "schema": SCHEMA},
    }
    assert body["reasoning_effort"] == "low"
    assert body["max_completion_tokens"] == 123
    assert body["temperature"] == 0


def test_returns_the_parsed_json_object():
    client, _ = client_for(lambda r: completion('{"answer": "ok"}'))
    assert call(client) == {"answer": "ok"}


def test_omits_reasoning_effort_when_not_requested():
    client, seen = client_for(lambda r: completion("{}"))
    call(client)
    assert "reasoning_effort" not in json.loads(seen[0].content)


@pytest.mark.parametrize(
    "response",
    [
        completion('{"answer": "cut off', finish_reason="length"),
        completion("this is not json"),
        completion(""),
        completion(None),
        completion("[1, 2, 3]"),  # valid JSON but not an object
        httpx.Response(200, json={"unexpected": "shape"}),
        httpx.Response(200, text="<html>gateway error</html>"),
    ],
)
def test_bad_completions_become_llm_errors(response):
    client, _ = client_for(lambda r: response)
    with pytest.raises(LLMError):
        call(client)


def test_http_failures_become_llm_errors_and_never_leak_the_key():
    client, _ = client_for(lambda r: httpx.Response(401, json={"error": "invalid key"}))
    with pytest.raises(LLMError) as excinfo:
        call(client)
    assert "gsk_TESTKEY" not in str(excinfo.value)


def test_rate_limits_are_retried_then_succeed():
    responses = [httpx.Response(429, headers={"Retry-After": "1"}), completion('{"answer": "ok"}')]
    client, seen = client_for(lambda r: responses.pop(0))
    assert call(client) == {"answer": "ok"}
    assert len(seen) == 2
