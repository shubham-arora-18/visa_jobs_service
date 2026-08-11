from __future__ import annotations

import json

import httpx
import pytest
import respx

from visa_jobs_api.shared.http import BRIGHTDATA_REQUEST_URL, FetchError, fetch_html


async def test_fetch_html_via_brightdata_returns_content_on_success() -> None:
    with respx.mock:
        respx.post(BRIGHTDATA_REQUEST_URL).mock(return_value=httpx.Response(200, text="<html>real content</html>"))
        async with httpx.AsyncClient() as client:
            html = await fetch_html(
                client,
                "https://au.indeed.com/viewjob?jk=abc",
                via_brightdata=True,
                brightdata_api_key="key",
                brightdata_zone="zone",
                brightdata_country="au",
            )

    assert html == "<html>real content</html>"


async def test_fetch_html_via_brightdata_sends_zone_url_country_and_raw_format() -> None:
    with respx.mock:
        route = respx.post(BRIGHTDATA_REQUEST_URL).mock(return_value=httpx.Response(200, text="<html></html>"))
        async with httpx.AsyncClient() as client:
            await fetch_html(
                client,
                "https://au.indeed.com/viewjob?jk=abc",
                via_brightdata=True,
                brightdata_api_key="key",
                brightdata_zone="zone",
                brightdata_country="au",
            )

    sent_body = json.loads(route.calls.last.request.content)
    assert sent_body == {
        "zone": "zone",
        "url": "https://au.indeed.com/viewjob?jk=abc",
        "country": "au",
        "format": "raw",
    }
    assert route.calls.last.request.headers["Authorization"] == "Bearer key"


async def test_fetch_html_via_brightdata_raises_on_a_200_with_an_empty_body() -> None:
    # Confirmed live: Bright Data's proxy layer can return a 200 wrapping
    # its own internal error (a source IP blacklisted in the zone's access
    # settings) instead of a normal HTTP error status -- an empty body must
    # be treated as a failure, never as "page has no content".
    with respx.mock:
        respx.post(BRIGHTDATA_REQUEST_URL).mock(
            return_value=httpx.Response(200, text="", headers={"x-brd-err-msg": "IP blacklisted"})
        )
        async with httpx.AsyncClient() as client:
            with pytest.raises(FetchError):
                await fetch_html(
                    client,
                    "https://au.indeed.com/viewjob?jk=abc",
                    via_brightdata=True,
                    brightdata_api_key="key",
                    brightdata_zone="zone",
                    brightdata_country="au",
                )


async def test_fetch_html_via_brightdata_requires_credentials_and_country() -> None:
    async with httpx.AsyncClient() as client:
        with pytest.raises(ValueError, match="brightdata_api_key"):
            await fetch_html(client, "https://au.indeed.com/viewjob?jk=abc", via_brightdata=True)
