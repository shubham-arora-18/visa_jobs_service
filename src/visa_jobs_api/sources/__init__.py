"""Shared contract that every job source implements.

Adding a new producer means creating a new subpackage here with its own
fetch/parse/filter logic, plus a class implementing JobSource, then
registering an instance of it in aggregation.aggregator.SOURCES. The
aggregator never needs to know anything about a specific source's internals
-- it only ever awaits fetch_jobs().
"""

from __future__ import annotations

from typing import Protocol

from visa_jobs_api.shared.models import NormalizedJob


class JobSource(Protocol):
    """A pluggable async producer of normalized job listings.

    Each source owns its entire pipeline (discovery, fetching, parsing,
    filtering) and exposes only this one method, returning already-filtered
    (recency window, visa-confirmed) NormalizedJob instances. Implementations
    must raise on failure rather than returning a partial/empty list, so the
    aggregator can tell "no jobs found this run" apart from "this source is
    broken".
    """

    name: str

    async def fetch_jobs(self) -> list[NormalizedJob]: ...
