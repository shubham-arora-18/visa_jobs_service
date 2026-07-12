"""Business logic for a digest run: orchestrates the aggregator and emailer.

Kept separate from the router (HTTP concerns) and the aggregator (source
orchestration) so each layer has one job: the router translates HTTP
<-> DTOs, this service coordinates the steps of a run, and the aggregator
only knows about sources and NormalizedJob.
"""

from __future__ import annotations

import asyncio
import logging

import httpx

from visa_jobs_api.aggregation.aggregator import (
    build_sources,
    collect_jobs,
    digest_headline,
    group_and_sort_by_country,
    render_digest_html,
)
from visa_jobs_api.api.dto.digest import DigestRunResponse, SourceCount
from visa_jobs_api.config import Settings
from visa_jobs_api.emailer import send_failure_email, send_success_email
from visa_jobs_api.shared.call_stats import CallStats
from visa_jobs_api.shared.models import NormalizedJob
from visa_jobs_api.sources.indeed.queries import DEFAULT_KEYWORDS as INDEED_DEFAULT_KEYWORDS
from visa_jobs_api.sources.linkedin.queries import DEFAULT_KEYWORDS as LINKEDIN_DEFAULT_KEYWORDS

logger = logging.getLogger(__name__)


async def run_digest(
    *,
    settings: Settings,
    linkedin_keywords: str = LINKEDIN_DEFAULT_KEYWORDS,
    indeed_keywords: str = INDEED_DEFAULT_KEYWORDS,
    posted_within_hours: int = 24,
    posted_within_label: str = "1 Day",
) -> DigestRunResponse:
    """Run all sources in parallel, aggregate+group+sort, email the digest, and summarize the run.

    A source failing (or a single URL within it) never blocks the digest
    from being sent -- collect_jobs() already turns those into
    SourceFailure entries instead of raising, and this function folds them
    into the email (see render_digest_html) and the response
    (source_failures) rather than treating them as run-ending errors. Only
    a genuinely catastrophic, non-source-attributable failure (e.g. the
    overall run timing out, or the email step itself erroring) reaches the
    except block below, which still reports it via a failure email
    (mirroring job_digest's cli.py "never fail silently on an unattended
    run" pattern) before re-raising, so a caller triggering this over HTTP
    still sees an error response rather than a false "success".
    """
    call_stats = CallStats()
    try:
        async with httpx.AsyncClient() as http_client:
            sources = build_sources(
                settings=settings,
                http_client=http_client,
                call_stats=call_stats,
                linkedin_keywords=linkedin_keywords,
                indeed_keywords=indeed_keywords,
                posted_within_hours=posted_within_hours,
            )
            # Logged even if a source fails partway through (finally, not
            # after) -- this is exactly when knowing how many Bright
            # Data/Decodo calls had already gone out is most useful.
            try:
                jobs, failures = await asyncio.wait_for(
                    collect_jobs(sources), timeout=settings.digest_run_timeout_seconds
                )
            finally:
                call_stats.log_summary(logger)

        for failure in failures:
            logger.error("Source '%s' did not complete this run: %s", failure.source, failure.error)

        grouped = group_and_sort_by_country(jobs)
        subject = digest_headline(len(jobs), posted_within_label=posted_within_label)
        html_body = render_digest_html(grouped, posted_within_label=posted_within_label, failures=failures)

        await send_success_email(settings=settings, subject=subject, html_body=html_body)
        logger.info("Digest sent successfully: %s", subject)

        return DigestRunResponse(
            total_jobs=len(jobs),
            countries=len(grouped),
            by_source=_count_by_source(jobs),
            email_sent_to=settings.recipient_list(),
            source_failures=[f"{failure.source}: {failure.error}" for failure in failures],
        )
    except Exception as exc:
        logger.exception("Digest run failed")
        await send_failure_email(settings=settings, reason=f"{type(exc).__name__}: {exc}")
        raise


def _count_by_source(jobs: list[NormalizedJob]) -> list[SourceCount]:
    counts: dict[str, int] = {}
    for job in jobs:
        counts[job.source] = counts.get(job.source, 0) + 1
    return [SourceCount(source=source, confirmed_jobs=count) for source, count in sorted(counts.items())]
