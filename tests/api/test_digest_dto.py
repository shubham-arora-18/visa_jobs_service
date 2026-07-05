from __future__ import annotations

import pytest
from pydantic import ValidationError

from visa_jobs_api.api.dto.digest import DigestRunRequest
from visa_jobs_api.sources.linkedin.queries import DEFAULT_KEYWORDS


def test_default_request_uses_default_keywords_and_a_one_day_window() -> None:
    request = DigestRunRequest()

    assert request.linkedin_keywords == DEFAULT_KEYWORDS
    assert request.posted_within == "day"
    assert request.posted_within_hours() == 24


def test_week_and_month_resolve_to_the_expected_hour_counts() -> None:
    assert DigestRunRequest(posted_within="week").posted_within_hours() == 24 * 7
    assert DigestRunRequest(posted_within="month").posted_within_hours() == 24 * 30


def test_custom_keywords_are_kept_verbatim() -> None:
    request = DigestRunRequest(linkedin_keywords="(Rust OR Go) AND sponsor")

    assert request.linkedin_keywords == "(Rust OR Go) AND sponsor"


def test_invalid_posted_within_value_is_rejected() -> None:
    with pytest.raises(ValidationError):
        DigestRunRequest(posted_within="year")
