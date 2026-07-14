from __future__ import annotations

import logging

from visa_jobs_api.shared.call_stats import CallStats


def test_records_all_four_call_types_independently_per_country() -> None:
    stats = CallStats()
    stats.record_indeed_search_call("United States")
    stats.record_indeed_search_call("United States")
    stats.record_indeed_description_call("United States")
    stats.record_linkedin_search_call("Ireland")
    stats.record_linkedin_description_call("Ireland")
    stats.record_linkedin_description_call("Ireland")

    assert stats.indeed_search_calls == {"United States": 2}
    assert stats.indeed_description_calls == {"United States": 1}
    assert stats.linkedin_search_calls == {"Ireland": 1}
    assert stats.linkedin_description_calls == {"Ireland": 2}


def test_log_summary_includes_a_line_per_country_with_all_four_counts(caplog) -> None:
    stats = CallStats()
    stats.record_indeed_search_call("United States")
    stats.record_indeed_description_call("United States")
    stats.record_linkedin_search_call("United States")
    stats.record_linkedin_description_call("United States")
    stats.record_linkedin_search_call("Ireland")

    logger = logging.getLogger("test-call-stats")
    with caplog.at_level(logging.INFO, logger="test-call-stats"):
        stats.log_summary(logger)

    messages = "\n".join(record.getMessage() for record in caplog.records)
    assert "United States: Selenium calls for Indeed job card: 1" in messages
    assert "Decodo API calls for Indeed job details: 1" in messages
    assert "Decodo API calls for LinkedIn job card: 1" in messages
    assert "Decodo API calls for LinkedIn job details: 1" in messages
    # A country with only one of the four call types still gets a line,
    # with the other three shown as 0 rather than being omitted.
    assert "Ireland: Selenium calls for Indeed job card: 0" in messages


def test_log_summary_handles_no_calls_recorded(caplog) -> None:
    stats = CallStats()
    logger = logging.getLogger("test-call-stats-empty")
    with caplog.at_level(logging.INFO, logger="test-call-stats-empty"):
        stats.log_summary(logger)
    # Just the header line, no per-country lines, and no crash.
    assert any(
        "Selenium (Indeed search) / Decodo (Indeed details, LinkedIn)" in record.getMessage()
        for record in caplog.records
    )
