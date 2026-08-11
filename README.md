# visa-jobs-api

FastAPI service that aggregates visa-sponsoring software engineering job
listings from Hacker News ("Who is hiring?"), LinkedIn, and Indeed into
one emailed digest, grouped by country. Defaults to the last 24 hours;
configurable per-request (see below).

See `ARCHITECTURE.md` for a diagram of how it fits together, and
`DECISIONS.md` for the reasoning behind the non-obvious choices made while
building it. See `LOCAL_SETUP.md` for a full checklist to run this on a
new machine, including the local scheduled-run setup described below.

## Setup

```bash
python3.11 -m venv .venv
.venv/bin/pip install -e ".[test]"
cp .env.example .env  # fill in real values
```

Also requires Google Chrome installed (Indeed search uses Selenium --
Selenium 4's built-in Selenium Manager auto-downloads a matching
chromedriver, no separate driver install needed).

Required `.env` values: `HF_TOKEN` (Hugging Face Inference Providers),
`GMAIL_ADDRESS`/`GMAIL_APP_PASSWORD`/`DIGEST_RECIPIENTS` (email delivery),
`BRIGHTDATA_API_KEY`/`BRIGHTDATA_ZONE` (LinkedIn's search + description
fetches, and Indeed's description fetches, via Bright Data's Web Unlocker
-- see `DECISIONS.md`). Indeed's *search* pages go through Selenium/a real
headless Chrome instead of a proxy (see `sources/indeed/selenium_client.py`).

## Run

```bash
.venv/bin/uvicorn visa_jobs_api.main:app --reload
```

Then trigger a run (body is optional -- omit it entirely for the defaults
shown below):

```bash
curl -X POST http://127.0.0.1:8000/digest/run \
  -H "Content-Type: application/json" \
  -d '{
    "linkedin_keywords": "(Python OR Backend OR Java) AND (sponsor OR sponsorship)",
    "indeed_keywords": "(\"Python\" OR \"Backend\" OR \"Java\" OR \"software\") AND (\"sponsor\" OR \"sponsorship\")",
    "posted_within": "day"
  }'
```

`linkedin_keywords` and `indeed_keywords` are independent -- tune one
without affecting the other. `posted_within` accepts `"day"` (24h,
default), `"week"` (168h), or `"month"` (720h), applied to all three
sources (Indeed maps it to the closest `fromage` value it supports: 1/3/7/14
days -- see `sources/indeed/search.py`). This runs all three sources
concurrently, emails the combined digest to `DIGEST_RECIPIENTS`, and
returns a JSON summary of what was found. A combined per-country
breakdown of every Selenium (Indeed search) and Bright Data (Indeed
details, LinkedIn) call made during the run is logged at the end (see
`shared/call_stats.py`).

## Scheduled daily run

Currently runs **locally**, not on GitHub Actions -- Indeed search via
Selenium is blocked from GitHub Actions' datacenter IP (see `DECISIONS.md`).
`scripts/run_digest_locally.sh` runs the digest via the `visa-jobs-digest`
CLI entry point (same `run_digest` logic as the API, defaulting to a
1-day window), scheduled through a macOS `launchd` LaunchAgent for a daily
9:00 AM run. See `LOCAL_SETUP.md` for the full setup checklist.

`.github/workflows/daily-digest.yml` still exists for manual testing
(`workflow_dispatch` only, no schedule) -- requires these set as repo
secrets/variables: `HF_TOKEN`, `GMAIL_ADDRESS`, `GMAIL_APP_PASSWORD`,
`BRIGHTDATA_API_KEY`, `BRIGHTDATA_ZONE` (secrets), `DIGEST_RECIPIENTS`
(variable). Or just run the same command locally:

```bash
.venv/bin/visa-jobs-digest
```

## Test

```bash
.venv/bin/pytest
```
