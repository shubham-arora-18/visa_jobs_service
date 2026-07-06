from __future__ import annotations

from datetime import datetime, timezone

import pytest

from visa_jobs_api.aggregation.aggregator import (
    DigestBuildError,
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

    jobs = await collect_jobs(sources)

    assert {job.title for job in jobs} == {"A1", "B1", "B2"}


async def test_collect_jobs_tags_failure_with_source_name() -> None:
    sources = [_FakeSource("broken", error=ValueError("network down"))]

    with pytest.raises(DigestBuildError, match="source 'broken' failed: network down"):
        await collect_jobs(sources)


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
