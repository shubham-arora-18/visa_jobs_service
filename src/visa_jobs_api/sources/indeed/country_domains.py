"""Maps each searched country to its Indeed country site + Bright Data geo code.

Indeed runs a separate localized site per country (not one global
indeed.com with a location filter) -- e.g. Netherlands is nl.indeed.com,
not www.indeed.com?l=Netherlands. Every domain here was confirmed
reachable and, for several, verified live to return real, correct-country
job listings -- see indeed_scraper_experiment/DECISIONS.md.

Bright Data's Web Unlocker needs an explicit two-letter `country` geo
parameter per request (see shared/http.py) so its exit node's IP/locale
matches the domain being requested -- without it, a first test of
indeed.com returned an entire page in Spanish for a US-only,
no-location-filter query, because the proxy exit node's assumed geo didn't
match.

Restricted to English-dominant job markets, not the full 14-country list
LinkedIn searches: neither Bright Data's `country` geo param nor an
`hl=en` URL override reliably forces English results on Indeed (tested
extensively -- see indeed_scraper_experiment/DECISIONS.md), so countries
whose primary business language isn't English (Germany, Netherlands,
Switzerland, Sweden, France, Japan) are left out here rather than risk
silently returning wrong-language/low-signal results for an English-only
search+parse+confirm pipeline. Re-including any of these later would need
either a translated query per country or the same LLM-based non-English
handling LinkedIn's description pipeline already has.
"""

from __future__ import annotations

# country -> (Indeed domain, Bright Data geo code)
COUNTRY_DOMAINS: dict[str, tuple[str, str]] = {
    "United States": ("www.indeed.com", "us"),
    "Canada": ("ca.indeed.com", "ca"),
    "United Kingdom": ("uk.indeed.com", "gb"),
    "Ireland": ("ie.indeed.com", "ie"),
    "Australia": ("au.indeed.com", "au"),
    "New Zealand": ("nz.indeed.com", "nz"),
    "Singapore": ("sg.indeed.com", "sg"),
    "United Arab Emirates": ("ae.indeed.com", "ae"),
}
