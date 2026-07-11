from __future__ import annotations

import httpx
import pytest

from visa_jobs_api.config import Settings
from visa_jobs_api.shared.call_stats import CallStats
from visa_jobs_api.sources.indeed.models import SearchQuery
from visa_jobs_api.sources.indeed.search import _build_search_url, _fromage_for_hours, fetch_all_job_cards


def _card_html(job_key: str, title: str, company: str, location: str) -> str:
    return f"""
    <div class="job_seen_beacon">
      <h3 class="jobTitle"><a data-jk="{job_key}"><span title="{title}">{title}</span></a></h3>
      <span data-testid="company-name">{company}</span>
      <div data-testid="text-location">{location}</div>
    </div>
    """


def _page_html(cards: list[str]) -> str:
    return f"<div>{''.join(cards)}</div>"


def _settings(**overrides: object) -> Settings:
    defaults: dict[str, object] = dict(
        hf_token="fake",
        gmail_address="a@b.com",
        gmail_app_password="pw",
        digest_recipients="me@example.com",
        decodo_username="decodo-user",
        decodo_password="decodo-pass",
        brightdata_api_key="bd-key",
        brightdata_zone="bd-zone",
        indeed_posts_per_page=2,
        indeed_max_pages_per_query=3,
        indeed_search_concurrency=5,
    )
    defaults.update(overrides)
    return Settings(**defaults)


def test_fromage_for_hours_maps_a_day_to_1() -> None:
    assert _fromage_for_hours(24) == 1


def test_fromage_for_hours_maps_a_week_to_7() -> None:
    assert _fromage_for_hours(24 * 7) == 7


def test_fromage_for_hours_maps_a_month_to_the_max_supported_value_14() -> None:
    # Indeed's own fromage filter has no 30-day option -- 14 is the
    # longest window it exposes, so a month-long request is capped there.
    assert _fromage_for_hours(24 * 30) == 14


def test_fromage_for_hours_rounds_up_to_the_next_supported_option() -> None:
    # 2 days doesn't match any exact option -- rounds up to 3, not down to 1.
    assert _fromage_for_hours(24 * 2) == 3


def test_build_search_url_includes_keywords_fromage_and_job_type_experience_filter() -> None:
    url = _build_search_url("www.indeed.com", keywords='(Python) AND ("sponsor")', start=15, posted_within_hours=24)

    assert url.startswith("https://www.indeed.com/jobs?q=")
    assert "fromage=1" in url
    assert "start=15" in url
    # The Job Type (Full-time/Permanent) + Experience Level (Senior) filter
    # the user selected directly in Indeed's own UI and provided as a
    # requirement -- see search.py's _SC_FILTER constant.
    assert "sc=0kf" in url


async def test_fetch_all_job_cards_stops_after_a_partial_page(monkeypatch: pytest.MonkeyPatch) -> None:
    pages = [
        _page_html(
            [
                _card_html("1", "Job 1", "Acme", "New York, NY"),
                _card_html("2", "Job 2", "Acme", "New York, NY"),
            ]
        ),
        _page_html([_card_html("3", "Job 3", "Acme", "New York, NY")]),
    ]
    call_count = 0

    async def fake_fetch_html(client, url, **kwargs):
        nonlocal call_count
        page = pages[call_count]
        call_count += 1
        return page

    monkeypatch.setattr("visa_jobs_api.sources.indeed.search.fetch_html", fake_fetch_html)

    settings = _settings()
    async with httpx.AsyncClient() as client:
        cards = await fetch_all_job_cards(
            client, [SearchQuery(keywords="Python", country="United States")], settings=settings, posted_within_hours=24
        )

    assert len(cards) == 3
    assert call_count == 2  # never fetched the (nonexistent) page 2


async def test_fetch_all_job_cards_retries_a_suspicious_empty_page_before_trusting_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Real bug this guards against: a live re-fetch with identical params
    # returned real cards moments after an earlier attempt returned 0 --
    # see indeed_scraper_experiment/DECISIONS.md.
    responses = ["<html>blocked or empty page</html>", _page_html([_card_html("1", "Job 1", "Acme", "NY")])]
    call_count = 0

    async def fake_fetch_html(client, url, **kwargs):
        nonlocal call_count
        html = responses[call_count]
        call_count += 1
        return html

    monkeypatch.setattr("visa_jobs_api.sources.indeed.search.fetch_html", fake_fetch_html)

    settings = _settings(indeed_max_pages_per_query=1)
    async with httpx.AsyncClient() as client:
        cards = await fetch_all_job_cards(
            client, [SearchQuery(keywords="Python", country="United States")], settings=settings, posted_within_hours=24
        )

    assert call_count == 2
    assert len(cards) == 1


async def test_fetch_all_job_cards_accepts_zero_after_exhausting_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    call_count = 0

    async def fake_fetch_html(client, url, **kwargs):
        nonlocal call_count
        call_count += 1
        return "<html>blocked or empty page</html>"

    monkeypatch.setattr("visa_jobs_api.sources.indeed.search.fetch_html", fake_fetch_html)

    settings = _settings(indeed_max_pages_per_query=1)
    async with httpx.AsyncClient() as client:
        cards = await fetch_all_job_cards(
            client, [SearchQuery(keywords="Python", country="United States")], settings=settings, posted_within_hours=24
        )

    assert call_count == 2  # both retry attempts exhausted, 0 accepted as final
    assert cards == []


async def test_fetch_all_job_cards_records_one_search_call_per_page_fetched(monkeypatch: pytest.MonkeyPatch) -> None:
    pages = [
        _page_html(
            [
                _card_html("1", "Job 1", "Acme", "New York, NY"),
                _card_html("2", "Job 2", "Acme", "New York, NY"),
            ]
        ),
        _page_html([_card_html("3", "Job 3", "Acme", "New York, NY")]),
    ]
    call_count = 0

    async def fake_fetch_html(client, url, **kwargs):
        nonlocal call_count
        page = pages[call_count]
        call_count += 1
        return page

    monkeypatch.setattr("visa_jobs_api.sources.indeed.search.fetch_html", fake_fetch_html)

    settings = _settings()
    stats = CallStats()
    async with httpx.AsyncClient() as client:
        await fetch_all_job_cards(
            client,
            [SearchQuery(keywords="Python", country="United States")],
            settings=settings,
            posted_within_hours=24,
            stats=stats,
        )

    assert stats.indeed_search_calls == {"United States": 2}
