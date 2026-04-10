"""Pipeline orchestrator — discover → transcript → summarize → write.

Pure orchestration. NEVER invokes git. The launchd chain runs:
    python pipeline/run.py && scripts/commit-and-push.sh

Key behaviors (deterministic failure contract):
  - Per-video try/except — one video's failure never halts the run
  - TransientFailure → skip (no write), auto-retried next run via glob exclusion
  - PermanentFailure → write placeholder JSON with status=failed
  - Discovery failures logged, that channel is skipped, other channels continue

Exit codes:
  0 — success (one or more briefings written, or nothing new to process)
  1 — fatal error (config missing, no API key, cannot even start)
  2 — all channels failed (nothing written AND discovery failed for all)
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Bootstrap sys.path so `python pipeline/run.py` works from any directory.
# Without this, the absolute imports below fail with ModuleNotFoundError
# because running a file directly doesn't add its parent's parent to sys.path.
# `python -m pipeline.run` also works, but we want the simpler form to work
# too (launchd, CI, manual local invocation).
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import yaml

from pipeline.fetchers.discovery import DiscoveryFailure, discover_new_videos
from pipeline.fetchers.transcript_extractor import (
    PermanentTranscriptFailure,
    TransientTranscriptFailure,
    extract_transcript,
)
from pipeline.logging_config import setup_logging
from pipeline.models import (
    Briefing,
    BriefingStatus,
    FailureReason,
    VideoMeta,
)
from pipeline.summarizers.base import (
    PermanentSummarizerError,
    Summarizer,
    TransientSummarizerError,
    load_summarizer,
)
from pipeline.writers.json_store import (
    list_processed_video_ids,
    list_processed_video_ids_by_channel,
    write_briefing,
)

logger = logging.getLogger(__name__)


def load_config(config_path: Path | str) -> dict:
    """Load and minimally validate config.yaml."""
    config_path = Path(config_path)
    if not config_path.exists():
        raise FileNotFoundError(f"config file not found: {config_path}")

    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))

    if "pipeline" not in config or "channels" not in config:
        raise ValueError(f"config missing required sections (pipeline, channels): {config_path}")

    for i, ch in enumerate(config["channels"]):
        if not ch.get("id"):
            raise ValueError(
                f"channel [{i}] ({ch.get('name', 'unknown')}) has empty id — "
                f"run scripts/resolve-channel-ids.py"
            )

    return config


def build_briefing_from_success(
    meta: VideoMeta,
    summary: str,
    provider: str,
    model: str,
    prompt_version: str,
) -> Briefing:
    """Construct an ok-status Briefing from a successful summarize."""
    return Briefing(
        video_id=meta.video_id,
        channel_slug=meta.channel_slug,
        channel_name=meta.channel_name,
        title=meta.title,
        published_at=meta.published_at,
        video_url=f"https://www.youtube.com/watch?v={meta.video_id}",
        thumbnail_url=f"https://i.ytimg.com/vi/{meta.video_id}/hqdefault.jpg",
        duration_seconds=meta.duration_seconds or 0,
        discovery_source=meta.discovery_source,
        status=BriefingStatus.OK,
        summary=summary,
        failure_reason=None,
        generated_at=datetime.now(timezone.utc),
        provider=provider,
        model=model,
        prompt_version=prompt_version,
    )


def build_briefing_from_permanent_failure(
    meta: VideoMeta,
    failure_code: str,
    provider: str,
    model: str,
    prompt_version: str,
) -> Briefing:
    """Construct a failed-status Briefing placeholder."""
    try:
        reason = FailureReason(failure_code)
    except ValueError:
        logger.warning("unknown failure_code %r — mapping to empty_transcript", failure_code)
        reason = FailureReason.EMPTY_TRANSCRIPT

    return Briefing(
        video_id=meta.video_id,
        channel_slug=meta.channel_slug,
        channel_name=meta.channel_name,
        title=meta.title,
        published_at=meta.published_at,
        video_url=f"https://www.youtube.com/watch?v={meta.video_id}",
        thumbnail_url=f"https://i.ytimg.com/vi/{meta.video_id}/hqdefault.jpg",
        duration_seconds=meta.duration_seconds or 0,
        discovery_source=meta.discovery_source,
        status=BriefingStatus.FAILED,
        summary=None,
        failure_reason=reason,
        generated_at=datetime.now(timezone.utc),
        provider=provider,
        model=model,
        prompt_version=prompt_version,
    )


def process_video(
    meta: VideoMeta,
    summarizer: Summarizer,
    briefings_dir: Path,
    transcript_cache_dir: Path | None,
) -> Briefing | None:
    """Process a single video: transcript → summarize → write.

    Returns:
        The Briefing (ok or failed placeholder) that was written to disk.
        None if the video was skipped due to a transient failure (retry next run).
    """
    logger.info("[%s] processing: %s", meta.channel_slug, meta.title)

    try:
        transcript_result = extract_transcript(meta.video_id, cache_dir=transcript_cache_dir)
    except TransientTranscriptFailure as e:
        logger.warning("[%s] transient transcript failure, skipping: %s", meta.channel_slug, e.reason)
        return None
    except PermanentTranscriptFailure as e:
        logger.info("[%s] permanent transcript failure: %s", meta.channel_slug, e.reason)
        briefing = build_briefing_from_permanent_failure(
            meta=meta,
            failure_code=e.failure_code,
            provider=summarizer.provider,
            model=summarizer.model,
            prompt_version=summarizer.prompt_version,
        )
        write_briefing(briefing, briefings_dir)
        return briefing

    try:
        result = summarizer.summarize(transcript_result.text, meta)
    except TransientSummarizerError as e:
        logger.warning("[%s] transient summarizer failure, skipping: %s", meta.channel_slug, e)
        return None
    except PermanentSummarizerError as e:
        logger.info("[%s] permanent summarizer failure: %s", meta.channel_slug, e)
        briefing = build_briefing_from_permanent_failure(
            meta=meta,
            failure_code=e.failure_code,
            provider=summarizer.provider,
            model=summarizer.model,
            prompt_version=summarizer.prompt_version,
        )
        write_briefing(briefing, briefings_dir)
        return briefing

    briefing = build_briefing_from_success(
        meta=meta,
        summary=result.summary,
        provider=result.provider,
        model=result.model,
        prompt_version=result.prompt_version,
    )
    write_briefing(briefing, briefings_dir)
    return briefing


def run(
    config_path: Path | str,
    briefings_dir: Path | str,
    dry_run: bool = False,
    limit: int | None = None,
    only_channel: str | None = None,
) -> int:
    """Main entrypoint.

    Args:
        config_path: Path to config.yaml.
        briefings_dir: Where to write briefing JSON files.
        dry_run: If True, discover only — no transcript/summarize/write.
        limit: Optional cap on total videos processed this run. Useful for
            smoke tests and for gentle first runs after long absences.
        only_channel: Optional slug filter. When set, only that one channel
            is processed; other channels are skipped with a log.

    Returns:
        Exit code (0 success, 2 all channels failed).
    """
    config = load_config(config_path)
    briefings_dir = Path(briefings_dir)
    briefings_dir.mkdir(parents=True, exist_ok=True)

    pipeline_cfg = config["pipeline"]
    transcript_cache_dir = Path(pipeline_cfg.get("transcript_cache_dir", "data/transcripts"))
    transcript_cache_dir.mkdir(parents=True, exist_ok=True)

    summarizer_cfg = pipeline_cfg["summarizer"]
    summarizer = load_summarizer(
        provider=summarizer_cfg["provider"],
        model=summarizer_cfg["model"],
        prompt_version=summarizer_cfg.get("prompt_version", "v1"),
    )
    # Apply length constraints from config
    summarizer.min_chars = pipeline_cfg.get("summary_min_chars", 700)
    summarizer.max_chars = pipeline_cfg.get("summary_max_chars", 1200)

    # Per-channel discovery cap — how many NEW videos per channel per run
    max_per_channel = pipeline_cfg.get("max_new_videos_per_channel", 10)

    # Per-channel known set so the saturation check is scoped correctly.
    # See list_processed_video_ids_by_channel for the rationale.
    known_by_channel = list_processed_video_ids_by_channel(briefings_dir)
    total_known = sum(len(v) for v in known_by_channel.values())
    logger.info(
        "pipeline starting: %d channels, %d known videos total, max %d new per channel%s%s",
        len(config["channels"]),
        total_known,
        max_per_channel,
        f", limit={limit}" if limit else "",
        f", only_channel={only_channel}" if only_channel else "",
    )

    total_written = 0
    total_skipped = 0
    channels_failed = 0

    for channel in config["channels"]:
        channel_slug = channel["slug"]

        if only_channel and channel_slug != only_channel:
            logger.debug("[%s] skipped (only_channel=%s)", channel_slug, only_channel)
            continue

        # Only this channel's known IDs are passed in. Cross-channel known IDs
        # would cause false-positive saturation: a video_id from another
        # channel can never appear in this channel's RSS, so the "any RSS
        # item match a known id?" check would always be no, triggering yt-dlp
        # catchup unnecessarily.
        channel_known = known_by_channel.get(channel_slug, set())

        try:
            new_videos = discover_new_videos(
                channel_id=channel["id"],
                channel_slug=channel_slug,
                channel_name=channel["name"],
                known_video_ids=channel_known,
                max_new_videos=max_per_channel,
            )
        except DiscoveryFailure as e:
            logger.error("[%s] discovery failed, skipping channel: %s", channel_slug, e)
            channels_failed += 1
            continue

        if not new_videos:
            logger.info("[%s] no new videos", channel_slug)
            continue

        logger.info("[%s] %d new video(s) to process", channel_slug, len(new_videos))

        for meta in new_videos:
            # Enforce --limit across all channels (not per-channel)
            if limit is not None and (total_written + total_skipped) >= limit:
                logger.info("limit=%d reached, stopping", limit)
                break

            if dry_run:
                logger.info("[DRY-RUN] would process %s: %s", meta.video_id, meta.title)
                total_skipped += 1
                continue

            try:
                result = process_video(
                    meta=meta,
                    summarizer=summarizer,
                    briefings_dir=briefings_dir,
                    transcript_cache_dir=transcript_cache_dir,
                )
            except Exception as e:  # noqa: BLE001 — REGRESSION: never halt on unexpected errors
                logger.exception(
                    "[%s] unhandled exception processing %s, continuing to next video: %s",
                    channel_slug,
                    meta.video_id,
                    e,
                )
                total_skipped += 1
                continue

            if result is None:
                total_skipped += 1
            else:
                total_written += 1
                channel_known.add(meta.video_id)  # avoid re-processing within same run

        # Honor --limit at the outer loop too so we don't start a new channel
        if limit is not None and (total_written + total_skipped) >= limit:
            break

    logger.info(
        "pipeline complete: wrote %d, skipped %d, %d channels failed",
        total_written,
        total_skipped,
        channels_failed,
    )

    if total_written == 0 and channels_failed == len(config["channels"]):
        return 2  # all channels failed, nothing written
    return 0


def main():
    parser = argparse.ArgumentParser(description="YouTube Briefing pipeline")
    parser.add_argument("--config", default="config.yaml", help="Path to config.yaml")
    parser.add_argument("--briefings-dir", default="data/briefings", help="Where to write JSON files")
    parser.add_argument("--dry-run", action="store_true", help="Discover only, no transcript/summarize/write")
    parser.add_argument("--log-dir", default="logs", help="Log directory")
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Cap total videos processed this run (useful for smoke tests)",
    )
    parser.add_argument(
        "--only-channel",
        default=None,
        help="Process only this channel slug (e.g. 'shuka'). Other channels are skipped.",
    )
    args = parser.parse_args()

    # Load .env if present. Missing .env is fine — env vars may come from
    # the shell, from launchd plist, or from GitHub Actions secrets injection.
    try:
        from dotenv import load_dotenv
        load_dotenv(_REPO_ROOT / ".env", override=False)
    except ImportError:
        pass  # python-dotenv is optional — not installed in minimal CI images

    setup_logging(log_dir=args.log_dir)

    try:
        exit_code = run(
            config_path=args.config,
            briefings_dir=args.briefings_dir,
            dry_run=args.dry_run,
            limit=args.limit,
            only_channel=args.only_channel,
        )
    except FileNotFoundError as e:
        logger.error("config error: %s", e)
        sys.exit(1)
    except ValueError as e:
        logger.error("config validation: %s", e)
        sys.exit(1)

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
