"""Shared async HTTP fetch helper, optionally routed through Bright Data's Web Unlocker.

LinkedIn rate-limits/blocks bursts of direct requests (see
linkedin_visa_scraper/DECISIONS.md for the investigation) -- Bright Data's
Web Unlocker API handles the anti-bot bypass on their end instead. A failed
fetch raises FetchError after retries are exhausted; it must never be
treated as "page has no content", since that previously produced silently
wrong results (every job looking like it had no description).
"""

from __future__ import annotations

import logging

import httpx
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)

BRIGHTDATA_REQUEST_URL = "https://api.brightdata.com/request"
DIRECT_FETCH_TIMEOUT_SECONDS = 15.0
# Web Unlocker does real anti-bot bypass work per request (fingerprinting,
# challenge-solving), so individual calls run several seconds to ~12s+ --
# much slower than a direct fetch, hence the longer timeout.
BRIGHTDATA_FETCH_TIMEOUT_SECONDS = 60.0

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
async def _get_via_brightdata(client: httpx.AsyncClient, url: str, *, api_key: str, zone: str) -> httpx.Response:
    response = await client.post(
        BRIGHTDATA_REQUEST_URL,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        json={"zone": zone, "url": url, "format": "raw"},
        timeout=BRIGHTDATA_FETCH_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    return response


async def fetch_html(
    client: httpx.AsyncClient,
    url: str,
    *,
    via_brightdata: bool,
    brightdata_api_key: str | None = None,
    brightdata_zone: str | None = None,
) -> str:
    """Fetch a URL's raw HTML, optionally unblocked through Bright Data.

    Raises FetchError (chained from the underlying httpx exception) once
    every retry attempt has failed.
    """
    try:
        if via_brightdata:
            if not brightdata_api_key or not brightdata_zone:
                raise ValueError("brightdata_api_key and brightdata_zone are required when via_brightdata=True")
            response = await _get_via_brightdata(client, url, api_key=brightdata_api_key, zone=brightdata_zone)
        else:
            response = await _get_direct(client, url)
    except (httpx.TimeoutException, httpx.TransportError, httpx.HTTPStatusError) as exc:
        raise FetchError(f"failed to fetch {url!r} after retries") from exc
    return response.text
