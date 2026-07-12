from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from visa_jobs_api.config import Settings
from visa_jobs_api.shared.call_stats import CallStats
from visa_jobs_api.shared.visa_llm import LlmVerdict, VisaLlmError
from visa_jobs_api.sources.indeed.models import IndeedJobCandidate, JobCard
from visa_jobs_api.sources.indeed.source import IndeedSource


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
        indeed_llm_concurrency=5,
    )
    defaults.update(overrides)
    return Settings(**defaults)


def _card(**overrides: object) -> JobCard:
    defaults: dict[str, object] = dict(
        title="Software Engineer",
        company="Acme",
        location="New York, NY",
        url="https://www.indeed.com/viewjob?jk=1",
        query_country="United States",
    )
    defaults.update(overrides)
    return JobCard(**defaults)


def _mock_completion_returning(content: str) -> SimpleNamespace:
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


def _make_llm_client(content: str) -> MagicMock:
    client = MagicMock()
    client.chat.completions.create = AsyncMock(return_value=_mock_completion_returning(content))
    return client


async def test_fetch_jobs_uses_the_llms_tech_stack_and_role_group(monkeypatch: pytest.MonkeyPatch) -> None:
    card = _card()
    candidate = IndeedJobCandidate(card=card, description="We use React and Node.js. We offer visa sponsorship.", language="en")

    monkeypatch.setattr("visa_jobs_api.sources.indeed.source.fetch_all_job_cards", AsyncMock(return_value=[card]))
    monkeypatch.setattr("visa_jobs_api.sources.indeed.source.fetch_all_descriptions", AsyncMock(return_value=[candidate]))
    llm_client = _make_llm_client(
        '{"offers_sponsorship": true, "reason": "explicit offer", "country": "United States", '
        '"tech_stack": ["React", "Node.js"], "role_group": "Frontend"}'
    )
    monkeypatch.setattr("visa_jobs_api.sources.indeed.source.build_client", lambda hf_token: llm_client)

    async with httpx.AsyncClient() as http_client:
        source = IndeedSource(settings=_settings(), http_client=http_client, call_stats=CallStats())
        jobs = await source.fetch_jobs()

    assert jobs[0].tech_stack == ["React", "Node.js"]
    assert jobs[0].role_group == "Frontend"


async def test_fetch_jobs_uses_query_country_not_the_llms_guess(monkeypatch: pytest.MonkeyPatch) -> None:
    # Same deterministic-country reasoning as LinkedIn's source.py: the
    # query's own country is known exactly, unlike an LLM guess.
    card = _card(query_country="United States")
    candidate = IndeedJobCandidate(card=card, description="We offer visa sponsorship.", language="en")

    monkeypatch.setattr("visa_jobs_api.sources.indeed.source.fetch_all_job_cards", AsyncMock(return_value=[card]))
    monkeypatch.setattr("visa_jobs_api.sources.indeed.source.fetch_all_descriptions", AsyncMock(return_value=[candidate]))
    llm_client = _make_llm_client('{"offers_sponsorship": true, "reason": "explicit offer", "country": "France"}')
    monkeypatch.setattr("visa_jobs_api.sources.indeed.source.build_client", lambda hf_token: llm_client)

    async with httpx.AsyncClient() as http_client:
        source = IndeedSource(settings=_settings(), http_client=http_client, call_stats=CallStats())
        jobs = await source.fetch_jobs()

    assert len(jobs) == 1
    assert jobs[0].country == "United States"
    assert jobs[0].source == "indeed"


async def test_fetch_jobs_skips_llm_when_no_regex_mention_found(monkeypatch: pytest.MonkeyPatch) -> None:
    card = _card()
    candidate = IndeedJobCandidate(card=card, description="Great team, fully remote, no visa content here.", language="en")

    monkeypatch.setattr("visa_jobs_api.sources.indeed.source.fetch_all_job_cards", AsyncMock(return_value=[card]))
    monkeypatch.setattr("visa_jobs_api.sources.indeed.source.fetch_all_descriptions", AsyncMock(return_value=[candidate]))
    llm_client = _make_llm_client('{"offers_sponsorship": true, "reason": "n/a"}')
    monkeypatch.setattr("visa_jobs_api.sources.indeed.source.build_client", lambda hf_token: llm_client)

    async with httpx.AsyncClient() as http_client:
        source = IndeedSource(settings=_settings(), http_client=http_client, call_stats=CallStats())
        jobs = await source.fetch_jobs()

    assert jobs == []
    llm_client.chat.completions.create.assert_not_called()


