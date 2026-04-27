"""Tests for scripts/select-golden-set.py."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "select-golden-set.py"
SPEC = importlib.util.spec_from_file_location("select_golden_set", SCRIPT)
assert SPEC and SPEC.loader
select_golden_set = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = select_golden_set
SPEC.loader.exec_module(select_golden_set)


def _write_transcript(path: Path, video_id: str, chars: int) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / f"{video_id}.txt").write_text("가" * chars, encoding="utf-8")


def _write_briefing(
    path: Path,
    video_id: str,
    channel_slug: str,
    *,
    source_type: str = "youtube",
) -> None:
    path.mkdir(parents=True, exist_ok=True)
    data = {
        "video_id": video_id,
        "channel_slug": channel_slug,
        "channel_name": channel_slug.upper(),
        "title": f"title {video_id}",
        "source_type": source_type,
    }
    (path / f"2026-01-01-{channel_slug}-{video_id}.json").write_text(
        json.dumps(data, ensure_ascii=False),
        encoding="utf-8",
    )


def test_collect_candidates_uses_briefing_metadata_and_buckets(tmp_path: Path):
    transcripts_dir = tmp_path / "transcripts"
    briefings_dir = tmp_path / "briefings"
    _write_transcript(transcripts_dir, "vid001", 9500)
    _write_transcript(transcripts_dir, "vid002", 30_500)
    _write_briefing(briefings_dir, "vid001", "shuka")
    _write_briefing(briefings_dir, "vid002", "mer", source_type="naver_blog")

    candidates = select_golden_set.collect_candidates(
        transcripts_dir=transcripts_dir,
        briefings_dir=briefings_dir,
    )

    by_id = {candidate.video_id: candidate for candidate in candidates}
    assert by_id["vid001"].channel_slug == "shuka"
    assert by_id["vid001"].length_bucket == "<10k"
    assert by_id["vid002"].source_type == "naver_blog"
    assert by_id["vid002"].length_bucket == ">30k"
    assert len(by_id["vid001"].sha256) == 64


def test_select_golden_set_prefers_naver_then_round_robins_channels(tmp_path: Path):
    transcripts_dir = tmp_path / "transcripts"
    briefings_dir = tmp_path / "briefings"
    for video_id, slug, source_type in [
        ("naver1", "mer", "naver_blog"),
        ("shuka1", "shuka", "youtube"),
        ("shuka2", "shuka", "youtube"),
        ("under1", "understanding", "youtube"),
    ]:
        _write_transcript(transcripts_dir, video_id, 12_000)
        _write_briefing(briefings_dir, video_id, slug, source_type=source_type)

    candidates = select_golden_set.collect_candidates(
        transcripts_dir=transcripts_dir,
        briefings_dir=briefings_dir,
    )
    selected = select_golden_set.select_golden_set(
        candidates,
        target_size=3,
        naver_blog_min=1,
    )

    assert selected[0].video_id == "naver1"
    assert {candidate.channel_slug for candidate in selected} == {
        "mer",
        "shuka",
        "understanding",
    }


def test_select_golden_set_covers_existing_length_buckets(tmp_path: Path):
    transcripts_dir = tmp_path / "transcripts"
    briefings_dir = tmp_path / "briefings"
    samples = [
        ("short1", "shuka", 5_000),
        ("mid1", "shuka", 12_000),
        ("long1", "understanding", 22_000),
        ("verylong1", "parkjonghoon", 32_000),
        ("extra1", "shuka", 13_000),
    ]
    for video_id, slug, chars in samples:
        _write_transcript(transcripts_dir, video_id, chars)
        _write_briefing(briefings_dir, video_id, slug)

    candidates = select_golden_set.collect_candidates(
        transcripts_dir=transcripts_dir,
        briefings_dir=briefings_dir,
    )
    selected = select_golden_set.select_golden_set(
        candidates,
        target_size=4,
        naver_blog_min=0,
    )

    assert {candidate.length_bucket for candidate in selected} == {
        "<10k",
        "10k~20k",
        "20k~30k",
        ">30k",
    }


def test_build_manifest_counts_channels_and_buckets(tmp_path: Path):
    transcripts_dir = tmp_path / "transcripts"
    briefings_dir = tmp_path / "briefings"
    _write_transcript(transcripts_dir, "vid001", 9500)
    _write_briefing(briefings_dir, "vid001", "shuka")
    candidate = select_golden_set.collect_candidates(
        transcripts_dir=transcripts_dir,
        briefings_dir=briefings_dir,
    )[0]

    manifest = select_golden_set.build_manifest(
        [candidate],
        target_size=2,
        transcripts_dir=transcripts_dir,
        briefings_dir=briefings_dir,
    )

    assert manifest["selected_count"] == 1
    assert manifest["target_size"] == 2
    assert manifest["counts_by_channel"] == {"shuka": 1}
    assert manifest["counts_by_bucket"]["<10k"] == 1
    assert Path(manifest["items"][0]["transcript_path"]).is_absolute()
