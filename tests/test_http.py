import httpx
import pytest

from pia.http import SourceError, get_with_retry, request_with_retry


def make_client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


class Script:
    """Replays a list of responses/exceptions and records calls and sleeps."""

    def __init__(self, *steps):
        self.steps = list(steps)
        self.calls = 0
        self.sleeps: list[float] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        step = self.steps.pop(0) if len(self.steps) > 1 else self.steps[0]
        if isinstance(step, Exception):
            raise step
        return step

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)


def test_returns_response_on_success():
    script = Script(httpx.Response(200, text="ok"))
    resp = get_with_retry(make_client(script), "https://x.test", sleep=script.sleep)
    assert resp.text == "ok"
    assert script.calls == 1


def test_retries_server_errors_with_exponential_backoff():
    script = Script(httpx.Response(503), httpx.Response(502), httpx.Response(200, text="ok"))
    resp = get_with_retry(make_client(script), "https://x.test", sleep=script.sleep)
    assert resp.text == "ok"
    assert script.sleeps == [1, 2]


def test_honors_retry_after_on_rate_limit():
    script = Script(httpx.Response(429, headers={"Retry-After": "7"}), httpx.Response(200))
    get_with_retry(make_client(script), "https://x.test", sleep=script.sleep)
    assert script.sleeps == [7]


def test_caps_a_huge_retry_after():
    script = Script(httpx.Response(429, headers={"Retry-After": "3600"}), httpx.Response(200))
    get_with_retry(make_client(script), "https://x.test", sleep=script.sleep, max_wait=30)
    assert script.sleeps == [30]


def test_client_errors_are_not_retried():
    script = Script(httpx.Response(404))
    with pytest.raises(SourceError, match="404"):
        get_with_retry(make_client(script), "https://x.test", sleep=script.sleep)
    assert script.calls == 1
    assert script.sleeps == []


def test_gives_up_after_retries_and_raises_source_error():
    script = Script(httpx.Response(500))
    with pytest.raises(SourceError):
        get_with_retry(make_client(script), "https://x.test", retries=2, sleep=script.sleep)
    assert script.calls == 3  # first attempt + 2 retries


def test_post_resends_the_same_json_body_and_headers_on_retry():
    bodies, auth = [], []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(request.content)
        auth.append(request.headers.get("authorization"))
        return httpx.Response(429 if len(bodies) == 1 else 200, headers={"Retry-After": "2"}, text="ok")

    resp = request_with_retry(
        make_client(handler),
        "POST",
        "https://x.test/chat",
        json={"model": "m"},
        headers={"Authorization": "Bearer secret"},
        sleep=lambda s: None,
    )
    assert resp.status_code == 200
    assert bodies[0] == bodies[1] == b'{"model":"m"}'
    assert auth == ["Bearer secret", "Bearer secret"]


def test_error_messages_never_contain_request_headers():
    script = Script(httpx.Response(401))
    with pytest.raises(SourceError) as excinfo:
        request_with_retry(
            make_client(script), "POST", "https://x.test/chat", headers={"Authorization": "Bearer TOPSECRET"}
        )
    assert "TOPSECRET" not in str(excinfo.value)


def test_retries_network_errors():
    script = Script(httpx.ConnectError("boom"), httpx.Response(200, text="ok"))
    resp = get_with_retry(make_client(script), "https://x.test", sleep=script.sleep)
    assert resp.text == "ok"
    assert script.calls == 2


def test_source_errors_carry_the_http_status_so_callers_can_tell_why_a_request_failed():
    from pia.http import SourceError as Err

    with pytest.raises(Err) as rejected:
        get_with_retry(make_client(Script(httpx.Response(422))), "https://x.test", sleep=lambda s: None)
    assert rejected.value.status == 422
    with pytest.raises(Err) as exhausted:
        get_with_retry(make_client(Script(httpx.Response(529))), "https://x.test", retries=1, sleep=lambda s: None)
    assert exhausted.value.status == 529
    with pytest.raises(Err) as network:
        get_with_retry(make_client(Script(httpx.ConnectError("boom"))), "https://x.test", retries=0, sleep=lambda s: None)
    assert network.value.status is None  # no response at all
