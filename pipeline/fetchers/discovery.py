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
    min_duration_seconds: int | None = None,
) -> list[VideoMeta]:
    """Find videos from a channel that are not yet in known_video_ids.

    Two-tier strategy:
      1. Try RSS first (fast, cheap). YouTube's RSS feed always returns up
         to ~15 items (YouTube-side cap, not ours).
      2. If RSS is saturated (our newest-known not in response AND response
         has exactly 15 items), fall back to yt-dlp catchup for this channel.

    Filter order matters:
      1. Filter to new (not in known_video_ids)
      2. Filter out short videos when a duration floor is enabled and duration
         metadata can be resolved
      3. Cap to max_new_videos most recent

    Duration-filtering before capping matters: if a channel drops 5 Shorts in
    one day, you don't want the cap burning slots on them and skipping real
    videos. The downside: RSS doesn't carry duration, so we have to probe
    yt-dlp for the new candidates. One subprocess call per channel per run.

    Args:
        channel_id: YouTube UC... channel ID
        channel_slug: Project-local slug (e.g. "shuka")
        channel_name: Human-readable name (e.g. "슈카월드")
        known_video_ids: set of video_ids already processed (from glob of data/briefings/)
        max_new_videos: optional cap on how many NEW videos to return per run.
            None (default) means no cap (use whatever RSS gives us, up to 15).
        min_duration_seconds: optional floor on video length, in seconds.
            Videos shorter than this are dropped when duration metadata is
            available. If RSS duration probing is blocked by YouTube, the
            candidates are kept unverified so one yt-dlp failure does not starve
            the entire source. None or 0 disables the filter. Use 1200 to
            require 20+ minutes when durations can be resolved.

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
            # Enrich with durations (RSS doesn't carry them) and drop shorts.
            enriched, dropped = _enrich_and_filter_durations(
                new_rss, min_duration_seconds, channel_slug
            )
            capped = _apply_cap(enriched, max_new_videos)
            logger.info(
                "[%s] RSS discovery: %d total, %d new, %d kept after duration filter%s",
                channel_slug,
                len(rss_videos),
                len(new_rss),
                len(enriched),
                f" (capped to {len(capped)})" if len(capped) < len(enriched) else "",
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
        enriched, _ = _enrich_and_filter_durations(fallback, min_duration_seconds, channel_slug)
        return _apply_cap(enriched, max_new_videos)

    new_catchup = [v for v in catchup_videos if v.video_id not in known_video_ids]
    # Catchup videos already carry duration from yt-dlp — no probe needed.
    filtered_catchup = _filter_shorts(new_catchup, min_duration_seconds)
    capped = _apply_cap(filtered_catchup, max_new_videos)
    logger.info(
        "[%s] yt-dlp catchup: %d total, %d new, %d kept after duration filter%s",
        channel_slug,
        len(catchup_videos),
        len(new_catchup),
        len(filtered_catchup),
        f" (capped to {len(capped)})" if len(capped) < len(filtered_catchup) else "",
    )
    return capped


def _filter_shorts(videos: list[VideoMeta], min_duration_seconds: int | None) -> list[VideoMeta]:
    """Drop videos shorter than the threshold or missing duration metadata."""
    if not min_duration_seconds or min_duration_seconds <= 0:
        return videos
    return [
        v for v in videos
        if v.duration_seconds is not None and v.duration_seconds >= min_duration_seconds
    ]


def _enrich_and_filter_durations(
    videos: list[VideoMeta],
    min_duration_seconds: int | None,
    channel_slug: str,
) -> tuple[list[VideoMeta], int]:
    """Populate duration on RSS-sourced videos, then drop shorts.

    RSS feeds don't include duration, so every video from `_fetch_rss` arrives
    with `duration_seconds=None`. To filter Shorts we need to probe yt-dlp
    once for the candidate set.

    Returns:
        (kept_videos, dropped_count) where kept_videos preserves input order.
    """
    if not videos or not min_duration_seconds or min_duration_seconds <= 0:
        return videos, 0

    # Probe only videos whose duration we don't already know (all of them, for
    # RSS-sourced input, but this keeps the helper safe if called on a mixed list).
    to_probe = [v.video_id for v in videos if v.duration_seconds is None]
    durations: dict[str, int | None] = {}
    if to_probe:
        try:
            durations = _probe_durations(to_probe)
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "[%s] duration probe failed: %s — dropping RSS candidates with unverified duration",
                channel_slug,
                e,
            )
            return [], len(videos)

    # Replace each VideoMeta with a copy carrying the probed duration.
    enriched: list[VideoMeta] = []
    for v in videos:
        if v.duration_seconds is None and v.video_id in durations:
            d = durations[v.video_id]
            enriched.append(v.model_copy(update={"duration_seconds": d}))
        else:
            enriched.append(v)

    kept = _filter_shorts(enriched, min_duration_seconds)
    return kept, len(enriched) - len(kept)


def _probe_durations(video_ids: list[str]) -> dict[str, int | None]:
    """Batch-probe video durations via yt-dlp metadata fetch.

    Runs a single yt-dlp subprocess with all video URLs, prints `id|duration`
    for each, and returns a dict. Videos that yt-dlp can't resolve are mapped
    to None (keeps them from being silently dropped downstream).
    """
    if not video_ids:
        return {}

    urls = [f"https://www.youtube.com/watch?v={vid}" for vid in video_ids]
    cmd = [
        "yt-dlp",
        "--skip-download",
        "--no-warnings",
        "--ignore-errors",
        "--extractor-args", "youtube:lang=ko",
        "--print", "%(id)s|%(duration)s",
        *urls,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    except FileNotFoundError as e:
        raise RuntimeError("yt-dlp binary not found — required for duration probe") from e
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"yt-dlp duration probe timeout ({180}s)") from e

    # We pass --ignore-errors so rc may be nonzero when some videos are
    # unavailable. Only fail hard if we got nothing back at all.
    if result.returncode != 0 and not result.stdout.strip():
        raise RuntimeError(
            f"yt-dlp duration probe exit {result.returncode}: {result.stderr[:300]}"
        )

    durations: dict[str, int | None] = {}
    for line in result.stdout.strip().split("\n"):
        line = line.strip()
        if not line or "|" not in line:
            continue
        vid, _, dur_str = line.partition("|")
        vid = vid.strip()
        dur_str = dur_str.strip()
        if not vid:
            continue
        try:
            durations[vid] = int(float(dur_str)) if dur_str and dur_str != "NA" else None
        except ValueError:
            durations[vid] = None

    return durations


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

    Notes:
    - `--extractor-args "youtube:lang=ko"` forces YouTube to return Korean
      metadata (titles, descriptions). Without this, yt-dlp running on a
      non-Korean IP (e.g. a GitHub Actions runner in the US) gets the
      English machine-translated titles, which then bake into briefings as
      "Trump Strikes the Heart of Tehran..." instead of "트럼프, 테헤란
      심장부 타격..".
    - `release_timestamp` and `timestamp` give us a real publication time
      when `upload_date` returns NA — `upload_date` is often blank in
      `--flat-playlist` mode for non-Shorts videos.
    """
    url = f"https://www.youtube.com/channel/{channel_id}/videos"
    cmd = [
        "yt-dlp",
        "--flat-playlist",
        "--playlist-items", f"1-{YTDLP_CATCHUP_LIMIT}",
        "--extractor-args", "youtube:lang=ko",
        "--print",
        "%(id)s|%(title)s|%(upload_date)s|%(duration)s|%(release_timestamp)s|%(timestamp)s",
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
    """Parse yt-dlp's pipe-delimited output format into VideoMeta objects.

    Expected format (6 pipe-delimited fields):
        id|title|upload_date|duration|release_timestamp|timestamp

    upload_date is often "NA" in --flat-playlist mode. release_timestamp
    and timestamp are unix epochs that almost always have a value, so we
    fall through to those when upload_date is missing.

    When all three fields are NA (observed on parkjonghoon, globelab,
    jisik-inside), we do a per-video non-flat yt-dlp probe for the survivors
    before yielding — non-flat mode reliably populates upload_date. This
    keeps published_at accurate instead of collapsing to `now()`.
    """
    rows: list[tuple[str, str, int | None, datetime | None]] = []
    for line in stdout.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        parts = line.split("|", 5)
        if len(parts) < 3:
            logger.warning("malformed yt-dlp line: %r", line)
            continue

        video_id = parts[0].strip()
        title = parts[1].strip()
        upload_date_str = parts[2].strip()
        duration_str = parts[3].strip() if len(parts) > 3 else ""
        release_ts_str = parts[4].strip() if len(parts) > 4 else ""
        timestamp_str = parts[5].strip() if len(parts) > 5 else ""

        published_at = _parse_ytdlp_publish_date(
            upload_date_str, release_ts_str, timestamp_str, video_id
        )

        try:
            duration_seconds: int | None = int(float(duration_str)) if duration_str and duration_str != "NA" else None
        except ValueError:
            duration_seconds = None

        rows.append((video_id, title, duration_seconds, published_at))

    missing_ids = [vid for vid, _, _, pub in rows if pub is None]
    probed: dict[str, datetime] = {}
    if missing_ids:
        logger.info(
            "[%s] flat-playlist returned all-NA dates for %d video(s) — probing per-video",
            channel_slug,
            len(missing_ids),
        )
        try:
            probed = _probe_publish_dates(missing_ids)
        except Exception as e:  # noqa: BLE001 — fail open to now() rather than dropping videos
            logger.warning("[%s] publish date probe failed: %s", channel_slug, e)

    for video_id, title, duration_seconds, published_at in rows:
        if published_at is None:
            resolved = probed.get(video_id)
            if resolved is None:
                logger.warning(
                    "[%s] could not resolve published_at for %s — falling back to now()",
                    channel_slug,
                    video_id,
                )
                resolved = datetime.now(timezone.utc)
            published_at = resolved

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


def _parse_ytdlp_publish_date(
    upload_date_str: str,
    release_ts_str: str,
    timestamp_str: str,
    video_id: str,
) -> datetime | None:
    """Resolve a publish date from yt-dlp's three available sources.

    yt-dlp's `--flat-playlist` mode populates these fields inconsistently:
      - `upload_date` (YYYYMMDD) is often "NA" for non-Shorts videos.
      - `release_timestamp` (unix epoch) is populated for scheduled/premiere uploads.
      - `timestamp` (unix epoch) is populated for most regular uploads.

    Returns None when all three fields are missing/unparseable — the caller
    is expected to recover via a non-flat per-video probe rather than
    collapsing to now() here (which produced same-second timestamps across
    unrelated videos and broke filename dates + newest-first sort).
    """
    # Tier 1: upload_date as YYYYMMDD
    if upload_date_str and upload_date_str != "NA":
        try:
            return datetime.strptime(upload_date_str, "%Y%m%d").replace(tzinfo=timezone.utc)
        except ValueError:
            logger.debug("[%s] upload_date %r not YYYYMMDD, trying timestamps", video_id, upload_date_str)

    # Tier 2: release_timestamp / timestamp (unix epoch)
    for label, ts_str in (("release_timestamp", release_ts_str), ("timestamp", timestamp_str)):
        if not ts_str or ts_str == "NA":
            continue
        try:
            return datetime.fromtimestamp(int(float(ts_str)), tz=timezone.utc)
        except (ValueError, OSError):
            logger.debug("[%s] %s %r unparseable", video_id, label, ts_str)

    return None


def _probe_publish_dates(video_ids: list[str]) -> dict[str, datetime]:
    """Fetch accurate publish dates for individual videos via yt-dlp.

    --flat-playlist mode drops metadata for some channels (parkjonghoon,
    globelab, jisik-inside observed in prod). A non-flat fetch reliably
    returns `upload_date`, `release_timestamp`, and `timestamp`.

    Returns a dict of video_id → datetime. Videos yt-dlp couldn't resolve
    are omitted; the caller decides how to handle misses.
    """
    if not video_ids:
        return {}

    urls = [f"https://www.youtube.com/watch?v={vid}" for vid in video_ids]
    cmd = [
        "yt-dlp",
        "--skip-download",
        "--no-warnings",
        "--ignore-errors",
        "--extractor-args", "youtube:lang=ko",
        "--print", "%(id)s|%(upload_date)s|%(release_timestamp)s|%(timestamp)s",
        *urls,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    except FileNotFoundError as e:
        raise RuntimeError("yt-dlp binary not found — required for publish date probe") from e
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"yt-dlp publish date probe timeout ({180}s)") from e

    if result.returncode != 0 and not result.stdout.strip():
        raise RuntimeError(
            f"yt-dlp publish date probe exit {result.returncode}: {result.stderr[:300]}"
        )

    dates: dict[str, datetime] = {}
    for line in result.stdout.strip().split("\n"):
        line = line.strip()
        if not line or "|" not in line:
            continue
        parts = line.split("|", 3)
        if len(parts) < 2:
            continue
        vid = parts[0].strip()
        if not vid:
            continue
        upload_date_str = parts[1].strip() if len(parts) > 1 else ""
        release_ts_str = parts[2].strip() if len(parts) > 2 else ""
        timestamp_str = parts[3].strip() if len(parts) > 3 else ""
        dt = _parse_ytdlp_publish_date(upload_date_str, release_ts_str, timestamp_str, vid)
        if dt is not None:
            dates[vid] = dt

    return dates
