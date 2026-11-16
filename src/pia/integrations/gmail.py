"""Read-only Gmail, for university announcements and opportunities that only arrive by mail.

OFF until the owner authorises it. Nothing here runs unless `pia gmail-connect` has been completed by the owner.

Design (docs/platform/GMAIL.md has the setup steps and the reasoning):
  * Scope: `https://www.googleapis.com/auth/gmail.readonly` and nothing else. With that scope Google itself
    refuses any attempt to send, modify, label or delete, so "read-only" does not depend on this code being right.
  * Flow: OAuth 2.0 for an installed application with PKCE and a loopback redirect. The owner creates their own
    OAuth client (a "Desktop app") in Google Cloud; PIA holds no shared client secret.
  * Storage: the refresh token lives in data/secrets/gmail.json (git-ignored with the rest of data/). It never
    enters the database, the logs or the API.
  * Selectivity: PIA does not read the whole mailbox. It asks Gmail for recent messages matching a query built from
    the owner's sender list and keywords, and keeps only those. Bodies are truncated before storage.
  * Plain httpx over the shared retry layer, like every other integration: no Google SDK.
"""

import base64
import hashlib
import json
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.utils import parseaddr, parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlencode

import httpx

from pia.acquire.textextract import extract
from pia.config import PROJECT_ROOT
from pia.http import SourceError, request_with_retry
from pia.models import RawItem
from pia.sources.base import clean_text, make_item

SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
API = "https://gmail.googleapis.com/gmail/v1/users/me"
TOKEN_FILE = PROJECT_ROOT / "data" / "secrets" / "gmail.json"
MAX_BODY_CHARS = 6000
DEFAULT_KEYWORDS = ("internship", "fellowship", "scholarship", "hackathon", "competition", "workshop", "summer school", "applications",
                    "registration", "deadline", "opportunity", "research project", "call for", "notice", "seminar", "talk")


class GmailNotConnected(SourceError):
    """No stored authorisation. The owner has not connected Gmail (or has disconnected it)."""


# ---------- OAuth ----------


def new_pkce() -> tuple[str, str]:
    """(verifier, challenge) for PKCE (RFC 7636, S256)."""
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def authorisation_url(client_id: str, redirect_uri: str, state: str, challenge: str) -> str:
    return AUTH_URL + "?" + urlencode({
        "client_id": client_id, "redirect_uri": redirect_uri, "response_type": "code", "scope": SCOPE, "state": state,
        "code_challenge": challenge, "code_challenge_method": "S256", "access_type": "offline", "prompt": "consent",
    })


def _token_request(client: httpx.Client, data: dict) -> dict:
    try:
        response = request_with_retry(client, "POST", TOKEN_URL, data=data, retries=2)
    except SourceError as exc:
        raise SourceError(f"Google refused the authorisation request (HTTP {exc.status})", status=exc.status) from exc
    payload = response.json()
    if "access_token" not in payload:
        raise SourceError("Google returned no access token")
    return payload


def exchange_code(client: httpx.Client, *, client_id: str, client_secret: str, code: str, verifier: str, redirect_uri: str, now: datetime) -> dict:
    payload = _token_request(client, {"client_id": client_id, "client_secret": client_secret, "code": code, "code_verifier": verifier,
                                      "grant_type": "authorization_code", "redirect_uri": redirect_uri})
    if SCOPE not in payload.get("scope", SCOPE).split():
        raise SourceError("Google did not grant the read-only Gmail permission")
    if not payload.get("refresh_token"):
        raise SourceError("Google returned no refresh token; remove PIA's access in your Google account and connect again")
    return {"client_id": client_id, "client_secret": client_secret, "refresh_token": payload["refresh_token"], "access_token": payload["access_token"],
            "expires_at": (now + timedelta(seconds=int(payload.get("expires_in", 3600)) - 60)).isoformat(), "scope": SCOPE, "connected_at": now.isoformat()}


def save_token(token: dict, path: Path = TOKEN_FILE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(token, indent=2), encoding="utf-8")
    try:
        path.chmod(0o600)  # effective on POSIX hosts; on Windows the user profile's ACLs apply
    except OSError:
        pass


def load_token(path: Path = TOKEN_FILE) -> dict:
    if not path.is_file():
        raise GmailNotConnected("Gmail is not connected. Run `pia gmail-connect` to authorise read-only access.")
    return json.loads(path.read_text(encoding="utf-8"))


def disconnect(client: httpx.Client | None = None, path: Path = TOKEN_FILE) -> bool:
    """Forget the authorisation locally and ask Google to revoke it. Returns True if a token was stored."""
    if not path.is_file():
        return False
    token = json.loads(path.read_text(encoding="utf-8"))
    path.unlink()
    if client is not None:
        try:
            client.post("https://oauth2.googleapis.com/revoke", data={"token": token.get("refresh_token", "")}, timeout=15)
        except httpx.HTTPError:
            pass  # the local token is gone either way; access can also be removed in the Google account
    return True


