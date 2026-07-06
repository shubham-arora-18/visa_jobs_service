"""Shared async HTTP fetch helper, optionally routed through Decodo's Scraper API.

LinkedIn rate-limits/blocks bursts of direct requests (see
linkedin_visa_scraper/DECISIONS.md for the investigation) -- Decodo's
Scraper API handles the anti-bot bypass (proxying + rendering) on their end
instead. A failed fetch raises FetchError after retries are exhausted; it
must never be treated as "page has no content", since that previously
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
async def _get_via_decodo(client: httpx.AsyncClient, url: str, *, username: str, password: str) -> str:
    response = await client.post(
        DECODO_SCRAPE_URL,
        auth=(username, password),
        json={"url": url, "proxy_pool": "standard"},
        timeout=DECODO_FETCH_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    result = response.json()["results"][0]
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
    via_decodo: bool,
    decodo_username: str | None = None,
    decodo_password: str | None = None,
) -> str:
    """Fetch a URL's raw HTML, optionally unblocked through Decodo.

    Raises FetchError (chained from the underlying httpx exception) once
    every retry attempt has failed.
    """
    try:
        if via_decodo:
            if not decodo_username or not decodo_password:
                raise ValueError("decodo_username and decodo_password are required when via_decodo=True")
            return await _get_via_decodo(client, url, username=decodo_username, password=decodo_password)
        response = await _get_direct(client, url)
    except (httpx.TimeoutException, httpx.TransportError, httpx.HTTPStatusError) as exc:
        raise FetchError(f"failed to fetch {url!r} after retries") from exc
    return response.text
