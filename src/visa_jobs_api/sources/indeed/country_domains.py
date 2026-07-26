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

Restricted to English-dominant job markets, not the full 18-country list
LinkedIn searches: forcing English results on Indeed for a non-English-
market domain was never reliably achieved (tested extensively -- see
indeed_scraper_experiment/DECISIONS.md), so countries whose primary
business language isn't English (Germany, Netherlands, Switzerland,
Sweden, France, Japan, and -- confirmed live on 2026-07-26, same
UI-language-not-forceable-to-English pattern -- Finland, Denmark, Austria,
Belgium) are left out here rather than risk silently returning
wrong-language/low-signal results for an English-only search+parse+confirm
pipeline. Re-including any of these later would need either a translated
query per country or the same LLM-based non-English handling LinkedIn's
description pipeline already has.
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

# country -> ISO 3166-1 alpha-2 code, for Bright Data's geo-pinning
# `country` param on description fetches (see sources/indeed/description.py
# and shared/http.py). Required, not optional -- a test without it returned
# a page in the wrong locale even though the domain itself was already the
# correct country's (see indeed_scraper_experiment/DECISIONS.md). Note this
# is "gb", not "uk", for the United Kingdom -- Bright Data expects the real
# ISO code, unlike Indeed's own domain naming. (The Selenium-driven
# residential proxy, unlike Bright Data, doesn't target a specific exit
# country per request at all -- see selenium_client.py's docstring for
# why -- so it isn't a second consumer of this despite once being one.)
ISO_COUNTRY_CODES: dict[str, str] = {
    "United States": "us",
    "Canada": "ca",
    "United Kingdom": "gb",
    "Ireland": "ie",
    "Australia": "au",
    "New Zealand": "nz",
    "Singapore": "sg",
    "United Arab Emirates": "ae",
}
