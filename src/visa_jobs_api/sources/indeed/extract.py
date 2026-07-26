"""Parses job cards out of an Indeed search-results page.

Selectors were reverse-engineered directly from a real, live Indeed
response (see indeed_scraper_experiment/DECISIONS.md) -- there is no
official API and no documentation to work from:

- Each result is `<div class="job_seen_beacon">`.
- Job title + job key: `<h3 class="jobTitle"><a data-jk="{job_key}" ...><span title="...">{title}</span></a></h3>`.
- Company: `<span data-testid="company-name">`.
- Location: `<div data-testid="text-location">`.
- The canonical job URL is *not* the `href` on the title link (that's a
  tracking/redirect URL, `/rc/clk?jk=...&bb=...`) -- it's built directly as
  `https://{domain}/viewjob?jk={job_key}`, same shape as LinkedIn's job
  cards being built from `data-entity-urn` rather than trusting a raw
  `href`.

has_additional_pages() parses the pagination nav's numbered page links
(`a[data-testid^="pagination-page-"]`) to decide whether a query's first
page is worth paginating past at all. Confirmed live (see
indeed_scraper_experiment/DECISIONS.md): a first page with an EMPTY
pagination nav (no numbered links at all) means there is genuinely only
one page of real results -- every subsequent `start=N` offset tested still
returned an HTTP 200 with cards, but they were the exact same cards as
page 0, not new ones. A first page with ANY numbered links means more
pages are worth trying, but the highest number shown is a sliding window,
not the true total (a UK test kept returning fresh cards on pages up to
`start=90` while the nav only ever showed page numbers 1-5) -- so this is
a one-time go/no-go gate checked on page 0 only, never a page-count bound.
"""

from __future__ import annotations

from bs4 import BeautifulSoup, Tag

from visa_jobs_api.sources.indeed.models import JobCard

# An earlier version of this classification parsed Indeed's <title> for a
# leading result count (e.g. "0 Python Backend ... Jobs - 26 July 2026 |
# Indeed"), on the theory that a bot-block/CAPTCHA interstitial can't
# dynamically render the query keywords into a title the way a real page
# does. Confirmed live against ~20 real fetches across all 8 countries
# (see DECISIONS.md) that this gave false positives: Indeed uses at least
# 5 different real-page title templates depending on market/query (e.g.
# "... (with Salaries) | Indeed Australia", "$65K-$121K ... | Indeed" with
# a salary range instead of a count, "... | Indeed" for UAE) -- most of
# which have no leading count at all, so a real, fully-rendered zero-result
# page was being misclassified as "likely blocked" purely because its
# title didn't match one specific template.
#
# Replaced with structural checks on the page body instead, both confirmed
# live to be reliable across every sample gathered:
# - `_SEARCH_CHROME_MARKER` (the search box's own input field) is present
#   on every genuine Indeed search page seen, with 0 or many results alike,
#   and absent on a real captured Cloudflare Turnstile challenge page (39KB,
#   no Indeed content at all beyond the challenge JS).
# - `_ZERO_RESULT_MARKERS` are Indeed's own embedded client-state fields
#   (`"originalResultCount":0`/`"jobCountInfo":"No Result"`) confirmed
#   present, byte-identical, on every genuine zero-result page regardless
#   of which title template it used -- far more stable than scraping
#   rendered, potentially-localized text.
_SEARCH_CHROME_MARKER = 'id="text-input-what"'
_ZERO_RESULT_MARKERS = ('"originalResultCount":0', '"jobCountInfo":"No Result"')


def has_additional_pages(html: str) -> bool:
    """Whether this page's pagination nav advertises any further page at all."""
    soup = BeautifulSoup(html, "html.parser")
    return bool(soup.select('a[data-testid^="pagination-page-"]'))


def page_title(html: str) -> str:
    """The page's <title> text, or "" if absent. Purely for human-readable
    log context now -- see is_genuine_indeed_page/is_confirmed_zero_result
    for the actual block-vs-zero classification."""
    soup = BeautifulSoup(html, "html.parser")
    return soup.title.get_text(strip=True) if soup.title else ""


def is_genuine_indeed_page(html: str) -> bool:
    """Whether this looks like an actually-rendered Indeed search page at
    all (its own search-box input present), as opposed to a bot-block/
    CAPTCHA interstitial or an unrelated redirect (e.g. a login page)."""
    return _SEARCH_CHROME_MARKER in html


def is_confirmed_zero_result(html: str) -> bool:
    """Whether Indeed's own embedded client state confirms zero results for
    this query -- a genuine zero, not a parsing/selector problem."""
    return any(marker in html for marker in _ZERO_RESULT_MARKERS)


def parse_job_cards(html: str, *, domain: str, query_country: str) -> list[JobCard]:
    soup = BeautifulSoup(html, "html.parser")
    cards: list[JobCard] = []
    for card_div in soup.find_all("div", class_="job_seen_beacon"):
        card = _parse_one_card(card_div, domain=domain, query_country=query_country)
        if card is not None:
            cards.append(card)
    return cards


def _parse_one_card(card_div: Tag, *, domain: str, query_country: str) -> JobCard | None:
    title_tag = card_div.find("h3", class_="jobTitle")
    if title_tag is None:
        return None
    link_tag = title_tag.find("a", attrs={"data-jk": True})
    if link_tag is None:
        return None
    job_key = link_tag["data-jk"]

    title_span = title_tag.find("span")
    title = title_span.get_text(strip=True) if title_span else title_tag.get_text(strip=True)

    company_tag = card_div.find(attrs={"data-testid": "company-name"})
    location_tag = card_div.find(attrs={"data-testid": "text-location"})

    return JobCard(
        title=title,
        company=company_tag.get_text(strip=True) if company_tag else "",
        location=location_tag.get_text(strip=True) if location_tag else "",
        url=f"https://{domain}/viewjob?jk={job_key}",
        query_country=query_country,
    )
