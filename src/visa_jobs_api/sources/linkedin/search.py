"""Fetches and parses LinkedIn's guest job-search endpoint.

LinkedIn's `seeMoreJobPostings` endpoint returns a fixed page size
(confirmed empirically to be `post_per_page` results per call -- there is no
total-result-count field anywhere in the response, since this endpoint is
built for infinite-scroll "load more", not a real paged search API). The
last page is detected the same way LinkedIn's own frontend does: a page
returning fewer than a full page of results means there's nothing left, so
`max_pages_per_query` is a cap, not a fixed count -- see
linkedin_visa_scraper/DECISIONS.md for the investigation that found this.

Each query's own pages must be fetched in order (you can't know if page 2
exists until you've seen page 1's result count), so bounded concurrency here
is one task per query, not one per page.

A page that fails to fetch after retries (shared.http.FetchError) stops
that one query's pagination early, keeping whatever pages it already
gathered, rather than failing the whole query -- and, in turn, the whole
source or digest run -- over one bad page.
"""

from __future__ import annotations

import logging
from datetime import date
from urllib.parse import quote

import httpx
from bs4 import BeautifulSoup, Tag

from visa_jobs_api.config import Settings
from visa_jobs_api.shared.call_stats import CallStats
from visa_jobs_api.shared.concurrency import gather_limited
from visa_jobs_api.shared.http import FetchError, fetch_html
from visa_jobs_api.sources.linkedin.models import JobCard, SearchQuery

logger = logging.getLogger(__name__)

_SEARCH_URL = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"


def _build_search_url(query: SearchQuery, *, start: int, timespan_seconds: int) -> str:
    keywords = quote(query.keywords)
    location = quote(query.location)
    return (
        f"{_SEARCH_URL}?keywords={keywords}&location={location}&f_WT={query.work_type}"
        f"&geoId=&f_TPR=r{timespan_seconds}&start={start}"
    )


def _parse_job_cards(html: str, *, query_country: str) -> list[JobCard]:
    soup = BeautifulSoup(html, "html.parser")
    cards: list[JobCard] = []
    for info_div in soup.find_all("div", class_="base-search-card__info"):
        card = _parse_one_card(info_div, query_country=query_country)
        if card is not None:
            cards.append(card)
    return cards


def _parse_one_card(info_div: Tag, *, query_country: str) -> JobCard | None:
    parent = info_div.parent
    entity_urn = parent.get("data-entity-urn") if parent else None
    if not entity_urn:
        return None
    job_id = entity_urn.split(":")[-1]

    title_tag = info_div.find("h3")
    company_tag = info_div.find("a", class_="hidden-nested-link")
    location_tag = info_div.find("span", class_="job-search-card__location")
    date_tag = info_div.find("time")
    if title_tag is None or date_tag is None or not date_tag.get("datetime"):
        return None

    return JobCard(
        title=title_tag.get_text(strip=True),
        company=company_tag.get_text(strip=True).replace("\n", " ") if company_tag else "",
        location=location_tag.get_text(strip=True) if location_tag else "",
        posted_on=date.fromisoformat(date_tag["datetime"]),
        url=f"https://www.linkedin.com/jobs/view/{job_id}/",
        query_country=query_country,
    )


async def _fetch_query_job_cards(
    client: httpx.AsyncClient, query: SearchQuery, *, settings: Settings, posted_within_hours: int, stats: CallStats
) -> list[JobCard]:
    page_size = settings.linkedin_posts_per_page
    timespan_seconds = posted_within_hours * 3600
    cards: list[JobCard] = []
    for page in range(settings.linkedin_max_pages_per_query):
        url = _build_search_url(query, start=page_size * page, timespan_seconds=timespan_seconds)
        try:
            html = await fetch_html(
                client,
                url,
                via_decodo=True,
                decodo_username=settings.decodo_username,
                decodo_password=settings.decodo_password,
            )
        except FetchError as exc:
            logger.error(
                "LinkedIn search %r/%r: page %d failed to fetch -- keeping the %d card(s) already "
                "gathered for this query: %s",
                query.keywords,
                query.location,
                page,
                len(cards),
                exc,
            )
            break
        stats.record_linkedin_search_call(query.location)
        page_cards = _parse_job_cards(html, query_country=query.location)
        cards.extend(page_cards)
        if len(page_cards) < page_size:
            break
    logger.info("LinkedIn search %r/%r: %d job cards", query.keywords, query.location, len(cards))
    return cards


async def fetch_all_job_cards(
    client: httpx.AsyncClient,
    queries: list[SearchQuery],
    *,
    settings: Settings,
    posted_within_hours: int,
    stats: CallStats | None = None,
) -> list[JobCard]:
    """Fetch every query's job cards, at most `linkedin_search_concurrency` queries at a time."""
    stats = stats if stats is not None else CallStats()
    results = await gather_limited(
        queries,
        lambda query: _fetch_query_job_cards(
            client, query, settings=settings, posted_within_hours=posted_within_hours, stats=stats
        ),
        limit=settings.linkedin_search_concurrency,
    )
    return [card for query_cards in results for card in query_cards]
