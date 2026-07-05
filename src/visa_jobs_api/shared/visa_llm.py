"""Shared async LLM-based confirmation of visa-sponsorship candidates.

The regex heuristic in visa_keywords.py is a cheap first pass that
over-selects (e.g. it can't tell "sponsored by prominent executives" from a
real visa offer). This module re-checks each regex-flagged candidate with a
small LLM call via Hugging Face's Inference Providers router, keeping only
genuine offers -- and, in the same call, asks for a best-effort country
guess (see _COUNTRY_INSTRUCTION), so sources with unstructured location text
(e.g. HN's freeform posting headers) can still be grouped by country
without a second LLM round-trip. Not a guarantee either way -- results
should be spot-checked, not treated as authoritative.

Each source supplies only its own domain-specific screening criteria as
`system_prompt`; this module owns the output-format contract (the JSON
schema instruction), so the two sources' prompts can't drift out of sync on
what shape the model must respond in.
"""

from __future__ import annotations

import json
import logging

import openai
from openai import AsyncOpenAI
from pydantic import BaseModel, ConfigDict, ValidationError

logger = logging.getLogger(__name__)

HF_ROUTER_BASE_URL = "https://router.huggingface.co/v1"
DEFAULT_MODEL = "Qwen/Qwen3-4B-Instruct-2507:nscale"

_COUNTRY_INSTRUCTION = (
    "\n\nAlso infer the single country most associated with where this "
    "specific role is based, using any location/office/timezone information "
    'mentioned in the text (city, region, or an explicit country name). Use '
    'the country\'s common English name (e.g. "United States", "Germany", '
    '"Ireland"). If the role is remote with no identifiable country, or no '
    "location information is given at all, use null -- do not guess a "
    "country with no supporting textual evidence.\n\n"
    'Respond with strict JSON only, no other text: {"offers_sponsorship": true '
    'or false, "reason": "one sentence explanation", "country": "Country Name" '
    "or null}"
)


class LlmVerdict(BaseModel):
    """The model's judgment on whether a posting is a genuine sponsorship offer, plus a best-effort country."""

    model_config = ConfigDict(frozen=True)

    offers_sponsorship: bool
    reason: str
    country: str | None = None


class VisaLlmError(RuntimeError):
    """Raised when the LLM call fails or its response doesn't match the expected schema."""


def build_client(*, hf_token: str) -> AsyncOpenAI:
    return AsyncOpenAI(base_url=HF_ROUTER_BASE_URL, api_key=hf_token)


def _build_user_prompt(*, full_text: str, mentions: list[str], supporting_context: list[str]) -> str:
    """Build the user-turn prompt: full text, any supporting context, then the matched sentences.

    `mentions` may be empty -- e.g. non-English LinkedIn postings, where the
    English-only regex pass is skipped and every posting goes straight to
    the LLM.
    """
    parts = [f"Full posting text:\n{full_text}"]
    if supporting_context:
        parts.append(
            "Additional context (e.g. a reply confirming sponsorship):\n" + "\n---\n".join(supporting_context)
        )
    if mentions:
        parts.append("Sentence(s) that triggered a keyword match:\n" + "\n".join(f"- {m}" for m in mentions))
    return "\n\n".join(parts)


async def confirm_visa_offer(
    *,
    client: AsyncOpenAI,
    system_prompt: str,
    full_text: str,
    mentions: list[str],
    supporting_context: list[str] | None = None,
    model: str = DEFAULT_MODEL,
    log_context: str = "",
) -> LlmVerdict:
    """Ask the LLM whether a candidate posting is a genuine sponsorship offer, and its likely country."""
    user_prompt = _build_user_prompt(full_text=full_text, mentions=mentions, supporting_context=supporting_context or [])
    full_system_prompt = system_prompt + _COUNTRY_INSTRUCTION
    context_suffix = f" for {log_context}" if log_context else ""

    try:
        completion = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": full_system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0,
        )
    except openai.OpenAIError as exc:
        raise VisaLlmError(f"HF inference call failed{context_suffix}") from exc

    content = completion.choices[0].message.content
    if not content:
        raise VisaLlmError(f"HF inference call returned an empty response{context_suffix}")

    try:
        raw_verdict = json.loads(content)
    except json.JSONDecodeError as exc:
        raise VisaLlmError(f"LLM response was not valid JSON{context_suffix}: {content!r}") from exc

    try:
        return LlmVerdict.model_validate(raw_verdict)
    except ValidationError as exc:
        raise VisaLlmError(f"LLM response didn't match the expected schema{context_suffix}: {raw_verdict!r}") from exc
