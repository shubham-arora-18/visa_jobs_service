"""Wires the HN 'Who is hiring?' pipeline into a single async JobSource."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import httpx

from visa_jobs_api.config import Settings
from visa_jobs_api.shared.concurrency import gather_limited
from visa_jobs_api.shared.models import NormalizedJob
from visa_jobs_api.shared.visa_llm import build_client, confirm_visa_offer
from visa_jobs_api.sources.hn_who_is_hiring.discover import find_latest_who_is_hiring_item_id
from visa_jobs_api.sources.hn_who_is_hiring.extract import HnJobCandidate, extract_candidates
from visa_jobs_api.sources.hn_who_is_hiring.fetch import fetch_item_json
from visa_jobs_api.sources.hn_who_is_hiring.parse import parse_job_post

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    'You are screening Hacker News "Who is hiring" job postings for a report '
    "that only lists companies genuinely offering visa/immigration sponsorship. "
    "A posting counts as an offer only if the company states or clearly implies "
    "it will sponsor a work visa, H1B, green card, or immigration process for "
    "this hire. It does NOT count if the text: requires the candidate to already "
    'have a visa/green card/citizenship/work authorization as a precondition; '
    "says they cannot, won't, or don't sponsor; or uses \"sponsor\"/\"visa\" in an "
    "unrelated sense (e.g. sponsoring a conference, healthcare benefits, or the "
    "Visa payment network).\n\n"
    'Hedged-but-affirmative phrasing still counts as an offer: short header tags '
    'like "VISA possible", "visa sponsorship possible", or "sponsorship '
    'possible" mean sponsorship is on the table and should be treated the same '
    'as an unhedged "visa sponsorship available", UNLESS that hedge is paired '
    'with actual negating language (e.g. "sponsorship may not be possible for '
    'all roles", "probably can\'t sponsor"). Don\'t require a firm guarantee -- '
    'a plain, undisputed "possible" on its own is an offer, not a rejection.\n\n'
    "Sometimes the posting itself doesn't mention sponsorship, but a reply "
    "underneath it does (e.g. someone asks about it and the poster confirms). "
    "You may be shown one or more such reply comments below the posting -- "
    "treat a clear, non-question statement there the same as if it were in "
    "the posting itself."
)


class HnWhoIsHiringSource:
    """Produces normalized, visa-confirmed job listings from the current HN 'Who is hiring?' thread."""

    name = "hn_who_is_hiring"

    def __init__(self, *, settings: Settings, http_client: httpx.AsyncClient) -> None:
        self._settings = settings
        self._http_client = http_client
        self._llm_client = build_client(hf_token=settings.hf_token)

    async def fetch_jobs(self) -> list[NormalizedJob]:
        item_id = await find_latest_who_is_hiring_item_id(self._http_client)
        item_payload = await fetch_item_json(self._http_client, item_id)
        job_post = parse_job_post(item_payload)

        earliest_posted_at = datetime.now(tz=timezone.utc) - timedelta(hours=self._settings.job_posted_within_hours)
        candidates = extract_candidates(job_post, earliest_posted_at=earliest_posted_at)
        logger.info(
            "%s: %d candidates posted in the last %dh",
            self.name,
            len(candidates),
            self._settings.job_posted_within_hours,
        )

        confirmed = await gather_limited(
            candidates,
            self._confirm_candidate,
            limit=self._settings.hn_llm_concurrency,
        )
        jobs = [job for job in confirmed if job is not None]
        logger.info("%s: %d of %d candidates confirmed as genuine sponsorship offers", self.name, len(jobs), len(candidates))
        return jobs

    async def _confirm_candidate(self, candidate: HnJobCandidate) -> NormalizedJob | None:
        verdict = await confirm_visa_offer(
            client=self._llm_client,
            system_prompt=_SYSTEM_PROMPT,
            full_text=candidate.posting_text,
            mentions=candidate.visa_mentions,
            supporting_context=candidate.supporting_replies,
            log_context=f"HN comment {candidate.comment_id}",
        )
        if not verdict.offers_sponsorship:
            logger.info(
                "HN candidate %d (%s) rejected by LLM: %s", candidate.comment_id, candidate.company, verdict.reason
            )
            return None
        return NormalizedJob(
            source=self.name,
            title=candidate.role_line,
            company=candidate.company,
            url=candidate.url,
            posted_at=candidate.posted_at,
            country=verdict.country,
            location_label=candidate.location or "Not specified",
            tech_stack=candidate.tech_stack,
            visa_reason=verdict.reason,
        )
