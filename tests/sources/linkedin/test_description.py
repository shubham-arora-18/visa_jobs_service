from __future__ import annotations

from datetime import date

import httpx
import pytest

from visa_jobs_api.config import Settings
from visa_jobs_api.shared.call_stats import CallStats
from visa_jobs_api.shared.http import FetchError
from visa_jobs_api.sources.linkedin.description import _extract_description_text, fetch_all_descriptions
from visa_jobs_api.sources.linkedin.models import JobCard


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
        linkedin_description_concurrency=5,
    )
    defaults.update(overrides)
    return Settings(**defaults)


def _card(url: str = "https://www.linkedin.com/jobs/view/1/", query_country: str = "Ireland") -> JobCard:
    return JobCard(
        title="Software Engineer",
        company="Acme",
        location="Dublin, Ireland",
        posted_on=date(2026, 7, 4),
        url=url,
        query_country=query_country,
    )


def test_extract_description_text_returns_none_when_block_missing() -> None:
    assert _extract_description_text("<html><body>no description here</body></html>") is None


def test_extract_description_text_extracts_and_cleans_the_block() -> None:
    html = """
    <div class="description__text description__text--rich">
      <p>We offer visa sponsorship.<span>ignored</span></p>
      <ul><li>Python</li></ul>
      Show more
    </div>
    """
    text = _extract_description_text(html)
    assert "We offer visa sponsorship." in text
    assert "Show more" not in text
    assert "ignored" not in text


async def test_fetch_all_descriptions_skips_pages_with_no_description_block(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_fetch_html(client, url, **kwargs):
        if "1" in url:
            return '<div class="description__text description__text--rich">We offer visa sponsorship.</div>'
        return "<html>blocked or empty page</html>"

    monkeypatch.setattr("visa_jobs_api.sources.linkedin.description.fetch_html", fake_fetch_html)

    cards = [_card("https://www.linkedin.com/jobs/view/1/"), _card("https://www.linkedin.com/jobs/view/2/")]
    async with httpx.AsyncClient() as client:
        candidates = await fetch_all_descriptions(client, cards, settings=_settings())

    assert len(candidates) == 1
    assert candidates[0].card.url == "https://www.linkedin.com/jobs/view/1/"
    assert candidates[0].language == "en"


async def test_fetch_all_descriptions_records_one_call_per_card_by_query_country(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_fetch_html(client, url, **kwargs):
        return '<div class="description__text description__text--rich">We offer visa sponsorship.</div>'

    monkeypatch.setattr("visa_jobs_api.sources.linkedin.description.fetch_html", fake_fetch_html)

    cards = [
        _card("https://www.linkedin.com/jobs/view/1/", query_country="Ireland"),
        _card("https://www.linkedin.com/jobs/view/2/", query_country="Ireland"),
        _card("https://www.linkedin.com/jobs/view/3/", query_country="Germany"),
    ]
    stats = CallStats()
    async with httpx.AsyncClient() as client:
        await fetch_all_descriptions(client, cards, settings=_settings(), stats=stats)

    assert stats.linkedin_description_calls == {"Ireland": 2, "Germany": 1}


async def test_fetch_all_descriptions_skips_a_card_whose_fetch_fails_but_keeps_the_rest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A single job whose description page never loads after retries must
    # not cost the whole source every other job it already found.
    async def fake_fetch_html(client, url, **kwargs):
        if "1" in url:
            raise FetchError(f"failed to fetch {url!r} after retries")
        return '<div class="description__text description__text--rich">We offer visa sponsorship.</div>'

    monkeypatch.setattr("visa_jobs_api.sources.linkedin.description.fetch_html", fake_fetch_html)

    cards = [_card("https://www.linkedin.com/jobs/view/1/"), _card("https://www.linkedin.com/jobs/view/2/")]
    async with httpx.AsyncClient() as client:
        candidates = await fetch_all_descriptions(client, cards, settings=_settings())

    assert len(candidates) == 1
    assert candidates[0].card.url == "https://www.linkedin.com/jobs/view/2/"
