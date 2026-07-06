"""Runs every registered job source concurrently and combines their output into one digest.

To add a new producer: create a subpackage under sources/ with a class
implementing the JobSource protocol, then add an instance of it in
build_sources() below. Nothing else in this module needs to change.
"""

from __future__ import annotations

import asyncio
import html
import logging

import httpx

from visa_jobs_api.config import Settings
from visa_jobs_api.shared.models import NormalizedJob
from visa_jobs_api.sources import JobSource
from visa_jobs_api.sources.hn_who_is_hiring.source import HnWhoIsHiringSource
from visa_jobs_api.sources.linkedin.queries import DEFAULT_KEYWORDS
from visa_jobs_api.sources.linkedin.source import LinkedInSource

logger = logging.getLogger(__name__)

_REMOTE_OR_UNSPECIFIED = "Remote / Unspecified"


class DigestBuildError(RuntimeError):
    """Raised when a source fails to produce its job listings."""


def build_sources(
    *,
    settings: Settings,
    http_client: httpx.AsyncClient,
    linkedin_keywords: str = DEFAULT_KEYWORDS,
    posted_within_hours: int = 24,
) -> list[JobSource]:
    return [
        HnWhoIsHiringSource(settings=settings, http_client=http_client, posted_within_hours=posted_within_hours),
        LinkedInSource(
            settings=settings,
            http_client=http_client,
            keywords=linkedin_keywords,
            posted_within_hours=posted_within_hours,
        ),
    ]


async def collect_jobs(sources: list[JobSource]) -> list[NormalizedJob]:
    """Run every source concurrently, returning their combined, still-ungrouped job lists.

    A source's internal exception is caught only to tag it with which
    source produced it before re-raising -- the whole run is meant to fail
    loudly (and be reported by the caller) rather than silently send a
    digest missing a broken source's postings.
    """

    async def _run(source: JobSource) -> list[NormalizedJob]:
        logger.info("Running source: %s", source.name)
        try:
            return await source.fetch_jobs()
        except Exception as exc:
            raise DigestBuildError(f"source '{source.name}' failed: {exc}") from exc

    results = await asyncio.gather(*(_run(source) for source in sources))
    return [job for jobs in results for job in jobs]


def group_and_sort_by_country(jobs: list[NormalizedJob]) -> list[tuple[str, list[NormalizedJob]]]:
    """Group jobs by country (alphabetical order), sorting each country's jobs by posted_at descending.

    Jobs with no resolvable country (see shared.models.NormalizedJob) are
    bucketed under an explicit "Remote / Unspecified" group rather than
    being dropped or guessed into a real country.
    """
    by_country: dict[str, list[NormalizedJob]] = {}
    for job in jobs:
        by_country.setdefault(job.country or _REMOTE_OR_UNSPECIFIED, []).append(job)

    for country_jobs in by_country.values():
        country_jobs.sort(key=lambda job: job.posted_at, reverse=True)

    return [(country, by_country[country]) for country in sorted(by_country)]


def digest_headline(job_count: int, *, posted_within_label: str) -> str:
    return f"{job_count} Visa-Sponsoring Jobs Posted in the Last {posted_within_label}"


def render_digest_html(grouped: list[tuple[str, list[NormalizedJob]]], *, posted_within_label: str) -> str:
    """Render the full grouped-and-sorted digest as a self-contained HTML email."""
    job_count = sum(len(jobs) for _, jobs in grouped)
    body_sections = "\n".join(_render_country_section(country, jobs) for country, jobs in grouped) or (
        '<p style="color:#666;">No visa-sponsoring postings found in this window.</p>'
    )
    headline = digest_headline(job_count, posted_within_label=posted_within_label)
    return f"""\
<html>
<body style="font-family:-apple-system,Helvetica,Arial,sans-serif;color:#222;max-width:640px;margin:0 auto;padding:16px;">
<h1 style="font-size:22px;border-bottom:2px solid #1a73e8;padding-bottom:8px;margin-bottom:8px;">{headline}</h1>
{body_sections}
</body>
</html>
"""


def _render_country_section(country: str, jobs: list[NormalizedJob]) -> str:
    parts = [f'<h2 style="margin:24px 0 6px;font-size:18px;color:#111;">{html.escape(country)}</h2>']
    parts.append('<ul style="margin:0 0 8px;padding-left:20px;">')
    for job in jobs:
        tech = ", ".join(job.tech_stack) if job.tech_stack else "not mentioned"
        date_label = job.posted_at.strftime("%b %d, %Y")
        parts.append(
            '<li style="margin-bottom:14px;line-height:1.4;">'
            f'<a href="{html.escape(job.url)}" style="color:#1a73e8;text-decoration:none;font-weight:600;">'
            f"{html.escape(job.title)}</a> &mdash; {html.escape(job.company)}<br>"
            f'<span style="color:#555;font-size:13px;">Posted: {date_label} &middot; '
            f'{html.escape(job.location_label)} &middot; {html.escape(job.role_group)}</span><br>'
            f'<span style="color:#333;">Tech: {html.escape(tech)}</span><br>'
            f'<span style="color:#777;font-style:italic;">&ldquo;{html.escape(job.visa_reason)}&rdquo;</span>'
            "</li>"
        )
    parts.append("</ul>")
    return "\n".join(parts)
