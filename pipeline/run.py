"""Pipeline orchestrator — discover → transcript → summarize → write.

Pure orchestration. NEVER invokes git. The launchd chain runs:
    python pipeline/run.py && scripts/commit-and-push.sh

Key behaviors (deterministic failure contract):
  - Per-video try/except — one video's failure never halts the run
  - TransientFailure → skip (no write), auto-retried next run via glob exclusion
  - Summary contract failure → skip (no write), retry next run with a fresh model call
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
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
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

from pipeline.config import (
    AppConfig,
    BlogConfig,
    ChannelConfig,
    PipelineConfig,
    validate_config_dict,
)
from pipeline.fetchers.discovery import DiscoveryFailure, discover_new_videos
from pipeline.fetchers.naver_blog import discover_new_blog_posts, extract_blog_post_text
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
    SourceType,
    SummarySections,
    VideoMeta,
)
from pipeline.summarizers.base import (
    PermanentSummarizerError,
    Summarizer,
    TransientSummarizerError,
    load_summarizer,
)
from pipeline.writers.json_store import (
    list_processed_video_ids_by_channel,
    write_briefing,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SourceSpec:
    """One configured source plus its per-channel processed-id set."""

    kind: str
    source_id: str
    slug: str
    name: str
    known_video_ids: set[str]


@dataclass(frozen=True)
class DiscoveryOutcome:
    source: SourceSpec
    items: list[VideoMeta]
    failed: bool = False


def load_config(config_path: Path | str) -> AppConfig:
    """Load config.yaml and return a typed AppConfig."""
    config_path = Path(config_path)
    if not config_path.exists():
        raise FileNotFoundError(f"config file not found: {config_path}")

    config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    if not isinstance(config, dict):
        raise ValueError(f"config root must be a mapping: {config_path}")

    channels = config.get("channels", [])
    blogs = config.get("blogs", [])

    if "pipeline" not in config or (not channels and not blogs):
        raise ValueError(
            f"config missing required sections (pipeline, channels/blogs): {config_path}"
        )

    for i, ch in enumerate(channels if isinstance(channels, list) else []):
        if isinstance(ch, dict) and not ch.get("id"):
            raise ValueError(
                f"channel [{i}] ({ch.get('name', 'unknown')}) has empty id — "
                f"run scripts/resolve-channel-ids.py"
            )

    for i, blog in enumerate(blogs if isinstance(blogs, list) else []):
        if isinstance(blog, dict) and not blog.get("blog_id"):
            raise ValueError(
                f"blog [{i}] ({blog.get('name', 'unknown')}) has empty blog_id"
            )

    return validate_config_dict(config)


def _default_source_url(meta: VideoMeta) -> str:
    if meta.source_url is not None:
        return str(meta.source_url)
    return f"https://www.youtube.com/watch?v={meta.video_id}"


def _default_thumbnail_url(meta: VideoMeta) -> str:
    if meta.thumbnail_url is not None:
        return str(meta.thumbnail_url)
    return f"https://i.ytimg.com/vi/{meta.video_id}/hqdefault.jpg"


def build_briefing_from_success(
    meta: VideoMeta,
    summary: str,
    provider: str,
    model: str,
    prompt_version: str,
    summary_sections: SummarySections | None = None,
) -> Briefing:
    """Construct an ok-status Briefing from a successful summarize."""
    return Briefing(
        video_id=meta.video_id,
        channel_slug=meta.channel_slug,
        channel_name=meta.channel_name,
        title=meta.title,
        published_at=meta.published_at,
        video_url=_default_source_url(meta),
        thumbnail_url=_default_thumbnail_url(meta),
        duration_seconds=meta.duration_seconds or 0,
        discovery_source=meta.discovery_source,
        source_type=meta.source_type,
        status=BriefingStatus.OK,
        summary=summary,
        summary_sections=summary_sections,
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
        video_url=_default_source_url(meta),
        thumbnail_url=_default_thumbnail_url(meta),
        duration_seconds=meta.duration_seconds or 0,
        discovery_source=meta.discovery_source,
        source_type=meta.source_type,
        status=BriefingStatus.FAILED,
        summary=None,
        failure_reason=reason,
        generated_at=datetime.now(timezone.utc),
        provider=provider,
        model=model,
        prompt_version=prompt_version,
    )


def build_summarizer_from_config(pipeline_cfg: PipelineConfig) -> Summarizer:
    """Create a summarizer from typed pipeline config."""
    summarizer_cfg = pipeline_cfg.summarizer
    summarizer = load_summarizer(
        provider=summarizer_cfg.provider,
        model=summarizer_cfg.model,
        prompt_version=summarizer_cfg.prompt_version,
        repair_model=summarizer_cfg.repair_model,
        output_format=summarizer_cfg.output_format,
        temperature=summarizer_cfg.temperature,
        max_output_tokens=summarizer_cfg.max_output_tokens,
        request_timeout_seconds=summarizer_cfg.request_timeout_seconds,
        transient_retries=summarizer_cfg.transient_retries,
        transient_backoff_seconds=summarizer_cfg.transient_backoff_seconds,
    )
    summarizer.min_chars = pipeline_cfg.summary_min_chars
    summarizer.max_chars = pipeline_cfg.summary_max_chars
    summarizer.headline_max_chars = pipeline_cfg.summary_headline_max_chars
    summarizer.max_retries_on_short = summarizer_cfg.short_output_retries
    summarizer.max_format_repair_attempts = summarizer_cfg.repair_attempts
    summarizer.max_full_retries = summarizer_cfg.full_retries
    summarizer.context_max_chars = pipeline_cfg.context_max_chars
    return summarizer


def _is_retryable_summary_contract_failure(error: PermanentSummarizerError) -> bool:
    return (
        error.failure_code == "summarizer_refused"
        and "failed summary contract" in str(error)
    )


def process_video(
    meta: VideoMeta,
    summarizer: Summarizer,
    briefings_dir: Path,
    transcript_cache_dir: Path | None,
) -> Briefing | None:
    """Process a single source item: text extraction → summarize → write.

    Returns:
        The Briefing (ok or failed placeholder) that was written to disk.
        None if the video was skipped due to a transient failure (retry next run).
    """
    logger.info("[%s] processing: %s", meta.channel_slug, meta.title)

    try:
        if meta.source_type == SourceType.NAVER_BLOG:
            transcript_result = extract_blog_post_text(
                str(meta.source_url or _default_source_url(meta)),
                item_id=meta.video_id,
            )
        else:
            transcript_result = extract_transcript(meta.video_id, cache_dir=transcript_cache_dir)
    except TransientTranscriptFailure as e:
        logger.warning("[%s] transient text extraction failure, skipping: %s", meta.channel_slug, e.reason)
        return None
    except PermanentTranscriptFailure as e:
        logger.info("[%s] permanent text extraction failure: %s", meta.channel_slug, e.reason)
        briefing = build_briefing_from_permanent_failure(
            meta=meta,
            failure_code=e.failure_code,
            provider=summarizer.provider,
            model=summarizer.model,
            prompt_version=summarizer.prompt_version,
        )
        write_briefing(briefing, briefings_dir)
        return briefing

    effective_meta = meta
    if transcript_result.published_at is not None and transcript_result.published_at != meta.published_at:
        drift = abs(transcript_result.published_at - meta.published_at)
        if drift > timedelta(days=2):
            logger.warning(
                "[%s] rejecting published_at override (drift %s > 2d): discovery=%s, page=%s — keeping discovery value",
                meta.channel_slug,
                drift,
                meta.published_at.isoformat(),
                transcript_result.published_at.isoformat(),
            )
        else:
            logger.info(
                "[%s] overriding published_at from source page: %s -> %s",
                meta.channel_slug,
                meta.published_at.isoformat(),
                transcript_result.published_at.isoformat(),
            )
            effective_meta = meta.model_copy(update={"published_at": transcript_result.published_at})

    try:
        result = summarizer.summarize(transcript_result.text, effective_meta)
    except TransientSummarizerError as e:
        logger.warning("[%s] transient summarizer failure, skipping: %s", meta.channel_slug, e)
        return None
    except PermanentSummarizerError as e:
        if _is_retryable_summary_contract_failure(e):
            logger.warning(
                "[%s] summary contract failure, skipping for retry next run: %s",
                meta.channel_slug,
                e,
            )
            return None

        logger.info("[%s] permanent summarizer failure: %s", meta.channel_slug, e)
        briefing = build_briefing_from_permanent_failure(
            meta=effective_meta,
            failure_code=e.failure_code,
            provider=summarizer.provider,
            model=summarizer.model,
            prompt_version=summarizer.prompt_version,
        )
        write_briefing(briefing, briefings_dir)
        return briefing

    briefing = build_briefing_from_success(
        meta=effective_meta,
        summary=result.summary,
        provider=result.provider,
        model=result.model,
        prompt_version=result.prompt_version,
        summary_sections=result.summary_sections,
    )
    write_briefing(briefing, briefings_dir)
    return briefing


def _build_source_specs(
    config: AppConfig,
    known_by_channel: dict[str, set[str]],
    only_channel: str | None,
) -> list[SourceSpec]:
    specs: list[SourceSpec] = []
    for channel in config.channels:
        if only_channel and channel.slug != only_channel:
            logger.debug("[%s] skipped (only_channel=%s)", channel.slug, only_channel)
            continue
        specs.append(_youtube_source_spec(channel, known_by_channel))

    for blog in config.blogs:
        if only_channel and blog.slug != only_channel:
            logger.debug("[%s] skipped (only_channel=%s)", blog.slug, only_channel)
            continue
        specs.append(_blog_source_spec(blog, known_by_channel))

    return specs


def _youtube_source_spec(
    channel: ChannelConfig,
    known_by_channel: dict[str, set[str]],
) -> SourceSpec:
    return SourceSpec(
        kind="youtube",
        source_id=channel.id,
        slug=channel.slug,
        name=channel.name,
        known_video_ids=known_by_channel.get(channel.slug, set()),
    )


def _blog_source_spec(
    blog: BlogConfig,
    known_by_channel: dict[str, set[str]],
) -> SourceSpec:
    return SourceSpec(
        kind="naver_blog",
        source_id=blog.blog_id,
        slug=blog.slug,
        name=blog.name,
        known_video_ids=known_by_channel.get(blog.slug, set()),
    )


def _discover_source(
    source: SourceSpec,
    *,
    max_per_source: int,
    min_duration_seconds: int | None,
) -> DiscoveryOutcome:
    try:
        if source.kind == "youtube":
            items = discover_new_videos(
                channel_id=source.source_id,
                channel_slug=source.slug,
                channel_name=source.name,
                known_video_ids=source.known_video_ids,
                max_new_videos=max_per_source,
                min_duration_seconds=min_duration_seconds,
            )
        else:
            items = discover_new_blog_posts(
                blog_id=source.source_id,
                channel_slug=source.slug,
                channel_name=source.name,
                known_video_ids=source.known_video_ids,
                max_new_posts=max_per_source,
            )
    except DiscoveryFailure as e:
        logger.error("[%s] discovery failed, skipping source: %s", source.slug, e)
        return DiscoveryOutcome(source=source, items=[], failed=True)

    if not items:
        logger.info("[%s] no new items", source.slug)
    else:
        logger.info("[%s] %d new item(s) to process", source.slug, len(items))
    return DiscoveryOutcome(source=source, items=items)


def _discover_sources(
    sources: list[SourceSpec],
    pipeline_cfg: PipelineConfig,
) -> list[DiscoveryOutcome]:
    if not sources:
        return []

    max_workers = min(pipeline_cfg.max_discovery_concurrency, len(sources))
    if max_workers <= 1:
        return [
            _discover_source(
                source,
                max_per_source=pipeline_cfg.max_new_videos_per_channel,
                min_duration_seconds=pipeline_cfg.min_duration_seconds,
            )
            for source in sources
        ]

    outcomes: list[DiscoveryOutcome | None] = [None] * len(sources)
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_index = {
            executor.submit(
                _discover_source,
                source,
                max_per_source=pipeline_cfg.max_new_videos_per_channel,
                min_duration_seconds=pipeline_cfg.min_duration_seconds,
            ): index
            for index, source in enumerate(sources)
        }
        for future in as_completed(future_to_index):
            index = future_to_index[future]
            outcomes[index] = future.result()

    return [outcome for outcome in outcomes if outcome is not None]


def _plan_items_to_process(
    outcomes: list[DiscoveryOutcome],
    *,
    dry_run: bool,
    limit: int | None,
) -> tuple[list[VideoMeta], int]:
    planned: list[VideoMeta] = []
    dry_run_skipped = 0
    for outcome in outcomes:
        if outcome.failed:
            continue
        for meta in outcome.items:
            if limit is not None and len(planned) + dry_run_skipped >= limit:
                logger.info("limit=%d reached, stopping", limit)
                return planned, dry_run_skipped
            if dry_run:
                logger.info("[DRY-RUN] would process %s: %s", meta.video_id, meta.title)
                dry_run_skipped += 1
            else:
                planned.append(meta)
    return planned, dry_run_skipped


def _process_one_planned_item(
    meta: VideoMeta,
    *,
    pipeline_cfg: PipelineConfig,
    briefings_dir: Path,
    transcript_cache_dir: Path,
) -> Briefing | None:
    return process_video(
        meta=meta,
        summarizer=build_summarizer_from_config(pipeline_cfg),
        briefings_dir=briefings_dir,
        transcript_cache_dir=transcript_cache_dir,
    )


def _process_planned_items(
    planned_items: list[VideoMeta],
    *,
    pipeline_cfg: PipelineConfig,
    briefings_dir: Path,
    transcript_cache_dir: Path,
    known_by_channel: dict[str, set[str]],
) -> tuple[int, int]:
    if not planned_items:
        return 0, 0

    if pipeline_cfg.max_processing_concurrency <= 1:
        return _process_planned_items_sequentially(
            planned_items,
            pipeline_cfg=pipeline_cfg,
            briefings_dir=briefings_dir,
            transcript_cache_dir=transcript_cache_dir,
            known_by_channel=known_by_channel,
        )

    written = 0
    skipped = 0
    max_workers = min(pipeline_cfg.max_processing_concurrency, len(planned_items))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_meta = {
            executor.submit(
                _process_one_planned_item,
                meta,
                pipeline_cfg=pipeline_cfg,
                briefings_dir=briefings_dir,
                transcript_cache_dir=transcript_cache_dir,
            ): meta
            for meta in planned_items
        }
        for future in as_completed(future_to_meta):
            meta = future_to_meta[future]
            try:
                result = future.result()
            except Exception as e:  # noqa: BLE001
                logger.exception(
                    "[%s] unhandled exception processing %s, continuing to next item: %s",
                    meta.channel_slug,
                    meta.video_id,
                    e,
                )
                skipped += 1
                continue

            if result is None:
                skipped += 1
            else:
                written += 1
                known_by_channel.setdefault(meta.channel_slug, set()).add(meta.video_id)

    return written, skipped


def _process_planned_items_sequentially(
    planned_items: list[VideoMeta],
    *,
    pipeline_cfg: PipelineConfig,
    briefings_dir: Path,
    transcript_cache_dir: Path,
    known_by_channel: dict[str, set[str]],
) -> tuple[int, int]:
    summarizer = build_summarizer_from_config(pipeline_cfg)
    written = 0
    skipped = 0
    for meta in planned_items:
        try:
            result = process_video(
                meta=meta,
                summarizer=summarizer,
                briefings_dir=briefings_dir,
                transcript_cache_dir=transcript_cache_dir,
            )
        except Exception as e:  # noqa: BLE001
            logger.exception(
                "[%s] unhandled exception processing %s, continuing to next item: %s",
                meta.channel_slug,
                meta.video_id,
                e,
            )
            skipped += 1
            continue

        if result is None:
            skipped += 1
        else:
            written += 1
            known_by_channel.setdefault(meta.channel_slug, set()).add(meta.video_id)

    return written, skipped


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
        limit: Optional cap on total items processed this run. Useful for
            smoke tests and for gentle first runs after long absences.
        only_channel: Optional slug filter. When set, only that one source
            is processed; other sources are skipped with a log.

    Returns:
        Exit code (0 success, 2 all sources failed).
    """
    config = load_config(config_path)
    briefings_dir = Path(briefings_dir)
    briefings_dir.mkdir(parents=True, exist_ok=True)

    pipeline_cfg = config.pipeline
    transcript_cache_dir = Path(pipeline_cfg.transcript_cache_dir)
    transcript_cache_dir.mkdir(parents=True, exist_ok=True)

    # Per-channel known set so the saturation check is scoped correctly.
    # See list_processed_video_ids_by_channel for the rationale.
    known_by_channel = list_processed_video_ids_by_channel(briefings_dir)
    total_known = sum(len(v) for v in known_by_channel.values())
    sources = _build_source_specs(config, known_by_channel, only_channel)
    total_sources = len(sources)
    logger.info(
        "pipeline starting: %d sources (%d youtube + %d naver blog), %d known items total, max %d new per source, discovery_concurrency=%d, processing_concurrency=%d%s%s",
        total_sources,
        len([source for source in sources if source.kind == "youtube"]),
        len([source for source in sources if source.kind == "naver_blog"]),
        total_known,
        pipeline_cfg.max_new_videos_per_channel,
        pipeline_cfg.max_discovery_concurrency,
        pipeline_cfg.max_processing_concurrency,
        f", limit={limit}" if limit else "",
        f", only_channel={only_channel}" if only_channel else "",
    )

    outcomes = _discover_sources(sources, pipeline_cfg)
    sources_failed = sum(1 for outcome in outcomes if outcome.failed)
    planned_items, dry_run_skipped = _plan_items_to_process(
        outcomes,
        dry_run=dry_run,
        limit=limit,
    )
    total_written, process_skipped = _process_planned_items(
        planned_items,
        pipeline_cfg=pipeline_cfg,
        briefings_dir=briefings_dir,
        transcript_cache_dir=transcript_cache_dir,
        known_by_channel=known_by_channel,
    )
    total_skipped = dry_run_skipped + process_skipped

    logger.info(
        "pipeline complete: wrote %d, skipped %d, %d sources failed",
        total_written,
        total_skipped,
        sources_failed,
    )

    if total_sources > 0 and total_written == 0 and sources_failed == total_sources:
        return 2  # all sources failed, nothing written
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
