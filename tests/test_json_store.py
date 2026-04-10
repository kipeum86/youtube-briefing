"""Tests for json_store — Pydantic-validated writes, atomic, glob-based dedup."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from pipeline.models import (
    Briefing,
    BriefingStatus,
    DiscoverySource,
    FailureReason,
)
from pipeline.writers.json_store import (
    briefing_filename,
    iter_briefings,
    list_processed_video_ids,
    list_processed_video_ids_by_channel,
    write_briefing,
)


def _make_ok_briefing(video_id="abc123XYZ45", slug="shuka", **overrides) -> Briefing:
    defaults = dict(
        video_id=video_id,
        channel_slug=slug,
        channel_name="슈카월드",
        title="美 연준 금리인하 시그널",
        published_at=datetime(2026, 4, 9, 3, 0, 0, tzinfo=timezone.utc),
        video_url=f"https://www.youtube.com/watch?v={video_id}",
        thumbnail_url=f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg",
        duration_seconds=1847,
        discovery_source=DiscoverySource.RSS,
        status=BriefingStatus.OK,
        summary="파월 의장의 최근 발언에서 주목할 점은 표면적 인하 신호가 아니라 그 이면의 조건부 단서들이다. 슈카월드는 이번 영상에서 시장이 75bp 인하를 기정사실로 받아들인 순간부터 장기 금리가 오히려 상승하기 시작한 역설을 지적한다.",
        failure_reason=None,
        generated_at=datetime(2026, 4, 9, 21, 15, 0, tzinfo=timezone.utc),
        provider="gemini",
        model="gemini-2.5-flash",
        prompt_version="v1",
    )
    defaults.update(overrides)
    return Briefing(**defaults)


def _make_failed_briefing(video_id="failed123XY", slug="shuka") -> Briefing:
    return Briefing(
        video_id=video_id,
        channel_slug=slug,
        channel_name="슈카월드",
        title="멤버십 전용",
        published_at=datetime(2026, 4, 8, 3, 0, 0, tzinfo=timezone.utc),
        video_url=f"https://www.youtube.com/watch?v={video_id}",
        thumbnail_url=f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg",
        duration_seconds=1200,
        discovery_source=DiscoverySource.RSS,
        status=BriefingStatus.FAILED,
        summary=None,
        failure_reason=FailureReason.MEMBERS_ONLY,
        generated_at=datetime(2026, 4, 8, 21, 15, 0, tzinfo=timezone.utc),
        provider="gemini",
        model="gemini-2.5-flash",
        prompt_version="v1",
    )


class TestBriefingFilename:
    def test_canonical_format(self):
        b = _make_ok_briefing(video_id="abc123XYZ45", slug="shuka")
        # published_at is 2026-04-09 03:00 UTC = 2026-04-09 12:00 KST → "2026-04-09"
        assert briefing_filename(b) == "2026-04-09-shuka-abc123XYZ45.json"

    def test_kst_date_from_utc_late_night(self):
        """UTC 2026-04-09 16:00 = KST 2026-04-10 01:00 → date should be 04-10."""
        b = _make_ok_briefing(
            published_at=datetime(2026, 4, 9, 16, 0, 0, tzinfo=timezone.utc)
        )
        assert "2026-04-10" in briefing_filename(b)


class TestWriteBriefing:
    def test_write_ok_briefing(self, tmp_path: Path):
        b = _make_ok_briefing()
        path = write_briefing(b, tmp_path)

        assert path.exists()
        assert path.name == "2026-04-09-shuka-abc123XYZ45.json"

        # Round-trip: read back and verify content
        loaded = Briefing.model_validate_json(path.read_text(encoding="utf-8"))
        assert loaded.video_id == b.video_id
        assert loaded.summary == b.summary

    def test_write_failed_briefing(self, tmp_path: Path):
        b = _make_failed_briefing()
        path = write_briefing(b, tmp_path)
        assert path.exists()
        loaded = Briefing.model_validate_json(path.read_text(encoding="utf-8"))
        assert loaded.status == BriefingStatus.FAILED
        assert loaded.failure_reason == FailureReason.MEMBERS_ONLY
        assert loaded.summary is None

    def test_write_is_atomic_no_tmp_leftover(self, tmp_path: Path):
        b = _make_ok_briefing()
        write_briefing(b, tmp_path)

        tmp_files = list(tmp_path.glob("*.tmp"))
        assert tmp_files == []

    def test_write_creates_dir_if_missing(self, tmp_path: Path):
        nested = tmp_path / "data" / "briefings"
        # Directory doesn't exist yet
        b = _make_ok_briefing()
        path = write_briefing(b, nested)
        assert nested.exists()
        assert path.exists()

    def test_write_overwrites_existing_file(self, tmp_path: Path):
        b = _make_ok_briefing()
        write_briefing(b, tmp_path)
        # Overwrite with new content
        b2 = _make_ok_briefing(
            title="완전히 새로운 제목",
        )
        write_briefing(b2, tmp_path)

        files = list(tmp_path.glob("*.json"))
        assert len(files) == 1
        loaded = Briefing.model_validate_json(files[0].read_text(encoding="utf-8"))
        assert loaded.title == "완전히 새로운 제목"


class TestListProcessedVideoIds:
    def test_empty_dir_returns_empty_set(self, tmp_path: Path):
        assert list_processed_video_ids(tmp_path) == set()

    def test_nonexistent_dir_returns_empty_set(self, tmp_path: Path):
        assert list_processed_video_ids(tmp_path / "not-there") == set()

    def test_extracts_video_ids_from_filenames(self, tmp_path: Path):
        write_briefing(_make_ok_briefing(video_id="abc123XYZ45"), tmp_path)
        write_briefing(_make_ok_briefing(video_id="def456QRS78", slug="shuka"), tmp_path)
        write_briefing(_make_failed_briefing(video_id="fail789UVW"), tmp_path)

        ids = list_processed_video_ids(tmp_path)
        assert ids == {"abc123XYZ45", "def456QRS78", "fail789UVW"}

    def test_ignores_non_json_files(self, tmp_path: Path):
        write_briefing(_make_ok_briefing(), tmp_path)
        (tmp_path / "README.md").write_text("not a briefing")
        (tmp_path / "not-a-briefing.txt").write_text("text file")

        ids = list_processed_video_ids(tmp_path)
        assert ids == {"abc123XYZ45"}

    def test_ignores_malformed_filenames(self, tmp_path: Path):
        write_briefing(_make_ok_briefing(), tmp_path)
        # Manually create a malformed briefing-like file
        (tmp_path / "totally-wrong.json").write_text("{}")
        (tmp_path / "2026-04-09-incomplete.json").write_text("{}")

        ids = list_processed_video_ids(tmp_path)
        assert ids == {"abc123XYZ45"}


class TestListProcessedVideoIdsByChannel:
    """Per-channel mapping — scopes the saturation check to one channel.

    Regression: the global known set caused false-positive saturation across
    channels. A video_id from channel A can never appear in channel B's RSS
    feed, so including A's IDs in B's known set always fails the "any RSS
    match?" check, triggering needless yt-dlp catchup on B.
    """

    def test_empty_dir_returns_empty_dict(self, tmp_path: Path):
        assert list_processed_video_ids_by_channel(tmp_path) == {}

    def test_nonexistent_dir_returns_empty_dict(self, tmp_path: Path):
        assert list_processed_video_ids_by_channel(tmp_path / "not-there") == {}

    def test_groups_video_ids_by_channel_slug(self, tmp_path: Path):
        write_briefing(_make_ok_briefing(video_id="shuka0001XY", slug="shuka"), tmp_path)
        write_briefing(_make_ok_briefing(video_id="shuka0002XY", slug="shuka"), tmp_path)
        write_briefing(_make_ok_briefing(video_id="parkjh001XY", slug="parkjonghoon"), tmp_path)

        by_channel = list_processed_video_ids_by_channel(tmp_path)
        assert by_channel == {
            "shuka": {"shuka0001XY", "shuka0002XY"},
            "parkjonghoon": {"parkjh001XY"},
        }

    def test_channel_with_no_briefings_absent_from_result(self, tmp_path: Path):
        """Unprocessed channels don't appear — callers use .get(slug, set())."""
        write_briefing(_make_ok_briefing(video_id="shuka0001XY", slug="shuka"), tmp_path)

        by_channel = list_processed_video_ids_by_channel(tmp_path)
        assert "understanding" not in by_channel
        assert by_channel.get("understanding", set()) == set()

    def test_ignores_malformed_filenames(self, tmp_path: Path):
        write_briefing(_make_ok_briefing(video_id="shuka0001XY", slug="shuka"), tmp_path)
        (tmp_path / "totally-wrong.json").write_text("{}")
        (tmp_path / "2026-04-09-incomplete.json").write_text("{}")

        by_channel = list_processed_video_ids_by_channel(tmp_path)
        assert by_channel == {"shuka": {"shuka0001XY"}}


