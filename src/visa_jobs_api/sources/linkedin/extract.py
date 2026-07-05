"""Title filtering, de-duplication, and country derivation for LinkedIn job cards."""

from __future__ import annotations

from visa_jobs_api.sources.linkedin.models import JobCard
from visa_jobs_api.sources.linkedin.queries import TITLE_EXCLUDE, TITLE_INCLUDE

# Segments that mean "no specific country", not an actual country name --
# treating these as a country would silently misclassify remote/global
# postings into a fake location.
_NON_COUNTRY_LOCATION_MARKERS = {"remote", "worldwide", "anywhere", "global"}


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


def country_from_location(location: str) -> str | None:
    """Derive a country from LinkedIn's structured "City, Region, Country" location string.

    Deterministic, not a guess: LinkedIn's own location field is reliably
    structured enough that the last comma-separated segment is the country
    (e.g. "Dublin, County Dublin, Ireland" -> "Ireland", "Singapore" ->
    "Singapore"). Returns None for remote/global postings or an empty
    location, rather than treating "Remote" as if it were a country.
    """
    segments = [segment.strip() for segment in location.split(",") if segment.strip()]
    if not segments:
        return None
    last_segment = segments[-1]
    if last_segment.lower() in _NON_COUNTRY_LOCATION_MARKERS:
        return None
    return last_segment
