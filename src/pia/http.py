"""The one place that talks to the network, and the one place that retries.

Retry only what can plausibly succeed later: rate limits (429), server errors
(5xx) and network failures. A 404 or 400 will fail the same way forever.
"""

import logging
import time
from collections.abc import Callable

import httpx

log = logging.getLogger(__name__)

USER_AGENT = "pia-personal-agent/0.1 (personal learning project)"


class SourceError(Exception):
    """A source could not be fetched. Callers treat this as 'skip this source'."""


def _retry_delay(response: httpx.Response | None, attempt: int, max_wait: float) -> float:
    """Server-directed wait if given (Retry-After seconds), else 1s, 2s, 4s..."""
    if response is not None:
        header = response.headers.get("Retry-After", "")
        if header.isdigit():
            return min(float(header), max_wait)
    return min(2.0**attempt, max_wait)


def get_with_retry(client: httpx.Client, url: str, *, params: dict | None = None, **options) -> httpx.Response:
    return request_with_retry(client, "GET", url, params=params, **options)


def request_with_retry(
    client: httpx.Client,
    method: str,
    url: str,
    *,
    retries: int = 3,
    max_wait: float = 30,
    sleep: Callable[[float], None] = time.sleep,
    **request_kwargs,  # params / json / headers, passed straight to httpx
) -> httpx.Response:
    """Send a request, retrying only failures that can plausibly succeed later.

    Error messages contain the URL and status only, never headers, so an API key in an
    Authorization header cannot leak into logs or tracebacks.
    """
    last_problem = "no attempt made"
    for attempt in range(retries + 1):
        response = None
        try:
            response = client.request(method, url, **request_kwargs)
        except httpx.TransportError as exc:  # timeouts, DNS, connection resets
            last_problem = f"network error: {exc}"
        else:
            if response.status_code < 400:
                return response
            last_problem = f"HTTP {response.status_code}"
            retryable = response.status_code == 429 or response.status_code >= 500
            if not retryable:
                raise SourceError(f"{url}: {last_problem}")

        if attempt < retries:
            delay = _retry_delay(response, attempt, max_wait)
            log.warning("%s: %s, retrying in %.0fs", url, last_problem, delay)
            sleep(delay)
    raise SourceError(f"{url}: {last_problem} (gave up after {retries + 1} attempts)")
