from __future__ import annotations

from datetime import date

from visa_jobs_api.sources.linkedin.extract import dedupe_cards, filter_by_title
from visa_jobs_api.sources.linkedin.models import JobCard


def _card(title: str = "Software Engineer", company: str = "Acme", location: str = "Dublin, Ireland") -> JobCard:
    return JobCard(
        title=title, company=company, location=location, posted_on=date(2026, 7, 4), url="https://x/1", query_country="Ireland"
    )


def test_dedupe_cards_drops_same_title_and_company() -> None:
    cards = [_card(), _card(), _card(company="Other Co")]
    deduped = dedupe_cards(cards)
    assert len(deduped) == 2


def test_filter_by_title_keeps_included_titles() -> None:
    cards = [_card(title="Software Engineer"), _card(title="Sales Manager")]
    filtered = filter_by_title(cards)
    assert [c.title for c in filtered] == ["Software Engineer"]


def test_filter_by_title_excludes_intern_even_if_it_also_matches_include() -> None:
    cards = [_card(title="Software Engineer Intern")]
    assert filter_by_title(cards) == []
