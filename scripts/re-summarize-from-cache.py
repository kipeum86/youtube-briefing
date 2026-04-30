#!/usr/bin/env python3
"""Re-summarize existing briefings from local transcript cache.

By default this script only processes items with
`data/transcripts/{video_id}.txt`. With `--fetch-missing`, it can refill missing
source text using the existing fetchers before summarizing. Successful runs back
up the current briefing JSON directory, then overwrite re-summarized items in
place.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import yaml

from pipeline.config import validate_config_dict  # noqa: E402
from pipeline.fetchers.naver_blog import extract_blog_post_text  # noqa: E402
from pipeline.fetchers.transcript_extractor import (  # noqa: E402
    PermanentTranscriptFailure,
    TransientTranscriptFailure,
    extract_transcript,
)
from pipeline.models import Briefing, BriefingStatus, SourceType, VideoMeta  # noqa: E402
from pipeline.summarizers.base import (  # noqa: E402
    PermanentSummarizerError,
    Summarizer,
    TransientSummarizerError,
    load_summarizer,
)
from pipeline.writers.json_store import write_briefing  # noqa: E402


NowFn = Callable[[], datetime]


@dataclass(frozen=True)
class ResummarizeTarget:
    path: Path
    briefing: Briefing
    transcript_path: Path


@dataclass(frozen=True)
class TargetSelection:
    targets: list[ResummarizeTarget]
    skipped_status: int = 0
    skipped_channel: int = 0
    skipped_missing_cache: int = 0


def main() -> int:
    load_dotenv_if_present()
    args = parse_args()
    config = load_config(args.config)
    pipeline_cfg = config["pipeline"]
    briefings_dir = resolve_repo_path(args.briefings_dir)
    transcript_cache_dir = resolve_repo_path(
        args.transcript_cache_dir
        or Path(pipeline_cfg.get("transcript_cache_dir", "data/transcripts"))
    )

    selection = select_targets(
        briefings_dir=briefings_dir,
        transcript_cache_dir=transcript_cache_dir,
        status_filter=args.status,
        only_channel=args.only_channel,
        only_ids=set(args.only_id or []),
        limit=args.limit,
        sort_key=args.sort_key,
        fetch_missing=args.fetch_missing,
    )

    summarizer = None if args.dry_run else build_summarizer(config, args.prompt_version)
    result = resummarize_selection(
        selection=selection,
        briefings_dir=briefings_dir,
        summarizer=summarizer,
        dry_run=args.dry_run,
        fetch_missing=args.fetch_missing,
    )

    print_report(result)
    if args.output_json:
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    return 1 if result["failed"] else 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Re-summarize briefing JSONs using cached transcripts only.",
    )
    parser.add_argument("--config", type=Path, default=REPO_ROOT / "config.yaml")
    parser.add_argument(
        "--briefings-dir",
        type=Path,
        default=REPO_ROOT / "data" / "briefings",
    )
    parser.add_argument("--transcript-cache-dir", type=Path)
    parser.add_argument(
        "--status",
        choices=["ok", "all"],
        default="ok",
        help="By default only re-summarize status=ok. Use all to retry failed placeholders too.",
    )
    parser.add_argument("--prompt-version", help="Override config pipeline.summarizer.prompt_version.")
    parser.add_argument("--only-channel", help="Only re-summarize one channel_slug.")
    parser.add_argument(
        "--only-id",
        action="append",
        help="Only re-summarize a specific video/post id. May be repeated.",
    )
    parser.add_argument("--limit", type=int, help="Maximum number of cached items to process.")
    parser.add_argument(
        "--sort",
        dest="sort_key",
        choices=["filename", "published_at"],
        default="filename",
        help="Target ordering. Default preserves the existing filename-desc behavior.",
    )
    parser.add_argument(
        "--fetch-missing",
        action="store_true",
        help="Fetch and cache missing source text instead of skipping missing transcript caches.",
    )
    parser.add_argument("--dry-run", action="store_true", help="List targets without calling Gemini or writing files.")
    parser.add_argument("--output-json", type=Path)
    return parser.parse_args()


def load_config(config_path: Path) -> dict[str, Any]:
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"config root must be a mapping: {config_path}")
    validate_config_dict(raw)
    return raw


def resolve_repo_path(path: Path) -> Path:
    return path if path.is_absolute() else REPO_ROOT / path


def load_dotenv_if_present() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(REPO_ROOT / ".env", override=False)


def build_summarizer(config: dict[str, Any], prompt_version: str | None = None) -> Summarizer:
    pipeline_cfg = config["pipeline"]
    summarizer_cfg = pipeline_cfg["summarizer"]

    summarizer = load_summarizer(
        provider=summarizer_cfg["provider"],
        model=summarizer_cfg["model"],
        prompt_version=prompt_version or summarizer_cfg.get("prompt_version", "v1"),
        repair_model=summarizer_cfg.get("repair_model"),
        output_format=summarizer_cfg.get("output_format", "free"),
        temperature=summarizer_cfg.get("temperature"),
        max_output_tokens=summarizer_cfg.get("max_output_tokens", 1600),
        request_timeout_seconds=summarizer_cfg.get("request_timeout_seconds", 90),
        transient_retries=summarizer_cfg.get("transient_retries", 2),
        transient_backoff_seconds=summarizer_cfg.get("transient_backoff_seconds", 5),
    )
    summarizer.min_chars = pipeline_cfg.get("summary_min_chars", 700)
    summarizer.max_chars = pipeline_cfg.get("summary_max_chars", 1200)
    summarizer.headline_max_chars = pipeline_cfg.get("summary_headline_max_chars", 24)
    summarizer.max_retries_on_short = summarizer_cfg.get("short_output_retries", 1)
    summarizer.max_format_repair_attempts = summarizer_cfg.get("repair_attempts", 1)
    summarizer.max_full_retries = summarizer_cfg.get("full_retries", 1)
    summarizer.context_max_chars = pipeline_cfg.get("context_max_chars", 30_000)
    return summarizer


def select_targets(
    *,
    briefings_dir: Path,
    transcript_cache_dir: Path,
    status_filter: str = "ok",
    only_channel: str | None = None,
    only_ids: set[str] | None = None,
    limit: int | None = None,
    sort_key: str = "filename",
    fetch_missing: bool = False,
) -> TargetSelection:
    targets: list[ResummarizeTarget] = []
    skipped_status = 0
    skipped_channel = 0
    skipped_missing_cache = 0
    only_ids = only_ids or set()

    entries: list[tuple[Path, Briefing]] = []
    for path in sorted(briefings_dir.glob("*.json"), reverse=True):
        briefing = Briefing.model_validate_json(path.read_text(encoding="utf-8"))
        entries.append((path, briefing))

    if sort_key == "published_at":
        entries.sort(key=lambda entry: (entry[1].published_at, entry[0].name), reverse=True)
    elif sort_key != "filename":
        raise ValueError(f"unknown sort_key: {sort_key}")

    for path, briefing in entries:

        if status_filter == "ok" and briefing.status != BriefingStatus.OK:
            skipped_status += 1
            continue
        if only_channel and briefing.channel_slug != only_channel:
            skipped_channel += 1
            continue
        if only_ids and briefing.video_id not in only_ids:
            skipped_channel += 1
            continue

        transcript_path = transcript_cache_dir / f"{briefing.video_id}.txt"
        if not transcript_path.exists():
            if not fetch_missing:
                skipped_missing_cache += 1
                continue

        targets.append(
            ResummarizeTarget(
                path=path,
                briefing=briefing,
                transcript_path=transcript_path,
            )
        )
        if limit is not None and len(targets) >= limit:
            break

    return TargetSelection(
        targets=targets,
        skipped_status=skipped_status,
        skipped_channel=skipped_channel,
        skipped_missing_cache=skipped_missing_cache,
    )


def resummarize_selection(
    *,
    selection: TargetSelection,
    briefings_dir: Path,
    summarizer: Summarizer | None,
    dry_run: bool = False,
    fetch_missing: bool = False,
    now_fn: NowFn | None = None,
) -> dict[str, Any]:
    now_fn = now_fn or _utc_now
    result: dict[str, Any] = {
        "dry_run": dry_run,
        "target_count": len(selection.targets),
        "written": 0,
        "failed": 0,
        "skipped_status": selection.skipped_status,
        "skipped_channel": selection.skipped_channel,
        "skipped_missing_cache": selection.skipped_missing_cache,
        "backup_dir": None,
        "items": [],
    }

    if dry_run or not selection.targets:
        result["items"] = [
            _item_row(target, status="would_process") for target in selection.targets
        ]
        return result

    if summarizer is None:
        raise ValueError("summarizer is required unless dry_run=True")

    backup_dir = create_backup(briefings_dir, now_fn=now_fn)
    result["backup_dir"] = str(backup_dir)

    for target in selection.targets:
        try:
            transcript = read_or_fetch_transcript(target, fetch_missing=fetch_missing)
            summary_result = summarizer.summarize(
                transcript,
                briefing_to_video_meta(target.briefing),
            )
            updated = target.briefing.model_copy(
                update={
                    "status": BriefingStatus.OK,
                    "summary": summary_result.summary,
                    "summary_sections": summary_result.summary_sections,
                    "failure_reason": None,
                    "generated_at": now_fn(),
                    "provider": summary_result.provider,
                    "model": summary_result.model,
                    "prompt_version": summary_result.prompt_version,
                }
            )
            write_briefing(updated, briefings_dir)
            result["written"] += 1
            result["items"].append(_item_row(target, status="written"))
        except (
            FileNotFoundError,
            PermanentSummarizerError,
            PermanentTranscriptFailure,
            TransientSummarizerError,
            TransientTranscriptFailure,
            ValueError,
        ) as exc:
            result["failed"] += 1
            result["items"].append(
                _item_row(target, status="failed", error=f"{type(exc).__name__}: {exc}")
            )

    return result


def read_or_fetch_transcript(
    target: ResummarizeTarget,
    *,
    fetch_missing: bool = False,
) -> str:
    """Read cached source text, optionally fetching and caching missing text."""

    if target.transcript_path.exists():
        return target.transcript_path.read_text(encoding="utf-8")
    if not fetch_missing:
        raise FileNotFoundError(f"missing transcript cache: {target.transcript_path}")

    briefing = target.briefing
    if briefing.source_type == SourceType.NAVER_BLOG:
        result = extract_blog_post_text(str(briefing.video_url), item_id=briefing.video_id)
        _write_transcript_cache(target.transcript_path, result.text)
        return result.text

    result = extract_transcript(
        briefing.video_id,
        cache_dir=target.transcript_path.parent,
    )
    return result.text


def _write_transcript_cache(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def create_backup(briefings_dir: Path, now_fn: NowFn | None = None) -> Path:
    now_fn = now_fn or _utc_now
    stamp = now_fn().strftime("%Y%m%d-%H%M%S")
    backup_dir = briefings_dir.parent / f"{briefings_dir.name}.backup-{stamp}"
    shutil.copytree(briefings_dir, backup_dir)
    return backup_dir


def briefing_to_video_meta(briefing: Briefing) -> VideoMeta:
    return VideoMeta(
        video_id=briefing.video_id,
        channel_id=f"resummarize-{briefing.channel_slug}",
        channel_slug=briefing.channel_slug,
        channel_name=briefing.channel_name,
        title=briefing.title,
        published_at=briefing.published_at,
        discovery_source=briefing.discovery_source,
        source_type=briefing.source_type,
        source_url=briefing.video_url,
        thumbnail_url=briefing.thumbnail_url,
        duration_seconds=briefing.duration_seconds,
    )


def print_report(result: dict[str, Any]) -> None:
    prefix = "dry-run" if result["dry_run"] else "run"
    print(
        f"{prefix}: targets={result['target_count']} written={result['written']} "
        f"failed={result['failed']} skipped_missing_cache={result['skipped_missing_cache']}"
    )
    if result["backup_dir"]:
        print(f"backup: {result['backup_dir']}")
    for item in result["items"]:
        suffix = f" ({item['error']})" if item.get("error") else ""
        print(f"- {item['status']}: {item['channel_slug']}/{item['video_id']}{suffix}")


def _item_row(
    target: ResummarizeTarget,
    *,
    status: str,
    error: str | None = None,
) -> dict[str, str]:
    row = {
        "status": status,
        "video_id": target.briefing.video_id,
        "channel_slug": target.briefing.channel_slug,
        "path": str(target.path),
        "transcript_path": str(target.transcript_path),
    }
    if error:
        row["error"] = error
    return row


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


if __name__ == "__main__":
    raise SystemExit(main())
