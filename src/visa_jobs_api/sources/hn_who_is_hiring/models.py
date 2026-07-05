"""Typed domain models for a Hacker News item thread: JobPost -> Comment.

Ported unchanged from job_digest/src/job_digest/sources/hn_who_is_hiring/models.py.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Comment(BaseModel):
    """A single comment in the thread, with its nested replies attached."""

    model_config = ConfigDict(frozen=True)

    id: int
    author: str | None
    posted_at: datetime
    depth: int
    text: str | None
    is_deleted: bool
    is_flagged: bool
    replies: list[Comment] = Field(default_factory=list)

    @model_validator(mode="after")
    def _deleted_comments_have_no_author_or_text(self) -> Comment:
        if self.is_deleted and (self.author is not None or self.text is not None):
            raise ValueError("a deleted comment must not have an author or text")
        return self


class JobPost(BaseModel):
    """The root submission (e.g. an 'Ask HN: Who is hiring?' post) and its comment tree."""

    model_config = ConfigDict(frozen=True)

    id: int
    title: str
    author: str
    points: int
    posted_at: datetime
    text: str | None
    comment_count: int
    comments: list[Comment] = Field(default_factory=list)
