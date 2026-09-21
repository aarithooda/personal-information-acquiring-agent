"""The API contract: the exact shape of every response.

FastAPI validates each response against these models and generates the interactive docs at /docs
from them, so this file is the single description of what the front end can rely on.
"""

from typing import Literal

from pydantic import BaseModel

Category = Literal["ai", "software", "research", "other"]


class ItemOut(BaseModel):
    id: int
    title: str
    url: str
    source: str  # the first source that reported it
    seen_on: list[str]  # every source that reported it
    category: Category | None
    summary: str | None  # None for title-only items: we never keep an invented summary
    importance: int | None
    published_at: str | None
    discovered_at: str
    briefing_id: int | None
    briefed_at: str | None
    status: str
    favorite: bool
    favorited_at: str | None


class ItemsPage(BaseModel):
    items: list[ItemOut]
    total: int  # matching items overall, so the UI can offer "load more"
    limit: int
    offset: int


class BriefingSummary(BaseModel):
    id: int
    created_at: str
    covers_from: str | None
    covers_until: str
    shown: int
    skipped: int


class EntryRef(BaseModel):
    """A briefing entry, linked back to the stored item so it can be favorited."""

    item_id: int | None
    favorite: bool
    title: str
    url: str
    source: str | None
    category: Category | None


class HeadlineOut(EntryRef):
    explanation: str
    why_it_matters: str | None
    via: list[str]


class ExtraOut(EntryRef):
    summary: str | None


class SectionOut(BaseModel):
    category: Category
    label: str
    items: list[ExtraOut]


class BriefingDetail(BaseModel):
    id: int
    created_at: str
    covers_from: str | None
    covers_until: str
    shown: int
    skipped: int
    parsed: bool  # False: the layout was not recognised; show `markdown` as plain text instead
    markdown: str
    intro: str | None
    notes: list[str]
    headlines: list[HeadlineOut]
    sections: list[SectionOut]
    hidden: int
    empty_message: str | None


class Counts(BaseModel):
    library: int
    skipped: int
    favorites: int


class StatusOut(BaseModel):
    last_checked: str | None
    latest_briefing_id: int | None
    counts: Counts
    sources: list[str]


class FavoriteState(BaseModel):
    item_id: int
    favorite: bool
