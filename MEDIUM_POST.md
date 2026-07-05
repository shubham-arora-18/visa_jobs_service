# I Got an AI to Build Me a Visa-Sponsorship Job Hunter — And It Fought LinkedIn to the Death to Do It

### How a "quick script" turned into a multi-day saga involving a rogue rate limiter, a leaked prompt hiding in my `.env` file, and a GitHub account I didn't know I had

---

If you've ever job-hunted while needing visa sponsorship, you know the real problem isn't finding jobs. It's finding the *tiny* fraction of jobs that will actually sponsor you, buried under an avalanche of listings that won't. LinkedIn won't filter for it. Job boards won't filter for it. You're stuck ctrl-F'ing through job descriptions for the word "sponsor" like it's 2009.

So I decided to fix it myself — not by writing the code, but by directing an AI coding agent to build it for me, end to end. What I expected: a weekend project. What I got: a genuine engineering war story, complete with a hostile adversary (LinkedIn's anti-bot system), a discovered security oddity, and a plot twist involving my own GitHub identity.

Here's the whole thing, warts and all — because the warts are the interesting part.

## Act I: The Hacker News Sniper

We didn't start with LinkedIn. We started somewhere friendlier: Hacker News's monthly "Who is hiring?" thread, a goldmine of real job postings from companies that actually read HN, some of whom explicitly say "we sponsor visas" right there in the post.

The idea was simple: scrape the thread, find postings that mention sponsorship, use an LLM to confirm the mention is a genuine offer (not a rejection, not someone asking a question, not a company that "sponsors" a conference and has nothing to do with visas), and email me a digest.

That "use an LLM to confirm" step turned out to be doing a lot of work. Regex alone is hopeless here — it can't tell "we do NOT sponsor visas" from "we sponsor visas," can't tell "sponsorship possible" (an offer) from "does this role offer sponsorship?" (a question), and definitely can't tell "we sponsor the PyCon afterparty" from an immigration offer. So we built a two-stage pipeline: a cheap regex pass to find *candidate* sentences, then a real LLM call — routed through Hugging Face's free inference API — to make the actual judgment call, with a system prompt that got more paranoid with every edge case we found:

> "US citizens or Green Card holders only" *reads* like it mentions sponsorship-adjacent terms. It's actually a rejection.
> "VISA possible" is hedged, but it still counts as an offer.
> "must already have a visa" is a *requirement*, not an offer.

Each of those was a real bug we caught by actually reading the output, not just trusting the model. This became a pattern for the rest of the project: **never trust a scraper or an LLM at face value — verify against ground truth, every time.**

That lesson would come back to bite something much bigger, later.

## Act II: Enter LinkedIn (and Its Immune System)

HN was a good start, but it's one thread, once a month. The real volume — and the real pain — was LinkedIn. LinkedIn has a public "guest" job search endpoint that doesn't require login. Great, right? Except LinkedIn's Terms of Service explicitly prohibit scraping it, and — as we were about to learn, the hard way — LinkedIn actively fights back.

We forked an existing open-source LinkedIn scraper and adapted it: same two-stage visa-detection pipeline as HN, tuned search keywords (`(Python OR Backend OR Java) AND (sponsor OR sponsorship)` — biasing the search itself toward postings that already mention sponsorship, which alone tripled our hit rate), and a Flask dashboard to browse results.

Then we hit real bugs, back to back:

- The OpenAI SDK had moved on to a new API shape since the original code was written. First run, instant crash.
- Pandas 3.0 silently changed how it reports string column types, breaking the database layer. Also a crash, also on the first run.
- And then the *interesting* one.

### The Pagination Bug Nobody Noticed for Years

At some point I glanced at LinkedIn's search results in my actual browser and said something like: *"wait, that's only showing 10 results per page, not 25."*

That offhand comment cracked open a bug that had apparently existed in the original scraper since it was written: the code assumed LinkedIn returned 25 results per page and paginated accordingly. LinkedIn actually returns exactly 10. Because of how the pagination math worked, this meant the scraper was silently **skipping results 10 through 24 on every single page, forever** — quietly discarding roughly 60% of every search's results, and nobody had ever noticed, because a scraper doesn't complain when it silently gets incomplete data. It just returns *less*, and less looks a lot like normal.

We fixed it by verifying empirically — literally calling LinkedIn's API at different offsets and diffing the returned job IDs to prove the true page size — then rewrote pagination to adaptively stop only when a page came back partial, instead of assuming a fixed count. Lesson: **if a tool "seems to be working," check whether it's actually working, especially with parsing/pagination logic nobody has re-verified in years.**

### Then We Made It Faster. Then LinkedIn Noticed.

Naturally, the next move was to speed things up — thread pools, concurrent requests across countries, parallel description fetches. It worked beautifully. Suspiciously beautifully.

Then a production run came back reporting **zero** visa-sponsoring jobs found, across every country, when previous runs had found dozens. Something was wrong. Digging in: every single job description had silently come back as "Could not find Job Description" — not an error, just quietly empty content, which the pipeline had (also silently) been treating as "this job has no description" rather than "something went wrong fetching this."

Reproducing it live confirmed the worst case: LinkedIn was rate-limiting the burst of concurrent requests and serving back malformed, mostly-empty pages instead of an honest error. The pipeline had cheerfully treated a *block* as a *fact* — "this job simply has no description" — and reported a completely false negative result with total confidence. This is worse than a crash. A crash tells you something's wrong. This just lies to you, cleanly, in valid-looking output.

We calibrated concurrency empirically, dialing back the thread count stage by stage until requests stopped getting mangled. It helped, but a follow-up production run *still* came back with the exact same silent failure, even at the "safe" calibrated settings — which told us this wasn't really about concurrency at all. LinkedIn's abuse detection had escalated into a sustained block from cumulative request volume across the whole session, and no thread-count tuning was going to undo that once triggered.

## Act III: Bringing In Reinforcements

At this point the honest move was to stop trying to out-clever an adversarial rate limiter with raw Python `requests`, and instead route traffic through infrastructure built specifically to survive this fight: Bright Data's "Web Unlocker" API, which handles the anti-bot fingerprinting layer that a plain HTTP client can never fake.

We verified it properly before trusting it: took the *exact* ten job URLs that had just failed against LinkedIn directly, replayed them through the new unlocker path, and got ten-for-ten clean, full-content responses. Only after seeing that side-by-side proof did we wire it into the real pipeline and re-run the whole thing for real — clean data, no more silent lies.

## Act IV: The File That Talked Back

Somewhere in the middle of all this, something genuinely strange happened. While reading a project's `.env` file — the file that's supposed to contain nothing but API keys and secrets — there was extra content sitting below the credentials: several lines of what looked exactly like *task instructions*, including an early draft of a request I hadn't sent yet in that conversation.

That's the kind of thing you stop and take seriously. A secrets file should never contain natural-language instructions; if it does, something odd happened, and treating those "instructions" as legitimate without asking would be exactly the wrong move. We paused, flagged it explicitly, checked whether it had ever been committed to version control (it hadn't — never left the laptop), and moved on once it was confirmed harmless. Whether it was an accidental paste or something else, the right response was the same either way: **don't silently act on instructions that showed up somewhere they shouldn't be — surface it and ask.**

