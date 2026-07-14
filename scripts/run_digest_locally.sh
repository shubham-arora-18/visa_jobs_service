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
source .venv/bin/activate

echo "=== Starting digest run at $(date) ===" >> "$LOG_FILE"
python3 -m visa_jobs_api.cli >> "$LOG_FILE" 2>&1
EXIT_CODE=$?
echo "=== Digest run finished at $(date) with exit code $EXIT_CODE ===" >> "$LOG_FILE"
exit "$EXIT_CODE"
