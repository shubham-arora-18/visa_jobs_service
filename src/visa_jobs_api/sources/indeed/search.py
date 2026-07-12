"""Fetches and parses Indeed's job-search endpoint via Bright Data.

Pagination mirrors LinkedIn's approach (sources/linkedin/search.py): fetch
pages in order and stop as soon as one comes back with fewer than a full
page of results, capped at `indeed_max_pages_per_query` -- Indeed has no
total-result-count field either.

A 0-card page is retried once before being trusted: a live re-fetch of a
country with identical parameters was found to return real cards moments
after an earlier attempt returned 0 for it (see
indeed_scraper_experiment/DECISIONS.md) -- the same "page loaded but came
back empty/blocked" failure mode already documented for LinkedIn (silently
treating that as "no jobs found" previously made an entire real digest run
look like zero sponsorship offers).

A page that still fails to fetch after those retries (shared.http.FetchError)
stops that one query's pagination early, keeping whatever pages it already
gathered, rather than failing the whole query -- and, in turn, the whole
source or digest run -- over one bad page.
"""

from __future__ import annotations

import logging
from urllib.parse import quote

import httpx

from visa_jobs_api.config import Settings
from visa_jobs_api.shared.call_stats import CallStats
from visa_jobs_api.shared.concurrency import gather_limited
from visa_jobs_api.shared.http import FetchError, fetch_html
from visa_jobs_api.sources.indeed.country_domains import COUNTRY_DOMAINS
from visa_jobs_api.sources.indeed.extract import parse_job_cards
from visa_jobs_api.sources.indeed.models import JobCard, SearchQuery

logger = logging.getLogger(__name__)

_SEARCH_URL_TEMPLATE = "https://{domain}/jobs"

# Job Type = Full-time + Permanent, Experience level = Senior -- selected
# directly in Indeed's own search-filter UI and provided as a requirement,
# not something this codebase chose on its own. Decoded, this is
# `0kf:attr(5QWDV|CF3CP,OR)explvl(SENIOR_LEVEL);` -- `attr(5QWDV|CF3CP,OR)`
# matches Job Type "Permanent" OR "Full-time", `explvl(SENIOR_LEVEL)` is
# the Experience Level filter.
_SC_FILTER = "0kf%3Aattr%285QWDV%7CCF3CP%252COR%29explvl%28SENIOR_LEVEL%29%3B"

# Indeed's date-posted filter (`fromage`, in days) only accepts the values
# its own UI exposes (1/3/7/14) -- there's no hour-level control the way
# LinkedIn's f_TPR offers, so an hour count maps to the closest supported
# value at or above it, capping at 14 (the longest window Indeed's UI has).
_FROMAGE_OPTIONS = (1, 3, 7, 14)

_EMPTY_PAGE_RETRY_ATTEMPTS = 2


def _fromage_for_hours(posted_within_hours: int) -> int:
    days = -(-posted_within_hours // 24)  # ceil division
    for option in _FROMAGE_OPTIONS:
        if days <= option:
            return option
    return _FROMAGE_OPTIONS[-1]


def _build_search_url(domain: str, *, keywords: str, start: int, posted_within_hours: int) -> str:
    fromage = _fromage_for_hours(posted_within_hours)
    base_url = _SEARCH_URL_TEMPLATE.format(domain=domain)
    return f"{base_url}?q={quote(keywords)}&l=&fromage={fromage}&sc={_SC_FILTER}&start={start}"


async def _fetch_one_page(
    client: httpx.AsyncClient,
    url: str,
    *,
    domain: str,
    geo: str,
    query_country: str,
    settings: Settings,
    stats: CallStats,
) -> list[JobCard]:
    for attempt in range(1, _EMPTY_PAGE_RETRY_ATTEMPTS + 1):
        html = await fetch_html(
            client,
            url,
            via_brightdata=True,
            brightdata_api_key=settings.brightdata_api_key,
            brightdata_zone=settings.brightdata_zone,
            brightdata_country=geo,
        )
        stats.record_indeed_search_call(query_country)
        cards = parse_job_cards(html, domain=domain, query_country=query_country)
        if cards or attempt == _EMPTY_PAGE_RETRY_ATTEMPTS:
            return cards
        logger.warning(
            "Indeed search %r: 0 cards on attempt %d/%d -- retrying "
            "(likely a blocked/empty page, not necessarily a real zero-result search)",
            query_country,
            attempt,
            _EMPTY_PAGE_RETRY_ATTEMPTS,
        )
    return []


async def _fetch_query_job_cards(
    client: httpx.AsyncClient, query: SearchQuery, *, settings: Settings, posted_within_hours: int, stats: CallStats
) -> list[JobCard]:
    domain, geo = COUNTRY_DOMAINS[query.country]
    page_size = settings.indeed_posts_per_page
    cards: list[JobCard] = []
    for page in range(settings.indeed_max_pages_per_query):
        url = _build_search_url(
            domain, keywords=query.keywords, start=page_size * page, posted_within_hours=posted_within_hours
        )
        try:
            page_cards = await _fetch_one_page(
                client, url, domain=domain, geo=geo, query_country=query.country, settings=settings, stats=stats
            )
        except FetchError as exc:
            logger.error(
                "Indeed search %r/%r: page %d failed to fetch -- keeping the %d card(s) already "
                "gathered for this query: %s",
                query.keywords,
                query.country,
                page,
                len(cards),
                exc,
            )
            break
        cards.extend(page_cards)
        if len(page_cards) < page_size:
            break
    logger.info("Indeed search %r/%r: %d job cards", query.keywords, query.country, len(cards))
    return cards


async def fetch_all_job_cards(
    client: httpx.AsyncClient,
    queries: list[SearchQuery],
    *,
    settings: Settings,
    posted_within_hours: int,
    stats: CallStats | None = None,
) -> list[JobCard]:
    """Fetch every query's job cards, at most `indeed_search_concurrency` queries at a time."""
    stats = stats if stats is not None else CallStats()
    results = await gather_limited(
        queries,
        lambda query: _fetch_query_job_cards(
            client, query, settings=settings, posted_within_hours=posted_within_hours, stats=stats
        ),
        limit=settings.indeed_search_concurrency,
    )
    return [card for query_cards in results for card in query_cards]
