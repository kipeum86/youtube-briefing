#!/usr/bin/env python3
"""Select a deterministic transcript golden set for prompt evaluation."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter, defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


BUCKET_ORDER = ["<10k", "10k~20k", "20k~30k", ">30k"]


@dataclass(frozen=True)
class GoldenCandidate:
    video_id: str
    transcript_path: Path
    chars: int
    sha256: str
    length_bucket: str
    channel_slug: str
    channel_name: str
    source_type: str
    title: str


def main() -> int:
    args = parse_args()
    candidates = collect_candidates(
        transcripts_dir=args.transcripts_dir,
        briefings_dir=args.briefings_dir,
    )
    selected = select_golden_set(
        candidates,
        target_size=args.target_size,
        naver_blog_min=args.naver_blog_min,
    )
    manifest = build_manifest(
        selected,
        target_size=args.target_size,
        transcripts_dir=args.transcripts_dir,
        briefings_dir=args.briefings_dir,
    )

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    print_text_report(manifest)
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Select deterministic transcript samples for prompt evaluation.",
    )
    parser.add_argument(
        "--transcripts-dir",
        type=Path,
        default=REPO_ROOT / "data" / "transcripts",
        help="Directory containing cached transcript .txt files.",
    )
    parser.add_argument(
        "--briefings-dir",
        type=Path,
        default=REPO_ROOT / "data" / "briefings",
        help="Directory containing briefing JSON files used for metadata.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=REPO_ROOT / "tests" / "eval" / "transcripts" / "manifest.json",
        help="Manifest path to write.",
    )
    parser.add_argument("--target-size", type=int, default=20)
    parser.add_argument(
        "--naver-blog-min",
        type=int,
        default=2,
        help="Prefer at least this many Naver blog samples when cached transcripts exist.",
    )
    return parser.parse_args()


def collect_candidates(
    *,
    transcripts_dir: Path,
    briefings_dir: Path,
) -> list[GoldenCandidate]:
    metadata = load_briefing_metadata(briefings_dir)
    candidates: list[GoldenCandidate] = []

    for path in sorted(transcripts_dir.glob("*.txt")):
        text = path.read_text(encoding="utf-8")
        video_id = path.stem
        meta = metadata.get(video_id, {})
        candidates.append(
            GoldenCandidate(
                video_id=video_id,
                transcript_path=path,
                chars=len(text),
                sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
                length_bucket=length_bucket(len(text)),
                channel_slug=meta.get("channel_slug", "unknown"),
                channel_name=meta.get("channel_name", "Unknown"),
                source_type=meta.get("source_type", "youtube"),
                title=meta.get("title", video_id),
            )
        )

    return candidates


def load_briefing_metadata(briefings_dir: Path) -> dict[str, dict[str, Any]]:
    metadata: dict[str, dict[str, Any]] = {}
    for path in sorted(briefings_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        video_id = data.get("video_id")
        if isinstance(video_id, str):
            metadata[video_id] = data
    return metadata


def select_golden_set(
    candidates: list[GoldenCandidate],
    *,
    target_size: int = 20,
    naver_blog_min: int = 2,
) -> list[GoldenCandidate]:
    if target_size < 1:
        raise ValueError("target_size must be >= 1")

    selected: list[GoldenCandidate] = []
    selected_ids: set[str] = set()

    naver_candidates = [
        candidate for candidate in candidates if candidate.source_type == "naver_blog"
    ]
    for candidate in sorted_candidates(naver_candidates)[:naver_blog_min]:
        if len(selected) >= target_size:
            break
        selected.append(candidate)
        selected_ids.add(candidate.video_id)

    for bucket in BUCKET_ORDER:
        if len(selected) >= target_size:
            break
        if any(candidate.length_bucket == bucket for candidate in selected):
            continue
        bucket_candidates = [
            candidate
            for candidate in candidates
            if candidate.length_bucket == bucket and candidate.video_id not in selected_ids
        ]
        if not bucket_candidates:
            continue
        candidate = sorted_candidates(bucket_candidates)[0]
        selected.append(candidate)
        selected_ids.add(candidate.video_id)

    remaining = [
        candidate for candidate in candidates if candidate.video_id not in selected_ids
    ]
    grouped: dict[str, deque[GoldenCandidate]] = defaultdict(deque)
    for candidate in sorted_candidates(remaining):
        grouped[candidate.channel_slug].append(candidate)

    while len(selected) < target_size and grouped:
        for group in sorted(list(grouped)):
            if len(selected) >= target_size:
                break
            queue = grouped[group]
            if not queue:
                del grouped[group]
                continue
            candidate = queue.popleft()
            selected.append(candidate)
            selected_ids.add(candidate.video_id)
            if not queue:
                del grouped[group]

    return selected


def sorted_candidates(candidates: list[GoldenCandidate]) -> list[GoldenCandidate]:
    return sorted(
        candidates,
        key=lambda candidate: (
            candidate.channel_slug,
            BUCKET_ORDER.index(candidate.length_bucket),
            -candidate.chars,
            candidate.video_id,
        ),
    )


def build_manifest(
    selected: list[GoldenCandidate],
    *,
    target_size: int,
    transcripts_dir: Path,
    briefings_dir: Path,
) -> dict[str, Any]:
    items = [
        {
            "video_id": candidate.video_id,
            "channel_slug": candidate.channel_slug,
            "channel_name": candidate.channel_name,
            "source_type": candidate.source_type,
            "title": candidate.title,
            "transcript_path": display_path(candidate.transcript_path),
            "chars": candidate.chars,
            "length_bucket": candidate.length_bucket,
            "sha256": candidate.sha256,
        }
        for candidate in selected
    ]

    return {
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "target_size": target_size,
        "selected_count": len(selected),
        "transcripts_dir": display_path(transcripts_dir),
        "briefings_dir": display_path(briefings_dir),
        "bucket_order": BUCKET_ORDER,
        "counts_by_channel": dict(Counter(item["channel_slug"] for item in items)),
        "counts_by_bucket": {
            bucket: sum(1 for item in items if item["length_bucket"] == bucket)
            for bucket in BUCKET_ORDER
        },
        "items": items,
    }


def length_bucket(chars: int) -> str:
    if chars < 10_000:
        return "<10k"
    if chars < 20_000:
        return "10k~20k"
    if chars < 30_000:
        return "20k~30k"
    return ">30k"


def display_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(REPO_ROOT))
    except ValueError:
        return str(resolved)


def print_text_report(manifest: dict[str, Any]) -> None:
    print(f"selected: {manifest['selected_count']} / {manifest['target_size']}")
    print("by channel:")
    for channel, count in sorted(manifest["counts_by_channel"].items()):
        print(f"  {channel}: {count}")
    print("by length bucket:")
    for bucket in BUCKET_ORDER:
        print(f"  {bucket}: {manifest['counts_by_bucket'].get(bucket, 0)}")


if __name__ == "__main__":
    raise SystemExit(main())
