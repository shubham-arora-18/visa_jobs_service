from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from visa_jobs_api.config import Settings
from visa_jobs_api.sources.linkedin.models import JobCard, LinkedInJobCandidate
from visa_jobs_api.sources.linkedin.source import LinkedInSource


def _settings(**overrides: object) -> Settings:
    defaults: dict[str, object] = dict(
        hf_token="fake",
        gmail_address="a@b.com",
        gmail_app_password="pw",
        digest_recipients="me@example.com",
        brightdata_api_key="bd-key",
        brightdata_zone="zone",
        job_posted_within_hours=24,
        linkedin_llm_concurrency=5,
    )
    defaults.update(overrides)
    return Settings(**defaults)


def _card(**overrides: object) -> JobCard:
    defaults: dict[str, object] = dict(
        title="Software Engineer", company="Acme", location="Dublin, Ireland", posted_on=date.today(), url="https://x/1"
    )
    defaults.update(overrides)
    return JobCard(**defaults)


def _mock_completion_returning(content: str) -> SimpleNamespace:
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


def _make_llm_client(content: str) -> MagicMock:
    client = MagicMock()
    client.chat.completions.create = AsyncMock(return_value=_mock_completion_returning(content))
    return client


async def _fake_http_client() -> httpx.AsyncClient:
    return httpx.AsyncClient()


async def test_fetch_jobs_confirms_english_candidate_and_uses_structured_country(monkeypatch: pytest.MonkeyPatch) -> None:
    card = _card(location="Dublin, Ireland")
    candidate = LinkedInJobCandidate(card=card, description="We offer visa sponsorship.", language="en")

    monkeypatch.setattr("visa_jobs_api.sources.linkedin.source.fetch_all_job_cards", AsyncMock(return_value=[card]))
    monkeypatch.setattr(
        "visa_jobs_api.sources.linkedin.source.fetch_all_descriptions", AsyncMock(return_value=[candidate])
    )
    llm_client = _make_llm_client('{"offers_sponsorship": true, "reason": "explicit offer", "country": "France"}')
    monkeypatch.setattr("visa_jobs_api.sources.linkedin.source.build_client", lambda hf_token: llm_client)

    async with httpx.AsyncClient() as http_client:
        source = LinkedInSource(settings=_settings(), http_client=http_client)
        jobs = await source.fetch_jobs()

    # Structured location ("Ireland") wins over the LLM's guessed country
    # ("France") -- it's deterministic and more trustworthy than a guess.
    assert len(jobs) == 1
    assert jobs[0].country == "Ireland"
    assert jobs[0].source == "linkedin"


async def test_fetch_jobs_falls_back_to_llm_country_when_location_is_remote(monkeypatch: pytest.MonkeyPatch) -> None:
    card = _card(location="Remote")
    candidate = LinkedInJobCandidate(card=card, description="We offer visa sponsorship.", language="en")

    monkeypatch.setattr("visa_jobs_api.sources.linkedin.source.fetch_all_job_cards", AsyncMock(return_value=[card]))
    monkeypatch.setattr(
        "visa_jobs_api.sources.linkedin.source.fetch_all_descriptions", AsyncMock(return_value=[candidate])
    )
    llm_client = _make_llm_client('{"offers_sponsorship": true, "reason": "explicit offer", "country": "Germany"}')
    monkeypatch.setattr("visa_jobs_api.sources.linkedin.source.build_client", lambda hf_token: llm_client)

    async with httpx.AsyncClient() as http_client:
        source = LinkedInSource(settings=_settings(), http_client=http_client)
        jobs = await source.fetch_jobs()

    assert jobs[0].country == "Germany"


async def test_fetch_jobs_skips_llm_when_no_regex_mention_found(monkeypatch: pytest.MonkeyPatch) -> None:
    card = _card()
    candidate = LinkedInJobCandidate(card=card, description="Great team, fully remote, no visa content here.", language="en")

    monkeypatch.setattr("visa_jobs_api.sources.linkedin.source.fetch_all_job_cards", AsyncMock(return_value=[card]))
    monkeypatch.setattr(
        "visa_jobs_api.sources.linkedin.source.fetch_all_descriptions", AsyncMock(return_value=[candidate])
    )
    llm_client = _make_llm_client('{"offers_sponsorship": true, "reason": "n/a"}')
    monkeypatch.setattr("visa_jobs_api.sources.linkedin.source.build_client", lambda hf_token: llm_client)

    async with httpx.AsyncClient() as http_client:
        source = LinkedInSource(settings=_settings(), http_client=http_client)
        jobs = await source.fetch_jobs()

    assert jobs == []
    llm_client.chat.completions.create.assert_not_called()


async def test_fetch_jobs_calls_llm_directly_for_non_english_text(monkeypatch: pytest.MonkeyPatch) -> None:
    card = _card()
    candidate = LinkedInJobCandidate(card=card, description="Wir bieten Visa-Sponsoring an.", language="de")

    monkeypatch.setattr("visa_jobs_api.sources.linkedin.source.fetch_all_job_cards", AsyncMock(return_value=[card]))
    monkeypatch.setattr(
        "visa_jobs_api.sources.linkedin.source.fetch_all_descriptions", AsyncMock(return_value=[candidate])
    )
    llm_client = _make_llm_client('{"offers_sponsorship": true, "reason": "confirmed", "country": "Germany"}')
    monkeypatch.setattr("visa_jobs_api.sources.linkedin.source.build_client", lambda hf_token: llm_client)

    async with httpx.AsyncClient() as http_client:
        source = LinkedInSource(settings=_settings(), http_client=http_client)
        jobs = await source.fetch_jobs()

    assert len(jobs) == 1
    llm_client.chat.completions.create.assert_called_once()


async def test_fetch_jobs_excludes_cards_outside_the_recency_window(monkeypatch: pytest.MonkeyPatch) -> None:
    old_card = _card(posted_on=(datetime.now(tz=timezone.utc) - timedelta(days=5)).date())

    fetch_descriptions_mock = AsyncMock(return_value=[])
    monkeypatch.setattr("visa_jobs_api.sources.linkedin.source.fetch_all_job_cards", AsyncMock(return_value=[old_card]))
    monkeypatch.setattr("visa_jobs_api.sources.linkedin.source.fetch_all_descriptions", fetch_descriptions_mock)
    monkeypatch.setattr("visa_jobs_api.sources.linkedin.source.build_client", lambda hf_token: MagicMock())

    async with httpx.AsyncClient() as http_client:
        source = LinkedInSource(settings=_settings(job_posted_within_hours=24), http_client=http_client)
        jobs = await source.fetch_jobs()

    assert jobs == []
    # The old card must be filtered out before we ever spend a description
    # fetch on it.
    fetch_descriptions_mock.assert_called_once_with(http_client, [], settings=source._settings)
