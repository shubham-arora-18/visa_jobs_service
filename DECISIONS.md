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
- `role_group` is a strict `Literal["Frontend", "Backend", "Fullstack",
  "Other"]` on the LLM-response model -- an out-of-vocabulary value from the
  model fails validation loudly (same "no tolerant parsing" principle as
  everywhere else) rather than being silently coerced. The prompt is
  explicit that "Other" is the correct answer when unsure, to minimize how
  often that happens in practice.
- Verified against the real Hugging Face endpoint (not just mocked tests):
  a sample Backend/Python/Django/PostgreSQL posting correctly returned
  `tech_stack: ["Python", "Django", "PostgreSQL"]` and `role_group:
  "Backend"`.

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
- The window is configurable via `JOB_POSTED_WITHIN_HOURS` (default 24) and
  also narrows LinkedIn's own search-time filter (`f_TPR=r{seconds}`), so a
  smaller window also means fewer wasted Bright Data requests, not just a
  stricter post-hoc filter.

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
the actual configured `JOB_POSTED_WITHIN_HOURS`, so it stays accurate if
that's ever changed) per your explicit request, replacing job_digest's
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
- All 77 tests pass locally (`pytest`) -- 70 from the initial build plus 7
  added for the tech-stack/role-group fallback behavior (regex-wins-when-present
  for HN, LLM-always for LinkedIn, strict role_group validation, and the
  digest HTML actually showing them).

## Verified end-to-end locally

Started the service (`uvicorn visa_jobs_api.main:app`), hit `POST
/digest/run` for real: both sources ran concurrently (HN finished in ~1s
with 0 candidates in the 24h window; LinkedIn took the full
search-\>description-\>LLM path, including 8 Hugging Face calls that
completed essentially in parallel), found 3 confirmed sponsoring jobs
across 2 countries, and a real email ("3 Visa-Sponsoring Jobs Posted in the
Last 24 Hours") was delivered to the configured recipient via Gmail SMTP.
