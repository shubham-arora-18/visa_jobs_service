"""Title filtering and de-duplication shared by every source that produces
title/company job cards (currently LinkedIn and Indeed).

Originally lived in sources/linkedin/queries.py + extract.py as
LinkedIn-only, then Indeed needed the exact same logic -- moved here rather
than copied a second time, since copying this same logic into a separate
project (indeed_scraper_experiment) is exactly what caused it to silently
drift out of sync there (see indeed_scraper_experiment/DECISIONS.md's
title-filter section). Within a single codebase there's even less excuse
to duplicate it twice.

Title matching is role-noun + domain-signal co-occurrence, not a fixed
phrase list: a title passes if it contains any of TECH_ROLE_NOUNS
(engineer/developer/architect/programmer) *and* any of
TECH_DOMAIN_SIGNALS (software/cloud/devops/ai/...) *anywhere* in the
title, not necessarily adjacent. A fixed-phrase approach (the original
LinkedIn-only version) broke the moment a qualifier word was inserted
("Full Stack AI Engineer" doesn't contain the literal substring "full
stack engineer") or used a synonym the list didn't anticipate ("AI
Engineer", "DevOps Engineer", "Cloud Architect" had no entry at all) --
found via manual review of ~90 real Indeed job cards in
indeed_scraper_experiment. HARDWARE_DISCIPLINE_EXCLUDE exists specifically
to keep this broader net safe: without it, "Principal Desktop Engineer"
would pass on "principal" + "engineer" alone, and "Senior Electrical
Engineer" would pass on bare "engineer". TITLE_INCLUDE is kept as a small
supplementary list only for real titles with no role noun at all ("Tech
Lead") or no clean domain signal ("Release Engineer").
"""

from __future__ import annotations

import re
from typing import Protocol, TypeVar


class _TitledCard(Protocol):
    title: str
    company: str
    url: str


CardT = TypeVar("CardT", bound=_TitledCard)

TECH_ROLE_NOUNS = ["engineer", "developer", "architect", "programmer"]
TECH_DOMAIN_SIGNALS = [
    "software",
    "backend",
    "back-end",
    "full stack",
    "fullstack",
    "front-end",
    "frontend",
    "platform",
    "cloud",
    "devops",
    "site reliability",
    "infrastructure",
    "data",
    "ai",
    "ml",
    "machine learning",
    "distinguished",
    "staff",
    "principal",
    "solutions",
]

HARDWARE_DISCIPLINE_EXCLUDE = [
    "electrical engineer",
    "mechanical engineer",
    "civil engineer",
    "chemical engineer",
    "firing control",
    "building engineer",
    "sensor",
    "power system",
    "transmission planning",
    "water resources",
    "desktop engineer",
]

TITLE_INCLUDE = [
    "tech lead",
    "technical lead",
    "sre",
    "site reliability engineer",
    "release engineer",
    "build engineer",
]
TITLE_EXCLUDE = ["intern", "internship", "director", "chief", "manager", "qa engineer"]

# vp/svp/evp/avp need a word-boundary match, not plain substring inclusion:
# a space-padded " vp " check misses "VP"/"SVP" as the title's first word
# (no leading space in the raw title), and a bare "vp" substring check
# would false-positive on unrelated words. \b(?:[sea]?vp)\b matches vp on
# its own or with a Senior/Executive/Assistant prefix, as a whole word only.
_EXEC_TITLE_RE = re.compile(r"\b(?:[sea]?vp)\b")


def dedupe_cards(cards: list[CardT]) -> list[CardT]:
    """Drop cards with the same (title, company, url) as one already seen, keeping the first.

    url is included (not just title/company) so that identical generic
    titles at the same company posted separately in different countries
    (common for large multinational employers) are treated as distinct
    listings rather than colliding and silently dropping the later one.
    """
    seen: set[tuple[str, str, str]] = set()
    deduped: list[CardT] = []
    for card in cards:
        key = (card.title, card.company, card.url)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(card)
    return deduped


def filter_by_title(cards: list[CardT]) -> list[CardT]:
    """Keep only cards that look like relevant software/tech engineering roles."""
    return [card for card in cards if _passes_title_filter(card.title)]


def _passes_title_filter(title: str) -> bool:
    lowered = title.lower()
    if _EXEC_TITLE_RE.search(lowered):
        return False
    if any(excluded in lowered for excluded in TITLE_EXCLUDE):
        return False
    if any(excluded in lowered for excluded in HARDWARE_DISCIPLINE_EXCLUDE):
        return False
    has_role_noun = any(noun in lowered for noun in TECH_ROLE_NOUNS)
    has_domain_signal = any(signal in lowered for signal in TECH_DOMAIN_SIGNALS)
    if has_role_noun and has_domain_signal:
        return True
    return any(included in lowered for included in TITLE_INCLUDE)
