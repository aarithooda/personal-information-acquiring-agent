"""Turns a `sources` row into something that can fetch.

Adapter kinds, in the order the project prefers them (see docs/platform/SOURCES.md):
  rss         any RSS/Atom feed
  arxiv, hn, github, hf_papers      v1's API adapters, reused unchanged
  json_api    official JSON endpoints with a known shape (`preset`): hackathon and contest listings, and the
              public job-board APIs that most company career pages are built on (Greenhouse, Lever, Ashby)
  page        a page watcher for organisations with no feed or API: it reports NEW links on a listing page
  gmail       read-only university mail (see pia.integrations.gmail); inactive until authorised

Every adapter has `fetch(client, since, until) -> list[RawItem]`. An adapter that keeps memory between runs
exposes it as `.state` (a JSON-safe dict); the runner saves it after a successful fetch.
"""

import html
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import urlsplit

import httpx

from pia.acquire.feeds import FeedSource
from pia.acquire.safefetch import FetchFailed, FetchRefused, RobotsCache, guarded_get
from pia.acquire.textextract import extract
from pia.http import SourceError, get_with_retry
from pia.models import RawItem
from pia.sources.arxiv import ArxivSource
from pia.sources.base import make_item, parse_iso, parse_or_raise, strip_html
from pia.sources.github import GitHubSource
from pia.sources.hackernews import HackerNewsSource
from pia.sources.hf_papers import HFPapersSource

# What makes a job posting or a link an early-career opportunity. Deliberately broad: extraction and the
# owner's own judgement come later; this only keeps obviously unrelated postings (senior staff roles) out.
#
# Both patterns match whole words. A first live run against real boards showed why: loose fragments matched
# "President" (residen) and every "Program Manager" (program), so 70 ordinary jobs passed as opportunities.
JOB_TITLE_PATTERN = (  # a posting on a job board: its TITLE must name an early-career role
    r"\b(interns?|internships?|fellows?|fellowships?|residency|students?|undergraduates?|new grads?|new graduates?|recent graduates?|"
    r"graduate program(me)?s?|apprentices?|apprenticeships?|co-?op|summer|scholars?|scholarships?|trainees?|early[- ]career|campus|"
    r"university|research assistants?)\b"
)
OPPORTUNITY_PATTERN = (  # a link on a watched page: broader, because programme pages are worded freely
    r"\b(interns?|internships?|fellows?|fellowships?|residency|students?|graduates?|undergraduates?|apprentices?|apprenticeships?|"
    r"co-?op|summer|scholars?|scholarships?|trainees?|early[- ]career|new grads?|campus|hackathons?|competitions?|challenges?|"
    r"contests?|program(me)?s?(?! manager)|call for|applications? open|apply|olympiad|mentorships?|ambassadors?|research assistants?)\b"
)


class UnknownSourceKind(ValueError):
    pass


def _month_date(text: str, default_year: int) -> str | None:
    """'Dec 15, 2026' or 'Dec 15' -> ISO date. Returns None if it does not parse."""
    for fmt, value in (("%b %d, %Y", text), ("%b %d %Y", f"{text} {default_year}"), ("%B %d, %Y", text), ("%B %d %Y", f"{text} {default_year}")):
        try:
            return datetime.strptime(value.strip(), fmt).date().isoformat()
        except ValueError:
            continue
    return None


def devpost_deadline(period: str, today: datetime) -> str | None:
    """Devpost gives 'Nov 10 - Dec 15, 2026' or 'Dec 01 - 15, 2026'. The deadline is the END of the period."""
    if not period or " - " not in period:
        return None
    start, end = (part.strip() for part in period.split(" - ", 1))
    year_match = re.search(r"(\d{4})\s*$", end)
    year = int(year_match.group(1)) if year_match else today.year
    if re.match(r"^\d{1,2},", end):  # 'Dec 01 - 15, 2026': the end reuses the start's month
        end = f"{start.split()[0]} {end}"
    return _month_date(end, year)


# ---------- json_api presets ----------


def parse_devpost(text: str, name: str, now: datetime) -> list[RawItem]:
    items = []
    for entry in json.loads(text)["hackathons"]:
        themes = ", ".join(theme.get("name", "") for theme in entry.get("themes") or [])
        location = (entry.get("displayed_location") or {}).get("location") or ""
        period = entry.get("submission_period_dates") or ""
        prize = strip_html(str(entry.get("prize_amount") or ""))
        facts = [f"Organiser: {entry['organization_name']}." if entry.get("organization_name") else "",
                 f"Submission period: {period}." if period else "", f"Location: {location}." if location else "",
                 f"Prizes: {prize}." if prize and prize != "$0" else "", f"Themes: {themes}." if themes else ""]
        item = make_item(
            source=name, external_id=str(entry.get("id")), url=entry.get("url"), title=entry.get("title"),
            content=" ".join(fact for fact in facts if fact) or None, kind="opportunity",
            signals={"deadline": devpost_deadline(period, now), "location": location, "organisation": entry.get("organization_name") or "",
                     "registrations": entry.get("registrations_count"), "opportunity_type": "hackathon"},
        )
        if item:
            items.append(item)
    return items


