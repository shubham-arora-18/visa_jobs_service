from __future__ import annotations

from datetime import date

import httpx
import pytest

from visa_jobs_api.config import Settings
from visa_jobs_api.shared.call_stats import CallStats
from visa_jobs_api.shared.http import FetchError
from visa_jobs_api.sources.linkedin.models import SearchQuery
from visa_jobs_api.sources.linkedin.search import _parse_job_cards, fetch_all_job_cards


def _card_html(job_id: str, title: str, company: str, location: str, posted_on: str) -> str:
    return f"""
    <li>
      <div data-entity-urn="urn:li:jobPosting:{job_id}">
        <div class="base-search-card__info">
          <h3>{title}</h3>
          <a class="hidden-nested-link">{company}</a>
          <span class="job-search-card__location">{location}</span>
          <time datetime="{posted_on}"></time>
        </div>
      </div>
    </li>
    """


def _page_html(cards: list[str]) -> str:
    return f"<ul>{''.join(cards)}</ul>"


def _settings(**overrides: object) -> Settings:
    defaults: dict[str, object] = dict(
        gmail_address="a@b.com",
        gmail_app_password="pw",
        digest_recipients="me@example.com",
        decodo_username="decodo-user",
        decodo_password="decodo-pass",
        linkedin_posts_per_page=2,
        linkedin_max_pages_per_query=3,
        linkedin_search_concurrency=5,
    )
    defaults.update(overrides)
    return Settings(**defaults)


def test_parse_job_cards_extracts_all_fields() -> None:
    html = _page_html([_card_html("123", "Software Engineer", "Acme", "Dublin, Ireland", "2026-07-04")])

    cards = _parse_job_cards(html, query_country="Ireland")

    assert len(cards) == 1
    assert cards[0].title == "Software Engineer"
    assert cards[0].company == "Acme"
    assert cards[0].location == "Dublin, Ireland"
    assert cards[0].posted_on == date(2026, 7, 4)
    assert cards[0].url == "https://www.linkedin.com/jobs/view/123/"
    assert cards[0].query_country == "Ireland"


def test_parse_job_cards_skips_cards_missing_a_date() -> None:
    html = """
    <li><div data-entity-urn="urn:li:jobPosting:1"><div class="base-search-card__info">
      <h3>No Date Job</h3>
    </div></div></li>
    """
    assert _parse_job_cards(html, query_country="Ireland") == []


async def test_fetch_all_job_cards_stops_after_a_partial_page(monkeypatch: pytest.MonkeyPatch) -> None:
    # post_per_page=2: page 0 full (2 cards) -> continue; page 1 partial (1
    # card) -> stop, never fetch page 2.
    pages = [
        _page_html(
            [
                _card_html("1", "Job 1", "Acme", "Dublin, Ireland", "2026-07-04"),
                _card_html("2", "Job 2", "Acme", "Dublin, Ireland", "2026-07-04"),
            ]
        ),
        _page_html([_card_html("3", "Job 3", "Acme", "Dublin, Ireland", "2026-07-04")]),
    ]
    call_count = 0

    async def fake_fetch_html(client, url, **kwargs):
        nonlocal call_count
        page = pages[call_count]
        call_count += 1
        return page

    monkeypatch.setattr("visa_jobs_api.sources.linkedin.search.fetch_html", fake_fetch_html)

    settings = _settings()
    async with httpx.AsyncClient() as client:
        cards = await fetch_all_job_cards(
            client, [SearchQuery(keywords="Python", location="Ireland")], settings=settings, posted_within_hours=24
        )

    assert len(cards) == 3
    assert call_count == 2  # never fetched the (nonexistent) page 2


async def test_fetch_all_job_cards_records_one_search_call_per_page_fetched(monkeypatch: pytest.MonkeyPatch) -> None:
    pages = [
        _page_html(
            [
                _card_html("1", "Job 1", "Acme", "Dublin, Ireland", "2026-07-04"),
                _card_html("2", "Job 2", "Acme", "Dublin, Ireland", "2026-07-04"),
            ]
        ),
        _page_html([_card_html("3", "Job 3", "Acme", "Dublin, Ireland", "2026-07-04")]),
    ]
    call_count = 0

    async def fake_fetch_html(client, url, **kwargs):
        nonlocal call_count
        page = pages[call_count]
        call_count += 1
        return page

    monkeypatch.setattr("visa_jobs_api.sources.linkedin.search.fetch_html", fake_fetch_html)

    settings = _settings()
    stats = CallStats()
    async with httpx.AsyncClient() as client:
        await fetch_all_job_cards(
            client,
            [SearchQuery(keywords="Python", location="Ireland")],
            settings=settings,
            posted_within_hours=24,
            stats=stats,
        )

    # 2 pages fetched for Ireland (full page then partial page) -> 2 recorded
    # search calls, keyed by the query's own country.
    assert stats.linkedin_search_calls == {"Ireland": 2}


async def test_fetch_all_job_cards_stops_a_querys_pagination_on_fetch_failure_but_keeps_earlier_pages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A page that never loads after retries must not cost the whole query
    # (let alone the whole source) the cards its earlier pages already found.
    first_page = _page_html(
        [
            _card_html("1", "Job 1", "Acme", "Dublin, Ireland", "2026-07-04"),
            _card_html("2", "Job 2", "Acme", "Dublin, Ireland", "2026-07-04"),
        ]
    )
    call_count = 0

    async def fake_fetch_html(client, url, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return first_page
        raise FetchError(f"failed to fetch {url!r} after retries")

    monkeypatch.setattr("visa_jobs_api.sources.linkedin.search.fetch_html", fake_fetch_html)

    settings = _settings()
    async with httpx.AsyncClient() as client:
        cards = await fetch_all_job_cards(
            client, [SearchQuery(keywords="Python", location="Ireland")], settings=settings, posted_within_hours=24
        )

    assert len(cards) == 2


async def test_fetch_all_job_cards_continues_other_queries_when_one_querys_page_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_fetch_html(client, url, **kwargs):
        if "location=Ireland" in url:
            raise FetchError("boom")
        return _page_html([_card_html("1", "Job 1", "Acme", "New York, NY", "2026-07-04")])

    monkeypatch.setattr("visa_jobs_api.sources.linkedin.search.fetch_html", fake_fetch_html)

    settings = _settings(linkedin_max_pages_per_query=1)
    async with httpx.AsyncClient() as client:
        cards = await fetch_all_job_cards(
            client,
            [
                SearchQuery(keywords="Python", location="Ireland"),
                SearchQuery(keywords="Python", location="United States"),
            ],
            settings=settings,
            posted_within_hours=24,
        )

    assert len(cards) == 1
