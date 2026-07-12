from __future__ import annotations

import pytest
from pydantic import ValidationError

from visa_jobs_api.api.dto.digest import DigestRunRequest
from visa_jobs_api.sources.indeed.queries import DEFAULT_KEYWORDS as INDEED_DEFAULT_KEYWORDS
from visa_jobs_api.sources.linkedin.queries import DEFAULT_KEYWORDS as LINKEDIN_DEFAULT_KEYWORDS


def test_default_request_uses_default_keywords_and_a_one_day_window() -> None:
    request = DigestRunRequest()

    assert request.linkedin_keywords == LINKEDIN_DEFAULT_KEYWORDS
    assert request.indeed_keywords == INDEED_DEFAULT_KEYWORDS
    assert request.posted_within == "day"
    assert request.posted_within_hours() == 24
    assert request.posted_within_label() == "1 Day"


def test_week_and_month_resolve_to_the_expected_hour_counts() -> None:
    assert DigestRunRequest(posted_within="week").posted_within_hours() == 24 * 7
    assert DigestRunRequest(posted_within="month").posted_within_hours() == 24 * 30


def test_week_and_month_resolve_to_the_expected_display_labels() -> None:
    # The email headline should say "1 Week"/"1 Month", not an hour count.
    assert DigestRunRequest(posted_within="week").posted_within_label() == "1 Week"
    assert DigestRunRequest(posted_within="month").posted_within_label() == "1 Month"


def test_custom_keywords_are_kept_verbatim() -> None:
    request = DigestRunRequest(linkedin_keywords="(Rust OR Go) AND sponsor")

    assert request.linkedin_keywords == "(Rust OR Go) AND sponsor"


def test_linkedin_and_indeed_keywords_are_independently_settable() -> None:
    # Different inputs for each source, as required -- setting one must not
    # affect the other's default.
    request = DigestRunRequest(indeed_keywords="(Rust OR Go) AND sponsor")

    assert request.indeed_keywords == "(Rust OR Go) AND sponsor"
    assert request.linkedin_keywords == LINKEDIN_DEFAULT_KEYWORDS


def test_invalid_posted_within_value_is_rejected() -> None:
    with pytest.raises(ValidationError):
        DigestRunRequest(posted_within="year")
