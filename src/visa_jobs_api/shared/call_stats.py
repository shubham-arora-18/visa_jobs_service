"""Per-country, per-provider call counters for the whole digest run.

One instance is created per `run_digest()` call and threaded explicitly
through both the LinkedIn (Decodo) and Indeed (Bright Data) sources --
not kept as module-level global state, so counts from one digest run
never leak into another (concurrent API requests, repeated test runs).

Deliberately scoped to exactly the four call types requested: search-page
and description-page fetches for each of the two proxy-bound sources. LLM
confirmation calls go to Hugging Face, not Bright Data/Decodo, so they are
out of scope for this tracker.
"""

from __future__ import annotations

import logging
from collections import defaultdict


class CallStats:
    """Counts Bright Data (Indeed) and Decodo (LinkedIn) calls per query country."""

    def __init__(self) -> None:
        self.indeed_search_calls: dict[str, int] = defaultdict(int)
        self.indeed_description_calls: dict[str, int] = defaultdict(int)
        self.linkedin_search_calls: dict[str, int] = defaultdict(int)
        self.linkedin_description_calls: dict[str, int] = defaultdict(int)

    def record_indeed_search_call(self, country: str) -> None:
        self.indeed_search_calls[country] += 1

    def record_indeed_description_call(self, country: str) -> None:
        self.indeed_description_calls[country] += 1

    def record_linkedin_search_call(self, country: str) -> None:
        self.linkedin_search_calls[country] += 1

    def record_linkedin_description_call(self, country: str) -> None:
        self.linkedin_description_calls[country] += 1

    def log_summary(self, log: logging.Logger) -> None:
        countries = sorted(
            set(self.indeed_search_calls)
            | set(self.indeed_description_calls)
            | set(self.linkedin_search_calls)
            | set(self.linkedin_description_calls)
        )
        log.info("Bright Data (Indeed) / Decodo (LinkedIn) API calls by country:")
        for country in countries:
            log.info(
                "%s: Bright Data API calls for Indeed job card: %d  "
                "Bright Data API calls for Indeed job details: %d  "
                "Decodo API calls for LinkedIn job card: %d  "
                "Decodo API calls for LinkedIn job details: %d",
                country,
                self.indeed_search_calls.get(country, 0),
                self.indeed_description_calls.get(country, 0),
                self.linkedin_search_calls.get(country, 0),
                self.linkedin_description_calls.get(country, 0),
            )
