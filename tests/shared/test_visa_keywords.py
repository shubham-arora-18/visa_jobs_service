from __future__ import annotations

from visa_jobs_api.shared.visa_keywords import find_visa_mentions


def test_finds_positive_visa_mention() -> None:
    text = "We are a remote-first team. We offer visa sponsorship for the right candidate."
    assert find_visa_mentions(text) == ["We offer visa sponsorship for the right candidate."]


def test_excludes_negated_visa_mention() -> None:
    text = "Great team, fully remote. Unfortunately we cannot sponsor visas at this time."
    assert find_visa_mentions(text) == []


def test_excludes_no_sponsorship_phrasing() -> None:
    text = "US citizens only, no visa sponsorship available."
    assert find_visa_mentions(text) == []


def test_excludes_citizens_or_green_card_holders_only_as_a_restriction() -> None:
    text = "US citizens or Green Card holders only, sadly (legal reasons)."
    assert find_visa_mentions(text) == []


def test_excludes_unrelated_event_sponsorship() -> None:
    text = "We sponsor PyCon, DjangoCon, and Django Girls because we're invested in the ecosystem we build on."
    assert find_visa_mentions(text) == []


def test_includes_standalone_sponsorship_mention_without_visa_word() -> None:
    text = "Sponsorship is available for the right candidate."
    assert find_visa_mentions(text) == ["Sponsorship is available for the right candidate."]


def test_excludes_dont_contraction_with_straight_apostrophe() -> None:
    text = "We don't sponsor visas at the moment."
    assert find_visa_mentions(text) == []


def test_excludes_cant_contraction_with_curly_apostrophe() -> None:
    text = "We can’t sponsor visas but relocation assistance to SF is possible."
    assert find_visa_mentions(text) == []


def test_finds_h1b_and_green_card_mentions() -> None:
    text = "We support H1B transfers. Green card sponsorship also available after one year."
    assert len(find_visa_mentions(text)) == 2


def test_finds_sponsoring_ing_verb_form() -> None:
    text = "We are open to sponsoring visas for the right candidate."
    assert find_visa_mentions(text) == ["We are open to sponsoring visas for the right candidate."]


def test_excludes_questions() -> None:
    text = "Does this role offer visa sponsorship? We're not sure yet."
    assert find_visa_mentions(text) == []


def test_excludes_authorized_to_work_without_sponsorship_phrasing() -> None:
    # LinkedIn-specific eligibility-requirement phrasing that reads as an
    # offer under plain keyword matching even though it means the opposite.
    text = "Candidates must be authorized to work in the US without sponsorship."
    assert find_visa_mentions(text) == []
