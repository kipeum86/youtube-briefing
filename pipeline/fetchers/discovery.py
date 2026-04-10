"""Two-tier channel upload discovery.

Primary: YouTube RSS feeds (fast, no auth, returns latest 15 videos per channel)
Fallback: `yt-dlp --flat-playlist` catchup (handles RSS window saturation when
          the Mac was off for >1 week and older uploads fell off the RSS feed)

The decision between tiers is made per-channel: if the pipeline's most-recent
known video_id for this channel is NOT in the RSS response AND RSS returned
exactly 15 items (the saturation signal), switch to yt-dlp catchup for that
channel.

Returns VideoMeta objects, never raw strings. Downstream modules get a typed
boundary.
"""

from __future__ import annotations

import json
import logging
import subprocess
from datetime import datetime, timezone
from typing import Iterable

from pipeline.models import DiscoverySource, VideoMeta

logger = logging.getLogger(__name__)

RSS_URL_TEMPLATE = "https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
RSS_MAX_RESULTS = 15  # YouTube's RSS feed always caps at 15
YTDLP_CATCHUP_LIMIT = 50  # How many videos to pull from yt-dlp when doing catchup


class DiscoveryFailure(Exception):
    """Raised when BOTH RSS and yt-dlp catchup fail for a channel."""


def discover_new_videos(
    channel_id: str,
    channel_slug: str,
    channel_name: str,
    known_video_ids: set[str],
    max_new_videos: int | None = None,
) -> list[VideoMeta]:
    """Find videos from a channel that are not yet in known_video_ids.

    Two-tier strategy:
      1. Try RSS first (fast, cheap). YouTube's RSS feed always returns up
         to ~15 items (YouTube-side cap, not ours).
      2. If RSS is saturated (our newest-known not in response AND response
         has exactly 15 items), fall back to yt-dlp catchup for this channel.

    After filtering out already-processed videos, optionally cap the result
    to the `max_new_videos` most recent items. This is useful for keeping
    scheduled runs from processing bursts of 15 videos per channel — a more
    reasonable default is 10, leaving headroom for channels that occasionally
    post multiple videos in a day.

    Args:
        channel_id: YouTube UC... channel ID
        channel_slug: Project-local slug (e.g. "shuka")
        channel_name: Human-readable name (e.g. "슈카월드")
        known_video_ids: set of video_ids already processed (from glob of data/briefings/)
        max_new_videos: optional cap on how many NEW videos to return per run.
            None (default) means no cap (use whatever RSS gives us, up to 15).

    Returns:
        List of new VideoMeta objects, ordered newest-first. Empty list if nothing new.

    Raises:
        DiscoveryFailure: when both tiers fail permanently (e.g. channel ID wrong)
    """
    # Tier 1: RSS
    try:
        rss_videos = _fetch_rss(channel_id, channel_slug, channel_name)
    except Exception as e:  # noqa: BLE001 — any RSS failure is recoverable via yt-dlp
        logger.warning("[%s] RSS fetch failed: %s — falling back to yt-dlp", channel_slug, e)
        rss_videos = None

    if rss_videos is not None:
        # Filter to new videos
        new_rss = [v for v in rss_videos if v.video_id not in known_video_ids]

        # Check for RSS window saturation
        saturated = _is_rss_saturated(rss_videos, known_video_ids)
        if not saturated:
            capped = _apply_cap(new_rss, max_new_videos)
            logger.info(
                "[%s] RSS discovery: %d total, %d new%s",
                channel_slug,
                len(rss_videos),
                len(new_rss),
                f" (capped to {len(capped)})" if len(capped) < len(new_rss) else "",
            )
            return capped

        logger.info(
            "[%s] RSS window saturated (%d items, none known) — triggering yt-dlp catchup",
            channel_slug,
            len(rss_videos),
        )

    # Tier 2: yt-dlp catchup
    try:
        catchup_videos = _fetch_ytdlp_catchup(channel_id, channel_slug, channel_name)
    except Exception as e:  # noqa: BLE001
        if rss_videos is None:
            raise DiscoveryFailure(
                f"[{channel_slug}] both RSS and yt-dlp catchup failed: {e}"
            ) from e
        logger.warning(
            "[%s] yt-dlp catchup failed: %s — falling back to RSS results",
            channel_slug,
            e,
        )
        fallback = [v for v in rss_videos if v.video_id not in known_video_ids]
        return _apply_cap(fallback, max_new_videos)

    new_catchup = [v for v in catchup_videos if v.video_id not in known_video_ids]
    capped = _apply_cap(new_catchup, max_new_videos)
    logger.info(
        "[%s] yt-dlp catchup: %d total, %d new%s",
        channel_slug,
        len(catchup_videos),
        len(new_catchup),
        f" (capped to {len(capped)})" if len(capped) < len(new_catchup) else "",
    )
    return capped


def _apply_cap(videos: list[VideoMeta], cap: int | None) -> list[VideoMeta]:
    """Return the most-recent N videos from the list, or the full list if cap is None/0.

    Videos are assumed to be sorted newest-first by the caller (which matches
    the order RSS and yt-dlp catchup both return them).
    """
    if cap is None or cap <= 0 or len(videos) <= cap:
        return videos
    return videos[:cap]


# ---------------------------------------------------------------------------
# Tier 1: RSS
# ---------------------------------------------------------------------------


