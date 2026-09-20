"""Data shapes that cross module boundaries.

RawItem is what a source adapter hands to the rest of the system. It is
validated at the boundary so nothing downstream has to wonder whether a title
is blank or a timestamp is timezone-naive.
"""

from datetime import datetime, timezone

from pydantic import BaseModel, field_validator


def ensure_utc(value: datetime) -> datetime:
    """Naive datetimes are assumed to already be UTC; aware ones are converted."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class RawItem(BaseModel):
    source: str
    external_id: str
    url: str
    title: str
    content: str | None = None
    published_at: datetime | None = None
    signals: dict = {}  # source-specific popularity data, e.g. {"points": 120}

    @field_validator("source", "external_id", "url", "title")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value

    @field_validator("published_at")
    @classmethod
    def _utc(cls, value: datetime | None) -> datetime | None:
        return ensure_utc(value) if value else None
