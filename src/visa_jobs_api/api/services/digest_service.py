"""Business logic for a digest run: orchestrates the aggregator and emailer.

Kept separate from the router (HTTP concerns) and the aggregator (source
orchestration) so each layer has one job: the router translates HTTP
<-> DTOs, this service coordinates the steps of a run, and the aggregator
only knows about sources and NormalizedJob.
"""

from __future__ import annotations

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
from visa_jobs_api.shared.models import NormalizedJob
from visa_jobs_api.sources.linkedin.queries import DEFAULT_KEYWORDS

logger = logging.getLogger(__name__)


async def run_digest(
    *, settings: Settings, linkedin_keywords: str = DEFAULT_KEYWORDS, posted_within_hours: int = 24
) -> DigestRunResponse:
    """Run both sources in parallel, aggregate+group+sort, email the digest, and summarize the run.

    Any failure anywhere in the pipeline is reported via a failure email
    (mirroring job_digest's cli.py "never fail silently on an unattended
    run" pattern) before the exception is re-raised, so a caller triggering
    this over HTTP still sees an error response rather than a false
    "success".
    """
    try:
        async with httpx.AsyncClient() as http_client:
            sources = build_sources(
                settings=settings,
                http_client=http_client,
                linkedin_keywords=linkedin_keywords,
                posted_within_hours=posted_within_hours,
            )
            jobs = await collect_jobs(sources)

        grouped = group_and_sort_by_country(jobs)
        subject = digest_headline(len(jobs), posted_within_hours=posted_within_hours)
        html_body = render_digest_html(grouped, posted_within_hours=posted_within_hours)

        await send_success_email(settings=settings, subject=subject, html_body=html_body)
        logger.info("Digest sent successfully: %s", subject)

        return DigestRunResponse(
            total_jobs=len(jobs),
            countries=len(grouped),
            by_source=_count_by_source(jobs),
            email_sent_to=settings.recipient_list(),
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
