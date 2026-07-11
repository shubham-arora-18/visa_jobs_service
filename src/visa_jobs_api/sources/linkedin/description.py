"""Fetches and parses a single LinkedIn job posting's full description page.

A page that loads successfully but has no recognizable description block is
logged and skipped for that one job (it's more likely a removed/paywalled
listing than a systemic problem) -- but this must never be silently treated
as "job has no description" the way it previously was (see
linkedin_visa_scraper/DECISIONS.md: that exact silent-fallback bug made an
entire run's LinkedIn results look like zero jobs offered sponsorship, when
in fact every description fetch had been rate-limited/blocked and simply
never got real content to check). A network-level fetch failure
(shared.http.FetchError) is not caught here at all -- it propagates and
aborts the whole source's run, consistent with the JobSource contract.
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
from visa_jobs_api.sources.linkedin.models import JobCard, LinkedInJobCandidate

logger = logging.getLogger(__name__)


def _extract_description_text(html: str) -> str | None:
    soup = BeautifulSoup(html, "html.parser")
    div = soup.find("div", class_="description__text description__text--rich")
    if div is None:
        return None

    for element in div.find_all(["span", "a"]):
        element.decompose()
    for ul in div.find_all("ul"):
        for li in ul.find_all("li"):
            li.insert(0, "-")

    text = div.get_text(separator="\n").strip()
    text = text.replace("\n\n", "")
    text = text.replace("::marker", "-")
    text = text.replace("-\n", "- ")
    return text.replace("Show less", "").replace("Show more", "")


def _detect_language(text: str) -> str:
    try:
        return detect_language(text)
    except LangDetectException:
        return "en"


async def _fetch_one_description(
    client: httpx.AsyncClient, card: JobCard, *, settings: Settings, stats: CallStats
) -> LinkedInJobCandidate | None:
    html = await fetch_html(
        client,
        card.url,
        via_decodo=True,
        decodo_username=settings.decodo_username,
        decodo_password=settings.decodo_password,
    )
    stats.record_linkedin_description_call(card.query_country)
    description = _extract_description_text(html)
    if description is None:
        logger.warning("No description block found for %s -- skipping", card.url)
        return None
    return LinkedInJobCandidate(card=card, description=description, language=_detect_language(description))


async def fetch_all_descriptions(
    client: httpx.AsyncClient, cards: list[JobCard], *, settings: Settings, stats: CallStats | None = None
) -> list[LinkedInJobCandidate]:
    """Fetch every card's full description, at most `linkedin_description_concurrency` at a time."""
    stats = stats if stats is not None else CallStats()
    results = await gather_limited(
        cards,
        lambda card: _fetch_one_description(client, card, settings=settings, stats=stats),
        limit=settings.linkedin_description_concurrency,
    )
    return [candidate for candidate in results if candidate is not None]