def parse_codeforces(text: str, name: str, now: datetime) -> list[RawItem]:
    payload = json.loads(text)
    if payload.get("status") != "OK":
        raise ValueError(f"codeforces status {payload.get('status')}")
    items = []
    for contest in payload["result"]:
        if contest.get("phase") != "BEFORE" or not contest.get("startTimeSeconds"):
            continue
        start = datetime.fromtimestamp(contest["startTimeSeconds"], tz=timezone.utc)
        hours = round(contest.get("durationSeconds", 0) / 3600, 1)
        item = make_item(
            source=name, external_id=str(contest["id"]), url=f"https://codeforces.com/contests/{contest['id']}",
            title=contest.get("name"), kind="opportunity",
            content=f"Codeforces contest starting {start.strftime('%d %B %Y, %H:%M')} UTC, duration {hours} hours. Format: {contest.get('type', 'CF')}.",
            signals={"deadline": start.date().isoformat(), "organisation": "Codeforces", "mode": "Online", "opportunity_type": "competition"},
        )
        if item:
            items.append(item)
    return items


def _job(name, *, job_id, url, title, text, location, published, organisation) -> RawItem | None:
    return make_item(
        # Greenhouse sends its HTML entity-escaped ("&lt;p&gt;"): unescape first, or the tags survive as text.
        source=name, external_id=str(job_id), url=url, title=title, content=(strip_html(html.unescape(text or ""))[:6000] or None),
        published_at=published, kind="opportunity",
        signals={"location": location or "", "organisation": organisation, "opportunity_type": "internship"},
    )


def parse_greenhouse(text: str, name: str, organisation: str) -> list[RawItem]:
    jobs = json.loads(text)["jobs"]
    return [job for entry in jobs if (job := _job(
        name, job_id=entry.get("id"), url=entry.get("absolute_url"), title=entry.get("title"), text=entry.get("content"),
        location=(entry.get("location") or {}).get("name"), published=parse_iso(entry.get("first_published") or entry.get("updated_at")), organisation=organisation))]


def parse_lever(text: str, name: str, organisation: str) -> list[RawItem]:
    out = []
    for entry in json.loads(text):
        created = entry.get("createdAt")
        published = datetime.fromtimestamp(created / 1000, tz=timezone.utc) if isinstance(created, (int, float)) else None
        job = _job(name, job_id=entry.get("id"), url=entry.get("hostedUrl"), title=entry.get("text"), text=entry.get("descriptionPlain") or entry.get("description"),
                   location=(entry.get("categories") or {}).get("location"), published=published, organisation=organisation)
        if job:
            out.append(job)
    return out


def parse_ashby(text: str, name: str, organisation: str) -> list[RawItem]:
    jobs = json.loads(text)["jobs"]
    return [job for entry in jobs if (job := _job(
        name, job_id=entry.get("id"), url=entry.get("jobUrl"), title=entry.get("title"), text=entry.get("descriptionPlain") or entry.get("descriptionHtml"),
        location=entry.get("location"), published=parse_iso(entry.get("publishedAt")), organisation=organisation))]


JOB_BOARDS = {
    "greenhouse": ("https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true", parse_greenhouse),
    "lever": ("https://api.lever.co/v0/postings/{board}?mode=json", parse_lever),
    "ashby": ("https://api.ashbyhq.com/posting-api/job-board/{board}", parse_ashby),
}
_BOARD_HOSTS = {
    "boards.greenhouse.io": "greenhouse", "job-boards.greenhouse.io": "greenhouse", "boards.eu.greenhouse.io": "greenhouse",
    "jobs.lever.co": "lever", "jobs.eu.lever.co": "lever", "jobs.ashbyhq.com": "ashby",
}


def detect_job_board(url: str) -> tuple[str, str] | None:
    """('greenhouse', 'acme') for https://boards.greenhouse.io/acme/..., or None if the URL is not a known board."""
    parts = urlsplit(url if "://" in url else "https://" + url)
    preset = _BOARD_HOSTS.get((parts.hostname or "").lower())
    segments = [segment for segment in parts.path.split("/") if segment]
    if preset and segments and re.fullmatch(r"[A-Za-z0-9._-]{1,80}", segments[0]):
        return preset, segments[0]
    return None