async def test_fetch_jobs_excludes_candidates_the_llm_rejects(monkeypatch: pytest.MonkeyPatch) -> None:
    card = _card()
    candidate = IndeedJobCandidate(card=card, description="We offer visa sponsorship.", language="en")

    monkeypatch.setattr("visa_jobs_api.sources.indeed.source.fetch_all_job_cards", AsyncMock(return_value=[card]))
    monkeypatch.setattr("visa_jobs_api.sources.indeed.source.fetch_all_descriptions", AsyncMock(return_value=[candidate]))
    llm_client = _make_llm_client('{"offers_sponsorship": false, "reason": "unable to sponsor"}')
    monkeypatch.setattr("visa_jobs_api.sources.indeed.source.build_client", lambda hf_token: llm_client)

    async with httpx.AsyncClient() as http_client:
        source = IndeedSource(settings=_settings(), http_client=http_client, call_stats=CallStats())
        jobs = await source.fetch_jobs()

    assert jobs == []


async def test_fetch_jobs_applies_title_filter_and_dedup(monkeypatch: pytest.MonkeyPatch) -> None:
    relevant = _card(title="Backend Engineer", url="https://www.indeed.com/viewjob?jk=1")
    irrelevant = _card(title="Sales Manager", url="https://www.indeed.com/viewjob?jk=2")
    # Same (title, company, url) as `relevant` -- a true duplicate, e.g. the
    # same listing reappearing on a later search page.
    duplicate = _card(title="Backend Engineer", url="https://www.indeed.com/viewjob?jk=1")
    # Same title+company as `relevant` but a different url -- a distinct
    # posting (e.g. the same generic title/company posted separately in
    # another country) that must NOT be deduped away.
    same_title_other_posting = _card(title="Backend Engineer", url="https://www.indeed.com/viewjob?jk=3")

    fetch_descriptions_mock = AsyncMock(return_value=[])
    monkeypatch.setattr(
        "visa_jobs_api.sources.indeed.source.fetch_all_job_cards",
        AsyncMock(return_value=[relevant, irrelevant, duplicate, same_title_other_posting]),
    )
    monkeypatch.setattr("visa_jobs_api.sources.indeed.source.fetch_all_descriptions", fetch_descriptions_mock)
    monkeypatch.setattr("visa_jobs_api.sources.indeed.source.build_client", lambda hf_token: MagicMock())

    async with httpx.AsyncClient() as http_client:
        source = IndeedSource(settings=_settings(), http_client=http_client, call_stats=CallStats())
        await source.fetch_jobs()

    # Sales Manager excluded by title filter, the true (title, company, url)
    # duplicate dropped, but the distinct posting at a different url with
    # the same title+company survives.
    passed_cards = fetch_descriptions_mock.call_args.args[1]
    assert [c.url for c in passed_cards] == [
        "https://www.indeed.com/viewjob?jk=1",
        "https://www.indeed.com/viewjob?jk=3",
    ]


async def test_fetch_jobs_skips_a_candidate_whose_llm_call_fails_but_keeps_the_rest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # An LLM confirmation failure for one job (e.g. a malformed response)
    # must not cost the whole source every other job it already confirmed.
    failing = _card(url="https://www.indeed.com/viewjob?jk=1")
    working = _card(url="https://www.indeed.com/viewjob?jk=2")
    candidates = [
        IndeedJobCandidate(card=failing, description="We offer visa sponsorship.", language="en"),
        IndeedJobCandidate(card=working, description="We offer visa sponsorship.", language="en"),
    ]

    monkeypatch.setattr(
        "visa_jobs_api.sources.indeed.source.fetch_all_job_cards", AsyncMock(return_value=[failing, working])
    )
    monkeypatch.setattr(
        "visa_jobs_api.sources.indeed.source.fetch_all_descriptions", AsyncMock(return_value=candidates)
    )
    monkeypatch.setattr("visa_jobs_api.sources.indeed.source.build_client", lambda hf_token: MagicMock())

    async def fake_confirm_visa_offer(*, log_context, **kwargs):
        if "jk=1" in log_context:
            raise VisaLlmError("LLM response was not valid JSON")
        return LlmVerdict(offers_sponsorship=True, reason="explicit offer")

    monkeypatch.setattr("visa_jobs_api.sources.indeed.source.confirm_visa_offer", fake_confirm_visa_offer)

    async with httpx.AsyncClient() as http_client:
        source = IndeedSource(settings=_settings(), http_client=http_client, call_stats=CallStats())
        jobs = await source.fetch_jobs()

    assert len(jobs) == 1
    assert jobs[0].url == "https://www.indeed.com/viewjob?jk=2"
