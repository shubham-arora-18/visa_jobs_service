from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from visa_jobs_api.aggregation.aggregator import (
    SourceFailure,
    collect_jobs,
    digest_headline,
    group_and_sort_by_country,
    render_digest_html,
)
from visa_jobs_api.shared.models import NormalizedJob


def _job(
    *,
    source: str = "hn_who_is_hiring",
    title: str = "Backend Engineer",
    company: str = "Acme",
    country: str | None = "Ireland",
    posted_at: datetime | None = None,
    tech_stack: list[str] | None = None,
    role_group: str = "Other",
) -> NormalizedJob:
    return NormalizedJob(
        source=source,
        title=title,
        company=company,
        url=f"https://x/{title}",
        posted_at=posted_at or datetime(2026, 7, 4, tzinfo=timezone.utc),
        country=country,
        location_label=country or "Not specified",
        tech_stack=tech_stack or [],
        role_group=role_group,
        visa_reason="explicit offer",
    )


class _FakeSource:
    def __init__(self, name: str, jobs: list[NormalizedJob] | None = None, error: Exception | None = None) -> None:
        self.name = name
        self._jobs = jobs
        self._error = error

    async def fetch_jobs(self) -> list[NormalizedJob]:
        if self._error is not None:
            raise self._error
        assert self._jobs is not None
        return self._jobs


async def test_collect_jobs_runs_every_source_and_merges_results() -> None:
    sources = [_FakeSource("a", jobs=[_job(title="A1")]), _FakeSource("b", jobs=[_job(title="B1"), _job(title="B2")])]

    jobs, failures = await collect_jobs(sources)

    assert {job.title for job in jobs} == {"A1", "B1", "B2"}
    assert failures == []


async def test_collect_jobs_continues_with_other_sources_when_one_fails() -> None:
    # A single broken source (or a bad URL within it, which each source
    # already handles on its own) must never cost the digest the results
    # of every other source that worked fine.
    sources = [
        _FakeSource("working", jobs=[_job(title="Kept")]),
        _FakeSource("broken", error=ValueError("network down")),
    ]

    jobs, failures = await collect_jobs(sources)

    assert {job.title for job in jobs} == {"Kept"}
    assert failures == [SourceFailure(source="broken", error="ValueError: network down")]


async def test_collect_jobs_lets_other_sources_run_to_completion_when_one_fails() -> None:
    # Unlike a fail-fast contract, a source failing must not cancel its
    # still-running siblings -- they should be allowed to finish and
    # contribute their jobs to the digest.
    ran_to_completion = False

    class _SlowSource:
        name = "slow"

        async def fetch_jobs(self) -> list[NormalizedJob]:
            nonlocal ran_to_completion
            await asyncio.sleep(0.05)
            ran_to_completion = True
            return []

    sources = [_SlowSource(), _FakeSource("broken", error=ValueError("boom"))]

    jobs, failures = await collect_jobs(sources)

    assert ran_to_completion is True
    assert jobs == []
    assert failures == [SourceFailure(source="broken", error="ValueError: boom")]


async def test_collect_jobs_cancels_still_running_sources_on_external_cancellation() -> None:
    # A genuine external cancellation (e.g. the caller's overall run
    # timeout firing) is not a per-source failure -- it must still cancel
    # and await every still-running source cleanly rather than leave them
    # scraping in the background.
    ran_to_completion = False

    class _SlowSource:
        name = "slow"

        async def fetch_jobs(self) -> list[NormalizedJob]:
            nonlocal ran_to_completion
            await asyncio.sleep(0.2)
            ran_to_completion = True
            return []

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(collect_jobs([_SlowSource()]), timeout=0.05)

    await asyncio.sleep(0.2)
    assert ran_to_completion is False


def test_group_and_sort_by_country_orders_countries_alphabetically() -> None:
    jobs = [_job(country="Singapore"), _job(country="Ireland"), _job(country="Germany")]

    grouped = group_and_sort_by_country(jobs)

    assert [country for country, _ in grouped] == ["Germany", "Ireland", "Singapore"]


def test_group_and_sort_by_country_sorts_jobs_within_a_country_by_date_desc() -> None:
    older = _job(country="Ireland", title="Older", posted_at=datetime(2026, 7, 1, tzinfo=timezone.utc))
    newer = _job(country="Ireland", title="Newer", posted_at=datetime(2026, 7, 4, tzinfo=timezone.utc))

    grouped = group_and_sort_by_country([older, newer])

    assert [job.title for _, jobs in grouped for job in jobs] == ["Newer", "Older"]


def test_group_and_sort_by_country_buckets_missing_country_as_remote_unspecified() -> None:
    jobs = [_job(country=None), _job(country="Ireland")]

    grouped = group_and_sort_by_country(jobs)

    countries = [country for country, _ in grouped]
    assert "Remote / Unspecified" in countries


def test_digest_headline_includes_count_and_window() -> None:
    headline = digest_headline(5, posted_within_label="1 Day")
    assert headline == "5 Visa-Sponsoring Jobs Posted in the Last 1 Day"


def test_digest_headline_shows_week_label_for_week_window() -> None:
    headline = digest_headline(5, posted_within_label="1 Week")
    assert headline == "5 Visa-Sponsoring Jobs Posted in the Last 1 Week"


def test_render_digest_html_includes_headline_and_country_sections() -> None:
    jobs = [_job(country="Ireland", title="Backend Role")]
    grouped = group_and_sort_by_country(jobs)

    html_body = render_digest_html(grouped, posted_within_label="1 Day")

    assert digest_headline(1, posted_within_label="1 Day") in html_body
    assert "Ireland" in html_body
    assert "Backend Role" in html_body


def test_render_digest_html_shows_tech_stack_and_role_group() -> None:
    jobs = [_job(tech_stack=["Python", "Django"], role_group="Backend")]
    grouped = group_and_sort_by_country(jobs)

    html_body = render_digest_html(grouped, posted_within_label="1 Day")

    assert "Python, Django" in html_body
    assert "Backend" in html_body


def test_render_digest_html_shows_not_mentioned_only_when_tech_stack_truly_empty() -> None:
    jobs = [_job(tech_stack=[])]
    grouped = group_and_sort_by_country(jobs)

    html_body = render_digest_html(grouped, posted_within_label="1 Day")

    assert "Tech: not mentioned" in html_body


def test_render_digest_html_shows_a_message_when_there_are_no_jobs() -> None:
    html_body = render_digest_html([], posted_within_label="1 Day")

    assert "No visa-sponsoring postings found" in html_body


def test_render_digest_html_omits_failures_section_when_there_are_no_failures() -> None:
    html_body = render_digest_html([], posted_within_label="1 Day", failures=[])

    assert "did not complete" not in html_body


def test_render_digest_html_mentions_only_the_failed_source_names_not_error_details_or_links() -> None:
    # Deliberately terse for this audience (a mailing list, not a debug
    # console) -- only source names, never the raw error text or a URL.
    failures = [SourceFailure(source="indeed", error="FetchError: failed to fetch 'https://x/1' after retries")]

    html_body = render_digest_html([], posted_within_label="1 Day", failures=failures)

    assert "indeed" in html_body
    assert "did not complete" in html_body
    assert "FetchError" not in html_body
    assert "https://x/1" not in html_body


def test_render_digest_html_lists_multiple_failed_sources() -> None:
    failures = [
        SourceFailure(source="indeed", error="boom"),
        SourceFailure(source="linkedin", error="bang"),
    ]

    html_body = render_digest_html([], posted_within_label="1 Day", failures=failures)

    assert "indeed" in html_body
    assert "linkedin" in html_body
