import httpx
import pytest

from pia.http import SourceError, get_with_retry


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


def test_retries_network_errors():
    script = Script(httpx.ConnectError("boom"), httpx.Response(200, text="ok"))
    resp = get_with_retry(make_client(script), "https://x.test", sleep=script.sleep)
    assert resp.text == "ok"
    assert script.calls == 2
