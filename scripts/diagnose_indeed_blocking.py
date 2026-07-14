"""Diagnostic script for investigate-indeed-blocking.yml -- NOT part of the
production pipeline, not imported by anything under src/.

Investigates why Indeed search returns 0 job cards + no pagination nav for
every single country when run from GitHub Actions (confirmed live on
2026-07-14, run 29321931480 on add-visa-jobs-api), despite the exact same
Selenium code working cleanly when run locally from a residential/office
IP. The leading hypothesis is IP-reputation-based bot detection: Indeed
may be serving a block/challenge page that still parses as valid, contentless
HTML (no exception, no WebDriverException, just zero cards and no nav) --
see selenium_client.py and search.py's docstrings for the code this is
investigating.

Each invocation runs ONE variant (direct HTTP, Selenium with the browser +
proxy config given) and writes its evidence to OUTPUT_DIR: a page_source
dump, a screenshot (Selenium variants only), and a summary line. The
workflow runs multiple variants in one job and uploads OUTPUT_DIR as a
single artifact, so a human can diff full page contents across variants
after the fact rather than relying on truncated log output.

Usage: python3 diagnose_indeed_blocking.py --mode <mode> --output-dir <dir> [--proxy-server <url>] [--chrome-binary <path>]
  mode: "direct-http" | "selenium"
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from urllib.parse import quote

import httpx
from selenium import webdriver
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.chrome.options import Options

sys.path.insert(0, "src")
from visa_jobs_api.sources.indeed.extract import has_additional_pages, parse_job_cards  # noqa: E402

INDEED_URL = (
    "https://www.indeed.com/jobs?q="
    + quote('("Python" OR "Backend" OR "Java" OR "software") AND ("work authorization" OR "sponsor" OR "sponsorship")')
    + "&l=&fromage=1"
)
PAGE_SETTLE_SECONDS = 6

BLOCK_MARKERS = (
    "Just a moment",
    "cf-browser-verification",
    "Additional Verification Required",
    "verify you are a human",
    "Access to this page has been denied",
    "Sign in for the best experience",
)

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36"
)


def check_public_ip(*, proxies: str | None) -> str:
    try:
        response = httpx.get("https://api.ipify.org?format=json", proxy=proxies, timeout=15)
        return response.json().get("ip", "(unknown -- unexpected response shape)")
    except httpx.HTTPError as exc:
        return f"(failed to check: {exc})"


def run_direct_http(output_dir: Path) -> None:
    print(f"public IP (direct, no proxy): {check_public_ip(proxies=None)}")
    try:
        response = httpx.get(
            INDEED_URL, headers={"User-Agent": USER_AGENT}, timeout=15, follow_redirects=True
        )
        body = response.text
        (output_dir / "direct_http_body.html").write_text(body)
        print(f"direct HTTP: status={response.status_code}, body length={len(body)}")
        print(f"direct HTTP: final URL after redirects: {response.url}")
        markers_found = [marker for marker in BLOCK_MARKERS if marker in body]
        print(f"direct HTTP: block markers found: {markers_found or 'none'}")
        print(f"direct HTTP: 'job_seen_beacon' present: {'job_seen_beacon' in body}")
    except httpx.HTTPError as exc:
        print(f"direct HTTP: FAILED -- {exc}")


def build_driver(*, proxy_server: str | None, chrome_binary: str | None) -> webdriver.Chrome:
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--window-size=1920,1080")
    options.add_argument(f"user-agent={USER_AGENT}")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    if proxy_server:
        options.add_argument(f"--proxy-server={proxy_server}")
    if chrome_binary:
        options.binary_location = chrome_binary
    return webdriver.Chrome(options=options)


def run_selenium(output_dir: Path, *, proxy_server: str | None, chrome_binary: str | None) -> None:
    print(f"public IP (as seen without proxy, for reference): {check_public_ip(proxies=None)}")
    if proxy_server:
        print(f"public IP (through proxy {proxy_server}): {check_public_ip(proxies=proxy_server)}")

    driver = build_driver(proxy_server=proxy_server, chrome_binary=chrome_binary)
    try:
        print(f"chrome binary in use: {driver.capabilities.get('browserName')} {driver.capabilities.get('browserVersion')}")
        driver.get(INDEED_URL)
        time.sleep(PAGE_SETTLE_SECONDS)

        html = driver.page_source
        current_url = driver.current_url
        (output_dir / "selenium_page_source.html").write_text(html)
        try:
            driver.save_screenshot(str(output_dir / "selenium_screenshot.png"))
        except WebDriverException as exc:
            print(f"screenshot capture failed (non-fatal): {exc}")

        cards = parse_job_cards(html, domain="www.indeed.com", query_country="United States")
        nav_present = has_additional_pages(html)
        markers_found = [marker for marker in BLOCK_MARKERS if marker in html]

        print(f"selenium: current_url after settle: {current_url}")
        print(f"selenium: page_source length: {len(html)}")
        print(f"selenium: job cards parsed: {len(cards)}")
        print(f"selenium: pagination nav present: {nav_present}")
        print(f"selenium: block markers found: {markers_found or 'none'}")
        print(f"selenium: 'vjk=' in current_url: {'vjk=' in current_url}")
    except (TimeoutException, WebDriverException) as exc:
        print(f"selenium: FAILED to load page -- {exc}")
    finally:
        driver.quit()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", required=True, choices=["direct-http", "selenium"])
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--proxy-server", default=None, help="e.g. http://127.0.0.1:8899 (unauthenticated local relay)")
    parser.add_argument("--chrome-binary", default=None, help="Path to a specific Chrome/Chromium binary to use")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    if args.mode == "direct-http":
        run_direct_http(output_dir)
    else:
        run_selenium(output_dir, proxy_server=args.proxy_server, chrome_binary=args.chrome_binary)


if __name__ == "__main__":
    main()
