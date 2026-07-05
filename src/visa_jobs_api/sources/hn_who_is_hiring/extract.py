"""Extracts visa-sponsorship candidate postings from a JobPost.

Each top-level comment on a "Who is hiring?" thread is one company's job
posting; replies underneath it are discussion, not separate postings, so
they never become listings of their own. But a company doesn't always
mention sponsorship in the posting itself -- sometimes someone asks about it
in a reply and the poster confirms it there instead -- so replies (at any
depth) are also scanned for corroborating evidence for that same posting.

Only postings from the last `posted_within_hours` are considered at all
(HN's thread accumulates comments for an entire month, but this digest is a
24-hours-old snapshot) -- a reply confirming sponsorship doesn't rescue a
posting whose own top-level comment falls outside that window, since the
window is about when the job was posted, not when it was discussed.

Ported from job_digest/src/job_digest/sources/hn_who_is_hiring/visa_filter.py's
extract_visa_jobs/_detect_location/_detect_tech_stack, using the shared
regex matcher instead of a local copy.
"""

from __future__ import annotations

import re
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from visa_jobs_api.shared.visa_keywords import find_visa_mentions
from visa_jobs_api.sources.hn_who_is_hiring.models import Comment, JobPost

# HN's own posting convention is "Company | Role | Location | ...", so the
# role segment (index 1) is never treated as a location candidate -- role
# titles can coincidentally contain a comma-separated list (e.g. "Backend,
# Frontend, Fullstack Engineer") that would otherwise look like a "City,
# Country" match.
_LOCATION_WORK_MODE_PATTERN = re.compile(r"\b(remote|on-?site|hybrid|in[\s-]?person)\b", re.IGNORECASE)
_CITY_COUNTRY_PATTERN = re.compile(r"\b[A-Z][A-Za-z.'-]+(?:\s[A-Z][A-Za-z.'-]+)*,\s*[A-Z][A-Za-z.'-]+")

# Canonical tech name -> regexes that identify a mention of it. Word
# boundaries and, where needed, negative lookaheads keep short/ambiguous
# names (e.g. "Java" vs "JavaScript") from over- or under-matching.
_TECH_PATTERNS: dict[str, list[re.Pattern[str]]] = {
    "Python": [re.compile(r"\bpython\b", re.IGNORECASE)],
    "Java": [re.compile(r"\bjava\b(?!\s?script)", re.IGNORECASE)],
    "JavaScript": [re.compile(r"\bjavascript\b", re.IGNORECASE), re.compile(r"\bjs\b")],
    "TypeScript": [re.compile(r"\btypescript\b", re.IGNORECASE), re.compile(r"\bts\b")],
    "Ruby": [re.compile(r"\bruby\b", re.IGNORECASE)],
    "Ruby on Rails": [re.compile(r"\brails\b", re.IGNORECASE)],
    "Golang": [re.compile(r"\bgolang\b", re.IGNORECASE), re.compile(r"\bgo\s?lang\b", re.IGNORECASE)],
    "Rust": [re.compile(r"\brust\b", re.IGNORECASE)],
    "C++": [re.compile(r"c\+\+", re.IGNORECASE)],
    "C#": [re.compile(r"c#", re.IGNORECASE)],
    "PHP": [re.compile(r"\bphp\b", re.IGNORECASE)],
    "Swift": [re.compile(r"\bswift\b", re.IGNORECASE)],
    "Kotlin": [re.compile(r"\bkotlin\b", re.IGNORECASE)],
    "Scala": [re.compile(r"\bscala\b", re.IGNORECASE)],
    "Elixir": [re.compile(r"\belixir\b", re.IGNORECASE)],
    "React": [re.compile(r"\breact(?:\.js|js)?\b", re.IGNORECASE)],
    "Vue": [re.compile(r"\bvue(?:\.js|js)?\b", re.IGNORECASE)],
    "Angular": [re.compile(r"\bangular\b", re.IGNORECASE)],
    "Next.js": [re.compile(r"\bnext\.?js\b", re.IGNORECASE)],
    "Node.js": [re.compile(r"\bnode(?:\.js|js)?\b", re.IGNORECASE)],
    "Django": [re.compile(r"\bdjango\b", re.IGNORECASE)],
    "Flask": [re.compile(r"\bflask\b", re.IGNORECASE)],
    "FastAPI": [re.compile(r"\bfastapi\b", re.IGNORECASE)],
    "Spring": [re.compile(r"\bspring(?:boot| boot)?\b", re.IGNORECASE)],
    ".NET": [re.compile(r"\.net\b", re.IGNORECASE)],
    "AWS": [re.compile(r"\baws\b", re.IGNORECASE)],
    "GCP": [re.compile(r"\bgcp\b", re.IGNORECASE), re.compile(r"google cloud", re.IGNORECASE)],
    "Azure": [re.compile(r"\bazure\b", re.IGNORECASE)],
    "Docker": [re.compile(r"\bdocker\b", re.IGNORECASE)],
    "Kubernetes": [re.compile(r"\bkubernetes\b", re.IGNORECASE), re.compile(r"\bk8s\b", re.IGNORECASE)],
    "PostgreSQL": [re.compile(r"\bpostgres(?:ql)?\b", re.IGNORECASE)],
    "MySQL": [re.compile(r"\bmysql\b", re.IGNORECASE)],
    "MongoDB": [re.compile(r"\bmongo(?:db)?\b", re.IGNORECASE)],
    "Redis": [re.compile(r"\bredis\b", re.IGNORECASE)],
    "GraphQL": [re.compile(r"\bgraphql\b", re.IGNORECASE)],
    "SQL": [re.compile(r"\bsql\b", re.IGNORECASE)],
    "Swift/iOS": [re.compile(r"\bios\b", re.IGNORECASE)],
    "Android/Kotlin": [re.compile(r"\bandroid\b", re.IGNORECASE)],
}


