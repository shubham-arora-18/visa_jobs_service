from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from visa_jobs_api.config import Settings
from visa_jobs_api.shared.call_stats import CallStats
from visa_jobs_api.shared.visa_llm import LlmVerdict, VisaLlmError
from visa_jobs_api.sources.linkedin.models import JobCard, LinkedInJobCandidate
from visa_jobs_api.sources.linkedin.source import LinkedInSource


def _settings(**overrides: object) -> Settings:
    defaults: dict[str, object] = dict(
        hf_token="fake",
        gmail_address="a@b.com",
        gmail_app_password="pw",
        digest_recipients="me@example.com",
        decodo_username="decodo-user",
        decodo_password="decodo-pass",
        linkedin_llm_concurrency=5,
    )
    defaults.update(overrides)
    return Settings(**defaults)


def _card(**overrides: object) -> JobCard:
    defaults: dict[str, object] = dict(
        title="Software Engineer",
        company="Acme",
        location="Dublin, Ireland",
        posted_on=date.today(),
        url="https://x/1",
        query_country="Ireland",
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


async def test_fetch_jobs_uses_the_llms_tech_stack_and_role_group(monkeypatch: pytest.MonkeyPatch) -> None:
    # LinkedIn has no regex-based tech detector at all, unlike HN -- the
    # LLM's reading of the description is the only source for these fields.
    card = _card(location="Dublin, Ireland")
    candidate = LinkedInJobCandidate(card=card, description="We use React and Node.js. We offer visa sponsorship.", language="en")

    monkeypatch.setattr("visa_jobs_api.sources.linkedin.source.fetch_all_job_cards", AsyncMock(return_value=[card]))
    monkeypatch.setattr(
        "visa_jobs_api.sources.linkedin.source.fetch_all_descriptions", AsyncMock(return_value=[candidate])
    )
    llm_client = _make_llm_client(
        '{"offers_sponsorship": true, "reason": "explicit offer", "country": "Ireland", '
        '"tech_stack": ["React", "Node.js"], "role_group": "Frontend"}'
    )
    monkeypatch.setattr("visa_jobs_api.sources.linkedin.source.build_client", lambda hf_token: llm_client)

    async with httpx.AsyncClient() as http_client:
        source = LinkedInSource(settings=_settings(), http_client=http_client, call_stats=CallStats())
        jobs = await source.fetch_jobs()

    assert jobs[0].tech_stack == ["React", "Node.js"]
    assert jobs[0].role_group == "Frontend"


async def test_fetch_jobs_confirms_english_candidate_and_uses_query_country(monkeypatch: pytest.MonkeyPatch) -> None:
    card = _card(location="Dublin, Ireland", query_country="Ireland")
    candidate = LinkedInJobCandidate(card=card, description="We offer visa sponsorship.", language="en")

    monkeypatch.setattr("visa_jobs_api.sources.linkedin.source.fetch_all_job_cards", AsyncMock(return_value=[card]))
    monkeypatch.setattr(
        "visa_jobs_api.sources.linkedin.source.fetch_all_descriptions", AsyncMock(return_value=[candidate])
    )
    llm_client = _make_llm_client('{"offers_sponsorship": true, "reason": "explicit offer", "country": "France"}')
    monkeypatch.setattr("visa_jobs_api.sources.linkedin.source.build_client", lambda hf_token: llm_client)

    async with httpx.AsyncClient() as http_client:
        source = LinkedInSource(settings=_settings(), http_client=http_client, call_stats=CallStats())
        jobs = await source.fetch_jobs()

    # The card's query country ("Ireland" -- the SearchQuery that produced
    # it) wins over the LLM's guessed country ("France"): it's deterministic
    # and known exactly, unlike a guess.
    assert len(jobs) == 1
    assert jobs[0].country == "Ireland"
    assert jobs[0].source == "linkedin"


async def test_fetch_jobs_uses_query_country_even_when_card_location_is_remote_or_a_us_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Real bug: LinkedIn's own location text is unreliable as a country
    # source -- "Remote", "San Francisco, CA" (a US state abbreviation, not
    # a country), and comma-less metro names ("Greater Pittsburgh Region")
    # all break a from-text guess. The query country is known exactly
    # regardless of what the location text says.
    card = _card(location="San Francisco, CA", query_country="United States")
    candidate = LinkedInJobCandidate(card=card, description="We offer visa sponsorship.", language="en")

    monkeypatch.setattr("visa_jobs_api.sources.linkedin.source.fetch_all_job_cards", AsyncMock(return_value=[card]))
    monkeypatch.setattr(
        "visa_jobs_api.sources.linkedin.source.fetch_all_descriptions", AsyncMock(return_value=[candidate])
    )
    llm_client = _make_llm_client('{"offers_sponsorship": true, "reason": "explicit offer", "country": "Germany"}')
    monkeypatch.setattr("visa_jobs_api.sources.linkedin.source.build_client", lambda hf_token: llm_client)

    async with httpx.AsyncClient() as http_client:
        source = LinkedInSource(settings=_settings(), http_client=http_client, call_stats=CallStats())
        jobs = await source.fetch_jobs()

    assert jobs[0].country == "United States"


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
        source = LinkedInSource(settings=_settings(), http_client=http_client, call_stats=CallStats())
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
        source = LinkedInSource(settings=_settings(), http_client=http_client, call_stats=CallStats())
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
        source = LinkedInSource(settings=_settings(), http_client=http_client, call_stats=CallStats(), posted_within_hours=24)
        jobs = await source.fetch_jobs()

    assert jobs == []
    # The old card must be filtered out before we ever spend a description
    # fetch on it.
    fetch_descriptions_mock.assert_called_once_with(http_client, [], settings=source._settings, stats=source._call_stats)


async def test_fetch_jobs_skips_a_candidate_whose_llm_call_fails_but_keeps_the_rest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # An LLM confirmation failure for one job (e.g. a malformed response)
    # must not cost the whole source every other job it already confirmed.
    failing = _card(url="https://x/1")
    working = _card(url="https://x/2")
    candidates = [
        LinkedInJobCandidate(card=failing, description="We offer visa sponsorship.", language="en"),
        LinkedInJobCandidate(card=working, description="We offer visa sponsorship.", language="en"),
    ]

    monkeypatch.setattr(
        "visa_jobs_api.sources.linkedin.source.fetch_all_job_cards", AsyncMock(return_value=[failing, working])
    )
    monkeypatch.setattr(
        "visa_jobs_api.sources.linkedin.source.fetch_all_descriptions", AsyncMock(return_value=candidates)
    )
    monkeypatch.setattr("visa_jobs_api.sources.linkedin.source.build_client", lambda hf_token: MagicMock())

    async def fake_confirm_visa_offer(*, log_context, **kwargs):
        if "x/1" in log_context:
            raise VisaLlmError("LLM response was not valid JSON")
        return LlmVerdict(offers_sponsorship=True, reason="explicit offer")

    monkeypatch.setattr("visa_jobs_api.sources.linkedin.source.confirm_visa_offer", fake_confirm_visa_offer)

    async with httpx.AsyncClient() as http_client:
        source = LinkedInSource(settings=_settings(), http_client=http_client, call_stats=CallStats())
        jobs = await source.fetch_jobs()

    assert len(jobs) == 1
    assert jobs[0].url == "https://x/2"
