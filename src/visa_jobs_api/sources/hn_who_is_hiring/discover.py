"""Finds the current month's "Who is hiring?" thread on Hacker News.

The whoishiring bot posts a new "Ask HN: Who is hiring?" thread (and a
companion "Who wants to be hired?" thread) on the first of each month. There
is no fixed URL for "this month's" thread, so we look it up via HN's public
Algolia search API instead of hardcoding an item id.

Async port of job_digest/src/job_digest/sources/hn_who_is_hiring/discover.py
(httpx instead of requests; retry moved to tenacity, same policy).
"""

from __future__ import annotations

import logging

import httpx
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)

ALGOLIA_SEARCH_URL = "https://hn.algolia.com/api/v1/search_by_date"
REQUEST_TIMEOUT_SECONDS = 15.0
_WHO_IS_HIRING_TITLE_PREFIX = "Ask HN: Who is hiring?"
_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


class ThreadDiscoveryError(RuntimeError):
    """Raised when the current month's thread can't be found or identified."""


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
async def find_latest_who_is_hiring_item_id(client: httpx.AsyncClient) -> int:
    """Return the HN item id of the most recent "Who is hiring?" thread.

    Queries stories authored by the whoishiring account, sorted by date, and
    returns the newest one whose title is actually "Who is hiring?" (as
    opposed to the same account's "Who wants to be hired?" thread posted at
    the same time each month).
    """
    response = await client.get(
        ALGOLIA_SEARCH_URL,
        params={"tags": "story,author_whoishiring", "hitsPerPage": 10},
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    hits = response.json().get("hits", [])

    for hit in hits:
        title = hit.get("title", "")
        if title.startswith(_WHO_IS_HIRING_TITLE_PREFIX):
            item_id = int(hit["objectID"])
            logger.info("Discovered current thread: %r (id %d)", title, item_id)
            return item_id

    raise ThreadDiscoveryError(
        f"no 'Who is hiring?' thread found among the {len(hits)} most recent whoishiring posts"
    )
