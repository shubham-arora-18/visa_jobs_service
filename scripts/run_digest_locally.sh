#!/usr/bin/env bash
# Runs the full 3-source digest locally, on this machine -- a temporary
# stand-in for the GitHub Actions scheduled cron (removed from
# .github/workflows/daily-digest.yml) while Indeed search via Selenium is
# blocked from every datacenter IP tested so far (GitHub Actions' own
# runner, and a Decodo datacenter proxy) but works cleanly from this
# machine's residential/office IP. See DECISIONS.md's "Indeed search via
# Selenium works from a residential IP..." entry for the full
# investigation this is a workaround for.
#
# Intended to be invoked directly, or via the launchd LaunchAgent set up
# alongside this script (see its accompanying .plist) for a daily
# automatic run. Every invocation's full output goes to its own
# timestamped file under logs/ (gitignored) -- nothing here sends
# anything anywhere except the digest's own success/failure email.

set -uo pipefail

REPO_DIR="/Users/shubarora/Adobe/PersonalProjects/TheHuntProject/visa_jobs_api"
LOG_DIR="$REPO_DIR/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/digest_$(date +%Y-%m-%d_%H-%M-%S).log"

cd "$REPO_DIR"

echo "=== Starting digest run at $(date) ===" >> "$LOG_FILE"

# Sponsorship confirmation runs against a local LLM served via Docker
# Model Runner, brought up through docker-compose.yml's `llm` model
# resource (see DECISIONS.md's "Local LLM moved from a manual `docker
# model package` CLI step to `docker-compose.yml`..." entry for why:
# declarative --parallel/context-size tuning instead of a hand-run CLI
# incantation nothing in the repo could recreate). `docker compose down`
# runs on any exit from this point on (success, digest failure, or the
# healthcheck below timing out) via the trap, so a crashed run never
# leaves the model server running indefinitely.
trap 'docker compose down >> "'"$LOG_FILE"'" 2>&1' EXIT

echo "--- Bringing up docker compose (local LLM) ---" >> "$LOG_FILE"
docker compose up -d >> "$LOG_FILE" 2>&1

# Unlike a hosted API, a local model can simply not be ready yet (Docker
# Desktop still starting, or the model still loading) -- left unchecked,
# every source's LLM confirmation call would fail individually and get
# swallowed by aggregator.collect_jobs's per-source catch-all, producing a
# "successful" digest with zero jobs and no obvious reason why. Fail the
# whole run loudly instead, with a retry window for it to come up.
LLM_HEALTHCHECK_URL="http://localhost:12434/engines/llama.cpp/v1/models"
LLM_HEALTHCHECK_RETRIES=12
LLM_HEALTHCHECK_DELAY_SECONDS=5

llm_ready=false
for _ in $(seq 1 "$LLM_HEALTHCHECK_RETRIES"); do
    if curl -sf -m 5 "$LLM_HEALTHCHECK_URL" >/dev/null 2>&1; then
        llm_ready=true
        break
    fi
    sleep "$LLM_HEALTHCHECK_DELAY_SECONDS"
done

if [ "$llm_ready" != true ]; then
    echo "=== ERROR: Docker Model Runner not reachable at $LLM_HEALTHCHECK_URL after" \
        "$((LLM_HEALTHCHECK_RETRIES * LLM_HEALTHCHECK_DELAY_SECONDS))s -- is Docker Desktop running?" \
        "Aborting before any scraping starts. ===" >> "$LOG_FILE"
    exit 1
fi

source .venv/bin/activate

python3 -m visa_jobs_api.cli >> "$LOG_FILE" 2>&1
EXIT_CODE=$?
echo "=== Digest run finished at $(date) with exit code $EXIT_CODE ===" >> "$LOG_FILE"
exit "$EXIT_CODE"
