#!/usr/bin/env python3
"""Resolve a YouTube @handle URL to a UC... channel ID via yt-dlp.

Usage:
    python scripts/resolve-channel-ids.py https://www.youtube.com/@syukaworld
    # prints: UCsT0YIqwnpJCM-mx7-gSA4Q

Reliably handles every YouTube DOM change because yt-dlp does the heavy
lifting. HTML scraping is fragile; yt-dlp is maintained aggressively and
has tracked YouTube UI changes for 5+ years.
"""

from __future__ import annotations

import shutil
import subprocess
import sys


def resolve_channel_id(url: str) -> str:
    """Return the UC... channel ID for a given YouTube URL/handle.

    Args:
        url: Either @handle URL (https://www.youtube.com/@syukaworld),
             a /channel/UC... URL, a /user/... URL, or a video URL.

    Returns:
        The channel ID string (e.g. "UCsT0YIqwnpJCM-mx7-gSA4Q").

    Raises:
        RuntimeError: yt-dlp not installed or failed to resolve
        ValueError: url is empty
    """
    url = url.strip()
    if not url:
        raise ValueError("url is empty")

    if shutil.which("yt-dlp") is None:
        raise RuntimeError(
            "yt-dlp is not installed. Install via:\n"
            "  macOS:   brew install yt-dlp\n"
            "  Linux:   pipx install yt-dlp  (or apt install yt-dlp)\n"
            "  Windows: choco install yt-dlp"
        )

    # Use --playlist-items 1 so yt-dlp only pulls metadata for one video,
    # not the entire channel history. The channel_id is in the first item.
    cmd = [
        "yt-dlp",
        "--flat-playlist",
        "--playlist-items", "1",
        "--print", "%(channel_id)s",
        url,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60, check=False)
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"yt-dlp timed out after 60s: {url}") from e

    if result.returncode != 0:
        raise RuntimeError(
            f"yt-dlp failed (exit {result.returncode}):\n"
            f"{result.stderr.strip() or '(no stderr output)'}"
        )

    channel_id = result.stdout.strip().splitlines()[0] if result.stdout.strip() else ""
    if not channel_id or not channel_id.startswith("UC"):
        raise RuntimeError(
            f"yt-dlp returned unexpected channel_id: {channel_id!r}\n"
            f"stdout: {result.stdout[:200]}"
        )

    return channel_id


def main():
    if len(sys.argv) != 2:
        print("Usage: python scripts/resolve-channel-ids.py <url>", file=sys.stderr)
        print("Example: python scripts/resolve-channel-ids.py https://www.youtube.com/@syukaworld", file=sys.stderr)
        sys.exit(2)

    url = sys.argv[1]
    try:
        channel_id = resolve_channel_id(url)
    except (RuntimeError, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    print(channel_id)


if __name__ == "__main__":
    main()
