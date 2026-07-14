"""Shared async HTTP fetch helper, optionally routed through Decodo's Scraper API.

LinkedIn and Indeed both rate-limit/block bursts of direct requests (see
linkedin_visa_scraper/DECISIONS.md and indeed_scraper_experiment/DECISIONS.md
for the investigations) -- Decodo handles the bypass (proxying + rendering)
on its end instead, for both sources. Indeed used to require Bright Data
instead of Decodo (an earlier test found Decodo's non-JS-rendered pools
blocked outright for Indeed), but a later test found Decodo's JS-rendered
mode (`headless="html"`) fetches Indeed's search AND description pages
successfully (confirmed live: real content, correct English locale despite
no geo-pinning param, no login-wall markers) -- see
indeed_scraper_experiment/DECISIONS.md. Bright Data has been fully removed
as a result. A failed fetch raises FetchError after retries are exhausted;
it must never be treated as "page has no content", since that previously
produced silently wrong results (every job looking like it had no
description).
"""

from __future__ import annotations

import logging

import httpx
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)

DECODO_SCRAPE_URL = "https://scraper-api.decodo.com/v2/scrape"
DIRECT_FETCH_TIMEOUT_SECONDS = 15.0
# Decodo does real anti-bot bypass work per request (proxying,
# fingerprinting, headless rendering), so individual calls run several
# seconds to ~12s+ -- much slower than a direct fetch, hence the longer
# timeout.
DECODO_FETCH_TIMEOUT_SECONDS = 60.0

_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


class FetchError(RuntimeError):
    """Raised when a URL can't be fetched after retries are exhausted."""


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, (httpx.TimeoutException, httpx.TransportError)):
        return True
    return isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code in _RETRYABLE_STATUS_CODES


@retry(
    retry=retry_if_exception(_is_retryable),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=8),
    reraise=True,
)
async def _get_direct(client: httpx.AsyncClient, url: str) -> httpx.Response:
    response = await client.get(url, timeout=DIRECT_FETCH_TIMEOUT_SECONDS)
    response.raise_for_status()
    return response


@retry(
    retry=retry_if_exception(_is_retryable),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=8),
    reraise=True,
)
async def _get_via_decodo(
    client: httpx.AsyncClient, url: str, *, username: str, password: str, headless: str | None = None
) -> str:
    body: dict[str, str] = {"url": url, "proxy_pool": "standard"}
    if headless:
        # `headless="html"` turns on Decodo's JS-rendered mode -- required
        # for Indeed specifically (confirmed live: without it, Indeed's
        # pages come back blocked/failed; with it, real content). LinkedIn
        # doesn't pass this and works fine without it, so it's opt-in per
        # call rather than always-on. See module docstring.
        body["headless"] = headless
    response = await client.post(
        DECODO_SCRAPE_URL,
        auth=(username, password),
        json=body,
        timeout=DECODO_FETCH_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    payload = response.json()
    if "results" not in payload:
        # Decodo itself responded 200 but couldn't scrape the target at
        # all -- its own top-level `status`/`status_code`/`message` fields
        # are present instead of a `results` array (confirmed live: this
        # shape shows up for a scrape it fully gave up on, e.g. status_code
        # 613). Without this check, the `["results"][0]` access below
        # raises an unhandled KeyError instead of a clean FetchError.
        # Decodo's own status_code here is a proprietary code, not a real
        # HTTP status -- passing it straight through to
        # httpx.HTTPStatusError would silently disable retries (it won't
        # match _RETRYABLE_STATUS_CODES), so this is deliberately
        # normalized to 502 instead, since Decodo's own error message
        # explicitly suggests retrying often does help.
        raise httpx.HTTPStatusError(
            f"Decodo could not scrape {url!r}: {payload.get('message', payload)}",
            request=response.request,
            response=httpx.Response(502, request=response.request),
        )
    result = payload["results"][0]
    upstream_status = result["status_code"]
    if upstream_status >= 400:
        # Decodo itself responded 200 (the proxy call succeeded), but the
        # page it fetched on our behalf (e.g. LinkedIn) returned an error --
        # re-raise as an HTTPStatusError carrying that real upstream status
        # so _is_retryable classifies it (429/5xx) the same way a direct
        # fetch's failure would be.
        raise httpx.HTTPStatusError(
            f"upstream returned {upstream_status} for {url!r} via Decodo",
            request=response.request,
            response=httpx.Response(upstream_status, request=response.request),
        )
    return result["content"]


async def fetch_html(
    client: httpx.AsyncClient,
    url: str,
    *,
    via_decodo: bool = False,
    decodo_username: str | None = None,
    decodo_password: str | None = None,
    decodo_headless: str | None = None,
) -> str:
    """Fetch a URL's raw HTML, optionally unblocked through Decodo.

    `decodo_headless="html"` turns on Decodo's JS-rendered mode -- required
    for Indeed (see _get_via_decodo's docstring), not used for LinkedIn.

    Raises FetchError (chained from the underlying httpx exception) once
    every retry attempt has failed.
    """
    try:
        if via_decodo:
            if not decodo_username or not decodo_password:
                raise ValueError("decodo_username and decodo_password are required when via_decodo=True")
            return await _get_via_decodo(
                client, url, username=decodo_username, password=decodo_password, headless=decodo_headless
            )
        response = await _get_direct(client, url)
    except (httpx.TimeoutException, httpx.TransportError, httpx.HTTPStatusError) as exc:
        raise FetchError(f"failed to fetch {url!r} after retries") from exc
    return response.text
