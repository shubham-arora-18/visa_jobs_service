from __future__ import annotations

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
                "https://www.indeed.com/jobs?q=test",
                via_brightdata=True,
                brightdata_api_key="key",
                brightdata_zone="zone",
                brightdata_country="us",
            )

    assert html == "<html>real content</html>"


async def test_fetch_html_via_brightdata_raises_on_x_brd_error_even_with_outer_200(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Bright Data can respond with an outer HTTP 200 while its own
    # x-brd-error header says the fetch actually failed (IP blacklisted, an
    # authwall/login page detected instead of real content, etc.) -- found
    # live while investigating Indeed pagination. This must not be silently
    # treated as "page fetched successfully" with garbage content.
    with respx.mock:
        respx.post(BRIGHTDATA_REQUEST_URL).mock(
            return_value=httpx.Response(
                200,
                text="",
                headers={"x-brd-error": "Auth Failed (code: ip_blacklisted)", "x-brd-status-code": "401"},
            )
        )
        async with httpx.AsyncClient() as client:
            with pytest.raises(FetchError):
                await fetch_html(
                    client,
                    "https://www.indeed.com/jobs?q=test",
                    via_brightdata=True,
                    brightdata_api_key="key",
                    brightdata_zone="zone",
                    brightdata_country="us",
                )


async def test_fetch_html_via_brightdata_retries_a_retryable_x_brd_error() -> None:
    # x-brd-status-code 502 (e.g. "login page found"/authwall) is in the
    # same retryable set as a direct 502 would be -- unlike a 401
    # (blacklisted), where retrying immediately can't help.
    with respx.mock:
        route = respx.post(BRIGHTDATA_REQUEST_URL).mock(
            side_effect=[
                httpx.Response(200, text="", headers={"x-brd-error": "login page found", "x-brd-status-code": "502"}),
                httpx.Response(200, text="<html>real content</html>"),
            ]
        )
        async with httpx.AsyncClient() as client:
            html = await fetch_html(
                client,
                "https://www.indeed.com/jobs?q=test",
                via_brightdata=True,
                brightdata_api_key="key",
                brightdata_zone="zone",
                brightdata_country="us",
            )

    assert html == "<html>real content</html>"
    assert route.call_count == 2
