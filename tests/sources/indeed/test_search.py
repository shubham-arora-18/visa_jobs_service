from __future__ import annotations

import pytest

from visa_jobs_api.config import Settings
from visa_jobs_api.shared.call_stats import CallStats
from visa_jobs_api.sources.indeed.models import SearchQuery
from visa_jobs_api.sources.indeed.selenium_client import SeleniumFetchError
from visa_jobs_api.sources.indeed.search import _build_search_url, _fromage_for_hours, fetch_all_job_cards

_NAV_WITH_MORE_PAGES = """
<nav role="navigation" aria-label="pagination">
  <ul>
    <li><a data-testid="pagination-page-1">1</a></li>
    <li><a data-testid="pagination-page-2">2</a></li>
  </ul>
</nav>
"""
_NAV_WITH_NO_MORE_PAGES = '<nav role="navigation" aria-label="pagination"><ul></ul></nav>'


def _card_html(job_key: str, title: str, company: str, location: str) -> str:
    return f"""
    <div class="job_seen_beacon">
      <h3 class="jobTitle"><a data-jk="{job_key}"><span title="{title}">{title}</span></a></h3>
      <span data-testid="company-name">{company}</span>
      <div data-testid="text-location">{location}</div>
    </div>
    """


def _page_html(cards: list[str], *, nav: str = _NAV_WITH_NO_MORE_PAGES) -> str:
    return f"<div>{''.join(cards)}</div>{nav}"


