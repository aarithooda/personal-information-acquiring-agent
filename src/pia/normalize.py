"""Deterministic normalization. Deduplication depends on this being exact.

The canonical URL is the identity of an item: two sightings with the same
canonical URL are the same item, no matter which source reported them.
"""

import re
from urllib.parse import parse_qsl, urlencode, urlsplit

_TRACKING_PARAMS = {"fbclid", "gclid", "mc_cid", "mc_eid", "igshid", "ref_src", "ref"}
_ARXIV_PATH = re.compile(r"/(?:abs|pdf)/(.+)")


def _is_tracking(name: str) -> bool:
    return name.lower().startswith("utm_") or name.lower() in _TRACKING_PARAMS


def canonicalize_url(url: str) -> str:
    """Return a stable form of `url`, or raise ValueError if it is unusable."""
    parts = urlsplit((url or "").strip())
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError(f"unusable url: {url!r}")

    host = parts.hostname.lower().removeprefix("www.")
    try:
        port = parts.port
    except ValueError as exc:
        raise ValueError(f"unusable url: {url!r}") from exc
    netloc = host if port in (None, 80, 443) else f"{host}:{port}"

    if host in ("arxiv.org", "export.arxiv.org"):
        match = _ARXIV_PATH.fullmatch(parts.path)
        if match:
            paper_id = re.sub(r"\.pdf$", "", match.group(1))
            paper_id = re.sub(r"v\d+$", "", paper_id)
            return f"https://arxiv.org/abs/{paper_id}"

    query = sorted(
        (k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not _is_tracking(k)
    )
    path = parts.path.rstrip("/")
    canonical = f"https://{netloc}{path}"
    return f"{canonical}?{urlencode(query)}" if query else canonical
