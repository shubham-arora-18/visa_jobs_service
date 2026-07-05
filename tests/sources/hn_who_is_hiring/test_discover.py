from __future__ import annotations

import httpx
import pytest
import respx

from visa_jobs_api.sources.hn_who_is_hiring.discover import (
    ALGOLIA_SEARCH_URL,
    ThreadDiscoveryError,
    find_latest_who_is_hiring_item_id,
)

_SAMPLE_HITS = [
    {"objectID": "48747976", "title": "Ask HN: Who is hiring? (July 2026)"},
    {"objectID": "48747975", "title": "Ask HN: Who wants to be hired? (July 2026)"},
    {"objectID": "48357725", "title": "Ask HN: Who is hiring? (June 2026)"},
]


async def test_finds_the_most_recent_who_is_hiring_thread() -> None:
    with respx.mock:
        respx.get(ALGOLIA_SEARCH_URL).mock(return_value=httpx.Response(200, json={"hits": _SAMPLE_HITS}))
        async with httpx.AsyncClient() as client:
            item_id = await find_latest_who_is_hiring_item_id(client)

    assert item_id == 48747976


async def test_skips_who_wants_to_be_hired_thread_even_if_listed_first() -> None:
    hits = [
        {"objectID": "999", "title": "Ask HN: Who wants to be hired? (July 2026)"},
        {"objectID": "48747976", "title": "Ask HN: Who is hiring? (July 2026)"},
    ]
    with respx.mock:
        respx.get(ALGOLIA_SEARCH_URL).mock(return_value=httpx.Response(200, json={"hits": hits}))
        async with httpx.AsyncClient() as client:
            item_id = await find_latest_who_is_hiring_item_id(client)

    assert item_id == 48747976


async def test_raises_when_no_who_is_hiring_thread_found() -> None:
    hits = [{"objectID": "999", "title": "Ask HN: Who wants to be hired? (July 2026)"}]
    with respx.mock:
        respx.get(ALGOLIA_SEARCH_URL).mock(return_value=httpx.Response(200, json={"hits": hits}))
        async with httpx.AsyncClient() as client:
            with pytest.raises(ThreadDiscoveryError, match="no 'Who is hiring"):
                await find_latest_who_is_hiring_item_id(client)