def _settings(**overrides: object) -> Settings:
    defaults: dict[str, object] = dict(
        gmail_address="a@b.com",
        gmail_app_password="pw",
        digest_recipients="me@example.com",
        decodo_username="decodo-user",
        decodo_password="decodo-pass",
        indeed_posts_per_page=2,
        indeed_max_pages_per_query=3,
        indeed_search_concurrency=5,
        indeed_min_cards_per_page=2,
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
    assert "sc=" not in url


def test_build_search_url_omits_vjk_when_not_set() -> None:
    url = _build_search_url("www.indeed.com", keywords="Python", start=0, posted_within_hours=24)

    assert "vjk" not in url


def test_build_search_url_appends_vjk_when_set() -> None:
    url = _build_search_url("www.indeed.com", keywords="Python", start=0, posted_within_hours=24, vjk="abc123")

    assert url.endswith("&vjk=abc123")


async def test_fetch_all_job_cards_stops_after_page_0_when_nav_has_no_further_pages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Confirmed live: an empty pagination nav on page 0 means only one page
    # of real results exists -- every further start=N offset tested still
    # returned cards, but the same ones as page 0, not new ones. See
    # extract.has_additional_pages's docstring.
    call_count = 0

    async def fake_fetch(url: str, *, page_settle_seconds: float) -> tuple[str, str]:
        nonlocal call_count
        call_count += 1
        html = _page_html([_card_html("1", "Job 1", "Acme", "New York, NY")], nav=_NAV_WITH_NO_MORE_PAGES)
        return html, url

    monkeypatch.setattr("visa_jobs_api.sources.indeed.search.fetch_html_via_selenium", fake_fetch)

    settings = _settings()
    cards = await fetch_all_job_cards(
        [SearchQuery(keywords="Python", country="United States")], settings=settings, posted_within_hours=24
    )

    assert len(cards) == 1
    assert call_count == 1  # only page 0 -- pages 1 and 2 never requested


async def test_fetch_all_job_cards_continues_past_page_0_when_nav_advertises_more_pages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pages = [
        _page_html([_card_html("1", "Job 1", "Acme", "NY"), _card_html("2", "Job 2", "Acme", "NY")], nav=_NAV_WITH_MORE_PAGES),
        _page_html([_card_html("3", "Job 3", "Acme", "NY"), _card_html("4", "Job 4", "Acme", "NY")]),
        _page_html([_card_html("5", "Job 5", "Acme", "NY")]),
    ]
    requested_urls: list[str] = []

    async def fake_fetch(url: str, *, page_settle_seconds: float) -> tuple[str, str]:
        requested_urls.append(url)
        html = pages[len(requested_urls) - 1]
        current_url = f"{url}&vjk=captured123" if len(requested_urls) == 1 else url
        return html, current_url

    monkeypatch.setattr("visa_jobs_api.sources.indeed.search.fetch_html_via_selenium", fake_fetch)

    settings = _settings()
    cards = await fetch_all_job_cards(
        [SearchQuery(keywords="Python", country="United States")], settings=settings, posted_within_hours=24
    )

    assert len(cards) == 5
    assert len(requested_urls) == 3  # all 3 configured pages fetched
    # Page 0's captured vjk is reused on every subsequent page's URL.
    assert "vjk=captured123" in requested_urls[1]
    assert "vjk=captured123" in requested_urls[2]
    assert "vjk" not in requested_urls[0]  # page 0 itself has no vjk to attach yet


async def test_fetch_all_job_cards_stops_the_whole_query_when_page_0_fails_to_load(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Unlike a later page failing (see the "keeps going" test below), page 0
    # failing ends the query entirely -- there's no vjk or pagination
    # signal yet to build any subsequent page from.
    call_count = 0

    async def fake_fetch(url: str, *, page_settle_seconds: float) -> tuple[str, str]:
        nonlocal call_count
        call_count += 1
        raise SeleniumFetchError("boom")

    monkeypatch.setattr("visa_jobs_api.sources.indeed.search.fetch_html_via_selenium", fake_fetch)

    settings = _settings()
    cards = await fetch_all_job_cards(
        [SearchQuery(keywords="Python", country="United States")], settings=settings, posted_within_hours=24
    )

    assert cards == []
    assert call_count == 1  # only the failed page-0 attempt -- no retries, no later pages


async def test_fetch_all_job_cards_retries_a_suspicious_empty_page_before_trusting_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = ["<html>blocked or empty page</html>", _page_html([_card_html("1", "Job 1", "Acme", "NY")])]
    call_count = 0

    async def fake_fetch(url: str, *, page_settle_seconds: float) -> tuple[str, str]:
        nonlocal call_count
        html = responses[call_count]
        call_count += 1
        return html, url

    monkeypatch.setattr("visa_jobs_api.sources.indeed.search.fetch_html_via_selenium", fake_fetch)

    settings = _settings(indeed_max_pages_per_query=1)
    cards = await fetch_all_job_cards(
        [SearchQuery(keywords="Python", country="United States")], settings=settings, posted_within_hours=24
    )

    assert call_count == 2
    assert len(cards) == 1


async def test_fetch_all_job_cards_accepts_zero_after_exhausting_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    call_count = 0

    async def fake_fetch(url: str, *, page_settle_seconds: float) -> tuple[str, str]:
        nonlocal call_count
        call_count += 1
        return "<html>blocked or empty page</html>", url

    monkeypatch.setattr("visa_jobs_api.sources.indeed.search.fetch_html_via_selenium", fake_fetch)

    settings = _settings(indeed_max_pages_per_query=1)
    cards = await fetch_all_job_cards(
        [SearchQuery(keywords="Python", country="United States")], settings=settings, posted_within_hours=24
    )

    assert call_count == 2  # both retry attempts exhausted, 0 accepted as final
    assert cards == []


async def test_fetch_all_job_cards_stops_pagination_after_a_successful_but_empty_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pages = [
        _page_html([_card_html("1", "Job 1", "Acme", "NY"), _card_html("2", "Job 2", "Acme", "NY")], nav=_NAV_WITH_MORE_PAGES),
        "<html>genuinely no results</html>",
        "<html>genuinely no results</html>",
    ]
    call_count = 0

    async def fake_fetch(url: str, *, page_settle_seconds: float) -> tuple[str, str]:
        nonlocal call_count
        html = pages[min(call_count, len(pages) - 1)]
        call_count += 1
        return html, url

    monkeypatch.setattr("visa_jobs_api.sources.indeed.search.fetch_html_via_selenium", fake_fetch)

    settings = _settings(indeed_max_pages_per_query=3)
    cards = await fetch_all_job_cards(
        [SearchQuery(keywords="Python", country="United States")], settings=settings, posted_within_hours=24
    )

    assert len(cards) == 2  # page 0's cards only
    # page 0 (1 call) + page 1's 2 retry attempts (both empty) = 3; page 2 never requested
    assert call_count == 3


async def test_fetch_all_job_cards_stops_pagination_after_a_page_below_the_min_cards_threshold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pages = [
        _page_html([_card_html("1", "Job 1", "Acme", "NY"), _card_html("2", "Job 2", "Acme", "NY")], nav=_NAV_WITH_MORE_PAGES),
        _page_html([_card_html("3", "Job 3", "Acme", "NY")]),  # 1 card, below the 2-card threshold
        _page_html([_card_html("4", "Job 4", "Acme", "NY"), _card_html("5", "Job 5", "Acme", "NY")]),
    ]
    call_count = 0

    async def fake_fetch(url: str, *, page_settle_seconds: float) -> tuple[str, str]:
        nonlocal call_count
        html = pages[call_count]
        call_count += 1
        return html, url

    monkeypatch.setattr("visa_jobs_api.sources.indeed.search.fetch_html_via_selenium", fake_fetch)

    settings = _settings()
    cards = await fetch_all_job_cards(
        [SearchQuery(keywords="Python", country="United States")], settings=settings, posted_within_hours=24
    )

    assert len(cards) == 3  # page 0's 2 + page 1's 1 -- page 2 never requested
    assert call_count == 2


async def test_fetch_all_job_cards_records_one_search_call_per_page_fetched(monkeypatch: pytest.MonkeyPatch) -> None:
    pages = [
        _page_html([_card_html("1", "Job 1", "Acme", "NY"), _card_html("2", "Job 2", "Acme", "NY")], nav=_NAV_WITH_MORE_PAGES),
        _page_html([_card_html("3", "Job 3", "Acme", "NY"), _card_html("4", "Job 4", "Acme", "NY")]),
        _page_html([_card_html("5", "Job 5", "Acme", "NY"), _card_html("6", "Job 6", "Acme", "NY")]),
    ]
    call_count = 0

    async def fake_fetch(url: str, *, page_settle_seconds: float) -> tuple[str, str]:
        nonlocal call_count
        html = pages[call_count]
        call_count += 1
        return html, url

    monkeypatch.setattr("visa_jobs_api.sources.indeed.search.fetch_html_via_selenium", fake_fetch)

    settings = _settings()
    stats = CallStats()
    await fetch_all_job_cards(
        [SearchQuery(keywords="Python", country="United States")],
        settings=settings,
        posted_within_hours=24,
        stats=stats,
    )

    assert stats.indeed_search_calls == {"United States": 3}


async def test_fetch_all_job_cards_keeps_going_past_a_page_that_fails_to_fetch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_page = _page_html(
        [_card_html("1", "Job 1", "Acme", "NY"), _card_html("2", "Job 2", "Acme", "NY")], nav=_NAV_WITH_MORE_PAGES
    )
    call_count = 0

    async def fake_fetch(url: str, *, page_settle_seconds: float) -> tuple[str, str]:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return first_page, url
        raise SeleniumFetchError(f"failed to fetch {url!r} after retries")

    monkeypatch.setattr("visa_jobs_api.sources.indeed.search.fetch_html_via_selenium", fake_fetch)

    settings = _settings()
    cards = await fetch_all_job_cards(
        [SearchQuery(keywords="Python", country="United States")], settings=settings, posted_within_hours=24
    )

    assert len(cards) == 2
    assert call_count == 3  # all 3 configured pages were attempted, not just the first


async def test_fetch_all_job_cards_continues_other_queries_when_one_querys_page_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_fetch(url: str, *, page_settle_seconds: float) -> tuple[str, str]:
        if url.startswith("https://www.indeed.com/"):  # United States' domain -- see country_domains.py
            raise SeleniumFetchError("boom")
        html = _page_html([_card_html("1", "Job 1", "Acme", "Dublin")], nav=_NAV_WITH_NO_MORE_PAGES)
        return html, url

    monkeypatch.setattr("visa_jobs_api.sources.indeed.search.fetch_html_via_selenium", fake_fetch)

    settings = _settings(indeed_max_pages_per_query=1)
    cards = await fetch_all_job_cards(
        [
            SearchQuery(keywords="Python", country="United States"),
            SearchQuery(keywords="Python", country="Ireland"),
        ],
        settings=settings,
        posted_within_hours=24,
    )

    assert len(cards) == 1
