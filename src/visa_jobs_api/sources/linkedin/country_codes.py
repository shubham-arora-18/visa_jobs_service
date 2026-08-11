"""Maps each searched country to its ISO 3166-1 alpha-2 code.

Bright Data's Web Unlocker requires an explicit `country` geo-pin param on
every request -- confirmed necessary during Indeed's provider testing (a
request without it came back in the wrong locale even though the target URL
was already country-specific, see indeed_scraper_experiment/DECISIONS.md).
LinkedIn's guest search endpoint is one global URL (not a per-country
domain like Indeed), so this geo-pin is the only thing that targets a
specific country's exit node for LinkedIn's fetches. Kept alongside
`queries.py`'s `_COUNTRIES` list rather than a shared module, mirroring
`sources/indeed/country_domains.py`'s equivalent table -- see that module's
docstring for why "gb", not "uk", for the United Kingdom.
"""

from __future__ import annotations

ISO_COUNTRY_CODES: dict[str, str] = {
    "United States": "us",
    "Canada": "ca",
    "United Kingdom": "gb",
    "Germany": "de",
    "Netherlands": "nl",
    "Ireland": "ie",
    "Switzerland": "ch",
    "Australia": "au",
    "Singapore": "sg",
    "United Arab Emirates": "ae",
    "New Zealand": "nz",
    "Sweden": "se",
    "France": "fr",
    "Finland": "fi",
    "Denmark": "dk",
    "Austria": "at",
    "Belgium": "be",
}
