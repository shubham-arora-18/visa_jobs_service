"""Fetches Hacker News items (with their full comment tree) via Algolia's API.

The direct HN page (news.ycombinator.com) rate-limits aggressively and
readily 429s requests from shared IP ranges like GitHub Actions runners.
Algolia's public search API mirrors the same underlying data as a single
JSON payload (minus deleted/flagged-hidden comments, which the rest of this
pipeline already skips anyway) without that problem.

Async port of job_digest/src/job_digest/sources/hn_who_is_hiring/fetch.py.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)

ALGOLIA_ITEM_URL = "https://hn.algolia.com/api/v1/items"
REQUEST_TIMEOUT_SECONDS = 15.0
USER_AGENT = "hn-scraper/1.0 (personal research project)"
_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


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
async def fetch_item_json(client: httpx.AsyncClient, item_id: int) -> dict[str, Any]:
    """Fetch a Hacker News item, with its full nested comment tree, as JSON.

    Retries transient failures (timeouts, connection errors, 429/5xx) with
    backoff before giving up, then raises httpx.HTTPStatusError /
    httpx.TransportError for the caller to handle.
    """
    logger.info("Fetching HN item %d via Algolia", item_id)
    response = await client.get(
        f"{ALGOLIA_ITEM_URL}/{item_id}",
        timeout=REQUEST_TIMEOUT_SECONDS,
        headers={"User-Agent": USER_AGENT},
    )
    response.raise_for_status()
    logger.info("Fetched %d bytes for item %d via Algolia", len(response.content), item_id)
    return response.json()
