"""Search queries LinkedIn is scraped for.

The country list is kept as a code constant rather than an env var -- like
HN's tech-stack keyword table, it's a structured list that's edited
occasionally, not a per-request tunable. The keyword expression, on the
other hand, is exposed as an API input (see api.dto.digest.DigestRunRequest)
so a caller can tune what LinkedIn is searched for without a code change;
DEFAULT_KEYWORDS is used when a caller doesn't specify one. It uses
LinkedIn's own boolean search syntax (AND/OR, quoted phrases) to bias
results toward postings that already mention sponsorship, which
meaningfully improves the confirmed-hit rate over a bare title search (see
linkedin_visa_scraper/DECISIONS.md).

TITLE_INCLUDE/TITLE_EXCLUDE were originally a short list of exact fixed
phrases ("software engineer", "full stack engineer", ...), copied into the
standalone `indeed_scraper_experiment` project and found there (via manual
review of ~90 real job cards) to silently drop a real chunk of genuinely
relevant titles -- "Full Stack AI Engineer" doesn't contain the literal
substring "full stack engineer" (the inserted "AI" breaks it), and there
was no entry at all for "AI Engineer", "DevOps Engineer", "Cloud
Architect", or "Full Stack Developer" (only "...Engineer" was covered).
Replaced with TECH_ROLE_NOUNS + TECH_DOMAIN_SIGNALS: a title passes if it
contains any role noun (engineer/developer/architect/programmer) *and* any
domain signal (software/cloud/devops/ai/...) anywhere in the title, not
necessarily adjacent -- this generalizes past the exact-phrase problem for
free. HARDWARE_DISCIPLINE_EXCLUDE exists specifically to keep this broader
net safe: without it, "Principal Desktop Engineer" would pass on
"principal" + "engineer" alone, and "Senior Electrical Engineer" would
pass on bare "engineer". TITLE_INCLUDE is kept as a small supplementary
list only for real titles with no role noun at all ("Tech Lead") or no
clean domain signal ("Release Engineer"). See
indeed_scraper_experiment/DECISIONS.md for the full before/after review
this was verified against.
"""

from __future__ import annotations

from visa_jobs_api.sources.linkedin.models import SearchQuery

DEFAULT_KEYWORDS = "(Python OR Backend OR Java) AND (sponsor OR sponsorship)"

_COUNTRIES = [
    "United States",
    "Canada",
    "United Kingdom",
    "Germany",
    "Netherlands",
    "Ireland",
    "Switzerland",
    "Australia",
    "Singapore",
    "United Arab Emirates",
    "New Zealand",
    "Sweden",
    "France",
    "Japan",
]


def build_search_queries(keywords: str = DEFAULT_KEYWORDS) -> list[SearchQuery]:
    return [SearchQuery(keywords=keywords, location=country) for country in _COUNTRIES]


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
TITLE_EXCLUDE = ["intern", "internship", "director", " vp ", "chief", "manager", "qa engineer"]