class HnJobCandidate(BaseModel):
    """A single top-level posting that regex-matched a visa mention and is within the recency window."""

    model_config = ConfigDict(frozen=True)

    comment_id: int
    posted_at: datetime
    company: str
    role_line: str
    location: str | None
    tech_stack: list[str]
    visa_mentions: list[str]
    url: str
    posting_text: str
    # Full text of any reply (anywhere in the thread under this posting)
    # that contributed a visa mention not found in the posting itself.
    supporting_replies: list[str] = Field(default_factory=list)


def extract_candidates(job_post: JobPost, *, earliest_posted_at: datetime) -> list[HnJobCandidate]:
    """Return every top-level posting, posted at/after earliest_posted_at, that mentions visa sponsorship."""
    candidates: list[HnJobCandidate] = []
    for comment in job_post.comments:
        if comment.is_deleted or comment.text is None or comment.posted_at < earliest_posted_at:
            continue

        own_mentions = find_visa_mentions(comment.text)
        reply_mentions, supporting_replies = _find_visa_mentions_in_replies(comment.replies)
        all_mentions = own_mentions + reply_mentions
        if not all_mentions:
            continue

        candidates.append(_to_candidate(comment, all_mentions, supporting_replies))
    return candidates


def _find_visa_mentions_in_replies(replies: list[Comment]) -> tuple[list[str], list[str]]:
    """Recursively scan a posting's replies for genuine (non-question) visa mentions."""
    mentions: list[str] = []
    supporting_replies: list[str] = []
    for reply in replies:
        if not reply.is_deleted and reply.text:
            found = find_visa_mentions(reply.text)
            if found:
                mentions.extend(found)
                supporting_replies.append(reply.text)
        nested_mentions, nested_replies = _find_visa_mentions_in_replies(reply.replies)
        mentions.extend(nested_mentions)
        supporting_replies.extend(nested_replies)
    return mentions, supporting_replies


def _to_candidate(comment: Comment, mentions: list[str], supporting_replies: list[str]) -> HnJobCandidate:
    # The header line (company/role/location) is always the first physical
    # line of the posting, whether HN separated it from the body with a
    # paragraph break or the poster only pressed Enter once.
    role_line = comment.text.splitlines()[0].strip() if comment.text else ""
    company = role_line.split("|", 1)[0].strip() or role_line
    return HnJobCandidate(
        comment_id=comment.id,
        posted_at=comment.posted_at,
        company=company,
        role_line=role_line,
        location=_detect_location(role_line),
        tech_stack=_detect_tech_stack(comment.text or ""),
        visa_mentions=mentions,
        url=f"https://news.ycombinator.com/item?id={comment.id}",
        posting_text=comment.text or "",
        supporting_replies=supporting_replies,
    )


def _detect_location(role_line: str) -> str | None:
    segments = [segment.strip() for segment in role_line.split("|") if segment.strip()]
    location_segments = [
        segment
        for segment in segments[2:]
        if _LOCATION_WORK_MODE_PATTERN.search(segment) or _CITY_COUNTRY_PATTERN.search(segment)
    ]
    return " | ".join(location_segments) if location_segments else None


def _detect_tech_stack(text: str) -> list[str]:
    found = [name for name, patterns in _TECH_PATTERNS.items() if any(p.search(text) for p in patterns)]
    return sorted(found)
