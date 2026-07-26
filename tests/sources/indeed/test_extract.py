from __future__ import annotations

from visa_jobs_api.sources.indeed.extract import (
    has_additional_pages,
    is_confirmed_zero_result,
    is_genuine_indeed_page,
    page_title,
    parse_job_cards,
)


def _card_html(job_key: str, title: str, company: str, location: str) -> str:
    return f"""
    <div class="job_seen_beacon">
      <h3 class="jobTitle"><a data-jk="{job_key}"><span title="{title}">{title}</span></a></h3>
      <span data-testid="company-name">{company}</span>
      <div data-testid="text-location">{location}</div>
    </div>
    """


def test_parse_job_cards_extracts_all_fields() -> None:
    html = _card_html("abc123", "Backend Engineer", "Acme", "New York, NY")

    cards = parse_job_cards(html, domain="www.indeed.com", query_country="United States")

    assert len(cards) == 1
    assert cards[0].title == "Backend Engineer"
    assert cards[0].company == "Acme"
    assert cards[0].location == "New York, NY"
    assert cards[0].url == "https://www.indeed.com/viewjob?jk=abc123"
    assert cards[0].query_country == "United States"


def test_parse_job_cards_uses_the_country_specific_domain_for_the_url() -> None:
    html = _card_html("xyz789", "Software Engineer", "Acme", "Dublin")

    cards = parse_job_cards(html, domain="ie.indeed.com", query_country="Ireland")

    assert cards[0].url == "https://ie.indeed.com/viewjob?jk=xyz789"


def test_parse_job_cards_skips_cards_missing_a_job_key() -> None:
    html = """
    <div class="job_seen_beacon">
      <h3 class="jobTitle"><span>No Link Job</span></h3>
    </div>
    """
    assert parse_job_cards(html, domain="www.indeed.com", query_country="United States") == []


def test_parse_job_cards_handles_missing_company_and_location() -> None:
    html = """
    <div class="job_seen_beacon">
      <h3 class="jobTitle"><a data-jk="xyz"><span title="Some Job">Some Job</span></a></h3>
    </div>
    """
    cards = parse_job_cards(html, domain="www.indeed.com", query_country="United States")
    assert len(cards) == 1
    assert cards[0].company == ""
    assert cards[0].location == ""


def test_has_additional_pages_true_when_numbered_links_present() -> None:
    html = """
    <nav role="navigation" aria-label="pagination">
      <ul>
        <li><a data-testid="pagination-page-1">1</a></li>
        <li><a data-testid="pagination-page-2">2</a></li>
        <li><a data-testid="pagination-page-next">Next</a></li>
      </ul>
    </nav>
    """
    assert has_additional_pages(html) is True


def test_has_additional_pages_false_when_nav_is_empty() -> None:
    # Real case confirmed live: an empty <ul> inside the pagination nav --
    # no numbered links at all -- means only one page of real results
    # exists, even though the nav element itself is still present.
    html = '<nav role="navigation" aria-label="pagination"><ul></ul></nav>'
    assert has_additional_pages(html) is False


def test_has_additional_pages_false_when_nav_is_absent_entirely() -> None:
    assert has_additional_pages("<html><body>no nav here</body></html>") is False


def test_page_title_extracts_title_text() -> None:
    html = "<html><head><title>Python Jobs in Ireland | Indeed.com</title></head><body></body></html>"
    assert page_title(html) == "Python Jobs in Ireland | Indeed.com"


def test_page_title_returns_empty_string_when_absent() -> None:
    assert page_title("<html><body>no title here</body></html>") == ""


def test_is_genuine_indeed_page_true_when_search_box_present() -> None:
    # The search box's own input field, confirmed live to be present on
    # every genuine Indeed search page gathered (0 results or many, across
    # every title template variant seen) -- see DECISIONS.md.
    html = '<html><body><input id="text-input-what" value="Python"></body></html>'
    assert is_genuine_indeed_page(html) is True


def test_is_genuine_indeed_page_false_when_search_box_absent() -> None:
    # Confirmed live against an actual captured Cloudflare Turnstile
    # challenge page (39KB, no Indeed content at all beyond challenge JS).
    html = "<html><head><title>Just a moment...</title></head><body></body></html>"
    assert is_genuine_indeed_page(html) is False


def test_is_confirmed_zero_result_true_when_result_count_marker_present() -> None:
    # Indeed's own embedded client-state field, confirmed live to be
    # byte-identical across every genuine zero-result page gathered,
    # regardless of which title template that page used.
    html = '<html><body><script>{"jobCountInfo":"No Result","originalResultCount":0}</script></body></html>'
    assert is_confirmed_zero_result(html) is True


def test_is_confirmed_zero_result_false_when_marker_absent() -> None:
    html = "<html><body>real results here</body></html>"
    assert is_confirmed_zero_result(html) is False
