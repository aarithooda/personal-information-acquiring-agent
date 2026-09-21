"""The Jev client: typed questions in, validated typed answers out.

The response fixture is a REAL response from the Jev API (saved during development), so parsing is checked
against what the service actually returns, not just what the documentation says.
"""

import json
from pathlib import Path

import httpx
import pytest

from pia.jev.client import (
    ChoiceAnswer,
    JevClient,
    NoulAnswer,
    ScoreAnswer,
    choice,
    noul,
    score,
)
from pia.llm.client import LLMBadOutput, LLMUnavailable

REAL = json.loads((Path(__file__).parent / "fixtures" / "jev_response.json").read_text(encoding="utf-8"))

QUESTIONS = {
    "interest_match": score("How well does it match?", ["unrelated", "adjacent", "moderate", "direct"]),
    "substance": score("How much substance?", ["none", "some", "concrete", "significant"]),
    "low_value_pattern": noul("Matches a low-value pattern?"),
    "low_value_exception": noul("An exception applies?"),
    "wildcard": noul("Is it a wildcard?"),
    "buildable": noul("Could it be built on?"),
    "too_little_info": noul("Too little information?"),
    "category": choice("Which area?", {"ai": "AI", "software": "Software", "research": "Research", "other": "Other"}),
    "injection": noul("Does it instruct the reader?"),
}


