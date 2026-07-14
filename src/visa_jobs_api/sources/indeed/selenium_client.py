"""Fetches an Indeed search-results page through a real, headless Chrome browser.

Used for Indeed's *search* pages specifically (description-page fetches go
through Decodo instead -- see sources/indeed/description.py; both used to
go through Bright Data, since fully removed). One thing a raw-HTTP/proxy
fetch structurally cannot do drove this to Selenium instead of Decodo for
search specifically, confirmed live (see indeed_scraper_experiment/DECISIONS.md):

- Indeed attaches a `vjk` (viewed-job-key) query param to the address bar
  via client-side JS after a search page loads -- it never appears in the
  server-rendered HTML a raw HTTP/proxy fetch returns (confirmed for both
  Bright Data and Decodo), only in a real browser's post-JS URL, since a
  scrape API has no equivalent to a browser's navigable address bar.
  Search.py needs this per-query vjk to build every subsequent page's URL.
- A real browser, hitting Indeed directly with no proxy at all, was not
  blocked in any of dozens of live test requests -- unlike a raw direct
  fetch (client-side Cloudflare bot-detection, HTTP 403). Whether this
  holds under GitHub Actions' runner IPs specifically (vs. the
  residential/office IP this was validated from) is untested -- worth
  watching the scheduled run's logs for a regression back to
  blocked/empty pages.

Selenium's WebDriver calls are all synchronous/blocking; fetch_html_via_selenium
wraps them in asyncio.to_thread so a slow page load doesn't stall the event
loop other sources (HN, LinkedIn) run on concurrently.
"""

from __future__ import annotations

import asyncio
import logging
import time

from selenium import webdriver
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.chrome.options import Options

logger = logging.getLogger(__name__)

# An ordinary desktop Chrome UA -- Selenium's default UA string can itself
# be a bot signal, confirmed as part of settling on this config live (see
# indeed_scraper_experiment/DECISIONS.md).
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36"
)


class SeleniumFetchError(RuntimeError):
    """Raised when a URL can't be loaded through Selenium/Chrome."""


def _build_driver() -> webdriver.Chrome:
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--window-size=1920,1080")
    options.add_argument(f"user-agent={_USER_AGENT}")
    # Required to run as root, which CI containers commonly do -- Chrome
    # refuses to start otherwise.
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    return webdriver.Chrome(options=options)


def _fetch_sync(url: str, *, page_settle_seconds: float) -> tuple[str, str]:
    """Loads `url`, waits for its JS to settle, returns (html, current_url).

    current_url reflects any client-side URL mutation (e.g. `vjk` being
    appended) that happened after the initial navigation -- driver.get()
    alone wouldn't show that, only reading current_url after the settle
    wait does.
    """
    driver = _build_driver()
    try:
        driver.get(url)
        # Selenium has no built-in "wait for a client-side JS URL mutation"
        # signal to hook an explicit wait on -- an arbitrary settle delay is
        # the same approach validated live in indeed_scraper_experiment.
        time.sleep(page_settle_seconds)
        return driver.page_source, driver.current_url
    except (TimeoutException, WebDriverException) as exc:
        raise SeleniumFetchError(f"failed to load {url!r} via Selenium: {exc}") from exc
    finally:
        driver.quit()


async def fetch_html_via_selenium(url: str, *, page_settle_seconds: float) -> tuple[str, str]:
    """Async wrapper around _fetch_sync -- see its docstring for return shape."""
    return await asyncio.to_thread(_fetch_sync, url, page_settle_seconds=page_settle_seconds)
