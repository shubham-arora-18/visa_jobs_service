from __future__ import annotations

import json

import httpx
import pytest
import respx

from visa_jobs_api.shared.http import BRIGHTDATA_REQUEST_URL, DECODO_SCRAPE_URL, FetchError, fetch_html


async def test_fetch_html_via_decodo_returns_content_on_success() -> None:
    with respx.mock:
        respx.post(DECODO_SCRAPE_URL).mock(
            return_value=httpx.Response(200, json={"results": [{"status_code": 200, "content": "<html>real content</html>"}]})
        )
        async with httpx.AsyncClient() as client:
            html = await fetch_html(
                client,
                "https://www.linkedin.com/jobs?q=test",
                via_decodo=True,
                decodo_username="user",
                decodo_password="pass",
            )

    assert html == "<html>real content</html>"


async def test_fetch_html_via_decodo_raises_on_a_bad_upstream_status() -> None:
    with respx.mock:
        respx.post(DECODO_SCRAPE_URL).mock(
            return_value=httpx.Response(200, json={"results": [{"status_code": 502, "content": ""}]})
        )
        async with httpx.AsyncClient() as client:
            with pytest.raises(FetchError):
                await fetch_html(
                    client,
                    "https://www.linkedin.com/jobs?q=test",
                    via_decodo=True,
                    decodo_username="user",
                    decodo_password="pass",
                )


async def test_fetch_html_via_decodo_raises_when_decodo_reports_a_total_scrape_failure() -> None:
    # Decodo can respond with an outer HTTP 200 but no `results` array at
    # all when it fully gives up on a scrape -- just its own top-level
    # status/status_code/message fields instead (confirmed live: this
    # shape shows up as status_code 613, "We were not able to scrape the
    # target"). Without handling this, `["results"][0]` raises an
    # unhandled KeyError instead of a clean FetchError.
    with respx.mock:
        respx.post(DECODO_SCRAPE_URL).mock(
            return_value=httpx.Response(
                200, json={"status": "failed", "status_code": 613, "message": "We were not able to scrape the target."}
            )
        )
        async with httpx.AsyncClient() as client:
            with pytest.raises(FetchError):
                await fetch_html(
                    client,
                    "https://www.indeed.com/jobs?q=test",
                    via_decodo=True,
                    decodo_username="user",
                    decodo_password="pass",
                )


async def test_fetch_html_via_decodo_includes_headless_param_when_set() -> None:
    with respx.mock:
        route = respx.post(DECODO_SCRAPE_URL).mock(
            return_value=httpx.Response(200, json={"results": [{"status_code": 200, "content": "<html></html>"}]})
        )
        async with httpx.AsyncClient() as client:
            await fetch_html(
                client,
                "https://www.indeed.com/viewjob?jk=abc",
                via_decodo=True,
                decodo_username="user",
                decodo_password="pass",
                decodo_headless="html",
            )

    sent_body = json.loads(route.calls.last.request.content)
    assert sent_body["headless"] == "html"


async def test_fetch_html_via_decodo_omits_headless_param_when_not_set() -> None:
    # LinkedIn's calls don't pass decodo_headless -- confirms it isn't sent
    # at all in that case, not sent as an empty/None value.
    with respx.mock:
        route = respx.post(DECODO_SCRAPE_URL).mock(
            return_value=httpx.Response(200, json={"results": [{"status_code": 200, "content": "<html></html>"}]})
        )
        async with httpx.AsyncClient() as client:
            await fetch_html(
                client,
                "https://www.linkedin.com/jobs?q=test",
                via_decodo=True,
                decodo_username="user",
                decodo_password="pass",
            )

    sent_body = json.loads(route.calls.last.request.content)
    assert "headless" not in sent_body


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
