# Decision log

A new FastAPI service (`visa_jobs_api`) that reuses and rewrites the logic
from `job_digest` (Hacker News "Who is hiring?" source) and
`linkedin_visa_scraper` (LinkedIn source) into one combined, async,
emailed digest. This log records every non-obvious choice made while
building it.

## Repo and code-reuse approach

- **Separate new repo**, not added into job_digest's existing repo. You
  explicitly chose this after I laid out the tradeoff (job_digest's repo
  already has a real `origin`/`main`/PR history, which initially made it
  look like the natural home) -- with a separate service needing both
  sources' logic anyway, keeping it as its own repo avoids entangling two
  otherwise-independent projects.
- **Vendor and adapt, not a package dependency.** Both job_digest's
  `hn_who_is_hiring` source and `linkedin_visa_scraper`'s scraping logic
  were copied in and rewritten (async, typed, layered) rather than
  installed as a git-based pip dependency on either existing repo. Simpler
  for a personal project with three separate private repos -- no
  cross-repo SSH/install complexity at deploy or CI time -- at the cost of
  the HN logic now existing in two places that could drift apart if one is
  changed without the other.
- **Git push deferred to you.** The repo is git-initialized locally with
  normal commits, but nothing is pushed and no PR is opened -- you asked to
  handle that yourself. Along the way we discovered this machine's `gh`/SSH
  credentials only authenticate as `shubarora_adobe` (a different,
  Adobe-linked GitHub identity from `shubham-arora-18`, which owns your
  other repos), so if you want this under `shubham-arora-18`, you'll need
  to either push from a machine/credential set that has access to that
  account, or add this machine's SSH key to it first.

## Layering

Standard router / service / DTO / domain split, per the PR Review Prompt's
coupling & extensibility guidance:

- `api/routers/` -- HTTP <-> DTO translation only, no business logic.
- `api/services/` -- orchestrates a run (call the aggregator, then the
  emailer, then summarize). This is the one layer that knows about "a
  digest run" as a concept.
- `api/dto/` -- Pydantic response models (`DigestRunResponse`,
  `SourceCount`).
- `sources/<name>/` -- each source owns its entire pipeline
  (discover/fetch/parse/extract) behind one `JobSource.fetch_jobs()`
  method (see `sources/__init__.py`), mirroring job_digest's existing
  "adding a new producer needs no changes elsewhere" design.
- `shared/` -- code genuinely identical across sources: the regex
  mention-finder, the LLM confirmation call, the `NormalizedJob` DTO, the
  bounded-concurrency helper, and the HTTP fetch helper.
- `aggregation/` -- the only place that knows about *multiple* sources at
  once: runs them concurrently, merges, groups, sorts, renders.

## Async conversion scope

Converted **both** sources fully to async (httpx instead of requests,
`AsyncOpenAI` instead of `OpenAI`), not just the new LinkedIn code -- you
confirmed this explicitly, since "async throughout the project for all api
calls" is more literally satisfied this way, and it lets both sources run
under one shared event loop via `asyncio.gather` rather than bridging a
sync source onto a thread. This touches previously-working, tested HN code
(`discover.py`, `fetch.py`, the old sync `llm_filter.py`), so its tests
were rewritten against the async versions (see `tests/sources/hn_who_is_hiring/`)
rather than left as-is.

Email delivery was also made async (`aiosmtplib` instead of `smtplib`) for
the same reason -- SMTP delivery is a network call like any other.

