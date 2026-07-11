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


def test_filter_by_title_catches_role_noun_and_domain_signal_even_with_a_word_between_them() -> None:
    # Real miss this guards against: "Full Stack AI Engineer" doesn't
    # contain the literal phrase "full stack engineer" -- "AI" breaks it.
    cards = [_card(title="Full Stack AI Engineer")]
    assert len(filter_by_title(cards)) == 1


def test_filter_by_title_catches_generic_ai_and_devops_engineer_titles() -> None:
    # Real misses this guards against: no fixed-phrase entry covered these
    # at all, unlike "software engineer"/"backend engineer".
    cards = [_card(title="Distinguished AI Engineer"), _card(title="Senior DevOps Engineer")]
    assert len(filter_by_title(cards)) == 2


def test_filter_by_title_catches_developer_variant_not_just_engineer() -> None:
    # "software developer" was covered, but "full stack developer" wasn't --
    # the role-noun/domain-signal rule catches both without listing every combination.
    cards = [_card(title="Sr. Full Stack Developer")]
    assert len(filter_by_title(cards)) == 1


def test_filter_by_title_excludes_hardware_engineering_disciplines_despite_broader_matching() -> None:
    # The broader role-noun + domain-signal rule would otherwise wrongly
    # pass these: "Principal Desktop Engineer" matches "principal" +
    # "engineer", and "Senior Electrical Engineer" matches "engineer" +
    # (nothing else) -- HARDWARE_DISCIPLINE_EXCLUDE exists specifically for this.
    cards = [
        _card(title="Senior Electrical Engineer"),
        _card(title="Firing Controls Engineer"),
        _card(title="Principal Desktop Engineer"),
    ]
    assert filter_by_title(cards) == []


def test_filter_by_title_keeps_tech_lead_despite_no_role_noun() -> None:
    cards = [_card(title="Tech Lead")]
    assert len(filter_by_title(cards)) == 1
