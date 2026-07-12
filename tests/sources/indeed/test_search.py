from __future__ import annotations

import httpx
import pytest

from visa_jobs_api.config import Settings
from visa_jobs_api.shared.call_stats import CallStats
from visa_jobs_api.shared.http import FetchError
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


def test_build_search_url_includes_keywords_fromage_and_start() -> None:
    url = _build_search_url("www.indeed.com", keywords='(Python) AND ("sponsor")', start=15, posted_within_hours=24)

    assert url.startswith("https://www.indeed.com/jobs?q=")
    assert "fromage=1" in url
    assert "start=15" in url
    # The Job Type/Experience Level sc= filter was removed -- broader net,
    # relying on the deterministic title-filter + LLM confirmation instead.
    assert "sc=" not in url


def test_build_search_url_omits_vjk_when_not_set() -> None:
    url = _build_search_url("www.indeed.com", keywords="Python", start=0, posted_within_hours=24)

    assert "vjk" not in url


def test_build_search_url_appends_vjk_when_set() -> None:
    url = _build_search_url(
        "www.indeed.com", keywords="Python", start=0, posted_within_hours=24, vjk="abc123"
    )

    assert url.endswith("&vjk=abc123")


async def test_fetch_all_job_cards_fetches_every_configured_page_even_after_a_partial_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Unlike LinkedIn, Indeed does NOT stop early on a partial/short page --
    # a later offset was repeatedly observed to return a full page of
    # entirely new real postings right after an earlier offset came back
    # short/empty (see search.py's module docstring). indeed_max_pages_per_query
    # is 3 by default in _settings(), so all 3 pages must be fetched.
    pages = [
        _page_html(
            [
                _card_html("1", "Job 1", "Acme", "New York, NY"),
                _card_html("2", "Job 2", "Acme", "New York, NY"),
            ]
        ),
        _page_html([_card_html("3", "Job 3", "Acme", "New York, NY")]),  # partial page -- no longer stops anything
        _page_html([_card_html("4", "Job 4", "Acme", "New York, NY")]),
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

    assert len(cards) == 4
    assert call_count == 3  # all 3 configured pages fetched, despite page 1 being partial


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
    # indeed_max_pages_per_query is 3 by default in _settings() -- and every
    # configured page is now always fetched (see the "fetches every
    # configured page" test above), so 3 pages means 3 recorded calls.
    pages = [
        _page_html(
            [
                _card_html("1", "Job 1", "Acme", "New York, NY"),
                _card_html("2", "Job 2", "Acme", "New York, NY"),
            ]
        ),
        _page_html([_card_html("3", "Job 3", "Acme", "New York, NY")]),
        _page_html([_card_html("4", "Job 4", "Acme", "New York, NY")]),
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

    assert stats.indeed_search_calls == {"United States": 3}


async def test_fetch_all_job_cards_keeps_going_past_a_page_that_fails_to_fetch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A page that never loads after retries must not cost the whole query
    # (let alone the whole source) the cards its earlier pages already
    # found -- and, unlike the old behavior, must not stop the remaining
    # configured pages from being tried either (see search.py's module
    # docstring for why). indeed_max_pages_per_query is 3 by default, so
    # all 3 pages are attempted even though pages 2 and 3 both fail.
    first_page = _page_html(
        [_card_html("1", "Job 1", "Acme", "New York, NY"), _card_html("2", "Job 2", "Acme", "New York, NY")]
    )
    call_count = 0

    async def fake_fetch_html(client, url, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return first_page
        raise FetchError(f"failed to fetch {url!r} after retries")

    monkeypatch.setattr("visa_jobs_api.sources.indeed.search.fetch_html", fake_fetch_html)

    settings = _settings()
    async with httpx.AsyncClient() as client:
        cards = await fetch_all_job_cards(
            client, [SearchQuery(keywords="Python", country="United States")], settings=settings, posted_within_hours=24
        )

    assert len(cards) == 2
    assert call_count == 3  # all 3 configured pages were attempted, not just the first


async def test_fetch_all_job_cards_continues_other_queries_when_one_querys_page_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_fetch_html(client, url, **kwargs):
        if url.startswith("https://www.indeed.com/"):  # United States' domain -- see country_domains.py
            raise FetchError("boom")
        return _page_html([_card_html("1", "Job 1", "Acme", "Dublin")])

    monkeypatch.setattr("visa_jobs_api.sources.indeed.search.fetch_html", fake_fetch_html)

    settings = _settings(indeed_max_pages_per_query=1)
    async with httpx.AsyncClient() as client:
        cards = await fetch_all_job_cards(
            client,
            [
                SearchQuery(keywords="Python", country="United States"),
                SearchQuery(keywords="Python", country="Ireland"),
            ],
            settings=settings,
            posted_within_hours=24,
        )

    assert len(cards) == 1