def client_for(handler, **kwargs):
    seen: list[httpx.Request] = []

    def recording(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    http = httpx.Client(transport=httpx.MockTransport(recording))
    return JevClient("jev_TESTKEY", http, sleep=lambda s: None, **kwargs), seen


def ok(body=None):
    return lambda request: httpx.Response(200, json=body or REAL)


# ---------- building questions in the documented shape ----------


def test_question_builders_produce_the_documented_shapes():
    assert noul("Is it urgent?") == {"type": "noul", "instructions": "Is it urgent?"}
    assert noul("Q?", {"true": "yes case", "false": "no case"})["criteria"] == {"true": "yes case", "false": "no case"}
    assert choice("Which?", {"a": "A", "b": "B"}) == {"type": "choice", "instructions": "Which?", "criteria": {"a": "A", "b": "B"}}
    assert score("How?", ["low", "high"]) == {"type": "score", "instructions": "How?", "criteria": ["low", "high"]}


@pytest.mark.parametrize(
    "build",
    [
        lambda: noul("  "),
        lambda: score("How?", ["only one level"]),
        lambda: score("How?", [f"level {i}" for i in range(11)]),  # the API allows 2 to 10 levels
        lambda: choice("Which?", {"only": "one option"}),
        lambda: choice("Which?", {f"o{i}": "x" for i in range(256)}),  # the API allows up to 255 options
    ],
)
def test_impossible_questions_are_rejected_before_any_request_is_made(build):
    with pytest.raises(ValueError):
        build()


# ---------- the request ----------


def test_sends_state_model_and_questions_to_the_documented_endpoint_with_bearer_auth():
    client, seen = client_for(ok())
    state = {"reader_profile": {"a": 1}, "item": {"title": "T"}}
    client.system_one(state, QUESTIONS)
    request = seen[0]
    assert (request.method, str(request.url)) == ("POST", "https://api.typesafe.ai/v1/systemone")
    assert request.headers["authorization"] == "Bearer jev_TESTKEY"
    assert json.loads(request.content) == {"state": state, "model": "jev-latest", "questions": QUESTIONS}


def test_the_model_can_be_pinned():
    client, seen = client_for(ok(), model="jev-1.13.0")
    client.system_one({"item": {"title": "T"}}, QUESTIONS)
    assert json.loads(seen[0].content)["model"] == "jev-1.13.0"


def test_an_empty_question_set_is_refused_locally():
    client, seen = client_for(ok())
    with pytest.raises(ValueError):
        client.system_one({"item": {}}, {})
    assert seen == []


# ---------- parsing a REAL response ----------


def test_a_real_response_parses_into_typed_answers():
    client, _ = client_for(ok())
    result = client.system_one({"item": {"title": "T"}}, QUESTIONS)
    assert result.model == "jev-1.13.0"
    assert result.usage.input_tokens > 1000 and result.usage.output_tokens > 0
    match = result.score("interest_match")
    assert isinstance(match, ScoreAnswer) and 0 <= match.score <= 3 and 0 <= match.confidence <= 1
    assert abs(sum(match.probabilities.values()) - 1) < 0.05
    assert isinstance(result.noul("buildable"), NoulAnswer) and 0 <= result.noul("buildable").noul <= 1
    category = result.choice("category")
    assert isinstance(category, ChoiceAnswer) and category.choice in category.probabilities


def test_asking_for_an_answer_of_the_wrong_kind_is_a_programming_error():
    client, _ = client_for(ok())
    result = client.system_one({"item": {"title": "T"}}, QUESTIONS)
    with pytest.raises(TypeError):
        result.noul("interest_match")  # that one is a score


# ---------- bad or partial responses are BadOutput, never a crash ----------


def mutate(edit):
    body = json.loads(json.dumps(REAL))
    edit(body)
    return body


@pytest.mark.parametrize(
    "body",
    [
        mutate(lambda b: b["answers"].pop("substance")),  # a question was not answered
        mutate(lambda b: b["answers"].update(substance=b["answers"]["buildable"])),  # answered with the wrong type
        mutate(lambda b: b["answers"]["buildable"].update(noul=1.7)),  # a probability above 1
        mutate(lambda b: b["answers"]["buildable"].pop("noul")),
        mutate(lambda b: b.pop("usage")),
        {"unexpected": "shape"},
        {"model": "m", "answers": "not a map", "usage": {"input_tokens": 1, "output_tokens": 1}},
    ],
)
def test_malformed_responses_become_bad_output_errors(body):
    client, _ = client_for(lambda request: httpx.Response(200, json=body))
    with pytest.raises(LLMBadOutput):
        client.system_one({"item": {"title": "T"}}, QUESTIONS)


def test_a_non_json_body_is_bad_output():
    client, _ = client_for(lambda request: httpx.Response(200, text="<html>gateway</html>"))
    with pytest.raises(LLMBadOutput):
        client.system_one({"item": {"title": "T"}}, QUESTIONS)


# ---------- failures: who is at fault decides the error type ----------


def test_a_rejected_request_is_bad_output_because_this_input_may_be_the_cause():
    client, _ = client_for(lambda request: httpx.Response(422, json={"error": "validation"}))
    with pytest.raises(LLMBadOutput):
        client.system_one({"item": {"title": "T"}}, QUESTIONS)


@pytest.mark.parametrize("status", [401, 403, 500, 529])
def test_auth_and_server_failures_are_unavailable_and_never_leak_the_key(status):
    client, _ = client_for(lambda request: httpx.Response(status), )
    with pytest.raises(LLMUnavailable) as excinfo:
        client.system_one({"item": {"title": "T"}}, QUESTIONS)
    assert "jev_TESTKEY" not in str(excinfo.value)


def test_overload_and_rate_limits_are_retried_then_succeed():
    responses = [httpx.Response(529), httpx.Response(429, headers={"Retry-After": "1"}), httpx.Response(200, json=REAL)]
    client, seen = client_for(lambda request: responses.pop(0))
    assert client.system_one({"item": {"title": "T"}}, QUESTIONS).model == "jev-1.13.0"
    assert len(seen) == 3


def test_a_network_failure_is_unavailable():
    def down(request):
        raise httpx.ConnectError("boom")

    client, _ = client_for(down)
    with pytest.raises(LLMUnavailable):
        client.system_one({"item": {"title": "T"}}, QUESTIONS)


# ---------- listing models (used by `pia doctor --online`: no item data is sent) ----------


def test_list_models_reads_the_models_endpoint():
    body = {"models": [{"name": "jev-latest", "description": "d", "release_date": "2026-09-10"}]}
    client, seen = client_for(lambda request: httpx.Response(200, json=body))
    assert client.list_models() == ["jev-latest"]
    assert (seen[0].method, str(seen[0].url)) == ("GET", "https://api.typesafe.ai/v1/models")
    assert seen[0].headers["authorization"] == "Bearer jev_TESTKEY"


def test_list_models_reports_a_rejected_key():
    client, _ = client_for(lambda request: httpx.Response(401))
    with pytest.raises(LLMUnavailable):
        client.list_models()
