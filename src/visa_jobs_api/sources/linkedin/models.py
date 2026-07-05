"""Typed domain models for the LinkedIn source."""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, ConfigDict


class SearchQuery(BaseModel):
    """One LinkedIn job-search query: a keyword expression scoped to a location."""

    model_config = ConfigDict(frozen=True)

    keywords: str
    location: str
    work_type: str = ""


class JobCard(BaseModel):
    """A single result row from a LinkedIn search-results page (title/company/location/date/url only)."""

    model_config = ConfigDict(frozen=True)

    title: str
    company: str
    location: str
    posted_on: date
    url: str


class LinkedInJobCandidate(BaseModel):
    """A JobCard with its full description fetched, ready for visa-mention extraction."""

    model_config = ConfigDict(frozen=True)

    card: JobCard
    description: str
    language: str
