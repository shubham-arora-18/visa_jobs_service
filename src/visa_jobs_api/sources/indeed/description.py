"""Fetches and parses a single Indeed job posting's full description page.

The description text lives in a single `<div id="jobDescriptionText">` --
confirmed live against a real job page (see
indeed_scraper_experiment/DECISIONS.md). A page that loads successfully
but has no recognizable description block is logged and skipped for that
one job, not silently treated as "job has no description" -- see
sources/linkedin/description.py's docstring for why that distinction
matters (a real production bug this exact pattern guards against).
"""

from __future__ import annotations

import logging

import httpx
from bs4 import BeautifulSoup
from langdetect import detect as detect_language
from langdetect.lang_detect_exception import LangDetectException

from visa_jobs_api.config import Settings
from visa_jobs_api.shared.call_stats import CallStats
from visa_jobs_api.shared.concurrency import gather_limited
from visa_jobs_api.shared.http import fetch_html
from visa_jobs_api.sources.indeed.country_domains import COUNTRY_DOMAINS
from visa_jobs_api.sources.indeed.models import IndeedJobCandidate, JobCard

logger = logging.getLogger(__name__)


def _extract_description_text(html: str) -> str | None:
    soup = BeautifulSoup(html, "html.parser")
    div = soup.find("div", id="jobDescriptionText")
    if div is None:
        return None
    return div.get_text(separator="\n", strip=True)


def _detect_language(text: str) -> str:
    try:
        return detect_language(text)
    except LangDetectException:
        return "en"


async def _fetch_one_description(
    client: httpx.AsyncClient, card: JobCard, *, settings: Settings, stats: CallStats
) -> IndeedJobCandidate | None:
    _, geo = COUNTRY_DOMAINS[card.query_country]
    html = await fetch_html(
        client,
        card.url,
        via_brightdata=True,
        brightdata_api_key=settings.brightdata_api_key,
        brightdata_zone=settings.brightdata_zone,
        brightdata_country=geo,
    )
    stats.record_indeed_description_call(card.query_country)
    description = _extract_description_text(html)
    if description is None:
        logger.warning("No description block found for %s -- skipping", card.url)
        return None
    return IndeedJobCandidate(card=card, description=description, language=_detect_language(description))


async def fetch_all_descriptions(
    client: httpx.AsyncClient, cards: list[JobCard], *, settings: Settings, stats: CallStats | None = None
) -> list[IndeedJobCandidate]:
    """Fetch every card's full description, at most `indeed_description_concurrency` at a time."""
    stats = stats if stats is not None else CallStats()
    results = await gather_limited(
        cards,
        lambda card: _fetch_one_description(client, card, settings=settings, stats=stats),
        limit=settings.indeed_description_concurrency,
    )
    return [candidate for candidate in results if candidate is not None]
