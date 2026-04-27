#!/usr/bin/env bash
#
# Re-summarize existing briefing JSONs using cached local transcripts only.
# Forwards all arguments to scripts/re-summarize-from-cache.py.

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null)" || {
    echo "ERROR: not inside a git repository" >&2
    exit 1
}
cd "$REPO_ROOT"

exec python3 scripts/re-summarize-from-cache.py "$@"
