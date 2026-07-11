from __future__ import annotations

from dataclasses import dataclass

from visa_jobs_api.shared.title_filter import dedupe_cards, filter_by_title


@dataclass
class _Card:
    title: str
    company: str = "Acme"
    url: str = "https://example.com/job/1"


def test_dedupe_cards_drops_same_title_company_and_url() -> None:
    cards = [_Card("Software Engineer"), _Card("Software Engineer"), _Card("Software Engineer", company="Other Co")]
    deduped = dedupe_cards(cards)
    assert len(deduped) == 2


def test_dedupe_cards_keeps_same_title_and_company_when_url_differs() -> None:
    # Same generic title/company posted separately in two different
    # countries (common for large multinational employers) must not
    # collide and silently drop the later listing.
    cards = [
        _Card("Software Engineer", url="https://example.com/job/us"),
        _Card("Software Engineer", url="https://example.com/job/ca"),
    ]
    deduped = dedupe_cards(cards)
    assert len(deduped) == 2


def test_filter_by_title_keeps_included_titles() -> None:
    cards = [_Card("Software Engineer"), _Card("Sales Manager")]
    filtered = filter_by_title(cards)
    assert [c.title for c in filtered] == ["Software Engineer"]


def test_filter_by_title_excludes_intern_even_if_it_also_matches_include() -> None:
    cards = [_Card("Software Engineer Intern")]
    assert filter_by_title(cards) == []


def test_filter_by_title_catches_role_noun_and_domain_signal_even_with_a_word_between_them() -> None:
    # Real miss this guards against: "Full Stack AI Engineer" doesn't
    # contain the literal phrase "full stack engineer" -- "AI" breaks it.
    cards = [_Card("Full Stack AI Engineer")]
    assert len(filter_by_title(cards)) == 1


def test_filter_by_title_catches_generic_ai_and_devops_engineer_titles() -> None:
    # Real misses this guards against: no fixed-phrase entry covered these
    # at all, unlike "software engineer"/"backend engineer".
    cards = [_Card("Distinguished AI Engineer"), _Card("Senior DevOps Engineer")]
    assert len(filter_by_title(cards)) == 2


def test_filter_by_title_catches_developer_variant_not_just_engineer() -> None:
    # "software developer" was covered, but "full stack developer" wasn't --
    # the role-noun/domain-signal rule catches both without listing every combination.
    cards = [_Card("Sr. Full Stack Developer")]
    assert len(filter_by_title(cards)) == 1


def test_filter_by_title_excludes_hardware_engineering_disciplines_despite_broader_matching() -> None:
    # The broader role-noun + domain-signal rule would otherwise wrongly
    # pass these: "Principal Desktop Engineer" matches "principal" +
    # "engineer", and "Senior Electrical Engineer" matches "engineer" +
    # (nothing else) -- HARDWARE_DISCIPLINE_EXCLUDE exists specifically for this.
    cards = [
        _Card("Senior Electrical Engineer"),
        _Card("Firing Controls Engineer"),
        _Card("Principal Desktop Engineer"),
    ]
    assert filter_by_title(cards) == []


def test_filter_by_title_keeps_tech_lead_despite_no_role_noun() -> None:
    cards = [_Card("Tech Lead")]
    assert len(filter_by_title(cards)) == 1


def test_filter_by_title_excludes_vp_titles_even_when_vp_is_the_first_word() -> None:
    # Regression: TITLE_EXCLUDE's " vp " entry only matched when "vp" had
    # a space on both sides in the raw title, so "VP of Software
    # Engineering"/"SVP of Backend Engineering" (vp/svp as the very first
    # word) slipped through despite having a role noun + domain signal.
    cards = [_Card("VP of Software Engineering"), _Card("SVP of Backend Engineering")]
    assert filter_by_title(cards) == []
