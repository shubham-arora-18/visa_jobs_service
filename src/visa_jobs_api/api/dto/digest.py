"""Request/response DTOs for the digest API."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from visa_jobs_api.sources.indeed.queries import DEFAULT_KEYWORDS as INDEED_DEFAULT_KEYWORDS
from visa_jobs_api.sources.linkedin.queries import DEFAULT_KEYWORDS as LINKEDIN_DEFAULT_KEYWORDS

PostedWithinWindow = Literal["day", "week", "month"]

_HOURS_BY_WINDOW: dict[PostedWithinWindow, int] = {
    "day": 24,
    "week": 24 * 7,
    "month": 24 * 30,
}

_LABEL_BY_WINDOW: dict[PostedWithinWindow, str] = {
    "day": "1 Day",
    "week": "1 Week",
    "month": "1 Month",
}


class DigestRunRequest(BaseModel):
    """Optional per-run overrides. Omit the body entirely to use all defaults."""

    model_config = ConfigDict(frozen=True)

    linkedin_keywords: str = LINKEDIN_DEFAULT_KEYWORDS
    indeed_keywords: str = INDEED_DEFAULT_KEYWORDS
    posted_within: PostedWithinWindow = "day"

    def posted_within_hours(self) -> int:
        return _HOURS_BY_WINDOW[self.posted_within]

    def posted_within_label(self) -> str:
        return _LABEL_BY_WINDOW[self.posted_within]


class SourceCount(BaseModel):
    model_config = ConfigDict(frozen=True)

    source: str
    confirmed_jobs: int


class DigestRunResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    total_jobs: int
    countries: int
    by_source: list[SourceCount]
    email_sent_to: list[str]