def _fetch_rss(channel_id: str, channel_slug: str, channel_name: str) -> list[VideoMeta]:
    """Fetch the latest 15 uploads via YouTube's RSS feed."""
    try:
        import feedparser
    except ImportError as e:
        raise RuntimeError("feedparser not installed — required for RSS discovery") from e

    url = RSS_URL_TEMPLATE.format(channel_id=channel_id)
    feed = feedparser.parse(url)

    if feed.get("bozo") and not feed.entries:
        raise RuntimeError(
            f"RSS feed malformed or empty for channel_id={channel_id}: "
            f"{feed.get('bozo_exception', 'unknown error')}"
        )

    videos: list[VideoMeta] = []
    for entry in feed.entries:
        video_id = _extract_video_id_from_rss_entry(entry)
        if not video_id:
            continue
        videos.append(
            VideoMeta(
                video_id=video_id,
                channel_id=channel_id,
                channel_slug=channel_slug,
                channel_name=channel_name,
                title=entry.get("title", "").strip(),
                published_at=_parse_rss_timestamp(entry.get("published", "")),
                discovery_source=DiscoverySource.RSS,
                duration_seconds=None,  # RSS doesn't give duration
            )
        )

    return videos


def _extract_video_id_from_rss_entry(entry) -> str | None:
    """Pull the video_id from a feedparser entry.

    YouTube's RSS entries encode the video_id in the `yt:videoId` element,
    which feedparser exposes as entry.yt_videoid.
    """
    video_id = entry.get("yt_videoid")
    if video_id:
        return str(video_id).strip()

    # Fallback: parse from the link URL
    link = entry.get("link", "")
    if "watch?v=" in link:
        return link.split("watch?v=", 1)[1].split("&")[0].strip()

    return None


def _parse_rss_timestamp(ts: str) -> datetime:
    """Parse an RSS published timestamp into a timezone-aware datetime."""
    if not ts:
        return datetime.now(timezone.utc)
    try:
        # RSS 2.0 / Atom format: "2026-04-09T03:00:00+00:00"
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        logger.warning("failed to parse RSS timestamp %r — using now()", ts)
        return datetime.now(timezone.utc)


def _is_rss_saturated(rss_videos: list[VideoMeta], known_video_ids: set[str]) -> bool:
    """Detect whether the RSS window has rolled past our last-known video.

    Saturation signal: RSS returned exactly 15 items AND none of them match
    our known set (or this is the first-ever run). If we have known IDs but
    NONE appear in the 15 most-recent, we've fallen behind the RSS window and
    need yt-dlp catchup to find the videos that rolled off.
    """
    if not rss_videos:
        return False

    # First-ever run for this channel: take whatever RSS gives us, not saturated
    if not known_video_ids:
        return False

    # If any known video is in the RSS window, we're caught up
    for v in rss_videos:
        if v.video_id in known_video_ids:
            return False

    # Known IDs exist but none are in the 15-item window — saturated
    return len(rss_videos) >= RSS_MAX_RESULTS


# ---------------------------------------------------------------------------
# Tier 2: yt-dlp catchup
# ---------------------------------------------------------------------------


def _fetch_ytdlp_catchup(channel_id: str, channel_slug: str, channel_name: str) -> list[VideoMeta]:
    """Pull the latest N uploads via yt-dlp --flat-playlist.

    Only called when RSS saturation is detected. N is bounded by
    YTDLP_CATCHUP_LIMIT to avoid pulling the entire channel history.
    """
    url = f"https://www.youtube.com/channel/{channel_id}/videos"
    cmd = [
        "yt-dlp",
        "--flat-playlist",
        "--playlist-items", f"1-{YTDLP_CATCHUP_LIMIT}",
        "--print", "%(id)s|%(title)s|%(upload_date)s|%(duration)s",
        url,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except FileNotFoundError as e:
        raise RuntimeError("yt-dlp binary not found — required for catchup") from e
    except subprocess.TimeoutExpired as e:
        raise RuntimeError("yt-dlp catchup timeout (120s)") from e

    if result.returncode != 0:
        raise RuntimeError(
            f"yt-dlp catchup exit {result.returncode}: {result.stderr[:300]}"
        )

    return list(_parse_ytdlp_output(
        result.stdout,
        channel_id=channel_id,
        channel_slug=channel_slug,
        channel_name=channel_name,
    ))


def _parse_ytdlp_output(
    stdout: str,
    *,
    channel_id: str,
    channel_slug: str,
    channel_name: str,
) -> Iterable[VideoMeta]:
    """Parse yt-dlp's pipe-delimited output format into VideoMeta objects."""
    for line in stdout.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        parts = line.split("|", 3)
        if len(parts) < 3:
            logger.warning("malformed yt-dlp line: %r", line)
            continue

        video_id = parts[0].strip()
        title = parts[1].strip()
        upload_date_str = parts[2].strip()
        duration_str = parts[3].strip() if len(parts) > 3 else ""

        try:
            published_at = datetime.strptime(upload_date_str, "%Y%m%d").replace(
                tzinfo=timezone.utc
            )
        except ValueError:
            logger.warning("bad upload_date from yt-dlp: %r", upload_date_str)
            published_at = datetime.now(timezone.utc)

        try:
            duration_seconds: int | None = int(float(duration_str)) if duration_str and duration_str != "NA" else None
        except ValueError:
            duration_seconds = None

        yield VideoMeta(
            video_id=video_id,
            channel_id=channel_id,
            channel_slug=channel_slug,
            channel_name=channel_name,
            title=title,
            published_at=published_at,
            discovery_source=DiscoverySource.YTDLP_CATCHUP,
            duration_seconds=duration_seconds,
        )
