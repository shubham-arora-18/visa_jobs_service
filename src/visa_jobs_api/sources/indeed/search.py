"""Fetches and parses Indeed's job-search endpoint via Bright Data.

Unlike LinkedIn (sources/linkedin/search.py), pagination here distinguishes
a *failed* fetch from a *successful-but-empty* one, rather than treating
both the same way:

- A page that fails to fetch after retries (shared.http.FetchError,
  including Bright Data itself reporting a failure via its x-brd-*
  headers -- see shared/http.py) does NOT stop this query's pagination --
  it's untrustworthy, not evidence of anything, so the next offset is
  still tried. A `start=N` offset erroring was repeatedly observed to be
  followed by a later offset in the very same query returning a full page
  of entirely new, real postings (see indeed_scraper_experiment/
  DECISIONS.md for the live investigation this is based on).
- A page that fetches successfully but has zero job cards on it DOES stop
  this query's pagination -- by the time `_fetch_one_page` returns an
  empty list (rather than raising), it has already retried that one
  offset once and confirmed it's still empty (see
  _EMPTY_PAGE_RETRY_ATTEMPTS below), so a real "nothing here" is a
  trustworthy signal that there's nothing further either.
- A page that fetches successfully but returns fewer than
  `indeed_min_cards_per_page` cards also stops this query's pagination --
  treated as the last page of real results. Note this is a deliberate
  simplifying assumption, not a guarantee: earlier live testing did
  observe a later offset return a full page of new postings right after
  an earlier offset came back partial (see indeed_scraper_experiment/
  DECISIONS.md), so this trades a small amount of missed coverage for
  fewer wasted requests on likely-exhausted queries.

This is Indeed-only; LinkedIn's pagination keeps its original stop-early
behavior, since LinkedIn's `seeMoreJobPostings` endpoint was not found to
have this problem.
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


def _build_search_url(domain: str, *, keywords: str, start: int, posted_within_hours: int, vjk: str = "") -> str:
    fromage = _fromage_for_hours(posted_within_hours)
    base_url = _SEARCH_URL_TEMPLATE.format(domain=domain)
    url = f"{base_url}?q={quote(keywords)}&l=&fromage={fromage}&start={start}"
    if vjk:
        url += f"&vjk={vjk}"
    return url


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
            domain,
            keywords=query.keywords,
            start=page_size * page,
            posted_within_hours=posted_within_hours,
            vjk=settings.indeed_vjk,
        )
        try:
            page_cards = await _fetch_one_page(
                client, url, domain=domain, geo=geo, query_country=query.country, settings=settings, stats=stats
            )
        except FetchError as exc:
            # Deliberately does not stop this query's pagination -- see
            # module docstring for why a failed offset (including Bright
            # Data itself reporting a failure) doesn't reliably mean later
            # offsets have nothing either.
            logger.error(
                "Indeed search %r/%r: page %d failed to fetch -- skipping this page and continuing "
                "to the next offset: %s",
                query.keywords,
                query.country,
                page,
                exc,
            )
            continue
        if not page_cards:
            # A *successful* fetch with zero cards, unlike a failed fetch
            # above, is trustworthy -- _fetch_one_page already retried this
            # one offset once and confirmed it's still empty -- so further
            # offsets are not attempted. See module docstring.
            logger.info(
                "Indeed search %r/%r: page %d fetched successfully with 0 job cards -- stopping "
                "pagination for this query (no further offsets attempted).",
                query.keywords,
                query.country,
                page,
            )
            break
        cards.extend(page_cards)
        if len(page_cards) < settings.indeed_min_cards_per_page:
            # A partial page is treated as the last page of real results --
            # see module docstring for the tradeoff this accepts.
            logger.info(
                "Indeed search %r/%r: page %d returned %d job card(s), below the %d-card "
                "threshold -- stopping pagination for this query (no further offsets attempted).",
                query.keywords,
                query.country,
                page,
                len(page_cards),
                settings.indeed_min_cards_per_page,
            )
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
