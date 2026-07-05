"""Common domain model that every source normalizes its own results into.

The aggregator only ever works with NormalizedJob -- it doesn't know or care
whether a listing came from Hacker News or LinkedIn. `country` is None when
neither the source's own structured data nor the LLM's best-effort guess
could confidently identify one; the aggregator buckets those into an
explicit "Remote / Unspecified" group at render time rather than guessing
further.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class NormalizedJob(BaseModel):
    model_config = ConfigDict(frozen=True)

    source: str
    title: str
    company: str
    url: str
    posted_at: datetime
    country: str | None
    location_label: str
    tech_stack: list[str] = Field(default_factory=list)
    visa_reason: str
