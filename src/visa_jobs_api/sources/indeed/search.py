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

import asyncio
import logging
from urllib.parse import quote

from visa_jobs_api.config import Settings
from visa_jobs_api.shared.call_stats import CallStats
from visa_jobs_api.shared.concurrency import gather_limited
from visa_jobs_api.sources.indeed.country_domains import COUNTRY_DOMAINS
from visa_jobs_api.sources.indeed.extract import (
    has_additional_pages,
    is_confirmed_zero_result,
    is_genuine_indeed_page,
    page_title,
    parse_job_cards,
)
from visa_jobs_api.sources.indeed.models import JobCard, SearchQuery
from visa_jobs_api.sources.indeed.selenium_client import ProxyConfig, SeleniumFetchError, fetch_html_via_selenium

logger = logging.getLogger(__name__)

_SEARCH_URL_TEMPLATE = "https://{domain}/jobs"

# Indeed's date-posted filter (`fromage`, in days) only accepts the values
# its own UI exposes (1/3/7/14) -- there's no hour-level control the way
# LinkedIn's f_TPR offers, so an hour count maps to the closest supported
# value at or above it, capping at 14 (the longest window Indeed's UI has).
_FROMAGE_OPTIONS = (1, 3, 7, 14)

_EMPTY_PAGE_RETRY_ATTEMPTS = 5


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


def _proxy_config(settings: Settings) -> ProxyConfig | None:
    """Builds a ProxyConfig from Settings, or None if no proxy is configured
    (Selenium then connects directly -- see selenium_client.py's docstring)."""
    if settings.indeed_selenium_proxy_host is None:
        return None
    # Settings._proxy_settings_are_all_or_nothing already guarantees these
    # three are set whenever the host is -- narrowing Optional for the type
    # checker, not validating untrusted input.
    assert settings.indeed_selenium_proxy_port is not None
    assert settings.indeed_selenium_proxy_username is not None
    assert settings.indeed_selenium_proxy_password is not None
    return ProxyConfig(
        host=settings.indeed_selenium_proxy_host,
        port=settings.indeed_selenium_proxy_port,
        username=settings.indeed_selenium_proxy_username,
        password=settings.indeed_selenium_proxy_password,
    )


def _describe_zero_cards(html: str) -> str:
    """One-line diagnosis of why a fetched page parsed to 0 job cards.

    Uses structural markers on the page body -- not the <title>, which an
    earlier version of this relied on and was confirmed live (~20 real
    fetches across all 8 countries, see DECISIONS.md) to give false
    positives: Indeed uses several different real-page title templates
    depending on market/query, most without a leading result count, so a
    real zero-result page was being misclassified as "likely blocked"
    purely from an unfamiliar title. Three distinguishable outcomes:

    - Not a genuine Indeed page at all (extract.is_genuine_indeed_page is
      False -- its search box isn't present): a real bot-block/CAPTCHA
      interstitial or an unrelated redirect (e.g. a login page), confirmed
      live against an actual captured Cloudflare Turnstile challenge page.
    - A genuine Indeed page whose own embedded client state confirms zero
      results (extract.is_confirmed_zero_result): a real BOT BLOCK failure
      it is not -- this is 0 JOBS, a confirmed genuine zero-result search.
    - A genuine Indeed page with neither signal: Indeed's own page doesn't
      say zero, yet this parser found no cards -- a selector/markup
      mismatch (Indeed changed its HTML) needing a code fix, not a block.
    """
    title = page_title(html)
    if not is_genuine_indeed_page(html):
        return f"BOT BLOCK failure -- page <title> {title!r} isn't a genuine Indeed page (no search-box markup found), likely a bot-block/CAPTCHA interstitial or redirect"
    if is_confirmed_zero_result(html):
        return f"0 JOBS, not a bot block -- Indeed's own page state confirms zero results (title: {title!r})"
    return f"neither a confirmed block nor a confirmed zero (title: {title!r}) -- likely a selector/markup mismatch (Indeed changed its HTML), not a block"


async def _fetch_page_with_retry(
    url: str, *, domain: str, query_country: str, settings: Settings, stats: CallStats
) -> tuple[list[JobCard], str, str]:
    """Returns (cards, html, current_url). Retries (see
    _EMPTY_PAGE_RETRY_ATTEMPTS) if the page loads successfully but shows 0
    cards, before trusting a genuine zero -- same "don't trust a lone empty
    page" mechanism as before, just wired to Selenium instead of Bright
    Data now. Bumped from 2 to 5 attempts to test whether the residential
    proxy's rotating IPs (a fresh exit IP each retry, since each attempt is
    a brand-new Selenium/Chrome session) make repeated attempts actually
    likely to land on a non-blocked IP, rather than just re-hitting the
    same block -- see DECISIONS.md. Raises SeleniumFetchError if every
    attempt fails to load at all."""
    html = ""
    current_url = url
    cards: list[JobCard] = []
    proxy = _proxy_config(settings)
    for attempt in range(1, _EMPTY_PAGE_RETRY_ATTEMPTS + 1):
        if attempt > 1:
            await asyncio.sleep(settings.indeed_sequential_call_delay_seconds)
        html, current_url = await fetch_html_via_selenium(
            url, page_settle_seconds=settings.indeed_page_settle_seconds, proxy=proxy
        )
        stats.record_indeed_search_call(query_country)
        cards = parse_job_cards(html, domain=domain, query_country=query_country)
        if cards:
            return cards, html, current_url
        if attempt == _EMPTY_PAGE_RETRY_ATTEMPTS:
            logger.warning(
                "Indeed search %r: 0 cards after %d attempt(s) -- accepting as final. URL: %s. %s",
                query_country,
                _EMPTY_PAGE_RETRY_ATTEMPTS,
                url,
                _describe_zero_cards(html),
            )
            return cards, html, current_url
        logger.warning(
            "Indeed search %r: 0 cards on attempt %d/%d -- retrying. URL: %s. %s",
            query_country,
            attempt,
            _EMPTY_PAGE_RETRY_ATTEMPTS,
            url,
            _describe_zero_cards(html),
        )
    return cards, html, current_url


async def _fetch_query_job_cards(
    query: SearchQuery, *, settings: Settings, posted_within_hours: int, stats: CallStats
) -> list[JobCard]:
    domain = COUNTRY_DOMAINS[query.country]
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
        await asyncio.sleep(settings.indeed_sequential_call_delay_seconds)
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
