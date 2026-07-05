from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import openai
import pytest

from visa_jobs_api.shared.visa_llm import VisaLlmError, confirm_visa_offer

_SYSTEM_PROMPT = "You are screening postings for genuine visa sponsorship offers."


def _mock_completion_returning(content: str | None) -> SimpleNamespace:
    message = SimpleNamespace(content=content)
    choice = SimpleNamespace(message=message)
    return SimpleNamespace(choices=[choice])


def _make_client(completion_or_exception: object) -> MagicMock:
    client = MagicMock()
    if isinstance(completion_or_exception, Exception):
        client.chat.completions.create = AsyncMock(side_effect=completion_or_exception)
    else:
        client.chat.completions.create = AsyncMock(return_value=completion_or_exception)
    return client


async def test_confirm_visa_offer_returns_verdict_with_country_on_valid_json() -> None:
    client = _make_client(
        _mock_completion_returning('{"offers_sponsorship": true, "reason": "explicit offer", "country": "Ireland"}')
    )

    verdict = await confirm_visa_offer(
        client=client, system_prompt=_SYSTEM_PROMPT, full_text="We offer visa sponsorship.", mentions=["We offer visa sponsorship."]
    )

    assert verdict.offers_sponsorship is True
    assert verdict.reason == "explicit offer"
    assert verdict.country == "Ireland"


async def test_confirm_visa_offer_country_defaults_to_none_when_omitted() -> None:
    client = _make_client(_mock_completion_returning('{"offers_sponsorship": true, "reason": "explicit offer"}'))

    verdict = await confirm_visa_offer(client=client, system_prompt=_SYSTEM_PROMPT, full_text="text", mentions=[])

    assert verdict.country is None


async def test_confirm_visa_offer_raises_on_api_error() -> None:
    client = _make_client(openai.APIConnectionError(request=MagicMock()))

    with pytest.raises(VisaLlmError, match="HF inference call failed"):
        await confirm_visa_offer(client=client, system_prompt=_SYSTEM_PROMPT, full_text="text", mentions=[])


async def test_confirm_visa_offer_raises_on_empty_response() -> None:
    client = _make_client(_mock_completion_returning(None))

    with pytest.raises(VisaLlmError, match="empty response"):
        await confirm_visa_offer(client=client, system_prompt=_SYSTEM_PROMPT, full_text="text", mentions=[])


async def test_confirm_visa_offer_raises_on_malformed_json() -> None:
    client = _make_client(_mock_completion_returning("not json"))

    with pytest.raises(VisaLlmError, match="not valid JSON"):
        await confirm_visa_offer(client=client, system_prompt=_SYSTEM_PROMPT, full_text="text", mentions=[])


async def test_confirm_visa_offer_raises_on_schema_mismatch() -> None:
    client = _make_client(_mock_completion_returning('{"unexpected": "shape"}'))

    with pytest.raises(VisaLlmError, match="didn't match the expected schema"):
        await confirm_visa_offer(client=client, system_prompt=_SYSTEM_PROMPT, full_text="text", mentions=[])


async def test_confirm_visa_offer_prompt_includes_supporting_context_when_given() -> None:
    client = _make_client(_mock_completion_returning('{"offers_sponsorship": true, "reason": "ok"}'))

    await confirm_visa_offer(
        client=client,
        system_prompt=_SYSTEM_PROMPT,
        full_text="Great team, fully remote.",
        mentions=[],
        supporting_context=["Yes, we sponsor visas for this role."],
    )

    sent_prompt = client.chat.completions.create.call_args.kwargs["messages"][1]["content"]
    assert "Yes, we sponsor visas for this role." in sent_prompt


async def test_confirm_visa_offer_prompt_omits_mentions_section_when_empty() -> None:
    # Non-English postings skip the regex pass entirely and go straight to
    # the LLM with an empty mentions list -- the prompt must not include a
    # dangling, empty "Sentence(s) that triggered..." section in that case.
    client = _make_client(_mock_completion_returning('{"offers_sponsorship": true, "reason": "ok"}'))

    await confirm_visa_offer(client=client, system_prompt=_SYSTEM_PROMPT, full_text="Wir bieten Visa-Sponsoring an.", mentions=[])

    sent_prompt = client.chat.completions.create.call_args.kwargs["messages"][1]["content"]
    assert "triggered a keyword match" not in sent_prompt


async def test_system_prompt_appends_country_instruction() -> None:
    client = _make_client(_mock_completion_returning('{"offers_sponsorship": true, "reason": "ok", "country": null}'))

    await confirm_visa_offer(client=client, system_prompt=_SYSTEM_PROMPT, full_text="text", mentions=[])

    sent_system_prompt = client.chat.completions.create.call_args.kwargs["messages"][0]["content"]
    assert "infer the single country" in sent_system_prompt
