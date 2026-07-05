from __future__ import annotations

from datetime import datetime, timedelta, timezone

from visa_jobs_api.sources.hn_who_is_hiring.extract import _detect_location, _detect_tech_stack, extract_candidates
from visa_jobs_api.sources.hn_who_is_hiring.models import Comment, JobPost

_NOW = datetime(2026, 7, 5, 12, 0, tzinfo=timezone.utc)


def _make_comment(
    id: int,
    text: str | None,
    *,
    depth: int = 0,
    is_deleted: bool = False,
    posted_at: datetime | None = None,
) -> Comment:
    return Comment(
        id=id,
        author=None if is_deleted else "someone",
        posted_at=posted_at or _NOW,
        depth=depth,
        text=text,
        is_deleted=is_deleted,
        is_flagged=False,
    )


def _make_job_post(comments: list[Comment]) -> JobPost:
    return JobPost(
        id=1,
        title="Ask HN: Who is hiring?",
        author="whoishiring",
        points=1,
        posted_at=_NOW,
        text=None,
        comment_count=len(comments),
        comments=comments,
    )


def test_detect_location_finds_city_country_segment() -> None:
    role_line = "BIT Capital | Head of Engineering | Berlin, Germany | ONSITE (2 days/week) | Full-time"
    assert _detect_location(role_line) == "Berlin, Germany | ONSITE (2 days/week)"


def test_detect_location_finds_remote_keyword_without_city() -> None:
    role_line = "Emergences Labs | AI Engineer | REMOTE (US) | Full-time"
    assert _detect_location(role_line) == "REMOTE (US)"


def test_detect_location_returns_none_when_no_signal_present() -> None:
    role_line = "Founding Senior (AI/Software) Engineer | NYC |"
    assert _detect_location(role_line) is None


def test_detect_tech_stack_avoids_java_javascript_confusion() -> None:
    assert _detect_tech_stack("We use JavaScript on the frontend.") == ["JavaScript"]


def test_extract_candidates_never_creates_a_separate_entry_for_a_reply() -> None:
    top_level = _make_comment(1, "Acme | Backend Engineer | REMOTE\n\nWe offer visa sponsorship.")
    reply = _make_comment(2, "We also sponsor visas for the right candidate.", depth=1)
    job_post = _make_job_post([top_level])
    job_post.comments[0].replies.append(reply)

    candidates = extract_candidates(job_post, earliest_posted_at=_NOW - timedelta(hours=24))

    assert [c.comment_id for c in candidates] == [1]


def test_extract_candidates_finds_a_mention_only_present_in_a_reply() -> None:
    top_level = _make_comment(1, "Acme | Backend Engineer | REMOTE\n\nGreat team, fully remote.")
    reply = _make_comment(2, "Yes, we do sponsor work visas for this role.", depth=1)
    job_post = _make_job_post([top_level])
    job_post.comments[0].replies.append(reply)

    candidates = extract_candidates(job_post, earliest_posted_at=_NOW - timedelta(hours=24))

    assert len(candidates) == 1
    assert candidates[0].visa_mentions == ["Yes, we do sponsor work visas for this role."]
    assert candidates[0].supporting_replies == ["Yes, we do sponsor work visas for this role."]


def test_extract_candidates_skips_deleted_and_non_matching_comments() -> None:
    deleted = _make_comment(1, None, is_deleted=True)
    no_visa = _make_comment(2, "Acme | Backend Engineer | REMOTE\n\nNo sponsorship, sorry.")
    has_visa = _make_comment(3, "Acme | Backend Engineer | REMOTE\n\nWe can sponsor a visa.")
    job_post = _make_job_post([deleted, no_visa, has_visa])

    candidates = extract_candidates(job_post, earliest_posted_at=_NOW - timedelta(hours=24))

    assert [c.comment_id for c in candidates] == [3]


def test_extract_candidates_parses_company_from_role_line() -> None:
    comment = _make_comment(1, "Hotwash | Founding Engineer | REMOTE (US)\n\nWe offer visa sponsorship.")
    job_post = _make_job_post([comment])

    candidates = extract_candidates(job_post, earliest_posted_at=_NOW - timedelta(hours=24))

    assert candidates[0].company == "Hotwash"
    assert candidates[0].url == "https://news.ycombinator.com/item?id=1"


def test_extract_candidates_excludes_postings_older_than_the_window() -> None:
    # Posted 30 hours ago -- outside a 24h window even though it otherwise
    # mentions sponsorship.
    old_comment = _make_comment(
        1,
        "Acme | Backend Engineer | REMOTE\n\nWe offer visa sponsorship.",
        posted_at=_NOW - timedelta(hours=30),
    )
    recent_comment = _make_comment(
        2,
        "Zeta | Backend Engineer | REMOTE\n\nWe offer visa sponsorship.",
        posted_at=_NOW - timedelta(hours=1),
    )
    job_post = _make_job_post([old_comment, recent_comment])

    candidates = extract_candidates(job_post, earliest_posted_at=_NOW - timedelta(hours=24))

    assert [c.comment_id for c in candidates] == [2]


def test_extract_candidates_a_reply_does_not_rescue_a_posting_outside_the_window() -> None:
    # The posting itself is outside the recency window; a fresh reply
    # confirming sponsorship must not pull it back in -- the window is about
    # when the job was *posted*, not when it was last discussed.
    old_top_level = _make_comment(
        1, "Acme | Backend Engineer | REMOTE\n\nGreat team.", posted_at=_NOW - timedelta(hours=48)
    )
    fresh_reply = _make_comment(2, "We do sponsor visas.", depth=1, posted_at=_NOW - timedelta(hours=1))
    job_post = _make_job_post([old_top_level])
    job_post.comments[0].replies.append(fresh_reply)

    candidates = extract_candidates(job_post, earliest_posted_at=_NOW - timedelta(hours=24))

    assert candidates == []
