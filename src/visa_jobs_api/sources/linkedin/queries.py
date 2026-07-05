"""Search queries LinkedIn is scraped for.

The country list is kept as a code constant rather than an env var -- like
HN's tech-stack keyword table, it's a structured list that's edited
occasionally, not a per-request tunable. The keyword expression, on the
other hand, is exposed as an API input (see api.dto.digest.DigestRunRequest)
so a caller can tune what LinkedIn is searched for without a code change;
DEFAULT_KEYWORDS is used when a caller doesn't specify one. It uses
LinkedIn's own boolean search syntax (AND/OR, quoted phrases) to bias
results toward postings that already mention sponsorship, which
meaningfully improves the confirmed-hit rate over a bare title search (see
linkedin_visa_scraper/DECISIONS.md).
"""

from __future__ import annotations

from visa_jobs_api.sources.linkedin.models import SearchQuery

DEFAULT_KEYWORDS = "(Python OR Backend OR Java) AND (sponsor OR sponsorship)"

_COUNTRIES = [
    "Canada",
    "United Kingdom",
    "Germany",
    "Netherlands",
    "Ireland",
    "Switzerland",
    "Australia",
    "Singapore",
    "United Arab Emirates",
]


def build_search_queries(keywords: str = DEFAULT_KEYWORDS) -> list[SearchQuery]:
    return [SearchQuery(keywords=keywords, location=country) for country in _COUNTRIES]


TITLE_INCLUDE = [
    "software engineer",
    "backend engineer",
    "back-end engineer",
    "full stack engineer",
    "fullstack engineer",
    "platform engineer",
    "software developer",
    "staff engineer",
    "principal engineer",
]
TITLE_EXCLUDE = ["intern", "internship", "director", " vp ", "chief", "manager", "qa engineer"]
