"""Fetches and parses Indeed's job-search endpoint via a real headless Chrome
browser (Selenium), not Bright Data -- see selenium_client.py's docstring
for why (dynamic `vjk` capture and live Bright-Data-vs-direct-vs-Selenium
comparisons in indeed_scraper_experiment/DECISIONS.md).

Pagination per query works in two stages:

1. Page 0 (start=0, no vjk -- there's nothing to attach until Indeed's own
   JS assigns one after this very load) is fetched first and always. Its
   resulting `vjk` (parsed from the post-load address bar URL) and its
   pagination nav (see extract.has_additional_pages) are both read from
   this one fetch:
   - If page 0's pagination nav has NO numbered page links at all, this
     query stops right here -- confirmed live that every further `start=N`
     offset on such a query still returns cards, but the *same* cards as
     page 0, not new ones (see indeed_scraper_experiment/DECISIONS.md).
   - If page 0 fails to load at all (SeleniumFetchError), this query stops
     too -- there's no vjk and no pagination signal to build subsequent
     pages from, unlike a later page failing (see below).
2. If page 0's nav says more pages exist, pages 1..indeed_max_pages_per_query-1
   are fetched with `start` incremented by indeed_posts_per_page and page
   0's captured `vjk` appended to every one of them. For THESE pages only,
   a failed fetch does NOT stop pagination (it's untrustworthy, not
   evidence of anything -- a failed offset was repeatedly observed to be
   followed by a later offset returning a full page of new postings), but
   a page that fetches successfully with zero cards, or fewer than
   `indeed_min_cards_per_page` cards, does stop pagination -- both
   trustworthy "nothing/little more here" signals once a page has actually
   loaded (a 0-card page is retried once first -- see
   _EMPTY_PAGE_RETRY_ATTEMPTS -- before being trusted).

This is Indeed-only; LinkedIn's pagination keeps its own original
stop-early behavior, since LinkedIn's `seeMoreJobPostings` endpoint was not
found to have any of these problems.
"""

from __future__ import annotations

import logging
from urllib.parse import quote

from visa_jobs_api.config import Settings
from visa_jobs_api.shared.call_stats import CallStats
from visa_jobs_api.shared.concurrency import gather_limited
from visa_jobs_api.sources.indeed.country_domains import COUNTRY_DOMAINS
from visa_jobs_api.sources.indeed.extract import has_additional_pages, parse_job_cards
from visa_jobs_api.sources.indeed.models import JobCard, SearchQuery
from visa_jobs_api.sources.indeed.selenium_client import SeleniumFetchError, fetch_html_via_selenium

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


def _extract_vjk(current_url: str) -> str:
    if "vjk=" not in current_url:
        return ""
    return current_url.split("vjk=", 1)[1].split("&", 1)[0]


async def _fetch_page_with_retry(
    url: str, *, domain: str, query_country: str, settings: Settings, stats: CallStats
) -> tuple[list[JobCard], str, str]:
    """Returns (cards, html, current_url). Retries once if the page loads
    successfully but shows 0 cards, before trusting a genuine zero -- same
    "don't trust a lone empty page" mechanism as before, just wired to
    Selenium instead of Bright Data now. Raises SeleniumFetchError if every
    attempt fails to load at all."""
    html = ""
    current_url = url
    cards: list[JobCard] = []
    for attempt in range(1, _EMPTY_PAGE_RETRY_ATTEMPTS + 1):
        html, current_url = await fetch_html_via_selenium(url, page_settle_seconds=settings.indeed_page_settle_seconds)
        stats.record_indeed_search_call(query_country)
        cards = parse_job_cards(html, domain=domain, query_country=query_country)
        if cards or attempt == _EMPTY_PAGE_RETRY_ATTEMPTS:
            return cards, html, current_url
        logger.warning(
            "Indeed search %r: 0 cards on attempt %d/%d -- retrying "
            "(likely a blocked/empty page, not necessarily a real zero-result search)",
            query_country,
            attempt,
            _EMPTY_PAGE_RETRY_ATTEMPTS,
        )
    return cards, html, current_url


async def _fetch_query_job_cards(
    query: SearchQuery, *, settings: Settings, posted_within_hours: int, stats: CallStats
) -> list[JobCard]:
    domain, _geo = COUNTRY_DOMAINS[query.country]
    page_size = settings.indeed_posts_per_page
    cards: list[JobCard] = []

    first_url = _build_search_url(domain, keywords=query.keywords, start=0, posted_within_hours=posted_within_hours)
    try:
        page_cards, html, current_url = await _fetch_page_with_retry(
            first_url, domain=domain, query_country=query.country, settings=settings, stats=stats
        )
    except SeleniumFetchError as exc:
        # Unlike a later page failing (see below), page 0 failing ends the
        # whole query -- there's no vjk and no pagination-nav signal to
        # build any subsequent page from without it.
        logger.error(
            "Indeed search %r/%r: page 0 failed to load -- skipping this query entirely: %s",
            query.keywords,
            query.country,
            exc,
        )
        logger.info("Indeed search %r/%r: %d job cards", query.keywords, query.country, 0)
        return []

    vjk = _extract_vjk(current_url)
    cards.extend(page_cards)

    if not has_additional_pages(html):
        logger.info(
            "Indeed search %r/%r: page 0's pagination nav advertises no further pages -- stopping "
            "here (%d job cards, vjk=%s).",
            query.keywords,
            query.country,
            len(cards),
            vjk or "none captured",
        )
        logger.info("Indeed search %r/%r: %d job cards", query.keywords, query.country, len(cards))
        return cards

    logger.info(
        "Indeed search %r/%r: page 0's pagination nav advertises further pages -- continuing "
        "(vjk=%s captured, %d job cards so far).",
        query.keywords,
        query.country,
        vjk or "none captured",
        len(cards),
    )

    for page in range(1, settings.indeed_max_pages_per_query):
        url = _build_search_url(
            domain,
            keywords=query.keywords,
            start=page_size * page,
            posted_within_hours=posted_within_hours,
            vjk=vjk,
        )
        try:
            page_cards, _html, _current_url = await _fetch_page_with_retry(
                url, domain=domain, query_country=query.country, settings=settings, stats=stats
            )
        except SeleniumFetchError as exc:
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
            query, settings=settings, posted_within_hours=posted_within_hours, stats=stats
        ),
        limit=settings.indeed_search_concurrency,
    )
    return [card for query_cards in results for card in query_cards]
