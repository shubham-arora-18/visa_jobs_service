"""Diagnostic script -- NOT part of the production pipeline, not imported by
anything under src/.

Compares Decodo vs Bright Data for fetching Indeed job description pages,
single attempt per URL per provider (no retries), so the numbers reflect
raw first-attempt reliability rather than retry-smoothed numbers. The same
set of URLs is tested against both providers for a fair, controlled
comparison.

Reads DECODO_USERNAME/DECODO_PASSWORD from visa_jobs_api's own .env, and
BRIGHTDATA_API_KEY/BRIGHTDATA_ZONE -- these aren't in visa_jobs_api's .env
(Bright Data was fully removed from this project when Indeed moved to
Decodo, see DECISIONS.md), so fall back to indeed_scraper_experiment/.env
if not set in the environment already.

Usage: python3 scripts/compare_decodo_brightdata.py
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import httpx
from dotenv import dotenv_values

_REPO_ROOT = Path(__file__).resolve().parent.parent
_EXPERIMENT_ENV = _REPO_ROOT.parent / "indeed_scraper_experiment" / ".env"

_env = {**dotenv_values(_REPO_ROOT / ".env"), **os.environ}
if "BRIGHTDATA_API_KEY" not in _env and _EXPERIMENT_ENV.exists():
    _env = {**dotenv_values(_EXPERIMENT_ENV), **_env}

DECODO_USERNAME = _env["DECODO_USERNAME"]
DECODO_PASSWORD = _env["DECODO_PASSWORD"]
DECODO_URL = "https://scraper-api.decodo.com/v2/scrape"

BRIGHTDATA_API_KEY = _env["BRIGHTDATA_API_KEY"]
BRIGHTDATA_ZONE = _env["BRIGHTDATA_ZONE"]
BRIGHTDATA_URL = "https://api.brightdata.com/request"

# (url, country) -- country used only for Bright Data's geo-pin param.
# Same URLs used in the prior (cloud-run) comparison, so this result is
# comparable to that one.
URLS = [
    ("https://au.indeed.com/viewjob?jk=2f9b6dc79d1a3743", "au"),
    ("https://au.indeed.com/viewjob?jk=62a51b3f5e7a7547", "au"),
    ("https://ca.indeed.com/viewjob?jk=6556b8d291b50ad5", "ca"),
    ("https://ca.indeed.com/viewjob?jk=701088afbc0177fa", "ca"),
    ("https://ie.indeed.com/viewjob?jk=ca0c9b9143a48b75", "ie"),
    ("https://ie.indeed.com/viewjob?jk=ebe4f20178b32409", "ie"),
    ("https://nz.indeed.com/viewjob?jk=f17458dbcd28872f", "nz"),
    ("https://sg.indeed.com/viewjob?jk=230e7906dbc04259", "sg"),
    ("https://sg.indeed.com/viewjob?jk=25b9531371dc15f4", "sg"),
    ("https://uk.indeed.com/viewjob?jk=b9bda1d716dd7a28", "gb"),
]


def fetch_decodo(client: httpx.Client, url: str) -> tuple[bool, str]:
    try:
        resp = client.post(
            DECODO_URL,
            auth=(DECODO_USERNAME, DECODO_PASSWORD),
            json={"url": url, "proxy_pool": "standard", "headless": "html"},
            timeout=60.0,
        )
        resp.raise_for_status()
        payload = resp.json()
        if "results" not in payload:
            return False, f"no results field: {payload.get('message', payload)}"
        result = payload["results"][0]
        status = result["status_code"]
        if status >= 400:
            return False, f"upstream status {status}"
        content = result["content"]
        has_desc = 'id="jobDescriptionText"' in content
        return has_desc, f"200, has_desc={has_desc}, len={len(content)}"
    except Exception as exc:  # noqa: BLE001
        return False, f"EXCEPTION: {exc!r}"


def fetch_brightdata(client: httpx.Client, url: str, country: str) -> tuple[bool, str]:
    try:
        resp = client.post(
            BRIGHTDATA_URL,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {BRIGHTDATA_API_KEY}"},
            json={"zone": BRIGHTDATA_ZONE, "url": url, "country": country, "format": "raw"},
            timeout=60.0,
        )
        if resp.status_code >= 400:
            return False, f"status {resp.status_code}"
        text = resp.text
        if not text:
            err = resp.headers.get("x-brd-err-msg", "empty body, no error header")
            return False, f"200 but empty body -- {err}"
        has_desc = 'id="jobDescriptionText"' in text
        return has_desc, f"200, has_desc={has_desc}, len={len(text)}"
    except Exception as exc:  # noqa: BLE001
        return False, f"EXCEPTION: {exc!r}"


def main() -> None:
    decodo_results = []
    brightdata_results = []

    with httpx.Client() as client:
        for url, _country in URLS:
            ok, detail = fetch_decodo(client, url)
            decodo_results.append(ok)
            print(f"[DECODO]     {url}  ->  {'OK' if ok else 'FAIL'}  ({detail})")
            time.sleep(1)

        for url, country in URLS:
            ok, detail = fetch_brightdata(client, url, country)
            brightdata_results.append(ok)
            print(f"[BRIGHTDATA] {url}  ->  {'OK' if ok else 'FAIL'}  ({detail})")
            time.sleep(1)

    d_success = sum(decodo_results)
    b_success = sum(brightdata_results)
    print()
    print(f"Decodo:     {d_success}/{len(URLS)} ({d_success / len(URLS) * 100:.0f}%)")
    print(f"BrightData: {b_success}/{len(URLS)} ({b_success / len(URLS) * 100:.0f}%)")


if __name__ == "__main__":
    main()
