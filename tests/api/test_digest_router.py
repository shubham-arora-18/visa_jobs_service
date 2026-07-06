from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from visa_jobs_api.api.dto.digest import DigestRunResponse, SourceCount
from visa_jobs_api.main import app
from visa_jobs_api.sources.linkedin.queries import DEFAULT_KEYWORDS


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_health_returns_ok(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_trigger_digest_run_returns_the_service_response(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_response = DigestRunResponse(
        total_jobs=3,
        countries=2,
        by_source=[SourceCount(source="hn_who_is_hiring", confirmed_jobs=1), SourceCount(source="linkedin", confirmed_jobs=2)],
        email_sent_to=["me@example.com"],
    )
    monkeypatch.setattr("visa_jobs_api.api.routers.digest.run_digest", AsyncMock(return_value=fake_response))

    response = client.post("/digest/run")

    assert response.status_code == 200
    assert response.json()["total_jobs"] == 3
    assert response.json()["email_sent_to"] == ["me@example.com"]


def test_trigger_digest_run_returns_502_when_the_pipeline_fails(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "visa_jobs_api.api.routers.digest.run_digest", AsyncMock(side_effect=RuntimeError("source 'linkedin' failed"))
    )

    response = client.post("/digest/run")

    assert response.status_code == 502
    assert "source 'linkedin' failed" in response.json()["detail"]


def test_trigger_digest_run_defaults_to_default_keywords_and_a_day_window(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_response = DigestRunResponse(total_jobs=0, countries=0, by_source=[], email_sent_to=["me@example.com"])
    run_digest_mock = AsyncMock(return_value=fake_response)
    monkeypatch.setattr("visa_jobs_api.api.routers.digest.run_digest", run_digest_mock)

    client.post("/digest/run")

    assert run_digest_mock.call_args.kwargs["linkedin_keywords"] == DEFAULT_KEYWORDS
    assert run_digest_mock.call_args.kwargs["posted_within_hours"] == 24
    assert run_digest_mock.call_args.kwargs["posted_within_label"] == "1 Day"


def test_trigger_digest_run_passes_through_custom_keywords_and_window(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_response = DigestRunResponse(total_jobs=0, countries=0, by_source=[], email_sent_to=["me@example.com"])
    run_digest_mock = AsyncMock(return_value=fake_response)
    monkeypatch.setattr("visa_jobs_api.api.routers.digest.run_digest", run_digest_mock)

    client.post("/digest/run", json={"linkedin_keywords": "(Rust OR Go) AND sponsor", "posted_within": "week"})

    assert run_digest_mock.call_args.kwargs["linkedin_keywords"] == "(Rust OR Go) AND sponsor"
    assert run_digest_mock.call_args.kwargs["posted_within_hours"] == 24 * 7
    assert run_digest_mock.call_args.kwargs["posted_within_label"] == "1 Week"
