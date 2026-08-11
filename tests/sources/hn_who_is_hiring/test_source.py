from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
import respx

from visa_jobs_api.config import Settings
from visa_jobs_api.sources.hn_who_is_hiring.discover import ALGOLIA_SEARCH_URL
from visa_jobs_api.sources.hn_who_is_hiring.fetch import ALGOLIA_ITEM_URL
from visa_jobs_api.sources.hn_who_is_hiring.source import HnWhoIsHiringSource


def _settings(**overrides: object) -> Settings:
    defaults: dict[str, object] = dict(
        gmail_address="a@b.com",
        gmail_app_password="pw",
        digest_recipients="me@example.com",
    )
    defaults.update(overrides)
    return Settings(**defaults)


def _mock_completion_returning(content: str) -> SimpleNamespace:
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


def _unix_ts(hours_ago: float) -> int:
    return int((datetime.now(tz=timezone.utc) - timedelta(hours=hours_ago)).timestamp())


def _item_payload() -> dict:
    return {
        "id": 48357725,
        "title": "Ask HN: Who is hiring? (July 2026)",
        "author": "whoishiring",
        "points": 1,
        "created_at_i": _unix_ts(24 * 20),  # posted three weeks ago, irrelevant to filtering
        "text": None,
        "children": [
            {
                "id": 1,
                "author": "acme_hr",
                "created_at_i": _unix_ts(1),  # 1 hour ago -- within window
                "text": "Acme | Backend Engineer | Berlin, Germany\n\nWe offer visa sponsorship.",
                "children": [],
            },
            {
                "id": 2,
                "author": "old_co_hr",
                "created_at_i": _unix_ts(30),  # 30 hours ago -- outside window
                "text": "OldCo | Backend Engineer | REMOTE\n\nWe offer visa sponsorship.",
                "children": [],
            },
        ],
    }


async def test_fetch_jobs_returns_only_confirmed_candidates_within_window(monkeypatch: pytest.MonkeyPatch) -> None:
    llm_client = MagicMock()
    llm_client.chat.completions.create = AsyncMock(
        return_value=_mock_completion_returning(
            '{"offers_sponsorship": true, "reason": "explicit offer", "country": "Germany", '
            '"tech_stack": ["Go"], "role_group": "Backend"}'
        )
    )
    monkeypatch.setattr("visa_jobs_api.sources.hn_who_is_hiring.source.build_client", lambda base_url: llm_client)

    with respx.mock:
        respx.get(ALGOLIA_SEARCH_URL).mock(
            return_value=httpx.Response(200, json={"hits": [{"objectID": "48357725", "title": "Ask HN: Who is hiring?"}]})
        )
        respx.get(f"{ALGOLIA_ITEM_URL}/48357725").mock(return_value=httpx.Response(200, json=_item_payload()))

        async with httpx.AsyncClient() as http_client:
            source = HnWhoIsHiringSource(settings=_settings(), http_client=http_client)
            jobs = await source.fetch_jobs()

    # Only comment 1 is within the 24h window -- comment 2 (30h old) must
    # never reach the LLM at all, let alone appear in the result.
    assert len(jobs) == 1
    assert jobs[0].company == "Acme"
    assert jobs[0].country == "Germany"
    assert jobs[0].source == "hn_who_is_hiring"
    assert llm_client.chat.completions.create.call_count == 1
    # The posting text ("We offer visa sponsorship.") has no regex-recognized
    # tech keyword, so the LLM's guess is used instead of an empty list.
    assert jobs[0].tech_stack == ["Go"]
    assert jobs[0].role_group == "Backend"


async def test_fetch_jobs_prefers_regex_detected_tech_stack_over_llm_guess(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = _item_payload()
    payload["children"][0]["text"] = "Acme | Backend Engineer | Berlin, Germany\n\nWe use Python. We offer visa sponsorship."

    llm_client = MagicMock()
    llm_client.chat.completions.create = AsyncMock(
        return_value=_mock_completion_returning(
            '{"offers_sponsorship": true, "reason": "explicit offer", "country": "Germany", '
            '"tech_stack": ["Rust"], "role_group": "Backend"}'
        )
    )
    monkeypatch.setattr("visa_jobs_api.sources.hn_who_is_hiring.source.build_client", lambda base_url: llm_client)

    with respx.mock:
        respx.get(ALGOLIA_SEARCH_URL).mock(
            return_value=httpx.Response(200, json={"hits": [{"objectID": "48357725", "title": "Ask HN: Who is hiring?"}]})
        )
        respx.get(f"{ALGOLIA_ITEM_URL}/48357725").mock(return_value=httpx.Response(200, json=payload))

        async with httpx.AsyncClient() as http_client:
            source = HnWhoIsHiringSource(settings=_settings(), http_client=http_client)
            jobs = await source.fetch_jobs()

    # The posting text literally mentions "Python" -- the regex detector's
    # finding is authoritative and must not be overridden by the LLM's
    # different guess ("Rust").
    assert jobs[0].tech_stack == ["Python"]


async def test_fetch_jobs_excludes_candidates_the_llm_rejects(monkeypatch: pytest.MonkeyPatch) -> None:
    llm_client = MagicMock()
    llm_client.chat.completions.create = AsyncMock(
        return_value=_mock_completion_returning('{"offers_sponsorship": false, "reason": "unrelated usage"}')
    )
    monkeypatch.setattr("visa_jobs_api.sources.hn_who_is_hiring.source.build_client", lambda base_url: llm_client)

    with respx.mock:
        respx.get(ALGOLIA_SEARCH_URL).mock(
            return_value=httpx.Response(200, json={"hits": [{"objectID": "48357725", "title": "Ask HN: Who is hiring?"}]})
        )
        respx.get(f"{ALGOLIA_ITEM_URL}/48357725").mock(return_value=httpx.Response(200, json=_item_payload()))

        async with httpx.AsyncClient() as http_client:
            source = HnWhoIsHiringSource(settings=_settings(), http_client=http_client)
            jobs = await source.fetch_jobs()

    assert jobs == []
