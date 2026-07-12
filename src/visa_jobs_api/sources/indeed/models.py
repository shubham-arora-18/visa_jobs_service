"""Typed domain models for the Indeed source."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class SearchQuery(BaseModel):
    """One Indeed job-search query: a keyword expression scoped to a country."""

    model_config = ConfigDict(frozen=True)

    keywords: str
    country: str


class JobCard(BaseModel):
    """A single result row from an Indeed search-results page (title/company/location/url only).

    Unlike LinkedIn's JobCard, there is no posted_on field -- Indeed's
    visible search-result card has no absolute posted-date anywhere (only a
    relative string buried in an obfuscated client-side JS state blob, not
    the rendered HTML); recency is enforced server-side via the `fromage`
    search parameter instead. See sources/indeed/search.py.
    """

    model_config = ConfigDict(frozen=True)

    title: str
    company: str
    location: str
    url: str
    query_country: str


class IndeedJobCandidate(BaseModel):
    """A JobCard with its full description fetched, ready for visa-mention extraction."""

    model_config = ConfigDict(frozen=True)

    card: JobCard
    description: str
    language: str
