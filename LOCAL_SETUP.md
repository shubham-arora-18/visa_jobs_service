# Running the digest locally on a new machine

The scheduled GitHub Actions cron is currently disabled -- Indeed search
(via Selenium) is blocked from GitHub Actions' datacenter IP (and from a
Decodo datacenter proxy tested as an alternative), but works cleanly from
an ordinary residential/office IP. See `DECISIONS.md`'s "Indeed search via
Selenium works from a residential IP..." entry for the full
investigation. Until that's resolved, the daily digest runs locally
instead, via a macOS `launchd` LaunchAgent. This doc is the checklist for
setting that up on a machine that hasn't run it before.

## 1. Clone the repo

```bash
git clone git@github.com:shubham-arora-18/visa_jobs_service.git
cd visa_jobs_service
```

A read-only clone is enough just to run the digest; you only need a
working GitHub SSH key on this machine if you'll also be pushing changes.

## 2. Python environment

Requires Python 3.11+.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

## 3. Install Google Chrome

Install it normally from google.com/chrome. Selenium 4's built-in
Selenium Manager auto-downloads a matching chromedriver itself -- no
separate driver install needed.

## 4. Create `.env`

```bash
cp .env.example .env
```

Fill in real values for `HF_TOKEN`, `GMAIL_ADDRESS`, `GMAIL_APP_PASSWORD`,
`DIGEST_RECIPIENTS`, `DECODO_USERNAME`, `DECODO_PASSWORD`. Everything else
(concurrency caps, pagination limits, `INDEED_PAGE_SETTLE_SECONDS`, etc.)
has a working default in `config.py` -- only override what you actually
want different.

## 5. Test it once manually before automating anything

```bash
python3 -m visa_jobs_api.cli
```

Confirm it runs cleanly end-to-end and the digest email actually arrives.
This also implicitly checks that this machine's IP isn't already
flagged/on a VPN/corporate network -- the entire reason local runs work at
all is being on an ordinary residential IP (see `DECISIONS.md`).

## 6. Set up the daily automatic run (launchd)

`scripts/run_digest_locally.sh` and the LaunchAgent `.plist` both hardcode
this repo's clone path -- on a new machine (different username or clone
location) these need editing, not just copying:

- Edit `REPO_DIR` near the top of `scripts/run_digest_locally.sh` to this
  machine's actual clone path.
- Create `~/Library/LaunchAgents/com.shubham.visa-jobs-digest.plist` (this
  file lives outside the repo, in your home directory, on purpose --
  never committed to git) with `ProgramArguments`, `StandardOutPath`, and
  `StandardErrorPath` pointed at this machine's paths. Use the version on
  the original machine as a template.
- Load it and confirm:

```bash
launchctl load ~/Library/LaunchAgents/com.shubham.visa-jobs-digest.plist
launchctl list | grep visa-jobs-digest
```

It's scheduled for 9:00 AM local time. Every run's full output lands in
its own timestamped file under `logs/` (gitignored).

## 7. (Optional) Investigate the GitHub Actions blocking issue further

`.github/workflows/investigate-indeed-blocking.yml` (manual-trigger only)
and `scripts/diagnose_indeed_blocking.py` exist for picking this
investigation back up -- see `DECISIONS.md` for what's been ruled out so
far and what hasn't been tried yet (a residential/ISP proxy, specifically).
