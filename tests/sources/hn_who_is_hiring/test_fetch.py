from __future__ import annotations

import httpx
import pytest
import respx

from visa_jobs_api.sources.hn_who_is_hiring.fetch import ALGOLIA_ITEM_URL, fetch_item_json

_ITEM_URL = f"{ALGOLIA_ITEM_URL}/48357725"


async def test_fetch_item_json_returns_parsed_payload_on_success() -> None:
    with respx.mock:
        route = respx.get(_ITEM_URL).mock(return_value=httpx.Response(200, json={"id": 48357725, "title": "hi"}))
        async with httpx.AsyncClient() as client:
            payload = await fetch_item_json(client, 48357725)

    assert payload == {"id": 48357725, "title": "hi"}
    assert route.call_count == 1


async def test_fetch_item_json_raises_http_error_on_persistent_4xx() -> None:
    with respx.mock:
        respx.get(_ITEM_URL).mock(return_value=httpx.Response(404))
        async with httpx.AsyncClient() as client:
            with pytest.raises(httpx.HTTPStatusError):
                await fetch_item_json(client, 48357725)


async def test_fetch_item_json_retries_transient_server_errors_then_succeeds() -> None:
    with respx.mock:
        route = respx.get(_ITEM_URL).mock(
            side_effect=[httpx.Response(503), httpx.Response(503), httpx.Response(200, json={"id": 48357725})]
        )
        async with httpx.AsyncClient() as client:
            payload = await fetch_item_json(client, 48357725)

    assert payload == {"id": 48357725}
    assert route.call_count == 3


async def test_fetch_item_json_gives_up_after_exhausting_retries() -> None:
    with respx.mock:
        respx.get(_ITEM_URL).mock(return_value=httpx.Response(503))
        async with httpx.AsyncClient() as client:
            with pytest.raises(httpx.HTTPStatusError):
                await fetch_item_json(client, 48357725)
