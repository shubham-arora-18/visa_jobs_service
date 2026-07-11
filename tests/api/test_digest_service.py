from __future__ import annotations

import logging
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from visa_jobs_api.api.services.digest_service import run_digest
from visa_jobs_api.config import Settings
from visa_jobs_api.shared.call_stats import CallStats
from visa_jobs_api.shared.models import NormalizedJob


def _settings(**overrides: object) -> Settings:
    defaults: dict[str, object] = dict(
        hf_token="fake",
        gmail_address="a@b.com",
        gmail_app_password="pw",
        digest_recipients="me@example.com,you@example.com",
        decodo_username="decodo-user",
        decodo_password="decodo-pass",
        brightdata_api_key="bd-key",
        brightdata_zone="bd-zone",
    )
    defaults.update(overrides)
    return Settings(**defaults)


def _job(source: str, country: str = "Ireland") -> NormalizedJob:
    return NormalizedJob(
        source=source,
        title="Backend Engineer",
        company="Acme",
        url="https://x/1",
        posted_at=datetime(2026, 7, 4, tzinfo=timezone.utc),
        country=country,
        location_label=country,
        tech_stack=[],
        visa_reason="explicit offer",
    )


async def test_run_digest_sends_success_email_and_returns_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    jobs = [_job("hn_who_is_hiring"), _job("linkedin"), _job("linkedin")]
    monkeypatch.setattr("visa_jobs_api.api.services.digest_service.build_sources", lambda **kwargs: [])
    monkeypatch.setattr("visa_jobs_api.api.services.digest_service.collect_jobs", AsyncMock(return_value=jobs))
    send_success = AsyncMock()
    send_failure = AsyncMock()
    monkeypatch.setattr("visa_jobs_api.api.services.digest_service.send_success_email", send_success)
    monkeypatch.setattr("visa_jobs_api.api.services.digest_service.send_failure_email", send_failure)

    result = await run_digest(settings=_settings())

    assert result.total_jobs == 3
    assert result.countries == 1
    assert {sc.source: sc.confirmed_jobs for sc in result.by_source} == {"hn_who_is_hiring": 1, "linkedin": 2}
    assert result.email_sent_to == ["me@example.com", "you@example.com"]
    send_success.assert_called_once()
    send_failure.assert_not_called()


async def test_run_digest_sends_failure_email_and_reraises_on_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("visa_jobs_api.api.services.digest_service.build_sources", lambda **kwargs: [])
    monkeypatch.setattr(
        "visa_jobs_api.api.services.digest_service.collect_jobs", AsyncMock(side_effect=RuntimeError("source 'linkedin' failed"))
    )
    send_success = AsyncMock()
    send_failure = AsyncMock()
    monkeypatch.setattr("visa_jobs_api.api.services.digest_service.send_success_email", send_success)
    monkeypatch.setattr("visa_jobs_api.api.services.digest_service.send_failure_email", send_failure)

    with pytest.raises(RuntimeError, match="source 'linkedin' failed"):
        await run_digest(settings=_settings())

    send_success.assert_not_called()
    send_failure.assert_called_once()
    assert "source 'linkedin' failed" in send_failure.call_args.kwargs["reason"]


async def test_run_digest_passes_a_shared_call_stats_instance_to_build_sources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    build_sources_mock = MagicMock(return_value=[])
    monkeypatch.setattr("visa_jobs_api.api.services.digest_service.build_sources", build_sources_mock)
    monkeypatch.setattr("visa_jobs_api.api.services.digest_service.collect_jobs", AsyncMock(return_value=[]))
    monkeypatch.setattr("visa_jobs_api.api.services.digest_service.send_success_email", AsyncMock())
    monkeypatch.setattr("visa_jobs_api.api.services.digest_service.send_failure_email", AsyncMock())

    await run_digest(settings=_settings())

    assert isinstance(build_sources_mock.call_args.kwargs["call_stats"], CallStats)


async def test_run_digest_logs_the_call_stats_summary_even_when_a_source_fails(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    # The combined per-country Bright Data/Decodo call summary is logged
    # from a finally block specifically so it's still visible when
    # diagnosing a failed run, not only on success.
    monkeypatch.setattr("visa_jobs_api.api.services.digest_service.build_sources", lambda **kwargs: [])
    monkeypatch.setattr(
        "visa_jobs_api.api.services.digest_service.collect_jobs", AsyncMock(side_effect=RuntimeError("boom"))
    )
    monkeypatch.setattr("visa_jobs_api.api.services.digest_service.send_success_email", AsyncMock())
    monkeypatch.setattr("visa_jobs_api.api.services.digest_service.send_failure_email", AsyncMock())

    with caplog.at_level(logging.INFO), pytest.raises(RuntimeError):
        await run_digest(settings=_settings())

    assert any("Bright Data (Indeed) / Decodo (LinkedIn)" in record.getMessage() for record in caplog.records)
