"""Wires the LinkedIn pipeline into a single async JobSource."""

from __future__ import annotations

import logging
from datetime import datetime, time, timedelta, timezone

import httpx

from visa_jobs_api.config import Settings
from visa_jobs_api.shared.call_stats import CallStats
from visa_jobs_api.shared.concurrency import gather_limited
from visa_jobs_api.shared.models import NormalizedJob
from visa_jobs_api.shared.title_filter import dedupe_cards, filter_by_title
from visa_jobs_api.shared.visa_keywords import find_visa_mentions
from visa_jobs_api.shared.visa_llm import VisaLlmError, build_client, confirm_visa_offer
from visa_jobs_api.sources.linkedin.description import fetch_all_descriptions
from visa_jobs_api.sources.linkedin.models import LinkedInJobCandidate
from visa_jobs_api.sources.linkedin.queries import DEFAULT_KEYWORDS, build_search_queries
from visa_jobs_api.sources.linkedin.search import fetch_all_job_cards

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "You are screening job postings for a report that only lists roles "
    "genuinely offering visa/immigration sponsorship. A posting counts as "
    "an offer only if the employer states or clearly implies it will "
    "sponsor a work visa, H1B, green card, or immigration process for this "
    "hire. It does NOT count if the text: requires the candidate to already "
    'have a visa/green card/citizenship/work authorization as a '
    "precondition; says they cannot, won't, or don't sponsor; or uses "
    '"sponsor"/"visa" in an unrelated sense (e.g. sponsoring a '
    "conference or open-source project).\n\n"
    'Hedged-but-affirmative phrasing still counts as an offer: "visa '
    'sponsorship possible" or "sponsorship considered for the right '
    'candidate" mean sponsorship is on the table, UNLESS paired with '
    "actual negating language."
)


class LinkedInSource:
    """Produces normalized, visa-confirmed job listings from LinkedIn's guest search API."""

    name = "linkedin"

    def __init__(
        self,
        *,
        settings: Settings,
        http_client: httpx.AsyncClient,
        call_stats: CallStats,
        keywords: str = DEFAULT_KEYWORDS,
        posted_within_hours: int = 24,
    ) -> None:
        self._settings = settings
        self._http_client = http_client
        self._llm_client = build_client(hf_token=settings.hf_token)
        self._keywords = keywords
        self._posted_within_hours = posted_within_hours
        self._call_stats = call_stats

    async def fetch_jobs(self) -> list[NormalizedJob]:
        queries = build_search_queries(self._keywords)
        cards = await fetch_all_job_cards(
            self._http_client,
            queries,
            settings=self._settings,
            posted_within_hours=self._posted_within_hours,
            stats=self._call_stats,
        )
        logger.info("%s: %d job cards scraped", self.name, len(cards))

        cards = filter_by_title(dedupe_cards(cards))
        earliest_posted_on = (datetime.now(tz=timezone.utc) - timedelta(hours=self._posted_within_hours)).date()
        cards = [card for card in cards if card.posted_on >= earliest_posted_on]
        logger.info("%s: %d cards after dedup/title-filter/recency-window", self.name, len(cards))

        candidates = await fetch_all_descriptions(
            self._http_client, cards, settings=self._settings, stats=self._call_stats
        )
        confirmed = await gather_limited(
            candidates,
            self._confirm_candidate,
            limit=self._settings.linkedin_llm_concurrency,
        )
        jobs = [job for job in confirmed if job is not None]
        logger.info(
            "%s: %d of %d candidates confirmed as genuine sponsorship offers", self.name, len(jobs), len(candidates)
        )
        return jobs

    async def _confirm_candidate(self, candidate: LinkedInJobCandidate) -> NormalizedJob | None:
        # The regex mention-finder is English-only, so for any other
        # language it's skipped entirely and every posting goes straight to
        # the LLM instead -- see shared.visa_llm and
        # linkedin_visa_scraper/DECISIONS.md for why.
        if candidate.language == "en":
            mentions = find_visa_mentions(candidate.description)
            if not mentions:
                return None
        else:
            mentions = []

        try:
            verdict = await confirm_visa_offer(
                client=self._llm_client,
                system_prompt=_SYSTEM_PROMPT,
                full_text=candidate.description,
                mentions=mentions,
                log_context=f"LinkedIn job {candidate.card.url}",
            )
        except VisaLlmError as exc:
            logger.error("LLM confirmation failed for %s -- skipping this job: %s", candidate.card.url, exc)
            return None
        if not verdict.offers_sponsorship:
            return None

        # The country a card came from is already known exactly -- it's
        # whichever SearchQuery.location produced it (see queries.py) --
        # rather than a guess parsed from LinkedIn's freeform location text,
        # which varies by country and breaks for US state abbreviations
        # ("San Francisco, CA") and comma-less metro names ("Greater
        # Pittsburgh Region") alike.
        country = candidate.card.query_country
        # There's no regex-based tech detector for LinkedIn descriptions
        # (unlike HN), so the LLM's reading of the text is the only source
        # for tech_stack here.
        return NormalizedJob(
            source=self.name,
            title=candidate.card.title,
            company=candidate.card.company,
            url=candidate.card.url,
            posted_at=datetime.combine(candidate.card.posted_on, time.min, tzinfo=timezone.utc),
            country=country,
            location_label=candidate.card.location or "Not specified",
            tech_stack=verdict.tech_stack,
            role_group=verdict.role_group,
            visa_reason=verdict.reason,
        )
