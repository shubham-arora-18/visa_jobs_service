"""Shared async HTTP fetch helper, optionally routed through Bright Data's
Web Unlocker.

LinkedIn and Indeed both rate-limit/block bursts of direct requests (see
linkedin_visa_scraper/DECISIONS.md and indeed_scraper_experiment/DECISIONS.md
for the investigations). Both sources' search and description fetches go
through Bright Data's Web Unlocker, geo-pinned per country (`country=...`)
-- LinkedIn used to go through Decodo's Scraper API instead, but that's been
retired in favor of standardizing on one provider; see DECISIONS.md. A
failed fetch raises FetchError after retries are exhausted; it must never be
treated as "page has no content", since that previously produced silently
wrong results (every job looking like it had no description). Bright Data
can also return a 200 wrapping its own internal error (e.g. a blacklisted
source IP, confirmed live) with an empty body -- that's detected and raised
as a retryable error too, for the same reason.
"""

from __future__ import annotations

import logging

import httpx
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)

BRIGHTDATA_REQUEST_URL = "https://api.brightdata.com/request"
DIRECT_FETCH_TIMEOUT_SECONDS = 15.0
# Bright Data does real anti-bot bypass work per request (proxying,
# fingerprinting, headless rendering), so individual calls run several
# seconds to ~12s+ -- much slower than a direct fetch, hence the longer
# timeout.
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
async def _get_via_brightdata(client: httpx.AsyncClient, url: str, *, api_key: str, zone: str, country: str) -> str:
    response = await client.post(
        BRIGHTDATA_REQUEST_URL,
        headers={"Authorization": f"Bearer {api_key}"},
        json={"zone": zone, "url": url, "country": country, "format": "raw"},
        timeout=BRIGHTDATA_FETCH_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    if not response.text:
        # Bright Data's proxy layer can return a 200 wrapping its own
        # internal error (confirmed live: a source IP blacklisted in the
        # zone's own access settings came back this way) instead of a
        # normal HTTP error status -- an empty body must never be treated
        # as "page has no content" (see module docstring), so it's raised
        # as a retryable error carrying Bright Data's own error header.
        err = response.headers.get("x-brd-err-msg", "empty body, no error header")
        raise httpx.HTTPStatusError(
            f"Bright Data returned an empty body for {url!r}: {err}",
            request=response.request,
            response=httpx.Response(502, request=response.request),
        )
    return response.text


async def fetch_html(
    client: httpx.AsyncClient,
    url: str,
    *,
    via_brightdata: bool = False,
    brightdata_api_key: str | None = None,
    brightdata_zone: str | None = None,
    brightdata_country: str | None = None,
) -> str:
    """Fetch a URL's raw HTML, optionally unblocked through Bright Data.

    `brightdata_country` geo-pins Bright Data's exit node (required -- an
    earlier test without it returned a page in the wrong locale, see
    indeed_scraper_experiment/DECISIONS.md).

    Raises FetchError (chained from the underlying httpx exception) once
    every retry attempt has failed.
    """
    try:
        if via_brightdata:
            if not brightdata_api_key or not brightdata_zone or not brightdata_country:
                raise ValueError(
                    "brightdata_api_key, brightdata_zone, and brightdata_country are required when "
                    "via_brightdata=True"
                )
            return await _get_via_brightdata(
                client, url, api_key=brightdata_api_key, zone=brightdata_zone, country=brightdata_country
            )
        response = await _get_direct(client, url)
    except (httpx.TimeoutException, httpx.TransportError, httpx.HTTPStatusError) as exc:
        raise FetchError(f"failed to fetch {url!r} after retries") from exc
    return response.text
