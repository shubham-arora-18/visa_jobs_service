"""Search queries LinkedIn is scraped for.

Kept as a code constant rather than an env var -- like HN's tech-stack
keyword table, this is a structured list that's edited occasionally, not a
per-deployment tunable. The keyword expression uses LinkedIn's own boolean
search syntax (AND/OR, quoted phrases) to bias results toward postings that
already mention sponsorship, which meaningfully improves the confirmed-hit
rate over a bare title search (see linkedin_visa_scraper/DECISIONS.md).
"""

from __future__ import annotations

from visa_jobs_api.sources.linkedin.models import SearchQuery

_KEYWORDS = "(Python OR Backend OR Java) AND (sponsor OR sponsorship)"

SEARCH_QUERIES: list[SearchQuery] = [
    SearchQuery(keywords=_KEYWORDS, location="United Kingdom"),
    SearchQuery(keywords=_KEYWORDS, location="Germany"),
    SearchQuery(keywords=_KEYWORDS, location="Netherlands"),
    SearchQuery(keywords=_KEYWORDS, location="Ireland"),
    SearchQuery(keywords=_KEYWORDS, location="Switzerland"),
    SearchQuery(keywords=_KEYWORDS, location="Australia"),
    SearchQuery(keywords=_KEYWORDS, location="Singapore"),
    SearchQuery(keywords=_KEYWORDS, location="United Arab Emirates"),
]

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