`parse.py` (HN's Algolia-JSON-to-model parsing) and all HTML parsing
(`BeautifulSoup` calls in both sources) stay synchronous -- these are local
CPU work, not API calls, so there's nothing to make async; forcing them
through `asyncio.to_thread` would add overhead for no benefit.

## Shared visa-detection module (`shared/visa_keywords.py`, `shared/visa_llm.py`)

job_digest's `hn_who_is_hiring/visa_filter.py` and
`linkedin_visa_scraper/visa_filter.py` had textually identical sentence-level
regex logic (the second was originally copied from the first, earlier this
project's history) and near-identical LLM-confirmation call plumbing, just
with different system-prompt wording. Merged into:

- `shared/visa_keywords.py` -- the regex mention-finder, verbatim, single
  source of truth now.
- `shared/visa_llm.py` -- the async LLM call, JSON parsing, and Pydantic
  validation, parameterized by `system_prompt` so each source still
  supplies its own domain-specific screening criteria (HN's framing
  mentions replies/threads; LinkedIn's doesn't). The *output-format*
  contract (the JSON schema instruction) is owned entirely by this shared
  module, appended to whatever prompt a source provides -- so the two
  sources' prompts can't drift out of sync on what shape the model must
  respond in.

## Tech stack and role-group fallback (post-launch follow-up)

After the first real email, "Tech: not mentioned" showed up for a LinkedIn
job -- LinkedIn never had any tech-stack detection at all (`tech_stack=[]`
was hardcoded in `sources/linkedin/source.py`), unlike HN, which has a
regex-based `_detect_tech_stack` keyword table. Rather than build a second
regex keyword table for LinkedIn, extended the same shared LLM call
(`shared/visa_llm.confirm_visa_offer`) to also return `tech_stack` (the
technologies/languages/frameworks it can find in the text) and `role_group`
(`Frontend`/`Backend`/`Fullstack`/`Other`) -- one more field each in the
same JSON response, no extra LLM round-trip.

- **HN**: keeps its regex-based `_detect_tech_stack` as authoritative when
  it finds something (it's reading literal text, not guessing); falls back
  to the LLM's tech_stack only when the regex table finds nothing.
- **LinkedIn**: always uses the LLM's tech_stack, since there's no
  regex-based detector there to prefer.
- **role_group** is a new field with no prior regex equivalent in either
  source, so it always comes from the LLM.
- Verified against the real Hugging Face endpoint (not just mocked tests):
  a sample Backend/Python/Django/PostgreSQL posting correctly returned
  `tech_stack: ["Python", "Django", "PostgreSQL"]` and `role_group:
  "Backend"`.

**Revised after a real production failure.** `role_group` started as a
strict `Literal["Frontend", "Backend", "Fullstack", "Other"]` -- an
out-of-vocabulary value would fail validation loudly, same as everywhere
else in the codebase. In practice, a real `posted_within: "week"` run
against the live model returned `role_group: "Data Engineering"` for a
genuine, correctly-identified Python/Spark/Databricks posting -- and that
single `ValidationError` aborted the *entire* digest run (both sources,
since one job's exception propagates out of `collect_jobs`'s
`asyncio.gather`), discarding every other confirmed sponsoring job found
that week and sending a failure email instead of the real digest.

That's a disproportionate cost for what is actually just an imprecise
label, not a broken response -- `offers_sponsorship`, `reason`, and
`country` were all still correctly parsed on that same response. Added a
`field_validator(mode="before")` on `role_group` specifically: an
unrecognized value is now coerced to `"Other"` with a `logger.warning`
(visible, not silent) instead of raising. The other fields keep strict
validation -- a type mismatch on `offers_sponsorship` (e.g. a string
instead of a bool) really would indicate a broken/hallucinated response,
which is a meaningfully different situation from the model using a more
specific job-title-like label than the four buckets we gave it.

## Country grouping (requirement: group by country, then sort)

LinkedIn's location field is reliably structured ("City, Region, Country"),
so its country is derived **deterministically** (`extract.country_from_location`:
last comma-separated segment, with an explicit denylist for
remote/global markers so "Remote" is never mistaken for a country name).

HN's postings are freeform header lines ("Company | Role | Location | ...")
that are often "Remote", a bare city name, or absent entirely -- no
reliable string-based country extraction is possible. You chose **best-effort
LLM guessing** for this case: `shared/visa_llm.confirm_visa_offer` was
extended to ask for a country guess in the *same* call that already
confirms sponsorship (one extra field in the JSON response, not a second
LLM round-trip), instructed to return `null` rather than guess when there's
no supporting textual evidence.

Whichever source, a job with no resolvable country lands in an explicit
**"Remote / Unspecified"** bucket at grouping time (`aggregation/aggregator.group_and_sort_by_country`)
rather than being dropped or forced into a guessed country with no
evidence.

## 24-hour recency window (both sources)

- **New for HN.** job_digest's existing HN source had no recency filter at
  all -- it processed every top-level comment in the entire month-long
  thread. `extract_candidates` now takes an `earliest_posted_at` cutoff and
  drops any top-level comment (i.e. job posting) older than it. A reply
  confirming sponsorship does **not** rescue a posting whose own top-level
  comment falls outside the window -- the window is about when the job was
  *posted*, not when it was last discussed (see the
  `test_extract_candidates_a_reply_does_not_rescue_a_posting_outside_the_window`
  regression test).
- **LinkedIn's window has coarser precision than the 24h target.**
  LinkedIn's search-card date is day-granularity only (no time-of-day) --
  there's no more precise timestamp available from the public guest API.
  The filter compares against `date.today() - N hours`, which in practice
  means "posted today, or partially into yesterday depending on what time
  the run happens" rather than a true rolling 24 hours. This is an inherent
  data-quality limit of the source, not a bug -- flagging it here rather
  than presenting false precision.
- The window also narrows LinkedIn's own search-time filter
  (`f_TPR=r{seconds}`), so a smaller window also means fewer wasted Bright
  Data requests, not just a stricter post-hoc filter.

## Recency window and LinkedIn keywords are per-request API inputs, not env vars

Originally `JOB_POSTED_WITHIN_HOURS` was an env var (default 24) and
LinkedIn's keyword expression was a private module constant in
`sources/linkedin/queries.py`. Both are now optional fields on the
`POST /digest/run` request body instead (`api/dto/digest.DigestRunRequest`),
so a caller can vary them per run without touching config or redeploying:

- `linkedin_keywords: str` -- defaults to
  `DEFAULT_KEYWORDS` (still `"(Python OR Backend OR Java) AND (sponsor OR
  sponsorship)"`, now exported from `sources/linkedin/queries.py` rather
  than private). Applied to every country in the fixed country list
  (`queries._COUNTRIES`, still a code constant -- only the keyword
  expression is a runtime input, not the country list itself).
- `posted_within: "day" | "week" | "month"` -- defaults to `"day"` (24h,
  preserving the existing behavior). Resolved to an hour count
  (`DigestRunRequest.posted_within_hours()`: 24/168/720) and threaded
  explicitly through `run_digest -> build_sources -> {HnWhoIsHiringSource,
  LinkedInSource}` as a constructor parameter -- **not** read from
  `Settings`, since `Settings` is a cached, process-wide singleton
  (`get_settings()` via `lru_cache`) and mutating it per-request would be a
  race condition under concurrent requests. `JOB_POSTED_WITHIN_HOURS` was
  removed from `Settings`/`.env` entirely now that it's fully
  request-driven.

Both fields are optional -- `POST /digest/run` with an empty/no body still
works exactly as before (default keywords, 24h window).

## Per-stage concurrency (rate-limit protection)

Every stage that makes repeated outbound calls has its own env-configurable
concurrency cap (`shared/concurrency.gather_limited`, an `asyncio.Semaphore`
wrapper), set in `config.py`/`.env`:

| Env var | Stage | Default |
|---|---|---|
| `HN_LLM_CONCURRENCY` | HN visa-confirmation LLM calls | 5 |
| `LINKEDIN_SEARCH_CONCURRENCY` | LinkedIn search-page fetches (one task per query, not per page -- see below) | 10 |
| `LINKEDIN_DESCRIPTION_CONCURRENCY` | LinkedIn job-description fetches | 10 |
| `LINKEDIN_LLM_CONCURRENCY` | LinkedIn visa-confirmation LLM calls | 16 |

HN's `discover`/`fetch` stages are each a single one-shot request (find the
current thread, fetch its whole comment tree as one payload) -- there's
nothing to bound concurrency *of* there, so no env var exists for them.

Defaults carry over the values calibrated empirically in
`linkedin_visa_scraper/DECISIONS.md` (search/description capped
conservatively since they hit LinkedIn directly via Bright Data; LLM
concurrency set higher since Hugging Face's router showed no throttling up
to 24 concurrent in that earlier testing).

## LinkedIn pagination and Bright Data (carried over from linkedin_visa_scraper)

- LinkedIn's guest search endpoint returns a fixed page size
  (`LINKEDIN_POSTS_PER_PAGE`, 10) with no total-result-count field --
  `LINKEDIN_MAX_PAGES_PER_QUERY` is a cap, not a fixed count. Each query
  fetches pages in order and stops as soon as one comes back with fewer
  than a full page (see `sources/linkedin/search.py`), same investigation
  as documented in `linkedin_visa_scraper/DECISIONS.md`.
- All LinkedIn fetches (search pages and description pages) route through
  Bright Data's Web Unlocker API (`shared/http.fetch_html`,
  `via_brightdata=True`) -- direct requests get rate-limited/blocked by
  LinkedIn at any meaningful concurrency, as that investigation found.
- A description page that loads but has no recognizable description block
  is logged and skipped for that one job, not silently treated as "no
  description" -- that exact silent-fallback bug previously made an entire
  LinkedIn run look like zero jobs offered sponsorship, when every fetch
  had actually been blocked. See `sources/linkedin/description.py`'s
  docstring.

## Email headline

Changed to **"N Visa-Sponsoring Jobs Posted in the Last {H} Hours"** (using
the actual resolved `posted_within_hours` for that run, so it stays
accurate whether the request used the default day window or an overridden
week/month one) per your explicit request, replacing job_digest's
original "N Visa-Sponsoring Jobs -- {Month Year}" headline (which no longer
makes sense once the digest is a 24-hour window rather than a monthly one).

## job_digest's existing cron is untouched

job_digest's own daily GitHub Actions workflow keeps calling its `cli.py`
exactly as before, producing its own (HN-only, monthly, unchanged) digest
independent of this new service -- you chose to keep these fully separate
rather than having this service replace or wrap job_digest's scheduled run.

## Testing

- Regex/LLM logic: full coverage in `tests/shared/`, largely ported from
  job_digest's existing HN regex/LLM tests (same behavior, now shared).
- HN source: `discover`/`fetch` tests rewritten against httpx+respx instead
  of requests+responses; `extract.py` tests ported from
  `visa_filter.py`'s old tests plus new tests for the 24h window
  (including the "a reply doesn't rescue an old posting" case); a new
  `source.py` orchestration test with a mocked LLM client and mocked HTTP
  responses.
- LinkedIn source: all new -- card parsing, adaptive pagination stopping,
  description extraction/skip-on-missing-block, dedup/title-filter/country
  derivation, and full orchestration (including the English-regex-gate vs.
  non-English-straight-to-LLM branch, and the "structured location wins
  over LLM guess, but LLM guess is used when location is remote" country
  logic).
- Aggregator: country grouping/alphabetical-sort/date-sort-within-country,
  concurrent multi-source collection, and failure-tagging.
- API layer: router (200/502 paths) and service layer (success email +
  summary vs. failure email + re-raise) with the aggregator/emailer mocked
  out.
- All 85 tests pass locally (`pytest`) -- 70 from the initial build, 7 for
  the tech-stack/role-group fallback behavior (regex-wins-when-present for
  HN, LLM-always for LinkedIn, graceful role_group coercion, and the digest
  HTML actually showing them), and 8 more for the `linkedin_keywords` /
  `posted_within` request inputs (DTO resolution, router pass-through,
  `build_search_queries`).

## Verified end-to-end locally

Started the service (`uvicorn visa_jobs_api.main:app`), hit `POST
/digest/run` for real: both sources ran concurrently (HN finished in ~1s
with 0 candidates in the 24h window; LinkedIn took the full
search-\>description-\>LLM path, including 8 Hugging Face calls that
completed essentially in parallel), found 3 confirmed sponsoring jobs
across 2 countries, and a real email ("3 Visa-Sponsoring Jobs Posted in the
Last 24 Hours") was delivered to the configured recipient via Gmail SMTP.

Later, real 24-hour and 1-week runs took ~40s and ~67s respectively
end-to-end (both sources, concurrently, through to a delivered email) --
not a linear blowup with job count, since both stages' concurrency stays
capped the same way regardless of how many candidates show up.

## Email headline shows a human label ("1 Day"/"1 Week"), not raw hours

`digest_headline`/`render_digest_html` originally took `posted_within_hours: int`
and rendered e.g. "Last 168 Hours". Per your request, changed to take
`posted_within_label: str` instead ("1 Day"/"1 Week"/"1 Month") -- a new
`DigestRunRequest.posted_within_label()` method maps the same
`posted_within` enum value used for `posted_within_hours()` to the display
string. The hour count still flows separately to the sources (search
timespan, recency filtering) unchanged -- only the *email's* wording
changed, not the actual filtering logic.

## Scheduled daily run (`cli.py`, `.github/workflows/daily-digest.yml`)

Added a `visa-jobs-digest` console script (`cli.py`) that calls the exact
same `run_digest` service function the FastAPI endpoint uses, with
`DigestRunRequest()`'s defaults (default LinkedIn keywords, 1-day window)
-- so the CLI and the API can never drift on "what a run actually does".
Deliberately does **not** start the FastAPI/uvicorn server and curl it in
CI -- calling the service function directly is simpler and more robust for
a one-shot scheduled job than spinning up and tearing down a web server
just to hit it once.

The GitHub Actions workflow mirrors job_digest's existing
`daily-digest.yml` pattern exactly (same secrets-based structure), on a
`30 3 * * *` cron (03:30 UTC = 09:00 IST) plus `workflow_dispatch` for
manual triggering. Repo secrets/vars needed: `HF_TOKEN`,
`GMAIL_ADDRESS`, `GMAIL_APP_PASSWORD` (secrets), `DIGEST_RECIPIENTS`,
`BRIGHTDATA_ZONE` (vars, not sensitive), `BRIGHTDATA_API_KEY` (secret).

Verified locally before adding the workflow: ran `visa-jobs-digest`
directly (same command CI will run) against live LinkedIn/HN/Hugging
Face/Gmail -- confirmed a real email ("5 Visa-Sponsoring Jobs Posted in
the Last 1 Day") was delivered.

**First real CI run failed** with `brightdata_api_key and brightdata_zone
are required when via_brightdata=True` -- `BRIGHTDATA_ZONE` had been added
as a repo *secret* in GitHub's UI, but the workflow read it from
`vars.BRIGHTDATA_ZONE` (repo *variables*), so it silently resolved to an
empty string in CI rather than erroring at the GitHub Actions level (a
missing `vars.*`/`secrets.*` reference just becomes `""`, not a workflow
failure -- the failure only surfaced once our own code's explicit
non-empty check caught it). Fixed by reading `secrets.BRIGHTDATA_ZONE`
instead, matching where it was actually configured, rather than asking to
move it. Also wired up the six concurrency variables
(`HN_LLM_CONCURRENCY`, `LINKEDIN_SEARCH_CONCURRENCY`,
`LINKEDIN_DESCRIPTION_CONCURRENCY`, `LINKEDIN_LLM_CONCURRENCY`,
`LINKEDIN_POSTS_PER_PAGE`, `LINKEDIN_MAX_PAGES_PER_QUERY`) into the
workflow's `env:` block -- they'd been added as repo variables but weren't
actually being passed through to the job at all, so they were silently
having no effect (falling back to config.py's hardcoded defaults).

## Switched from Bright Data to Decodo for LinkedIn fetches

Replaced Bright Data's Web Unlocker with Decodo's Scraper API
(`https://scraper-api.decodo.com/v2/scrape`) as the anti-bot proxy for all
LinkedIn fetches (`shared/http.py`, both call sites in
`sources/linkedin/search.py` and `sources/linkedin/description.py`).
`Settings.brightdata_api_key`/`brightdata_zone` became
`decodo_username`/`decodo_password` (Decodo authenticates via HTTP Basic
auth on the request itself, not a bearer token + zone name).

Decodo's response shape differs from Bright Data's: it wraps the fetched
page in `{"results": [{"content": ..., "status_code": ..., ...}]}` rather
than returning the raw HTML body directly, and the *outer* HTTP response
from Decodo can be 200 even when the *inner* `status_code` (LinkedIn's
actual response) is an error -- `_get_via_decodo` re-raises that inner
status as an `httpx.HTTPStatusError` so the existing retry/`FetchError`
classification in `fetch_html` keeps working unchanged.

Verified against the real, currently-used production queries before
swapping any code: manually curled Decodo with the exact `build_search_queries()`
keyword expression against three real countries (Canada/UK/Germany),
confirmed HTTP 200 and that `_parse_job_cards` parsed real job cards from
the response unmodified, then ran the actual `fetch_all_job_cards` and
`fetch_all_descriptions` functions live end-to-end against production
`.env` config before considering the swap done.

**Requires a network that doesn't block `scraper-api.decodo.com` by
SNI/hostname** -- testing from the Adobe corporate VPN got every request
reset during the TLS handshake (confirmed via `openssl s_client`: the
handshake succeeds with no SNI and fails the instant the real hostname is
sent), while the same requests worked immediately off that network. This
doesn't affect GitHub Actions (runs on GitHub-hosted runners, not this
VPN) but is worth knowing if this ever needs debugging from this laptop
again.

## LinkedIn title filter: fixed-phrase matching was silently dropping real jobs

Ported a fix from a separate experiment (`indeed_scraper_experiment`, which
had copied this exact `TITLE_INCLUDE`/`TITLE_EXCLUDE` list verbatim). A
manual review there of ~90 real Indeed job cards found the original
fixed-phrase approach was wrongly excluding a real chunk of relevant
titles -- "Full Stack AI Engineer" doesn't contain the literal substring
"full stack engineer" (the inserted "AI" breaks it), and there was no
entry at all for "AI Engineer", "DevOps Engineer", "Cloud Architect", or
"Full Stack **Developer**" (only "...Engineer" was covered). Since this is
the exact same list and matching logic LinkedIn's `queries.py`/`extract.py`
used, the same bug was live here too.

Replaced fixed-phrase matching with **role-noun + domain-signal
co-occurrence**: a title passes if it contains any of `TECH_ROLE_NOUNS`
(engineer/developer/architect/programmer) *and* any of
`TECH_DOMAIN_SIGNALS` (software/cloud/devops/ai/platform/...) *anywhere*
in the title, not necessarily adjacent -- this generalizes past the
exact-phrase problem for free. The broader net needed a new safeguard,
`HARDWARE_DISCIPLINE_EXCLUDE` (electrical/mechanical/sensor/desktop/etc.),
checked before the role+domain rule -- without it, "Principal Desktop
Engineer" would pass on "principal" + "engineer" alone, and "Senior
Electrical Engineer" would pass on bare "engineer". The original
`TITLE_INCLUDE` list is kept, shrunk to a handful of literal phrases for
real titles with no role noun at all ("Tech Lead") or no clean domain
signal ("Release Engineer").

Verified live against real LinkedIn data (3 countries, current production
`.env`) before considering this done: of 274 deduped real cards, the old
filter kept 128 and the new one keeps 179 -- a 51-title improvement,
including titles like "Software Development Engineer, Sponsored Products"
(Amazon, twice) that were being silently dropped despite the search query
itself already surfacing them. See
`indeed_scraper_experiment/DECISIONS.md` for the original review this
was found and verified against.

## Added Indeed as a third source, via Bright Data

Promoted `indeed_scraper_experiment` from a standalone testing project
into a real third source (`sources/indeed/`), mirroring LinkedIn's
pipeline shape exactly: search (paginated) -> dedupe/title-filter ->
description fetch -> regex + LLM sponsorship confirmation -> `NormalizedJob`.

**Uses Bright Data, not Decodo, deliberately.** Decodo was tested
extensively against Indeed in `indeed_scraper_experiment` (both `standard`
and `premium` proxy pools, with and without headless rendering) and found
to be blocked outright every time -- Indeed's Cloudflare bot-detection
redirects every request to its login page, sometimes with an honest `401`
(`standard` pool) and sometimes with a deceptive `200` that still contains
the login page instead of real results (`premium` pool). Bright Data works
cleanly. See `indeed_scraper_experiment/DECISIONS.md` for the full
investigation. `shared/http.py` now supports both `via_decodo` (LinkedIn)
and `via_brightdata` (Indeed) as alternative fetch paths on the same
`fetch_html()` function.

**Job Type (Full-time + Permanent) + Experience Level (Senior) filter is
intentional, not a default this codebase chose.** The user selected these
directly in Indeed's own search-filter UI and provided the resulting URL
as a requirement ("make sure you use all the filters that I had
provided"). Decoded, `sc=0kf:attr(5QWDV|CF3CP,OR)explvl(SENIOR_LEVEL);` --
see `sources/indeed/search.py::_SC_FILTER`. This does mean Indeed results
skew toward senior full-time/permanent roles specifically, unlike
LinkedIn's un-filtered search -- a deliberate difference between the two
sources' scope, not an oversight.

**Recency window reuses the exact same `posted_within` input** ("day"/
"week"/"month") already used for LinkedIn, mapped to Indeed's `fromage`
parameter (which only accepts the values Indeed's own UI exposes: 1/3/7/14
days, no hour-level control) via `_fromage_for_hours()` -- an hour count
rounds up to the closest supported option, capping at 14 for anything
longer (a 30-day "month" window has no exact `fromage` equivalent).

**Restricted to 8 English-dominant job markets** (United States, Canada,
United Kingdom, Ireland, Australia, New Zealand, Singapore, United Arab
Emirates), not LinkedIn's full 14-country list. Neither Bright Data's
`country` geo param nor an `hl=en` URL override reliably forces English
results on Indeed (tested extensively -- see
`indeed_scraper_experiment/DECISIONS.md`), so countries whose primary
business language isn't English (Germany, Netherlands, Switzerland,
Sweden, France, Japan) are left out rather than risk silently returning
wrong-language/low-signal results for this English-only
search+parse+confirm pipeline.

**`indeed_search_concurrency`/`indeed_description_concurrency` default to
4, not LinkedIn's 10.** This is not an arbitrary, more-conservative
guess -- 8 concurrent Bright Data requests were empirically found to
trigger `502 Bad Gateway` from Bright Data's own backend (not Indeed),
while 4 ran clean across 40 real page fetches with only a single
retry-recoverable failure. See `indeed_scraper_experiment/DECISIONS.md`.
`indeed_llm_concurrency` defaults to 16 (matching LinkedIn's), since LLM
calls go to Hugging Face, not Bright Data, and aren't subject to that
same ceiling.

**A 0-card search page is retried once before being trusted**, ported
directly from the experiment's proven fix: a live re-fetch with identical
parameters was found to return real cards moments after an earlier
attempt returned 0 for the same query -- the same "page loaded but came
back empty/blocked" failure mode already guarded against in LinkedIn's
description fetch.

**No absolute posted-date exists for Indeed** (unlike LinkedIn's `<time
datetime="...">`) -- `JobCard` has no `posted_on` field, and
`NormalizedJob.posted_at` uses the fetch time as an approximation
(recency is already bounded server-side by `fromage`). See
`sources/indeed/models.py`.

**`DigestRunRequest` gets an independent `indeed_keywords` field**
(default: the exact expression empirically tested in
`indeed_scraper_experiment`, `(Python OR Backend OR Java) AND ("sponsor"
OR "sponsorship")`) alongside the existing `linkedin_keywords` -- tuning
one source's query never touches the other's, as required.

## Shared title-filter and call-stats modules (LinkedIn + Indeed)

Indeed needed the exact same title filtering (role-noun + domain-signal
matching, hardware-discipline exclusion -- see the earlier "LinkedIn title
filter" entry above) and the exact same per-country call-counting pattern
LinkedIn already had. Rather than copy either a second time into
`sources/indeed/` -- which is exactly what caused `TITLE_INCLUDE`/
`TITLE_EXCLUDE` to silently drift out of sync when copied into the
separate `indeed_scraper_experiment` project -- both moved to
`shared/title_filter.py` and `shared/call_stats.py`, with LinkedIn
migrated to use them too. Within a single codebase there's even less
excuse to duplicate logic twice than there was across two separate
projects.

## Combined Bright Data / Decodo call tracker, per country, one shared instance per run

Requested explicitly: a single tracker covering **both** Indeed's Bright
Data calls and LinkedIn's Decodo calls, broken down per country, logged
once as part of the whole digest run -- not each source logging its own
separate summary (which is what LinkedIn's original `CallStats` did
before this change).

`shared/call_stats.py::CallStats` now tracks exactly four counters per
country: `indeed_search_calls`, `indeed_description_calls`,
`linkedin_search_calls`, `linkedin_description_calls`. **Deliberately
excludes LLM call counts** (LinkedIn's original version tracked these
too) -- LLM calls go to Hugging Face, not Bright Data/Decodo, so they're
out of scope for what was actually asked for here.

One `CallStats()` instance is created in `digest_service.run_digest()`
per run and threaded through `build_sources()` into both `LinkedInSource`
and `IndeedSource` (previously, `LinkedInSource` constructed its own
private instance internally) -- this is what makes it a single combined
summary rather than two separate per-source ones. `log_summary()` is
called from a `finally` block around `collect_jobs()`, not just on
success, so the breakdown is visible even when a source fails partway
through a run -- exactly the moment knowing how many calls had already
gone out is most valuable for debugging.

Output format (one line per country that had any activity):
```
United States: Bright Data API calls for Indeed job card: 12  Bright Data API calls for Indeed job details: 8  Decodo API calls for LinkedIn job card: 3  Decodo API calls for LinkedIn job details: 2
```

## Fixes from adversarial PR review of the Indeed integration

Four issues surfaced by a Principal-Engineer-style review of the full diff
(shared title-filter/call-stats extraction + Indeed source + combined
tracker), fixed before merge:

- **VP/SVP title exclusion never matched (blocker).** `TITLE_EXCLUDE`
  checked for the literal substring `" vp "` (space-padded), so it never
  fired when "VP"/"SVP" was the first word of a title (no leading space in
  the raw string) -- e.g. "VP of Software Engineering" passed straight
  through. Replaced with a dedicated word-boundary regex,
  `_EXEC_TITLE_RE = re.compile(r"\b(?:[sea]?vp)\b")`, matching `vp`
  standalone or with a Senior/Executive/Assistant prefix as a whole word
  anywhere in the title, checked separately from the plain-substring
  `TITLE_EXCLUDE` list.
- **Sibling source tasks weren't cancelled on failure.**
  `collect_jobs()` awaited `asyncio.gather()` over bare coroutines; by
  default `gather()` does *not* cancel the other awaitables when one
  raises, so a failing source (say, LinkedIn) left the others (HN, Indeed)
  still scraping in the background after `run_digest()`'s `httpx.AsyncClient`
  had already been closed on the way out. Fixed by wrapping each source in
  an explicit `asyncio.create_task()`, and on any exception from `gather()`,
  cancelling and awaiting the remaining tasks before re-raising.
- **`dedupe_cards()` collided across countries.** Keyed only on
  `(title, company)`, so the same generic title at the same company posted
  separately in two different countries (common for large multinational
  employers) silently collided and the later country's listing was
  dropped. `url` (unique per listing) added to the key: now
  `(title, company, url)`.
- **No internal deadline on a digest run.** Indeed's empty-page retry (2
  attempts) stacked on top of up to `indeed_max_pages_per_query` pages per
  country, each backed by a 60s Bright Data timeout, meant one query's
  search phase alone could take several minutes worst-case, with nothing
  capping the whole `POST /digest/run` call. Added
  `Settings.digest_run_timeout_seconds` (default 600s) and wrapped
  `collect_jobs()` in `asyncio.wait_for()` in `run_digest()`.

Left as follow-ups (minor/low-confidence, not blocking): `_SYSTEM_PROMPT`
duplicated between `linkedin/source.py` and `indeed/source.py`; Indeed's
`fromage` silently capping a "month" request at 14 days instead of 30 with
no indication in the response; Indeed's Job Type/Experience Level filter
(`_SC_FILTER`) hardcoded with no config override; synchronous BeautifulSoup
parsing inside async functions (pre-existing pattern, now used by two
sources instead of one).

## Indeed search via Selenium works from a residential IP, but is blocked from any datacenter IP -- including GitHub Actions

The Selenium-based Indeed search migration (replacing Bright Data for
search pages -- see selenium_client.py's docstring) was validated live and
worked cleanly, but only ever from this machine's residential/office IP.
The very first scheduled/dispatched run on GitHub Actions
(`add-visa-jobs-api`, run 29321931480, 2026-07-14) returned 0 job cards
and no pagination nav for every single one of the 8 countries, both retry
attempts each -- a complete, uniform failure, not normal Indeed
volatility. No `WebDriverException`/crash anywhere in the run: Chrome
launched fine, pages "loaded" successfully from Selenium's point of view,
just with no real content.

Follow-up investigation (`scripts/diagnose_indeed_blocking.py`,
`.github/workflows/investigate-indeed-blocking.yml`) narrowed this down
further: routing the exact same Selenium code through a Decodo
**datacenter** proxy (a different datacenter IP than GitHub Actions' own,
different ASN, different physical location) reproduced the identical
failure signature locally -- `page_source` containing both `"Just a
moment"` (Cloudflare's interstitial challenge) and `"Additional
Verification Required"`, 0 cards, no pagination nav, no `vjk` captured.

This means the root cause is **datacenter IP reputation specifically**,
not "GitHub Actions" and not "real browser vs. raw HTTP" as originally
framed -- a real, JS-executing browser session gets exactly the same
Cloudflare-style block as a raw HTTP fetch once it's coming from a
datacenter ASN, regardless of which specific datacenter or which specific
browser build. Only a residential/ISP-attributed exit IP has ever
avoided this, across every variant tested throughout this whole
investigation (raw direct HTTP, Bright Data, this Decodo datacenter proxy,
and GitHub Actions' own runner).

**Not yet tested**: a residential or "Static Residential (ISP)" proxy
(Decodo exposes these as separate products from the datacenter one tested
above) routing Selenium's traffic -- this is the more relevant next
experiment, since it's the one variable that hasn't been tried yet and
every other one has failed identically.

**Temporary mitigation while this is unresolved**: the GitHub Actions
`schedule:` cron trigger has been removed from `daily-digest.yml`
(`workflow_dispatch` kept, so it can still be triggered manually for
testing) -- the digest is being run locally instead via
`scripts/run_digest_locally.sh`, scheduled through a macOS launchd
LaunchAgent, until Indeed search can run from GitHub Actions without being
blocked.

## Indeed description fetches switched back from Decodo to Bright Data

Production logs (`logs/digest_2026-07-18_09-16-15.log` and others) showed a
steady stream of `Failed to fetch description for ... -- skipping this
job: failed to fetch ... after retries` errors for Indeed, even with
Decodo's JS-rendered mode (`headless="html"`) that had previously tested
clean. A live side-by-side comparison was run to check whether Bright Data
would do better:

- First pass (cloud-run, 8 URLs that had just failed via Decodo): Bright
  Data recovered 5/8 with real content; the other 3 were a mix of read
  timeouts and one 502.
- A follow-up 10-URL run (fresh URLs, same cloud environment) initially
  showed Bright Data at 0/10 -- but every response was a `200` with an
  empty body, and the response headers showed why:
  `x-brd-err-code: client_10050` / `ip_blacklisted` -- the cloud run's
  source IP was blocked in the Bright Data zone's own access-control
  settings (`linkedin_unlocker` zone), a zone-config issue, not a real
  scraping failure. That 0% number was discarded rather than reported as a
  real reliability figure -- see scripts/compare_decodo_brightdata.py,
  written so the same comparison could be re-run locally (off whatever
  cloud IP triggered the blacklist) after removing the IP from that list.
- Meanwhile the same 10-URL set run against Decodo (no retries, single
  attempt) came back only 2/10 successful: 3 upstream 401s (Indeed itself
  blocking the request) and 5 read timeouts.

Decodo's poor showing here, plus the first pass's clean Bright Data
recovery rate once the IP-blacklist confound is set aside, was enough for
the user to decide directly: move Indeed's description fetches back to
Bright Data, keeping Decodo for LinkedIn (which has had no comparable
reliability complaints). Indeed's *search* pages stay on Selenium either
way -- this only affects `sources/indeed/description.py`.

Implementation: `shared/http.py` gained `_get_via_brightdata` alongside
the existing `_get_via_decodo`, with the same retry/backoff policy and the
same "empty body is an error, not empty content" guard the IP-blacklist
incident above showed was necessary. Bright Data requires an explicit
`country` geo-pin param (Decodo's Indeed mode didn't) --
`sources/indeed/country_domains.py` gained `BRIGHTDATA_COUNTRY_CODES`
(ISO 3166-1 alpha-2 codes; note `"gb"` for the United Kingdom, not
Indeed's own domain-naming `"uk"`) for this. New required settings:
`brightdata_api_key`/`brightdata_zone` (`BRIGHTDATA_API_KEY`/
`BRIGHTDATA_ZONE`), wired through `.env`, `.env.example`, and
`daily-digest.yml`'s secrets. Reused the existing `linkedin_unlocker`
Bright Data zone (the same one `indeed_scraper_experiment` and this
comparison script used) rather than provisioning a new one -- the name is
a naming artifact from that zone's original purpose, not a functional
restriction; worth renaming in the Bright Data dashboard for clarity, but
not required for this to work.

**Not resolved by this change**: neither provider was 100% reliable in
testing (Bright Data still had timeouts, Decodo still had genuine
Indeed-side 401s) -- this reduces the loss rate, it doesn't eliminate it.
A Decodo-then-Bright-Data fallback per job (try one, fall back to the
other on failure) was discussed as a further improvement but not
implemented in this pass, since the user asked specifically for a
provider swap, not a fallback chain.

## Sponsorship-confirmation LLM moved off Hugging Face to a local Docker Model Runner instance

`shared/visa_llm.py` called Hugging Face's Inference Providers router
(`router.huggingface.co`) for every sponsorship-confirmation call across
all three sources. You decided to move this fully local instead -- no more
per-call cost/rate-limit dependency on a hosted provider -- using Docker
Desktop's Model Runner feature to serve the same Qwen3-4B-Instruct-2507
model on this machine.

**vllm backend crashes on this Mac.** The exact HF repo you'd pulled
(`hf.co/Qwen/Qwen3-4B-Instruct-2507`, raw BF16 safetensors) is tagged for
Docker Model Runner's `vllm` backend, not `llama.cpp`, in Docker's own
catalog. `vllm-metal` (the Apple Silicon Metal backend for vllm) fails at
engine-core initialization on this machine every time
(`RuntimeError: Engine core initialization failed`) -- a Docker Desktop/
macOS-Metal compatibility issue, not something fixable from this codebase.
Switched instead to `hf.co/unsloth/Qwen3-4B-Instruct-2507-GGUF`, a GGUF
build of the same model tagged for `llama.cpp`, which is confirmed stable
here.

**Default context size caused a GPU OOM.** The GGUF model's metadata
advertises a 262144-token max context, and `docker model run`/the REST API
reserve that much KV cache by default -- a ~39GB allocation, which failed
with `ggml_metal_synchronize: error: ... Insufficient Memory
(kIOGPUCommandBufferCallbackErrorOutOfMemory)` on a 48GB Mac already under
memory pressure from other apps. Fixed with `docker model package --from
<model> --context-size <n> <new-tag>`, which repackages a local variant
with a smaller, explicit context size -- this is how `qwen3-4b-visa-jobs:local`
was created. (Superseded by the docker-compose setup below, which replaced
this CLI-packaged tag with `hf.co/unsloth/qwen3-4b-instruct-2507-gguf`
served directly with the same context/parallel tuning declared in
`docker-compose.yml` instead.)

**Context-per-slot sizing caused real-run 500s.** An initial repackage at
8192 tokens total passed a synthetic short-prompt smoke test, but a full
live digest run produced repeated `500 Internal Server Error`s from the
local server and 11 LinkedIn jobs silently dropped after retries were
exhausted. Root cause, confirmed via the llama-server logs: Docker Model
Runner's llama.cpp backend defaults to 4 parallel request slots, and
splits the total context size evenly across them -- so 8192 total meant
each slot only got 2048 tokens, and real job descriptions (plus the system
prompt and JSON-schema instruction) routinely ran 1500-3000+ tokens,
overflowing a slot under concurrent load. There's no `--parallel`/slot-count
flag exposed via `docker model package` to reduce the slot count instead,
so the fix was to repackage with a larger total context (16384, i.e.
4096/slot) -- confirmed via a synthetic concurrent test using realistic
~2500-3000 token prompts (20/20 succeeded at 16384, where the same test
reliably 500'd some requests at 8192).

**Not tested**: this only works on a machine actually running Docker Model
Runner with this model loaded -- i.e. the same Mac `scripts/run_digest_locally.sh`
already runs the whole digest on. `daily-digest.yml`'s `workflow_dispatch`
trigger will now fail on the LLM confirmation step if run manually from
GitHub Actions, on top of the pre-existing Indeed/Selenium IP-blocking
issue that already made it not viable for the scheduled run.

## Local LLM moved from a manual `docker model package` CLI step to `docker-compose.yml`, with concurrency doubled

The `docker model package --context-size <n> <tag>` step above worked but
was an untracked, manual, one-off CLI incantation -- nothing in the repo
recreated it on a fresh machine, and there was no lever to reduce llama.cpp's
default 4 parallel slots (only total context size) short of hand-editing a
`--parallel` flag nobody could discover from `docker model`'s CLI help.

**`docker compose`'s native `models:` top-level key solves both.** Compose
(v5.1.4 here) supports declaring a model resource with `context_size` and
`runtime_flags`, backed by the same Docker Model Runner used before (still
a native, Metal-accelerated host process, not a containerized one -- see
the note in `docker-compose.yml` about why the app itself isn't
containerized). `docker-compose.yml`'s `llm` model declares
`runtime_flags: ["--parallel", "12"]` (you asked for 12 specifically) and
`context_size: 73728` (6144 tokens/slot). A placeholder `llm-anchor`
service (tiny `alpine`, `sleep infinity`) exists solely so `docker compose
up` has something that references the `llm` model and therefore actually
provisions it -- Compose only starts model resources some service depends
on.

**Getting to 12 slots safely took two more real-run iterations.** An
intermediate 8-slot/32768-context config (4096 tokens/slot, matching the
per-slot budget already validated in the CLI-packaged version) passed a
synthetic concurrent test cleanly, but a full live digest run still
dropped 1 LinkedIn job to a genuine error -- not a 500 this time, a `400`:
`request (4115 tokens) exceeds the available context size (4096 tokens)`.
Root cause: context_size split evenly across parallel slots is a *hard
per-request ceiling*, not just a throughput knob -- any single prompt
(system prompt + job description + JSON-schema instruction) larger than
its slot's share gets rejected outright, independent of how much total
memory or how many other slots are idle. Checking `llama-server`'s own
`prompt eval time` logs across that run showed most real prompts land
2000-3000 tokens, but with at least one spike past 4100 -- so a per-slot
budget that only just covers the *typical* case will eventually reject
the *outlier* case. Fixed by widening to 6144 tokens/slot (73728 total,
still 12 slots) -- confirmed with both a full live digest run (0 LLM
failures) and a synthetic stress test deliberately sized above the
original failure (12 concurrent requests at ~5500-6000 tokens each, all
succeeded).

Confirmed via `ps` that `llama-server` actually launches with
`--ctx-size 73728 --parallel 12` under this setup. Memory checked under
load (`top`): `llama-server` RSS grew from ~13.4GB idle to ~15.8GB under
the stress test, system down to ~100MB "unused" on this 48GB Mac (other
apps' baseline usage is high, and macOS reports very little "unused" as
normal steady-state behavior since it aggressively uses spare RAM for
reclaimable disk cache/compression) -- no crashes or OOMs observed at this
size. Pushing further (16+ slots) was not attempted given how little
headroom remained.

**One naming consequence**: Compose provisions the model directly under
its full pull reference, `hf.co/unsloth/qwen3-4b-instruct-2507-gguf` --
there's no equivalent to `docker model package`'s custom local tag inside
a Compose-managed model. `config.Settings.llm_model`/`shared/visa_llm.py`'s
`DEFAULT_MODEL`/`.env`/`.env.example` were all updated from the old
`qwen3-4b-visa-jobs:local` tag to this one accordingly.

`scripts/run_digest_locally.sh` now runs `docker compose up -d` before the
digest and `docker compose down` after (regardless of the digest's own
exit code, via a trap, so a mid-run failure doesn't leave the model
running indefinitely) -- see `LOCAL_SETUP.md` for the updated one-time
setup steps.
