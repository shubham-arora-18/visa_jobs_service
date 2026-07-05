"""Sentence-level regex heuristic for genuine, non-negated visa/sponsorship mentions.

Shared by both the HN and LinkedIn sources -- their original, independently
maintained copies of this exact heuristic (job_digest's
hn_who_is_hiring/visa_filter.py and linkedin_visa_scraper/visa_filter.py)
were textually identical, so this module is the single source of truth
going forward. This is a heuristic over free text, not a guarantee -- it
will miss unusual phrasing and should be spot-checked, not treated as
authoritative.
"""

from __future__ import annotations

import re

_STRONG_VISA_KEYWORD_PATTERN = re.compile(
    r"\b(visa|h-?1b|green\s?card|work\s?permit|immigration)\b", re.IGNORECASE
)
_SPONSOR_KEYWORD_PATTERN = re.compile(r"\bsponsor(?:s|ed|ship|ing)?\b", re.IGNORECASE)
# "sponsor"/"sponsorship" alone is ambiguous in job-ad prose -- postings
# often mention sponsoring a conference, meetup, or open-source project,
# unrelated to visas. A sentence that uses "sponsor" but without a
# visa-specific keyword, and also mentions one of these unrelated-sponsorship
# themes, is treated as a false positive rather than an offer.
_NON_VISA_SPONSORSHIP_CONTEXT_PATTERN = re.compile(
    r"\b(conferences?|events?|meetups?|hackathons?|workshops?|festivals?|podcasts?|"
    r"communit(?:y|ies)|open[\s-]?source|ecosystem|non-?profit)\b",
    re.IGNORECASE,
)

_NEGATION_PATTERN = re.compile(
    r"\b(no|not|cannot|unable|without|never|regretfully|unfortunately)\b"
    # Matches any contraction ending in "'t" (don't, doesn't, can't, won't,
    # isn't, ...) using either a straight or a curly/smart apostrophe --
    # there is no common positive English contraction of that shape.
    r"|\w+['’]t\b"
    # Eligibility restrictions ("citizens only", "must already have a visa")
    # read as an offer under plain keyword matching even though they mean
    # the opposite: sponsorship is not available.
    r"|citizens?\s+(?:or\s+green\s?card\s+holders?\s+)?only\b"
    r"|green\s?card\s+holders?\s+only\b"
    r"|must\s+(?:already\s+)?have\s+(?:a\s+|an\s+|valid\s+|existing\s+)*"
    r"(?:visa|green\s?card|work\s?permit|work\s+authorization)"
    r"|existing\s+work\s+authorization"
    # LinkedIn ads frequently state a requirement rather than an offer:
    # "must be authorized to work in the US without sponsorship."
    r"|without\s+(?:the\s+need\s+for\s+)?sponsorship",
    re.IGNORECASE,
)

_SENTENCE_SPLIT_PATTERN = re.compile(r"(?<=[.!?])\s+")


def find_visa_mentions(text: str) -> list[str]:
    """Return sentences in text that look like a genuine, non-negated visa-sponsorship offer."""
    mentions: list[str] = []
    for sentence in _split_sentences(text):
        # A question mentioning visas ("do you sponsor visas?") is someone
        # asking, not a statement that sponsorship is offered.
        if sentence.rstrip().endswith("?"):
            continue
        has_strong_keyword = bool(_STRONG_VISA_KEYWORD_PATTERN.search(sentence))
        has_sponsor_keyword = bool(_SPONSOR_KEYWORD_PATTERN.search(sentence))
        is_unrelated_sponsorship = (
            has_sponsor_keyword
            and not has_strong_keyword
            and _NON_VISA_SPONSORSHIP_CONTEXT_PATTERN.search(sentence)
        )
        if (
            (has_strong_keyword or has_sponsor_keyword)
            and not is_unrelated_sponsorship
            and not _NEGATION_PATTERN.search(sentence)
        ):
            mentions.append(sentence.strip())
    return mentions


def _split_sentences(text: str) -> list[str]:
    # A line break (blank or not) is always a boundary even without
    # sentence-ending punctuation, since header lines like "Company | Role |
    # Location" never end in a period.
    sentences: list[str] = []
    for line in text.splitlines():
        sentences.extend(_SENTENCE_SPLIT_PATTERN.split(line))
    return [sentence for sentence in sentences if sentence.strip()]
