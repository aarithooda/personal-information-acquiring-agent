"""The one way PIA fetches a URL it did not choose itself (reader, feed discovery, page watching).

URLs in feeds and pages are untrusted, so fetching them is a server-side request forgery surface. Rules:
  * http and https only;
  * the host must resolve ONLY to public addresses (no loopback, private, link-local, multicast or reserved
    ranges, which also covers cloud metadata endpoints);
  * redirects are followed by hand, at most MAX_REDIRECTS, and every hop is re-checked;
  * the body is streamed and cut off at `max_bytes`; only text-like content types are read;
  * no cookies, no credentials, a plain identifying User-Agent.

Known gap, accepted: the address is checked before the request and the HTTP client resolves again, so a host
that changes its DNS answer between the two (rebinding) could slip through. PIA holds no credentials for
internal services and binds to loopback or sits behind a login, which bounds what such a response could reach.
"""

import ipaddress
import socket
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import httpx

from pia.http import USER_AGENT

MAX_REDIRECTS = 5
TEXT_TYPES = ("text/", "application/xhtml", "application/xml", "application/rss", "application/atom", "application/json", "application/feed")

Resolver = Callable[[str], list[str]]


class FetchRefused(Exception):
    """The URL is not one PIA is willing to fetch (scheme, private address, robots.txt, content type)."""


class FetchFailed(Exception):
    """The fetch was allowed but did not succeed (network error, HTTP error, too many redirects)."""


@dataclass
class Fetched:
    url: str  # the final URL after redirects
    status: int
    content_type: str
    text: str
    truncated: bool = False


def system_resolver(host: str) -> list[str]:
    return sorted({info[4][0] for info in socket.getaddrinfo(host, None)})


def is_public_address(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address.split("%")[0])
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


def check_url(url: str, resolver: Resolver | None = None) -> None:
    resolver = resolver or system_resolver
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise FetchRefused(f"not an http(s) URL: {url[:120]}")
    if parts.username or parts.password:
        raise FetchRefused("URLs with credentials are not fetched")
    try:
        addresses = resolver(parts.hostname)
    except OSError as exc:
        raise FetchFailed(f"cannot resolve {parts.hostname}: {exc}") from exc
    if not addresses or not all(is_public_address(address) for address in addresses):
        raise FetchRefused(f"{parts.hostname} does not resolve to a public address")


def _read_capped(response: httpx.Response, max_bytes: int) -> tuple[bytes, bool]:
    chunks, size = [], 0
    for chunk in response.iter_bytes():
        chunks.append(chunk)
        size += len(chunk)
        if size >= max_bytes:
            return b"".join(chunks)[:max_bytes], True
    return b"".join(chunks), False


def guarded_get(
    client: httpx.Client,
    url: str,
    *,
    max_bytes: int = 2_000_000,
    timeout: float = 20,
    resolver: Resolver | None = None,
    robots: "RobotsCache | None" = None,
) -> Fetched:
    current = url
    for _ in range(MAX_REDIRECTS + 1):
        check_url(current, resolver)
        if robots is not None and not robots.allowed(client, current, resolver):
            raise FetchRefused(f"robots.txt disallows {current[:120]}")
        try:
            with client.stream(
                "GET", current, headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.5"},
                timeout=timeout, follow_redirects=False, cookies=None,
            ) as response:
                if response.status_code in (301, 302, 303, 307, 308) and response.headers.get("location"):
                    current = urljoin(current, response.headers["location"])
                    continue
                if response.status_code >= 400:
                    raise FetchFailed(f"HTTP {response.status_code} for {current[:120]}")
                content_type = response.headers.get("content-type", "").lower()
                if content_type and not content_type.startswith(TEXT_TYPES):
                    raise FetchRefused(f"not a text document ({content_type.split(';')[0]})")
                body, truncated = _read_capped(response, max_bytes)
                encoding = response.encoding or "utf-8"
        except httpx.HTTPError as exc:
            raise FetchFailed(f"network error for {current[:120]}: {exc.__class__.__name__}") from exc
        try:
            text = body.decode(encoding, errors="replace")
        except LookupError:
            text = body.decode("utf-8", errors="replace")
        return Fetched(current, response.status_code, content_type, text, truncated)
    raise FetchFailed(f"too many redirects from {url[:120]}")


class RobotsCache:
    """robots.txt per host, fetched once per process through the same guard. A missing or unreadable robots.txt
    means "allowed", which is the convention."""

    def __init__(self) -> None:
        self._parsers: dict[str, RobotFileParser | None] = {}

    def allowed(self, client: httpx.Client, url: str, resolver: Resolver | None = None) -> bool:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self._parsers:
            parser: RobotFileParser | None = None
            try:
                fetched = guarded_get(client, f"{origin}/robots.txt", max_bytes=200_000, timeout=10, resolver=resolver)
                parser = RobotFileParser()
                parser.parse(fetched.text.splitlines())
            except (FetchRefused, FetchFailed):
                parser = None
            self._parsers[origin] = parser
        parser = self._parsers[origin]
        return True if parser is None else parser.can_fetch(USER_AGENT, url)
