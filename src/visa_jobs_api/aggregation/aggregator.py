"""Runs every registered job source concurrently and combines their output into one digest.

To add a new producer: create a subpackage under sources/ with a class
implementing the JobSource protocol, then add an instance of it in
build_sources() below. Nothing else in this module needs to change.
"""

from __future__ import annotations

import asyncio
import html
import logging
from typing import NamedTuple

import httpx

from visa_jobs_api.config import Settings
from visa_jobs_api.shared.call_stats import CallStats
from visa_jobs_api.shared.models import NormalizedJob
from visa_jobs_api.sources import JobSource
from visa_jobs_api.sources.hn_who_is_hiring.source import HnWhoIsHiringSource
from visa_jobs_api.sources.indeed.queries import DEFAULT_KEYWORDS as INDEED_DEFAULT_KEYWORDS
from visa_jobs_api.sources.indeed.source import IndeedSource
from visa_jobs_api.sources.linkedin.queries import DEFAULT_KEYWORDS as LINKEDIN_DEFAULT_KEYWORDS
from visa_jobs_api.sources.linkedin.source import LinkedInSource

logger = logging.getLogger(__name__)

_REMOTE_OR_UNSPECIFIED = "Remote / Unspecified"


class SourceFailure(NamedTuple):
    """One source that didn't complete this run, and why."""

    source: str
    error: str


def build_sources(
    *,
    settings: Settings,
    http_client: httpx.AsyncClient,
    call_stats: CallStats,
    linkedin_keywords: str = LINKEDIN_DEFAULT_KEYWORDS,
    indeed_keywords: str = INDEED_DEFAULT_KEYWORDS,
    posted_within_hours: int = 24,
) -> list[JobSource]:
    return [
        HnWhoIsHiringSource(settings=settings, http_client=http_client, posted_within_hours=posted_within_hours),
        LinkedInSource(
            settings=settings,
            http_client=http_client,
            call_stats=call_stats,
            keywords=linkedin_keywords,
            posted_within_hours=posted_within_hours,
        ),
        IndeedSource(
            settings=settings,
            http_client=http_client,
            call_stats=call_stats,
            keywords=indeed_keywords,
            posted_within_hours=posted_within_hours,
        ),
    ]


async def collect_jobs(sources: list[JobSource]) -> tuple[list[NormalizedJob], list[SourceFailure]]:
    """Run every source concurrently, returning their combined jobs plus any per-source failures.

    A source's exception is caught, logged, and recorded as a SourceFailure
    instead of aborting the run -- one broken source (or a single bad URL
    within it, which each source already handles on its own) must never
    cost the digest the results of every other source that worked fine.
    The caller decides what to do with the returned failures (e.g. mention
    them in the digest email); this function itself never raises for a
    source's own failure.

    Sources still run as explicit tasks (not bare coroutines passed to
    gather) so that a genuine external cancellation -- e.g. the caller
    wrapping this in `asyncio.wait_for(..., timeout=...)` and that timeout
    firing -- still cancels and awaits every still-running source cleanly,
    rather than leaving them scraping in the background after the caller's
    httpx.AsyncClient is closed on the way out.
    """

    async def _run(source: JobSource) -> tuple[list[NormalizedJob], SourceFailure | None]:
        logger.info("Running source: %s", source.name)
        try:
            return await source.fetch_jobs(), None
        except Exception as exc:
            logger.exception("Source '%s' failed; continuing with the other sources", source.name)
            return [], SourceFailure(source=source.name, error=f"{type(exc).__name__}: {exc}")

    tasks = [asyncio.create_task(_run(source)) for source in sources]
    try:
        results = await asyncio.gather(*tasks)
    except BaseException:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise

    jobs: list[NormalizedJob] = []
    failures: list[SourceFailure] = []
    for source_jobs, failure in results:
        jobs.extend(source_jobs)
        if failure is not None:
            failures.append(failure)
    return jobs, failures


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


def render_digest_html(
    grouped: list[tuple[str, list[NormalizedJob]]],
    *,
    posted_within_label: str,
    failures: list[SourceFailure] | None = None,
) -> str:
    """Render the full grouped-and-sorted digest as a self-contained HTML email."""
    job_count = sum(len(jobs) for _, jobs in grouped)
    body_sections = "\n".join(_render_country_section(country, jobs) for country, jobs in grouped) or (
        '<p style="color:#666;">No visa-sponsoring postings found in this window.</p>'
    )
    headline = digest_headline(job_count, posted_within_label=posted_within_label)
    failures_section = _render_failures_section(failures or [])
    return f"""\
<html>
<body style="font-family:-apple-system,Helvetica,Arial,sans-serif;color:#222;max-width:640px;margin:0 auto;padding:16px;">
<h1 style="font-size:22px;border-bottom:2px solid #1a73e8;padding-bottom:8px;margin-bottom:8px;">{headline}</h1>
{failures_section}{body_sections}
</body>
</html>
"""


def _render_failures_section(failures: list[SourceFailure]) -> str:
    # Deliberately terse for this audience (a mailing list, not a debug
    # console): just which source(s) didn't complete, no URLs or raw
    # exception text -- those go to the server logs and the API response
    # instead (see SourceFailure.error / DigestRunResponse.source_failures).
    if not failures:
        return ""
    source_names = ", ".join(html.escape(failure.source) for failure in failures)
    return (
        '<p style="margin:0 0 16px;padding:10px 14px;background:#fff4f4;'
        'border:1px solid #e3a0a0;border-radius:6px;color:#a33;font-size:13px;">'
        f"Note: the following source(s) did not complete this run and may be missing postings below: "
        f"{source_names}."
        "</p>\n"
    )


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
