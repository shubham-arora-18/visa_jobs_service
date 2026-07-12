"""Search queries Indeed is scraped for.

DEFAULT_KEYWORDS is the exact expression empirically tested end-to-end in
indeed_scraper_experiment (93 raw cards -> 45 after title-filter -> 3
confirmed sponsorship offers, across a real 8-country/multi-page pull) --
`"software"` matters a lot for hit volume here (many senior engineering
titles don't literally contain "Python"/"Backend"/"Java" but do contain
"software"), so this is deliberately broader than LinkedIn's
DEFAULT_KEYWORDS, not a straight copy of it. Exposed as an independent API
input (see api.dto.digest.DigestRunRequest.indeed_keywords) so a caller
can tune Indeed's search without touching LinkedIn's, and vice versa.

Title filtering (TECH_ROLE_NOUNS/TECH_DOMAIN_SIGNALS/TITLE_INCLUDE/
TITLE_EXCLUDE, dedupe_cards/filter_by_title) lives in
shared/title_filter.py, shared with LinkedIn.
"""

from __future__ import annotations

from visa_jobs_api.sources.indeed.country_domains import COUNTRY_DOMAINS
from visa_jobs_api.sources.indeed.models import SearchQuery

DEFAULT_KEYWORDS = '("Python" OR "Backend" OR "Java" OR "software") AND ("sponsor" OR "sponsorship")'


def build_search_queries(keywords: str = DEFAULT_KEYWORDS) -> list[SearchQuery]:
    return [SearchQuery(keywords=keywords, country=country) for country in COUNTRY_DOMAINS]