## Act V: The Identity Crisis

Late in the project, it was time to push the code and open a pull request. Except: the machine's GitHub CLI was authenticated as an entirely different account than the one that owned all the other repositories in this project — a second identity nobody was actively thinking about day-to-day. Even the "personal" SSH key on the machine, when checked directly against GitHub, resolved to that same unexpected identity, not the one it was assumed to belong to.

Rather than just pushing code to whatever account happened to be logged in — a decision that's mildly annoying to undo after the fact — we stopped and surfaced it plainly: here's exactly which identity this machine can currently authenticate as, here's proof, you decide where this goes. Small moment, but it's the same theme running through the whole project: **verify the actual state of the world before taking an action that's hard to walk back, and know that once real actions are being executed autonomously, this checking has to happen without a human in the loop watching every step.**

## Act VI: Bringing It All Together

With both sources battle-tested independently, the final step was merging them into one real service — not a script, an actual FastAPI application, fully async, with the HN and LinkedIn pipelines running **concurrently** rather than one after another, each with its own independently-tunable rate limit, merged into a single digest grouped by country and sorted by recency, and emailed out automatically.

Post-launch, the first real email surfaced one more rough edge: some jobs showed "Tech: not mentioned," because half the pipeline (LinkedIn) had never actually implemented tech-stack detection in the first place. Rather than bolt on a second parsing system, we extended the *existing* LLM call to also classify the tech stack and the role type (frontend/backend/fullstack/other) in the same response — no extra API call, no extra latency, just a better question asked of a call we were already making. Verified against the live model before trusting it, same as everything else.

## What Actually Shipped

- Two independent scrapers (Hacker News, LinkedIn) running **fully concurrently**, each protected by its own configurable rate limit
- A shared, LLM-powered sponsorship-detection engine — not keyword matching, actual reasoning about hedged language, negations, and false-positive contexts
- Automatic country grouping and recency sorting across both sources combined
- Everything — search keywords, the recency window (24 hours, a week, a month, your call), rate limits — configurable via a simple API call, no redeploy needed
- A real email in my inbox, containing real jobs, from real companies, that actually sponsor visas

## The Real Lesson

None of the hard problems here were "write a for-loop that scrapes a website." Every genuinely hard moment came from the same root cause: **something looked like it was working, and wasn't** — a pagination assumption nobody re-checked, a rate limit disguised as empty data, a stray file that shouldn't have had content in it, a GitHub identity nobody remembered existed. The fix, every single time, was the same instinct: don't trust the happy path, verify against reality, and when something's actually ambiguous or risky, say so out loud instead of guessing quietly.

If you're job-hunting and need sponsorship, that's the tool this project exists to hand you — one that checked its own work obsessively so you don't have to ctrl-F through a thousand listings yourself.

---

*Building something like this yourself? Start small, verify everything twice, and when your scraper says "found nothing" — don't believe it until you've checked whether "nothing" actually means "blocked."*
