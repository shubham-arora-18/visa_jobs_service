"""Shared async HTTP fetch helper, optionally routed through Decodo's Scraper API
or Bright Data's Web Unlocker.

LinkedIn and Indeed both rate-limit/block bursts of direct requests (see
linkedin_visa_scraper/DECISIONS.md and indeed_scraper_experiment/DECISIONS.md
for the investigations) -- these anti-bot proxy services handle the bypass
(proxying + rendering) on their end instead. LinkedIn uses Decodo; Indeed
uses Bright Data specifically, because Decodo was tested extensively
against Indeed and found to be blocked outright (Indeed's Cloudflare
bot-detection redirects every request to a login wall, on both its
`standard` and `premium` proxy pools -- see
indeed_scraper_experiment/DECISIONS.md), while Bright Data works. A failed
fetch raises FetchError after retries are exhausted; it must never be
treated as "page has no content", since that previously produced silently
wrong results (every job looking like it had no description).
"""

from __future__ import annotations

import logging

import httpx
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)

DECODO_SCRAPE_URL = "https://scraper-api.decodo.com/v2/scrape"
BRIGHTDATA_REQUEST_URL = "https://api.brightdata.com/request"
DIRECT_FETCH_TIMEOUT_SECONDS = 15.0
# Decodo/Bright Data both do real anti-bot bypass work per request
# (proxying, fingerprinting, headless rendering), so individual calls run
# several seconds to ~12s+ -- much slower than a direct fetch, hence the
# longer timeout.
DECODO_FETCH_TIMEOUT_SECONDS = 60.0
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


@retry(
    retry=retry_if_exception(_is_retryable),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=8),
    reraise=True,
)
async def _get_via_brightdata(client: httpx.AsyncClient, url: str, *, api_key: str, zone: str, country: str) -> str:
    response = await client.post(
        BRIGHTDATA_REQUEST_URL,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        # `country` pins the proxy exit node's geo -- without it, a first
        # test of Indeed's US site came back entirely in Spanish even with
        # no location filter in the query itself (the exit node's assumed
        # locale didn't match) -- see indeed_scraper_experiment/DECISIONS.md.
        json={"zone": zone, "url": url, "country": country, "format": "raw"},
        timeout=BRIGHTDATA_FETCH_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    # Bright Data itself can respond 200 while its own x-brd-* headers say
    # the fetch failed (IP blacklisted, an authwall/login page was detected
    # instead of real content, etc.) -- found live while investigating
    # Indeed pagination (see indeed_scraper_experiment/DECISIONS.md). Without
    # this check, that failure is silently treated as "page fetched
    # successfully" with garbage/empty content -- exactly the "must never be
    # treated as page has no content" failure mode this module's docstring
    # already warns about for other failure paths. Re-raising with the real
    # x-brd-status-code lets _is_retryable classify it the same way any
    # other upstream failure is (e.g. 502/authwall retries, 401/blacklist
    # doesn't -- retrying an IP block immediately can't help).
    brd_error = response.headers.get("x-brd-error")
    if brd_error:
        brd_status_header = response.headers.get("x-brd-status-code")
        upstream_status = int(brd_status_header) if brd_status_header and brd_status_header.isdigit() else 502
        raise httpx.HTTPStatusError(
            f"Bright Data reported a failure for {url!r}: {brd_error}",
            request=response.request,
            response=httpx.Response(upstream_status, request=response.request),
        )
    return response.text


async def fetch_html(
    client: httpx.AsyncClient,
    url: str,
    *,
    via_decodo: bool = False,
    decodo_username: str | None = None,
    decodo_password: str | None = None,
    via_brightdata: bool = False,
    brightdata_api_key: str | None = None,
    brightdata_zone: str | None = None,
    brightdata_country: str | None = None,
) -> str:
    """Fetch a URL's raw HTML, optionally unblocked through Decodo or Bright Data.

    Raises FetchError (chained from the underlying httpx exception) once
    every retry attempt has failed.
    """
    try:
        if via_decodo:
            if not decodo_username or not decodo_password:
                raise ValueError("decodo_username and decodo_password are required when via_decodo=True")
            return await _get_via_decodo(client, url, username=decodo_username, password=decodo_password)
        if via_brightdata:
            if not brightdata_api_key or not brightdata_zone or not brightdata_country:
                raise ValueError(
                    "brightdata_api_key, brightdata_zone, and brightdata_country are required when via_brightdata=True"
                )
            return await _get_via_brightdata(
                client, url, api_key=brightdata_api_key, zone=brightdata_zone, country=brightdata_country
            )
        response = await _get_direct(client, url)
    except (httpx.TimeoutException, httpx.TransportError, httpx.HTTPStatusError) as exc:
        raise FetchError(f"failed to fetch {url!r} after retries") from exc
    return response.text
