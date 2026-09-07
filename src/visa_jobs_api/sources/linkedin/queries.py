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

Title filtering (TECH_ROLE_NOUNS/TECH_DOMAIN_SIGNALS/TITLE_INCLUDE/
TITLE_EXCLUDE, dedupe_cards/filter_by_title) now lives in
shared/title_filter.py, shared with the Indeed source -- see that
module's docstring for why (a fixed-phrase version of this was originally
LinkedIn-only here, copied into the standalone `indeed_scraper_experiment`
project, and found there to silently drift out of sync and drop genuinely
relevant titles).
"""

from __future__ import annotations

from visa_jobs_api.sources.linkedin.models import SearchQuery

DEFAULT_KEYWORDS = '(Python OR Backend OR Java OR "distributed systems") AND (sponsor OR sponsorship OR "work authorization" OR expat)'

_COUNTRIES = [
    # "United States",  # temporarily disabled
    "Canada",
    "United Kingdom",
    "Germany",
    "Netherlands",
    "Ireland",
    "Switzerland",
    "Australia",
    "Singapore",
    "United Arab Emirates",
    "New Zealand",
    "Sweden",
    "France",
    "Finland",
    "Denmark",
    "Austria",
    "Belgium",
]


def build_search_queries(keywords: str = DEFAULT_KEYWORDS) -> list[SearchQuery]:
    return [SearchQuery(keywords=keywords, location=country) for country in _COUNTRIES]
