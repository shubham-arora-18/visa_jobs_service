"""Parses Hacker News items (fetched via Algolia) into JobPost/Comment models.

Algolia's item payload already nests replies under `children`, so there is
no indent-reconstruction step needed here (unlike scraping the HTML page).
It also excludes fully-deleted and hidden-flagged comments from the tree
entirely, and represents any flagged comment's body as a literal "[flagged]"
placeholder string rather than its real text -- both are treated as
"content unavailable" here, consistent with how the rest of the pipeline
already skips comments with no text.

Pure CPU parsing, no I/O -- ported unchanged from
job_digest/src/job_digest/sources/hn_who_is_hiring/parse.py.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any

from bs4 import BeautifulSoup

from visa_jobs_api.sources.hn_who_is_hiring.models import Comment, JobPost

logger = logging.getLogger(__name__)

_PARAGRAPH_BREAK_RE = re.compile(r"<p\b[^>]*>", re.IGNORECASE)
_LINE_BREAK_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)
_FLAGGED_PLACEHOLDER = "[flagged]"
_DEAD_PLACEHOLDER = "[dead]"


class HnItemParseError(ValueError):
    """Raised when an Algolia HN item payload doesn't match the expected shape."""


def parse_job_post(payload: dict[str, Any]) -> JobPost:
    """Parse an Algolia HN item payload into a JobPost with its nested Comment tree."""
    try:
        post_id = int(payload["id"])
        title = payload["title"]
        author = payload["author"]
        points = payload["points"]
        posted_at = _parse_timestamp(payload, context="submission")
    except KeyError as exc:
        raise HnItemParseError(f"HN item payload is missing expected field: {exc}") from exc

    if title is None or author is None:
        raise HnItemParseError("HN item payload has a null title or author on the root story")

    text = _clean_optional_text(payload.get("text"))
    comments = [_parse_comment(child, depth=0) for child in payload.get("children") or []]
    comment_count = _count_all(comments)

    logger.info(
        "Parsed job post %d (%r): %d top-level comments, %d total comments",
        post_id,
        title,
        len(comments),
        comment_count,
    )

    return JobPost(
        id=post_id,
        title=title,
        author=author,
        points=points or 0,
        posted_at=posted_at,
        text=text,
        comment_count=comment_count,
        comments=comments,
    )


def _parse_comment(node: dict[str, Any], *, depth: int) -> Comment:
    try:
        comment_id = int(node["id"])
        posted_at = _parse_timestamp(node, context=f"comment {node.get('id')}")
    except KeyError as exc:
        raise HnItemParseError(f"comment payload is missing expected field: {exc}") from exc

    author = node.get("author")
    raw_text = node.get("text")
    is_flagged = raw_text is not None and raw_text.strip() == _FLAGGED_PLACEHOLDER
    is_dead_placeholder = raw_text is not None and raw_text.strip() == _DEAD_PLACEHOLDER
    is_deleted = author is None or raw_text is None or is_dead_placeholder

    text = None if (is_deleted or is_flagged) else _clean_optional_text(raw_text)

    replies = [_parse_comment(child, depth=depth + 1) for child in node.get("children") or []]

    return Comment(
        id=comment_id,
        author=None if is_deleted else author,
        posted_at=posted_at,
        depth=depth,
        text=text,
        is_deleted=is_deleted,
        is_flagged=is_flagged,
        replies=replies,
    )


def _count_all(comments: list[Comment]) -> int:
    return sum(1 + _count_all(comment.replies) for comment in comments)


def _parse_timestamp(payload: dict[str, Any], *, context: str) -> datetime:
    timestamp = payload.get("created_at_i")
    if timestamp is None:
        raise HnItemParseError(f"{context} is missing created_at_i")
    return datetime.fromtimestamp(timestamp, tz=timezone.utc)


def _clean_optional_text(raw_text: str | None) -> str | None:
    return clean_html_text(raw_text) if raw_text else None


def clean_html_text(inner_html: str) -> str:
    """Convert a raw HN-authored HTML fragment into readable plain text.

    HN marks paragraph breaks with a lone, unclosed <p>, which a real HTML
    parser auto-nests into a deep tree. Reading get_text() with a separator
    would then also insert that separator around every inline tag (e.g. a
    link), fragmenting a single line like "Stream (https://getstream.io/) |
    ..." into three pieces. So instead we turn <p>/<br> into explicit line
    breaks in the raw markup first, then let get_text() concatenate the
    remaining (now flat, paragraph-free) inline content without inserting
    any extra separators of its own.
    """
    inner_html = _PARAGRAPH_BREAK_RE.sub("\n\n", inner_html)
    inner_html = _LINE_BREAK_RE.sub("\n", inner_html)
    text = BeautifulSoup(inner_html, "lxml").get_text()
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
