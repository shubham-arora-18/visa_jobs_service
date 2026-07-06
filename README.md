# visa-jobs-api

FastAPI service that aggregates visa-sponsoring software engineering job
listings from Hacker News ("Who is hiring?") and LinkedIn into one emailed
digest, grouped by country. Defaults to the last 24 hours; configurable
per-request (see below).

See `ARCHITECTURE.md` for a diagram of how it fits together, and
`DECISIONS.md` for the reasoning behind the non-obvious choices made while
building it.

## Setup

```bash
python3.11 -m venv .venv
.venv/bin/pip install -e ".[test]"
cp .env.example .env  # fill in real values
```

Required `.env` values: `HF_TOKEN` (Hugging Face Inference Providers),
`GMAIL_ADDRESS`/`GMAIL_APP_PASSWORD`/`DIGEST_RECIPIENTS` (email delivery),
`DECODO_USERNAME`/`DECODO_PASSWORD` (LinkedIn scraping via Decodo's Scraper
API -- LinkedIn blocks direct scraping at any real concurrency, see
`DECISIONS.md`).

## Run

```bash
.venv/bin/uvicorn visa_jobs_api.main:app --reload
```

Then trigger a run (body is optional -- omit it entirely for the defaults
shown below):

```bash
curl -X POST http://127.0.0.1:8000/digest/run \
  -H "Content-Type: application/json" \
  -d '{"linkedin_keywords": "(Python OR Backend OR Java) AND (sponsor OR sponsorship)", "posted_within": "day"}'
```

`posted_within` accepts `"day"` (24h, default), `"week"` (168h), or
`"month"` (720h). This runs both sources concurrently, emails the combined
digest to `DIGEST_RECIPIENTS`, and returns a JSON summary of what was
found.

## Scheduled daily run

`.github/workflows/daily-digest.yml` runs the digest automatically every
day at 09:00 IST via the `visa-jobs-digest` CLI entry point (same
`run_digest` logic as the API, defaulting to a 1-day window -- no HTTP
server needed for the scheduled run). Requires these set as repo
secrets/variables: `HF_TOKEN`, `GMAIL_ADDRESS`, `GMAIL_APP_PASSWORD`,
`DECODO_USERNAME`, `DECODO_PASSWORD` (secrets), `DIGEST_RECIPIENTS`
(variable). Trigger it manually via the Actions tab
("workflow_dispatch") or run the same command locally:

```bash
.venv/bin/visa-jobs-digest
```

## Test

```bash
.venv/bin/pytest
```
