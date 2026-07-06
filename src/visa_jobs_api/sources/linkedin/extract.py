"""Title filtering and de-duplication for LinkedIn job cards."""

from __future__ import annotations

from visa_jobs_api.sources.linkedin.models import JobCard
from visa_jobs_api.sources.linkedin.queries import TITLE_EXCLUDE, TITLE_INCLUDE


def dedupe_cards(cards: list[JobCard]) -> list[JobCard]:
    """Drop cards with the same (title, company) as one already seen, keeping the first."""
    seen: set[tuple[str, str]] = set()
    deduped: list[JobCard] = []
    for card in cards:
        key = (card.title, card.company)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(card)
    return deduped


def filter_by_title(cards: list[JobCard]) -> list[JobCard]:
    """Keep only cards whose title matches TITLE_INCLUDE and none of TITLE_EXCLUDE."""
    return [card for card in cards if _passes_title_filter(card.title)]


def _passes_title_filter(title: str) -> bool:
    lowered = title.lower()
    if any(excluded in lowered for excluded in TITLE_EXCLUDE):
        return False
    return any(included in lowered for included in TITLE_INCLUDE)
