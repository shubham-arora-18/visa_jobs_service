from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from visa_jobs_api.api.services.digest_service import run_digest
from visa_jobs_api.config import Settings
from visa_jobs_api.shared.models import NormalizedJob


def _settings(**overrides: object) -> Settings:
    defaults: dict[str, object] = dict(
        hf_token="fake",
        gmail_address="a@b.com",
        gmail_app_password="pw",
        digest_recipients="me@example.com,you@example.com",
        brightdata_api_key="bd-key",
        brightdata_zone="zone",
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
