"""Fetches an Indeed search-results page through a real, headless Firefox browser.

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
  fetch (client-side Cloudflare bot-detection, HTTP 403).

**Firefox, not Chrome.** This used to run on Chrome. Confirmed live (see
DECISIONS.md): a residential-proxy setup that reliably hit real Cloudflare
"Just a moment..." challenges on Chrome -- same proxy, same query, same
retry logic -- got 0 blocks across all 8 countries (28 total fetches) when
switched to Firefox. Whatever Indeed's bot detection is keying on, it
looks specific to something about Chrome (fingerprint, automation flags,
or TLS signature), not the residential IP itself.

**Proxy auth, not IP-whitelist.** Firefox has no `--proxy-server=http://
user:pass@host:port` command-line support either, so a temporary unpacked
WebExtension is generated per fetch (`_build_proxy_extension`) that routes
traffic via `browser.proxy.onRequest` and answers the proxy's auth prompt
via `browser.webRequest.onAuthRequired` -- both still fully supported in
Firefox (unlike Chrome, which dropped blocking `webRequest` for most
extensions in Manifest V3 and, separately, stopped loading Manifest V2
extensions at all in regular builds -- see DECISIONS.md for both dead
ends this replaces). Real username/password auth means this doesn't
depend on a specific machine's IP staying whitelisted with the proxy
provider, unlike the Chrome/IP-whitelist setup this replaces.

Deliberately no per-request country-targeting suffix on the username --
the proxy just hands out whatever country is in its pool, randomly, same
for every query regardless of which Indeed country site it's hitting, by
design (see DECISIONS.md) -- this app doesn't try to match the proxy's
exit country to the domain being scraped.

Selenium's WebDriver calls are all synchronous/blocking; fetch_html_via_selenium
wraps them in asyncio.to_thread so a slow page load doesn't stall the event
loop other sources (HN, LinkedIn) run on concurrently.
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import tempfile
import time
from dataclasses import dataclass

from selenium import webdriver
from selenium.webdriver.firefox.options import Options

logger = logging.getLogger(__name__)

_PROXY_EXTENSION_MANIFEST = {
    "manifest_version": 2,
    "name": "Proxy Auth",
    "version": "1.0.0",
    "browser_specific_settings": {"gecko": {"id": "visa-jobs-api-proxy-auth@internal"}},
    "permissions": ["proxy", "webRequest", "webRequestBlocking", "<all_urls>"],
    "background": {"scripts": ["background.js"]},
}


class SeleniumFetchError(RuntimeError):
    """Raised when a URL can't be loaded through Selenium/Firefox."""


@dataclass(frozen=True)
class ProxyConfig:
    """A residential-proxy endpoint for Selenium's traffic. See module docstring."""

    host: str
    port: int
    username: str
    password: str


def _build_proxy_extension(proxy: ProxyConfig) -> str:
    """Writes a temporary unpacked Firefox WebExtension that routes traffic
    through `proxy` and answers Firefox's proxy-auth prompt automatically --
    no per-request country-targeting suffix on the username, see module
    docstring. Returns the extension directory's path; the caller is
    responsible for deleting it once Firefox has exited."""
    extension_dir = tempfile.mkdtemp(prefix="indeed_proxy_ext_")
    background_js = f"""
browser.proxy.onRequest.addListener(
  function (details) {{
    return {{ type: "http", host: {json.dumps(proxy.host)}, port: {proxy.port} }};
  }},
  {{ urls: ["<all_urls>"] }}
);

browser.webRequest.onAuthRequired.addListener(
  function (details) {{
    return {{
      authCredentials: {{
        username: {json.dumps(proxy.username)},
        password: {json.dumps(proxy.password)}
      }}
    }};
  }},
  {{ urls: ["<all_urls>"] }},
  ["blocking"]
);
"""
    with open(f"{extension_dir}/manifest.json", "w") as f:
        json.dump(_PROXY_EXTENSION_MANIFEST, f)
    with open(f"{extension_dir}/background.js", "w") as f:
        f.write(background_js)
    return extension_dir


def _build_driver() -> webdriver.Firefox:
    options = Options()
    options.add_argument("--headless")
    # Only the HTML/DOM is ever read -- images cost real bandwidth (and
    # real money on a metered residential proxy) for zero benefit here.
    # Not the same knob as Chrome's (a preference name, not a content
    # setting constant), but the same 0/1/2 = default/allow/block scale.
    options.set_preference("permissions.default.image", 2)
    return webdriver.Firefox(options=options)


def _fetch_sync(url: str, *, page_settle_seconds: float, proxy: ProxyConfig | None) -> tuple[str, str]:
    """Loads `url`, waits for its JS to settle, returns (html, current_url).

    current_url reflects any client-side URL mutation (e.g. `vjk` being
    appended) that happened after the initial navigation -- driver.get()
    alone wouldn't show that, only reading current_url after the settle
    wait does.
    """
    driver = _build_driver()
    extension_dir = _build_proxy_extension(proxy) if proxy is not None else None
    try:
        if extension_dir is not None:
            # Must be installed on an already-running driver -- unlike
            # Chrome's --load-extension, there's no command-line flag for
            # this. `temporary=True` skips Firefox's usual signing
            # requirement, which only unsigned/unpacked test extensions
            # like this one would otherwise fail.
            driver.install_addon(extension_dir, temporary=True)
        driver.get(url)
        # Selenium has no built-in "wait for a client-side JS URL mutation"
        # signal to hook an explicit wait on -- an arbitrary settle delay is
        # the same approach validated live in indeed_scraper_experiment.
        time.sleep(page_settle_seconds)
        return driver.page_source, driver.current_url
    except Exception as exc:
        # Deliberately broad, not just (TimeoutException, WebDriverException):
        # a hung/slow page load can surface as a raw urllib3.ReadTimeoutError
        # from the local geckodriver session instead (confirmed live for the
        # Chrome/chromedriver equivalent -- see digest_2026-07-24_09-51-45.log
        # -- geckodriver's own HTTP layer has the same structural gap).
        # Every caller of fetch_html_via_selenium relies on SeleniumFetchError
        # specifically to skip just the one page/query and keep going (see
        # search.py) -- letting a raw exception type through here bypasses
        # that and previously took down the whole Indeed source for the run
        # over a single slow page.
        raise SeleniumFetchError(f"failed to load {url!r} via Selenium: {exc}") from exc
    finally:
        driver.quit()
        if extension_dir is not None:
            shutil.rmtree(extension_dir, ignore_errors=True)


async def fetch_html_via_selenium(
    url: str, *, page_settle_seconds: float, proxy: ProxyConfig | None = None
) -> tuple[str, str]:
    """Async wrapper around _fetch_sync -- see its docstring for return shape."""
    return await asyncio.to_thread(_fetch_sync, url, page_settle_seconds=page_settle_seconds, proxy=proxy)
