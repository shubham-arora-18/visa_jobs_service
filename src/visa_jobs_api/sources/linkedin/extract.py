"""Title filtering and de-duplication for LinkedIn job cards.

See queries.py's module docstring for why title matching is role-noun +
domain-signal co-occurrence rather than a fixed phrase list.
"""

from __future__ import annotations

from visa_jobs_api.sources.linkedin.models import JobCard
from visa_jobs_api.sources.linkedin.queries import (
    HARDWARE_DISCIPLINE_EXCLUDE,
    TECH_DOMAIN_SIGNALS,
    TECH_ROLE_NOUNS,
    TITLE_EXCLUDE,
    TITLE_INCLUDE,
)


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
    """Keep only cards that look like relevant software/tech engineering roles."""
    return [card for card in cards if _passes_title_filter(card.title)]


def _passes_title_filter(title: str) -> bool:
    lowered = title.lower()
    if any(excluded in lowered for excluded in TITLE_EXCLUDE):
        return False
    if any(excluded in lowered for excluded in HARDWARE_DISCIPLINE_EXCLUDE):
        return False
    has_role_noun = any(noun in lowered for noun in TECH_ROLE_NOUNS)
    has_domain_signal = any(signal in lowered for signal in TECH_DOMAIN_SIGNALS)
    if has_role_noun and has_domain_signal:
        return True
    return any(included in lowered for included in TITLE_INCLUDE)
