"""Response DTOs for the digest API."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class SourceCount(BaseModel):
    model_config = ConfigDict(frozen=True)

    source: str
    confirmed_jobs: int


class DigestRunResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    total_jobs: int
    countries: int
    by_source: list[SourceCount]
    email_sent_to: list[str]
