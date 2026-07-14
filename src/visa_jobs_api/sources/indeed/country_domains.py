"""Maps each searched country to its Indeed country site.

Indeed runs a separate localized site per country (not one global
indeed.com with a location filter) -- e.g. Netherlands is nl.indeed.com,
not www.indeed.com?l=Netherlands. Every domain here was confirmed
reachable and, for several, verified live to return real, correct-country
job listings -- see indeed_scraper_experiment/DECISIONS.md.

No proxy geo-pinning parameter is needed here (unlike the Bright Data
setup this used to require) -- Decodo's JS-rendered fetches (see
shared/http.py) return the correct English-locale content for every domain
below with no geo param at all, confirmed live.

Restricted to English-dominant job markets, not the full 14-country list
LinkedIn searches: forcing English results on Indeed for a non-English-
market domain was never reliably achieved (tested extensively -- see
indeed_scraper_experiment/DECISIONS.md), so countries whose primary
business language isn't English (Germany, Netherlands, Switzerland,
Sweden, France, Japan) are left out here rather than risk silently
returning wrong-language/low-signal results for an English-only
search+parse+confirm pipeline. Re-including any of these later would need
either a translated query per country or the same LLM-based non-English
handling LinkedIn's description pipeline already has.
"""

from __future__ import annotations

# country -> Indeed domain
COUNTRY_DOMAINS: dict[str, str] = {
    "United States": "www.indeed.com",
    "Canada": "ca.indeed.com",
    "United Kingdom": "uk.indeed.com",
    "Ireland": "ie.indeed.com",
    "Australia": "au.indeed.com",
    "New Zealand": "nz.indeed.com",
    "Singapore": "sg.indeed.com",
    "United Arab Emirates": "ae.indeed.com",
}
