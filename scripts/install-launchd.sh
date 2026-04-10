#!/usr/bin/env bash
#
# install-launchd.sh — macOS launchd timer installer (LEGACY / OPTIONAL).
#
# ⚠️  DEPRECATED as the primary automation path. The recommended setup uses
# .github/workflows/pipeline.yml which runs on GitHub Actions and does not
# depend on your Mac being awake. This launchd script is kept for:
#
#   1. Developers who prefer local-only execution (privacy, no GitHub secrets)
#   2. Running the pipeline from a dedicated always-on Mac that has a
#      logged-in NotebookLM Google session (for use cases where tier 2 is
#      required and cannot be moved to CI).
#
# Creates ~/Library/LaunchAgents/com.kpsfamily.youtube-briefing.plist that runs
# pipeline/run.py && scripts/commit-and-push.sh on Mon/Wed/Fri at 06:00 KST
# (= 21:00 UTC Sun/Tue/Thu).
#
# Run once from the repo root:
#   ./scripts/install-launchd.sh
#
# To uninstall:
#   launchctl unload ~/Library/LaunchAgents/com.kpsfamily.youtube-briefing.plist
#   rm ~/Library/LaunchAgents/com.kpsfamily.youtube-briefing.plist

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null)" || {
    echo "ERROR: run this from inside the youtube-briefing git repo" >&2
    exit 1
}

PLIST_NAME="com.kpsfamily.youtube-briefing"
PLIST_PATH="$HOME/Library/LaunchAgents/${PLIST_NAME}.plist"

# Choose python interpreter: prefer .venv, fall back to system
PYTHON_BIN="$REPO_ROOT/.venv/bin/python"
if [ ! -x "$PYTHON_BIN" ]; then
    PYTHON_BIN="$(command -v python3 || command -v python)"
    if [ -z "$PYTHON_BIN" ]; then
        echo "ERROR: no python3 found — create .venv or install python3" >&2
        exit 1
    fi
fi

echo "Installing launchd timer..."
echo "  Repo:   $REPO_ROOT"
echo "  Python: $PYTHON_BIN"
echo "  Plist:  $PLIST_PATH"

mkdir -p "$HOME/Library/LaunchAgents"

cat > "$PLIST_PATH" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>${PLIST_NAME}</string>

    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>-c</string>
        <string>cd "${REPO_ROOT}" &amp;&amp; "${PYTHON_BIN}" pipeline/run.py &amp;&amp; ./scripts/commit-and-push.sh</string>
    </array>

    <key>StartCalendarInterval</key>
    <array>
        <!-- Monday 06:00 KST = Sunday 21:00 UTC -->
        <dict><key>Weekday</key><integer>1</integer><key>Hour</key><integer>6</integer><key>Minute</key><integer>0</integer></dict>
        <!-- Wednesday 06:00 KST -->
        <dict><key>Weekday</key><integer>3</integer><key>Hour</key><integer>6</integer><key>Minute</key><integer>0</integer></dict>
        <!-- Friday 06:00 KST -->
        <dict><key>Weekday</key><integer>5</integer><key>Hour</key><integer>6</integer><key>Minute</key><integer>0</integer></dict>
    </array>

    <!-- Run at next scheduled time if Mac was asleep -->
    <key>StartCalendarIntervalRunAtLoad</key>
    <false/>

    <!-- Capture stdout/stderr to files for debugging. Pipeline also logs via logging_config. -->
    <key>StandardOutPath</key>
    <string>${REPO_ROOT}/logs/launchd.out</string>
    <key>StandardErrorPath</key>
    <string>${REPO_ROOT}/logs/launchd.err</string>

    <!-- Prevent launchd from keeping the job running continuously -->
    <key>RunAtLoad</key>
    <false/>
</dict>
</plist>
PLIST

mkdir -p "$REPO_ROOT/logs"

# Unload any previous version, then load fresh
launchctl unload "$PLIST_PATH" 2>/dev/null || true
launchctl load "$PLIST_PATH"

echo ""
echo "✓ Installed. Verify with:"
echo "    launchctl list | grep ${PLIST_NAME}"
echo ""
echo "Runs on Mon/Wed/Fri at 06:00 KST (21:00 UTC previous day)."
echo "Logs: ${REPO_ROOT}/logs/pipeline.log and launchd.{out,err}"
echo ""
echo "To uninstall:"
echo "    launchctl unload ${PLIST_PATH}"
echo "    rm ${PLIST_PATH}"