@dataclass
class JsonApiSource:
    name: str
    preset: str
    board: str = ""
    organisation: str = ""
    match: str = JOB_TITLE_PATTERN  # job boards only: keep postings whose TITLE matches
    url: str = ""

    def fetch(self, client: httpx.Client, since: datetime, until: datetime) -> list[RawItem]:
        if self.preset == "devpost":
            url = self.url or "https://devpost.com/api/hackathons?status[]=upcoming&status[]=open&order_by=deadline"
            return parse_or_raise(self.name, lambda body: parse_devpost(body, self.name, until), get_with_retry(client, url).text)
        if self.preset == "codeforces":
            url = self.url or "https://codeforces.com/api/contest.list"
            return parse_or_raise(self.name, lambda body: parse_codeforces(body, self.name, until), get_with_retry(client, url).text)
        if self.preset in JOB_BOARDS:
            if not re.fullmatch(r"[A-Za-z0-9._-]{1,80}", self.board):
                raise SourceError(f"{self.name}: invalid job-board id")
            template, parse = JOB_BOARDS[self.preset]
            organisation = self.organisation or self.board
            jobs = parse_or_raise(self.name, lambda body: parse(body, self.name, organisation), get_with_retry(client, template.format(board=self.board)).text)
            pattern = re.compile(self.match, re.I)
            return [job for job in jobs if pattern.search(job.title)]
        raise SourceError(f"{self.name}: unknown json_api preset {self.preset!r}")


# ---------- page watcher ----------


@dataclass
class PageSource:
    """Reports links that are NEW on a listing page since the last look. For organisations with no feed or API.

    It never reads the linked pages itself; opportunity extraction does that later, through the same guarded
    fetcher. Limits: a page rendered entirely by JavaScript has no links to see, and a link whose text never
    changes when its content does (e.g. "Apply") is reported once. Both are surfaced as an empty or stale
    source in the UI rather than hidden.
    """

    name: str
    url: str
    match: str = OPPORTUNITY_PATTERN
    organisation: str = ""
    kind_hint: str = "opportunity"
    state: dict = field(default_factory=dict)
    max_new: int = 25
    _robots = RobotsCache()

    def fetch(self, client: httpx.Client, since: datetime, until: datetime) -> list[RawItem]:
        try:
            fetched = guarded_get(client, self.url, robots=self._robots)
        except (FetchRefused, FetchFailed) as exc:
            raise SourceError(f"{self.name}: {exc}") from exc
        page = extract(fetched.text, fetched.url)
        pattern = re.compile(self.match, re.I) if self.match else None
        seen = set(self.state.get("seen", []))
        items, now_seen = [], []
        for url, text in page.links:
            if not 6 <= len(text) <= 220 or url in now_seen:
                continue
            now_seen.append(url)
            if url in seen or (pattern and not pattern.search(text) and not pattern.search(url)):
                continue
            if len(items) < self.max_new:
                item = make_item(source=self.name, external_id=url, url=url, title=text, kind=self.kind_hint,
                                 signals={"organisation": self.organisation, "found_on": fetched.url})
                if item:
                    items.append(item)
        if not page.links:
            raise SourceError(f"{self.name}: the page has no readable links (it may be rendered by JavaScript)")
        # Remember every link seen (bounded), so an old link that drops off and returns is not reported again.
        self.state = {"seen": list(dict.fromkeys(now_seen + list(seen)))[:3000]}
        return items


# ---------- registry ----------

FIRST_LOOKBACK_DAYS = {"rss": 21, "arxiv": 4, "hn": 5, "github": 7, "hf_papers": 7, "json_api": 30, "page": 30, "gmail": 14}
KINDS = tuple(FIRST_LOOKBACK_DAYS)


def build_adapter(row) -> object:
    """`row` is a sources row (or any mapping with name, kind, url, config, state)."""
    kind = row["kind"]
    config = json.loads(row["config"] or "{}")
    state = json.loads(row["state"] or "{}") if "state" in row.keys() else {}
    name = row["name"]
    try:
        if kind == "rss":
            return FeedSource(name=name, url=row["url"], **config)
        if kind == "arxiv":
            return ArxivSource(name=name, **config)
        if kind == "hn":
            return HackerNewsSource(name=name, **config)
        if kind == "github":
            return GitHubSource(name=name, **config)
        if kind == "hf_papers":
            return HFPapersSource(name=name, **config)
        if kind == "json_api":
            return JsonApiSource(name=name, url=row["url"] or "", **config)
        if kind == "page":
            return PageSource(name=name, url=row["url"], state=state, **config)
        if kind == "gmail":
            from pia.integrations.gmail import GmailSource

            return GmailSource(name=name, state=state, **config)
    except TypeError as exc:
        raise UnknownSourceKind(f"bad config for source {name!r}: {exc}") from exc
    raise UnknownSourceKind(f"unknown source kind {kind!r}")