class TestIterBriefings:
    def test_empty_dir_yields_nothing(self, tmp_path: Path):
        assert list(iter_briefings(tmp_path)) == []

    def test_iterates_in_reverse_filename_order(self, tmp_path: Path):
        """Newest dates first (reverse alphabetical filename sort)."""
        write_briefing(
            _make_ok_briefing(
                video_id="older11XYZ", slug="shuka",
                published_at=datetime(2026, 4, 7, 3, 0, 0, tzinfo=timezone.utc),
            ),
            tmp_path,
        )
        write_briefing(
            _make_ok_briefing(
                video_id="newer22XYZ", slug="shuka",
                published_at=datetime(2026, 4, 9, 3, 0, 0, tzinfo=timezone.utc),
            ),
            tmp_path,
        )

        briefings = list(iter_briefings(tmp_path))
        assert len(briefings) == 2
        assert briefings[0].video_id == "newer22XYZ"  # newest first
        assert briefings[1].video_id == "older11XYZ"

    def test_skips_corrupted_json(self, tmp_path: Path):
        write_briefing(_make_ok_briefing(), tmp_path)
        # Create a file matching the pattern but with invalid JSON
        (tmp_path / "2026-04-09-shuka-corrupt99999.json").write_text("not valid json")

        briefings = list(iter_briefings(tmp_path))
        assert len(briefings) == 1  # corrupted one is skipped with a log
