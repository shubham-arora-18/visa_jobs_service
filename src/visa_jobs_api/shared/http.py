"""Shared async HTTP fetch helper, optionally routed through Decodo's Scraper
API or Bright Data's Web Unlocker.

LinkedIn and Indeed both rate-limit/block bursts of direct requests (see
linkedin_visa_scraper/DECISIONS.md and indeed_scraper_experiment/DECISIONS.md
for the investigations). LinkedIn goes through Decodo. Indeed's description
fetches went through Decodo's JS-rendered mode for a while, but a live
side-by-side comparison (10 identical URLs against each provider, single
attempt, no retries) found Decodo failing a large share of description
fetches (401s and read timeouts) -- see DECISIONS.md. Indeed's description
fetches now go through Bright Data's Web Unlocker instead, geo-pinned per
country (`country=...`); Decodo remains in use for LinkedIn. A failed fetch
raises FetchError after retries are exhausted; it must never be treated as
"page has no content", since that previously produced silently wrong
results (every job looking like it had no description). Bright Data can
also return a 200 wrapping its own internal error (e.g. a blacklisted
source IP, confirmed live) with an empty body -- that's detected and raised
as a retryable error too, for the same reason.
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
    via_decodo: bool = False,
    decodo_username: str | None = None,
    decodo_password: str | None = None,
    decodo_headless: str | None = None,
    via_brightdata: bool = False,
    brightdata_api_key: str | None = None,
    brightdata_zone: str | None = None,
    brightdata_country: str | None = None,
) -> str:
    """Fetch a URL's raw HTML, optionally unblocked through Decodo or Bright Data.

    `decodo_headless="html"` turns on Decodo's JS-rendered mode -- used for
    LinkedIn's Decodo calls, not Indeed's (Indeed now goes through Bright
    Data instead, see module docstring). `brightdata_country` geo-pins
    Bright Data's exit node (required -- an earlier test without it
    returned a page in the wrong locale, see indeed_scraper_experiment/DECISIONS.md).

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
