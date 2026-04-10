#!/usr/bin/env bash
#
# install-cron.sh — Linux cron equivalent of install-launchd.sh.
#
# Same purpose as install-launchd.sh but for Linux. Cloud CI (GitHub Actions)
# is not an option because YouTube blocks cloud runner IP ranges — see
# install-launchd.sh header for the full rationale.
#
# Adds a cron entry that runs pipeline/run.py && commit-and-push.sh
# on Mon/Wed/Fri at 06:00 KST (= 21:00 UTC Sun/Tue/Thu).
#
# Idempotent: re-running replaces the existing entry.

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null)" || {
    echo "ERROR: run this from inside the youtube-briefing git repo" >&2
    exit 1
}

PYTHON_BIN="$REPO_ROOT/.venv/bin/python"
if [ ! -x "$PYTHON_BIN" ]; then
    PYTHON_BIN="$(command -v python3 || command -v python)"
fi

MARKER="# youtube-briefing cron"
CRON_CMD="0 21 * * 0,2,4 cd $REPO_ROOT && $PYTHON_BIN pipeline/run.py && ./scripts/commit-and-push.sh  $MARKER"

# Remove any existing entry with our marker, then append new one
(crontab -l 2>/dev/null | grep -v "$MARKER" || true; echo "$CRON_CMD") | crontab -

echo "✓ Installed cron entry:"
echo "  $CRON_CMD"
echo ""
echo "Runs on Mon/Wed/Fri at 06:00 KST (Sun/Tue/Thu 21:00 UTC)."
echo "List with: crontab -l"
echo "Remove with: crontab -l | grep -v '$MARKER' | crontab -"
