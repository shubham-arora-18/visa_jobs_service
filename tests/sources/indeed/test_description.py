from __future__ import annotations

import httpx
import pytest

from visa_jobs_api.config import Settings
from visa_jobs_api.shared.call_stats import CallStats
from visa_jobs_api.shared.http import FetchError
from visa_jobs_api.sources.indeed.description import _extract_description_text, fetch_all_descriptions
from visa_jobs_api.sources.indeed.models import JobCard


def _settings(**overrides: object) -> Settings:
    defaults: dict[str, object] = dict(
        gmail_address="a@b.com",
        gmail_app_password="pw",
        digest_recipients="me@example.com",
        decodo_username="decodo-user",
        decodo_password="decodo-pass",
        brightdata_api_key="brightdata-key",
        brightdata_zone="brightdata-zone",
        indeed_description_concurrency=5,
    )
    defaults.update(overrides)
    return Settings(**defaults)


def _card(url: str = "https://www.indeed.com/viewjob?jk=1", query_country: str = "United States") -> JobCard:
    return JobCard(title="Software Engineer", company="Acme", location="New York, NY", url=url, query_country=query_country)


def test_extract_description_text_finds_the_description_div() -> None:
    html = '<html><body><div id="jobDescriptionText">We offer visa sponsorship.<br>Line two.</div></body></html>'
    text = _extract_description_text(html)
    assert text is not None
    assert "We offer visa sponsorship." in text
    assert "Line two." in text


def test_extract_description_text_returns_none_when_missing() -> None:
    assert _extract_description_text("<html><body>no description here</body></html>") is None


async def test_fetch_all_descriptions_skips_pages_with_no_description_block(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_fetch_html(client, url, **kwargs):
        if "jk=1" in url:
            return '<div id="jobDescriptionText">We offer visa sponsorship.</div>'
        return "<html>blocked or empty page</html>"

    monkeypatch.setattr("visa_jobs_api.sources.indeed.description.fetch_html", fake_fetch_html)

    cards = [
        _card("https://www.indeed.com/viewjob?jk=1"),
        _card("https://www.indeed.com/viewjob?jk=2"),
    ]
    async with httpx.AsyncClient() as client:
        candidates = await fetch_all_descriptions(client, cards, settings=_settings())

    assert len(candidates) == 1
    assert candidates[0].card.url == "https://www.indeed.com/viewjob?jk=1"
    assert candidates[0].language == "en"


async def test_fetch_all_descriptions_records_one_call_per_card_by_query_country(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_fetch_html(client, url, **kwargs):
        return '<div id="jobDescriptionText">We offer visa sponsorship.</div>'

    monkeypatch.setattr("visa_jobs_api.sources.indeed.description.fetch_html", fake_fetch_html)

    cards = [
        _card("https://www.indeed.com/viewjob?jk=1", query_country="United States"),
        _card("https://www.indeed.com/viewjob?jk=2", query_country="United States"),
        _card("https://ie.indeed.com/viewjob?jk=3", query_country="Ireland"),
    ]
    stats = CallStats()
    async with httpx.AsyncClient() as client:
        await fetch_all_descriptions(client, cards, settings=_settings(), stats=stats)

    assert stats.indeed_description_calls == {"United States": 2, "Ireland": 1}


async def test_fetch_all_descriptions_skips_a_card_whose_fetch_fails_but_keeps_the_rest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A single job whose description page never loads after retries must
    # not cost the whole source every other job it already found.
    async def fake_fetch_html(client, url, **kwargs):
        if "jk=1" in url:
            raise FetchError(f"failed to fetch {url!r} after retries")
        return '<div id="jobDescriptionText">We offer visa sponsorship.</div>'

    monkeypatch.setattr("visa_jobs_api.sources.indeed.description.fetch_html", fake_fetch_html)

    cards = [
        _card("https://www.indeed.com/viewjob?jk=1"),
        _card("https://www.indeed.com/viewjob?jk=2"),
    ]
    async with httpx.AsyncClient() as client:
        candidates = await fetch_all_descriptions(client, cards, settings=_settings())

    assert len(candidates) == 1
    assert candidates[0].card.url == "https://www.indeed.com/viewjob?jk=2"


async def test_fetch_all_descriptions_fetches_via_brightdata_with_the_mapped_country_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict] = []

    async def fake_fetch_html(client, url, **kwargs):
        calls.append(kwargs)
        return '<div id="jobDescriptionText">We offer visa sponsorship.</div>'

    monkeypatch.setattr("visa_jobs_api.sources.indeed.description.fetch_html", fake_fetch_html)

    cards = [_card("https://uk.indeed.com/viewjob?jk=1", query_country="United Kingdom")]
    async with httpx.AsyncClient() as client:
        await fetch_all_descriptions(client, cards, settings=_settings())

    assert len(calls) == 1
    assert calls[0]["via_brightdata"] is True
    assert calls[0]["brightdata_api_key"] == "brightdata-key"
    assert calls[0]["brightdata_zone"] == "brightdata-zone"
    # ISO code, not Indeed's own "uk" domain-naming.
    assert calls[0]["brightdata_country"] == "gb"
