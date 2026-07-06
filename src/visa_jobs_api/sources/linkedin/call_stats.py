"""Per-country call counters for the LinkedIn pipeline.

One instance is created per `LinkedInSource.fetch_jobs()` call and threaded
explicitly through search/description/LLM confirmation (not kept as
module-level global state), so counts from one digest run never leak into
another -- e.g. concurrent API requests, or repeated test runs.
"""

from __future__ import annotations

import logging
from collections import defaultdict


class CallStats:
    """Counts LinkedIn search-page fetches, description fetches, and LLM calls per query country."""

    def __init__(self) -> None:
        self.search_calls: dict[str, int] = defaultdict(int)
        self.description_calls: dict[str, int] = defaultdict(int)
        self.llm_calls: dict[str, int] = defaultdict(int)

    def record_search_call(self, country: str) -> None:
        self.search_calls[country] += 1

    def record_description_call(self, country: str) -> None:
        self.description_calls[country] += 1

    def record_llm_call(self, country: str) -> None:
        self.llm_calls[country] += 1

    def log_summary(self, log: logging.Logger) -> None:
        countries = sorted(set(self.search_calls) | set(self.description_calls) | set(self.llm_calls))
        log.info("LinkedIn calls by country (search page fetches / description fetches / LLM calls):")
        for country in countries:
            log.info(
                "  %s: search=%d, description=%d, llm=%d",
                country,
                self.search_calls.get(country, 0),
                self.description_calls.get(country, 0),
                self.llm_calls.get(country, 0),
            )
