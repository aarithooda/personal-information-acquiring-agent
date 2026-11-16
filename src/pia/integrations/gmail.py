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
import re
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
# Two ways a message gets in. (1) A TRUSTED SENDER: an exact address the owner has verified; everything from it is
# inspected. (2) A SPECIFIC KEYWORD from anyone else. Single generic words ("notice", "registration", "opportunity", "talk",
# "workshop") are deliberately absent: on a university mailbox they match routine circulars and flood the results.
DEFAULT_KEYWORDS = (
    "internship", "fellowship", "scholarship", "hackathon", "datathon", "summer school", "summer research", "call for papers",
    "call for applications", "call for nominations", "applications open", "applications are invited", "application deadline",
    "last date to apply", "research internship", "research opportunity", "internship opportunity", "pitch competition",
    "coding competition", "programming contest", "student nominations", "presentation competition",
)
KEYWORD_BODY_CHARS = 600  # how much of the body a keyword may appear in (the subject always counts)
# Always dropped, even from a trusted sender: Ph.D. viva-voce notices and the spam-quarantine digest.
DEFAULT_EXCLUDE_SUBJECTS = ("viva", "quarantined emails")
DEFAULT_EXCLUDE_SENDERS = ("noreply-emailsupport@iitr.ac.in",)
SLICE = timedelta(days=1)  # mail is read one day at a time, oldest first, so the cursor can only move over finished days
LIST_PAGE_SIZE = 100
MAX_LIST_PAGES = 50  # per day: 5,000 matching messages in one day would mean the filters are wrong, and fails loudly


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


def build_query(since: datetime, senders: list[str], keywords: list[str], extra: str = "", *, until: datetime | None = None,
                exclude_subjects: tuple | list = (), exclude_senders: tuple | list = ()) -> str:
    """A Gmail search: mail in [since, until) from a trusted sender, OR mentioning a keyword, minus the exclusions."""
    parts = [f"after:{int(since.timestamp())}"]
    if until is not None:
        parts.append(f"before:{int(until.timestamp())}")
    parts += ["-in:chats", "-in:spam", "-in:trash"]
    clauses = []
    if senders:
        clauses.append("from:(" + " OR ".join(sender.strip() for sender in senders if sender.strip()) + ")")
    if keywords:
        clauses.append("(" + " OR ".join(f'"{word}"' if " " in word else word for word in keywords) + ")")
    if clauses:
        parts.append("{" + " ".join(clauses) + "}")  # Gmail's braces mean OR
    for word in exclude_subjects:
        parts.append(f'-subject:"{word}"' if " " in word else f"-subject:{word}")
    for address in exclude_senders:
        parts.append(f"-from:{address}")
    if extra:
        parts.append(extra)
    return " ".join(parts)


