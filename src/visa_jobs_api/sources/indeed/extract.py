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


def has_additional_pages(html: str) -> bool:
    """Whether this page's pagination nav advertises any further page at all."""
    soup = BeautifulSoup(html, "html.parser")
    return bool(soup.select('a[data-testid^="pagination-page-"]'))


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
