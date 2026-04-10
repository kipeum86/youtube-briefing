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

import json
import shutil
import subprocess
import sys


def resolve_channel_id(url: str) -> str:
    """Return the UC... channel ID for a given YouTube URL/handle.

    Uses `yt-dlp -J --flat-playlist --playlist-items 1` which dumps a single
    JSON document where the channel's UC id lives at the top level. This is
    more reliable than `--print channel_id` because that operates on playlist
    ITEMS, where the field is often "NA" for @handle URLs — yt-dlp only fills
    in per-item channel_id for flat playlists, not for channel feeds.

    Args:
        url: Either @handle URL (https://www.youtube.com/@syukaworld),
             a /channel/UC... URL, a /user/... URL, or a video URL.

    Returns:
        The channel ID string (e.g. "UCsJ6RuBiTVWRX156FVbeaGg").

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

    cmd = [
        "yt-dlp",
        "-J",  # dump channel/playlist JSON at the top level
        "--flat-playlist",
        "--playlist-items", "1",
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

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as e:
        raise RuntimeError(
            f"yt-dlp JSON parse error: {e}\nFirst 500 chars of stdout: {result.stdout[:500]}"
        ) from e

    # Try the obvious location first, fall back to other fields that sometimes
    # hold the UC id (e.g. when given a video URL or an uploader URL).
    channel_id = (data.get("channel_id") or "").strip()
    if not _looks_like_uc(channel_id):
        for alt_field in ("uploader_id", "id"):
            alt = (data.get(alt_field) or "").strip()
            if _looks_like_uc(alt):
                channel_id = alt
                break

    if not _looks_like_uc(channel_id):
        raise RuntimeError(
            "Could not extract a UC... channel ID from yt-dlp output.\n"
            f"  channel_id={data.get('channel_id')!r}\n"
            f"  id={data.get('id')!r}\n"
            f"  uploader_id={data.get('uploader_id')!r}\n"
            f"Check that {url!r} is a real YouTube channel."
        )

    return channel_id


def _looks_like_uc(value: str) -> bool:
    """UC IDs are always 'UC' + 22 base64url chars, total 24."""
    return bool(value) and value.startswith("UC") and len(value) == 24


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