def decide(sender: str, subject: str, text: str, *, senders=(), keywords=(), exclude_subjects=(), exclude_senders=()) -> tuple[bool, str]:
    """Whether a message is kept, and why. The Gmail search narrows what is downloaded; this is the rule that is enforced,
    because Gmail's matching is fuzzy about punctuation and word forms. It is also what the dry run uses."""
    address = (sender or "").strip().lower()
    if address in {entry.strip().lower() for entry in exclude_senders}:
        return False, "excluded sender"
    subject_lower = (subject or "").lower()
    for word in exclude_subjects:
        if re.search(rf"\b{re.escape(word.lower())}\b", subject_lower):  # whole words: "viva" must not match "revival"
            return False, f"excluded subject ({word})"
    for entry in senders:
        entry = entry.strip().lower()
        if entry and (address == entry or (entry.startswith("@") and address.endswith(entry))):
            return True, "trusted sender"
    # A keyword counts in the subject or the opening of the body. A long circular that mentions "internship" in its tenth
    # paragraph, or a fee-waiver list that says "scholarship", is not an announcement about one.
    haystack = f"{subject_lower} {(text or '')[:KEYWORD_BODY_CHARS].lower()}"
    for word in keywords:
        if word.lower() in haystack:
            return True, f"keyword: {word}"
    return False, "no trusted sender, no keyword"


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
    exclude_subjects: list[str] = field(default_factory=lambda: list(DEFAULT_EXCLUDE_SUBJECTS))
    exclude_senders: list[str] = field(default_factory=lambda: list(DEFAULT_EXCLUDE_SENDERS))
    query: str = ""
    max_messages: int = 100  # a per-run budget of message bodies to read, NOT a cap on what is acquired: see fetch()
    state: dict = field(default_factory=dict)
    token_file: str = ""
    dropped: int = 0  # messages downloaded but rejected by `decide` in the last fetch (for logs and the dry run)

    def _query(self, start: datetime, end: datetime) -> str:
        # One second of overlap with the neighbouring day, in case Gmail treats after:/before: as exclusive at the boundary;
        # `seen` removes the duplicate, so a message dated exactly on a boundary can never fall between two days.
        return build_query(start - timedelta(seconds=1), self.senders, self.keywords, self.query, until=end + timedelta(seconds=1),
                           exclude_subjects=self.exclude_subjects, exclude_senders=self.exclude_senders)

    def _list_ids(self, client: httpx.Client, headers: dict, start: datetime, end: datetime) -> list[str]:
        """Every matching message id in [start, end), following nextPageToken to the end."""
        ids: list[str] = []
        token = None
        for _ in range(MAX_LIST_PAGES):
            params = {"q": self._query(start, end), "maxResults": LIST_PAGE_SIZE}
            if token:
                params["pageToken"] = token
            listing = request_with_retry(client, "GET", f"{API}/messages", headers=headers, params=params).json()
            ids += [reference["id"] for reference in listing.get("messages", [])]
            token = listing.get("nextPageToken")
            if not token:
                return ids
        raise SourceError(f"more than {MAX_LIST_PAGES * LIST_PAGE_SIZE} messages match in one day: the filters are too broad")

    def _read_day(self, client: httpx.Client, headers: dict, start: datetime, end: datetime, seen: set[str]) -> tuple[list[RawItem], int]:
        """Read and filter one finished slice. Raises if any message in it cannot be read, so the slice is retried whole."""
        items: list[RawItem] = []
        read = 0
        for message_id in self._list_ids(client, headers, start, end):
            if message_id in seen:
                continue  # also returned for the previous slice's boundary
            try:
                message = request_with_retry(client, "GET", f"{API}/messages/{message_id}", headers=headers, params={"format": "full"}).json()
            except SourceError as exc:
                if exc.status == 404:
                    continue  # deleted between listing and reading: nothing to acquire
                raise
            seen.add(message_id)
            read += 1
            item = message_to_item(message, self.name)
            if item is None:
                continue
            keep, _ = decide(item.signals.get("from", ""), item.title, item.content or "", senders=self.senders, keywords=self.keywords,
                             exclude_subjects=self.exclude_subjects, exclude_senders=self.exclude_senders)
            if keep:
                items.append(item)
            else:
                self.dropped += 1
        return items, read

    def fetch(self, client: httpx.Client, since: datetime, until: datetime) -> list[RawItem]:
        """Read [since, until) one day at a time, oldest first, following every page of results.

        The runner's cursor must never move past a message that was not processed. So this records, in `state`, how far it
        got: `fetched_until` is the end of the last day that was read completely. After `max_messages` bodies it stops at
        a day boundary and sets `more`, and the next run continues from there (the runner reads both). A failure part-way
        through a day abandons that whole day (items already read stay; the cursor stays before it). Items are stored by
        canonical URL, so reading a day twice does no harm."""
        path = Path(self.token_file) if self.token_file else TOKEN_FILE
        headers = {"Authorization": f"Bearer {access_token(client, until, path)}"}
        self.dropped = 0
        items: list[RawItem] = []
        seen: set[str] = set()
        position, budget = since, 0
        while position < until:
            end = min(position + SLICE, until)
            try:
                day_items, read = self._read_day(client, headers, position, end, seen)
            except SourceError as exc:
                if position == since:
                    raise  # nothing finished: report the failure, the cursor does not move
                # Keep what was finished, stop before the broken day, and wait the normal interval (not "call again at once"):
                # a message that keeps failing must not be retried every minute. The runner shows `warning` on the source,
                # so a stalled cursor is visible instead of the source looking healthy.
                self.state = {"fetched_until": position.isoformat(), "more": False,
                              "warning": f"Stopped before {position:%Y-%m-%d}: mail from that day could not be read ({str(exc)[:160]}). Newer mail waits."}
                return items
            items += day_items
            budget += read
            position = end
            if budget >= self.max_messages and position < until:
                self.state = {"fetched_until": position.isoformat(), "more": True}
                return items
        self.state = {"fetched_until": until.isoformat(), "more": False}
        return items