def access_token(client: httpx.Client, now: datetime, path: Path = TOKEN_FILE) -> str:
    token = load_token(path)
    if token.get("access_token") and token.get("expires_at", "") > now.isoformat():
        return token["access_token"]
    try:
        payload = _token_request(client, {"client_id": token["client_id"], "client_secret": token["client_secret"],
                                          "refresh_token": token["refresh_token"], "grant_type": "refresh_token"})
    except SourceError as exc:
        if exc.status in (400, 401):
            raise GmailNotConnected("Gmail's authorisation has expired or was revoked. Run `pia gmail-connect` again.") from exc
        raise
    token.update(access_token=payload["access_token"], expires_at=(now + timedelta(seconds=int(payload.get("expires_in", 3600)) - 60)).isoformat())
    save_token(token, path)
    return token["access_token"]


# ---------- reading ----------


def build_query(since: datetime, senders: list[str], keywords: list[str], extra: str = "") -> str:
    """A Gmail search: recent mail from the listed senders, OR recent mail mentioning a keyword."""
    parts = [f"after:{int(since.timestamp())}", "-in:chats", "-in:spam", "-in:trash"]
    clauses = []
    if senders:
        clauses.append("from:(" + " OR ".join(sender.strip() for sender in senders if sender.strip()) + ")")
    if keywords:
        clauses.append("(" + " OR ".join(f'"{word}"' if " " in word else word for word in keywords) + ")")
    if clauses:
        parts.append("{" + " ".join(clauses) + "}")  # Gmail's braces mean OR
    if extra:
        parts.append(extra)
    return " ".join(parts)


def _decode(data: str) -> str:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", errors="replace")


def body_text(payload: dict) -> str:
    """The message's readable text: text/plain if present, otherwise text extracted from text/html."""
    plain, rich = [], []

    def walk(part: dict) -> None:
        mime, data = part.get("mimeType", ""), (part.get("body") or {}).get("data")
        if data and not part.get("filename"):
            if mime == "text/plain":
                plain.append(_decode(data))
            elif mime == "text/html":
                rich.append(_decode(data))
        for child in part.get("parts") or []:
            walk(child)

    walk(payload)
    if plain:
        return clean_text(" ".join(plain))
    if rich:
        page = extract(" ".join(rich))
        return clean_text(page.text) or clean_text(" ".join(text for _, text in page.links))
    return ""


def message_to_item(message: dict, source: str) -> RawItem | None:
    headers = {header["name"].lower(): header["value"] for header in (message.get("payload") or {}).get("headers", [])}
    sender_name, sender_address = parseaddr(headers.get("from", ""))
    try:
        sent = parsedate_to_datetime(headers["date"]).astimezone(timezone.utc)
    except (KeyError, TypeError, ValueError):
        millis = message.get("internalDate")
        sent = datetime.fromtimestamp(int(millis) / 1000, tz=timezone.utc) if millis else None
    text = body_text(message.get("payload") or {}) or clean_text(message.get("snippet", ""))
    return make_item(
        source=source, external_id=message.get("id"),
        # An address that opens the message in the owner's own Gmail; it grants nothing. PIA identifies items by their
        # canonical URL, which drops the #fragment, so the message id must be in the query or every message would
        # collapse into one item. Gmail ignores the extra parameter and still opens the thread named in the fragment.
        url=f"https://mail.google.com/mail/u/0/?pia_message={message.get('id')}#all/{message.get('threadId') or message.get('id')}",
        title=clean_text(headers.get("subject", "")) or "(no subject)", content=text[:MAX_BODY_CHARS] or None, published_at=sent,
        author=sender_name or sender_address or None, kind="announcement", signals={"from": sender_address, "mail": True},
    )


@dataclass
class GmailSource:
    name: str
    senders: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=lambda: list(DEFAULT_KEYWORDS))
    query: str = ""
    max_messages: int = 40
    state: dict = field(default_factory=dict)
    token_file: str = ""

    def fetch(self, client: httpx.Client, since: datetime, until: datetime) -> list[RawItem]:
        path = Path(self.token_file) if self.token_file else TOKEN_FILE
        headers = {"Authorization": f"Bearer {access_token(client, until, path)}"}
        listing = request_with_retry(client, "GET", f"{API}/messages", headers=headers,
                                     params={"q": build_query(since, self.senders, self.keywords, self.query), "maxResults": self.max_messages}).json()
        items = []
        for reference in listing.get("messages", [])[: self.max_messages]:
            message = request_with_retry(client, "GET", f"{API}/messages/{reference['id']}", headers=headers, params={"format": "full"}).json()
            item = message_to_item(message, self.name)
            if item:
                items.append(item)
        return items
