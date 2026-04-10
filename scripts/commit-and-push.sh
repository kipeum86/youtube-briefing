#!/usr/bin/env bash
#
# commit-and-push.sh — stage data/, commit, rebase-if-moved, push.
#
# This script runs AFTER pipeline/run.py succeeds. It owns all git side
# effects so run.py stays pure and unit-testable. Only touches data/briefings/
# explicitly — never sweeps the user's working tree.
#
# Error handling:
#   - If nothing changed, exit 0 silently
#   - If rebase conflict, leave commit local and notify (macOS osascript)
#   - If push auth fails, leave commit local and notify
#
# Usage:
#   ./scripts/commit-and-push.sh
#
# Called by launchd after pipeline/run.py exits 0.

set -euo pipefail

# Navigate to the repo root regardless of where launchd invoked us from
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null)" || {
    echo "ERROR: not inside a git repository" >&2
    exit 1
}
cd "$REPO_ROOT"

# --- Stage only data/briefings/ (never the user's working state) ---
git add data/briefings/

if git diff --cached --quiet; then
    echo "No new briefings to commit — exiting cleanly."
    exit 0
fi

# Count new files for the commit message
NEW_COUNT=$(git diff --cached --name-only --diff-filter=A -- data/briefings/ | wc -l | tr -d ' ')
MOD_COUNT=$(git diff --cached --name-only --diff-filter=M -- data/briefings/ | wc -l | tr -d ' ')
DATE_STR=$(date +%Y-%m-%d)

MSG="Update briefings: ${DATE_STR}

New: ${NEW_COUNT}
Modified: ${MOD_COUNT}

Auto-committed by youtube-briefing pipeline."

git commit -m "$MSG"

# --- Fetch + rebase if origin moved ---
if git remote | grep -q '^origin$'; then
    echo "Fetching origin..."
    if ! git fetch origin main 2>/dev/null; then
        echo "WARN: git fetch failed — leaving commit local." >&2
        notify_osx "youtube-briefing: fetch failed" "Commit is local. Check network + credentials."
        exit 3
    fi

    # If remote has moved past our ancestor, rebase
    if ! git merge-base --is-ancestor origin/main HEAD; then
        echo "Remote moved during this run — rebasing on origin/main..."
        if ! git rebase origin/main; then
            git rebase --abort 2>/dev/null || true
            echo "ERROR: rebase conflict. Leaving commit local, manual merge required." >&2
            notify_osx "youtube-briefing: rebase conflict" "New briefings committed but not pushed. Manual merge required."
            exit 2
        fi
    fi

    echo "Pushing origin main..."
    if ! git push origin main 2>/dev/null; then
        echo "ERROR: git push failed. Commit is local, run 'git push origin main' once resolved." >&2
        notify_osx "youtube-briefing: push failed" "Commit is local. Check credentials."
        exit 4
    fi

    echo "Push succeeded. New: ${NEW_COUNT}, Modified: ${MOD_COUNT}"
else
    echo "No origin remote configured — commit is local only."
fi

exit 0

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

notify_osx() {
    local title="$1"
    local msg="$2"
    if command -v osascript >/dev/null 2>&1; then
        osascript -e "display notification \"${msg//\"/\\\"}\" with title \"${title//\"/\\\"}\"" 2>/dev/null || true
    fi
}
