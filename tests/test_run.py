"""Integration tests for pipeline/run.py — full orchestrator with all externals mocked.

Includes the 3 mandatory regression tests from the eng review:
  1. Permanent transcript failure on video 3 does not halt videos 4+
  2. Unhandled exception on video 3 does not halt videos 4+
  3. Channel RSS fetch failure does not halt other channels
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml

from pipeline import run
from pipeline.fetchers.discovery import DiscoveryFailure
from pipeline.fetchers.transcript_extractor import (
    PermanentTranscriptFailure,
    TranscriptResult,
    TransientTranscriptFailure,
)
from pipeline.models import BriefingStatus, DiscoverySource, VideoMeta
from pipeline.summarizers.base import (
    PermanentSummarizerError,
    SummarizerResult,
    TransientSummarizerError,
)


def _make_meta(vid: str, slug: str = "shuka", channel_name: str = "슈카월드") -> VideoMeta:
    return VideoMeta(
        video_id=vid,
        channel_id="UCsT0YIqwnpJCM-mx7-gSA4Q",
        channel_slug=slug,
        channel_name=channel_name,
        title=f"Test video {vid}",
        published_at=datetime(2026, 4, 9, tzinfo=timezone.utc),
        discovery_source=DiscoverySource.RSS,
        duration_seconds=1200,
    )


def _write_config(tmp_path: Path, channels: list[dict]) -> Path:
    config = {
        "pipeline": {
            "summarizer": {"provider": "gemini", "model": "gemini-2.5-flash", "prompt_version": "v1"},
            "summary_min_chars": 500,
            "summary_max_chars": 1000,
            "transcript_cache_dir": str(tmp_path / "transcripts"),
            "log_dir": str(tmp_path / "logs"),
        },
        "channels": channels,
    }
    path = tmp_path / "config.yaml"
    path.write_text(yaml.dump(config), encoding="utf-8")
    return path


@pytest.fixture
def fake_summarizer(monkeypatch):
    """A fake summarizer that returns Korean summaries by default.

    The fixture summary is ~800 chars (15 repeats × ~55 chars = ~830),
    safely inside the 700-1200 target window.
    """
    summarizer = MagicMock()
    summarizer.provider = "gemini"
    summarizer.model = "gemini-2.5-flash"
    summarizer.prompt_version = "v1"
    summarizer.summarize.return_value = SummarizerResult(
        summary="파월 의장의 발언에서 주목할 점은 표면적 인하 신호가 아니라 그 이면의 조건부 단서들이다. "
        * 15,
        provider="gemini",
        model="gemini-2.5-flash",
        prompt_version="v1",
    )

    monkeypatch.setattr(run, "load_summarizer", lambda **kw: summarizer)
    return summarizer


class TestLoadConfig:
    def test_valid_config_loads(self, tmp_path: Path):
        config_path = _write_config(
            tmp_path,
            channels=[
                {"id": "UCsT0YIqwnpJCM-mx7-gSA4Q", "name": "슈카월드", "slug": "shuka"},
            ],
        )
        config = run.load_config(config_path)
        assert "pipeline" in config
        assert len(config["channels"]) == 1

    def test_missing_file_raises(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError):
            run.load_config(tmp_path / "nope.yaml")

    def test_empty_channel_id_raises(self, tmp_path: Path):
        config_path = _write_config(
            tmp_path,
            channels=[{"id": "", "name": "슈카월드", "slug": "shuka"}],
        )
        with pytest.raises(ValueError, match="empty id"):
            run.load_config(config_path)

    def test_missing_pipeline_section_raises(self, tmp_path: Path):
        path = tmp_path / "config.yaml"
        path.write_text("channels: []\n")
        with pytest.raises(ValueError, match="missing required sections"):
            run.load_config(path)


class TestProcessVideo:
    def test_happy_path_writes_ok_briefing(self, tmp_path: Path, fake_summarizer, monkeypatch):
        monkeypatch.setattr(
            run,
            "extract_transcript",
            lambda vid, cache_dir: TranscriptResult(
                text="트랜스크립트 " * 100, source="transcript_api_stenographer"
            ),
        )

        result = run.process_video(
            meta=_make_meta("vid123XYZ45"),
            summarizer=fake_summarizer,
            briefings_dir=tmp_path / "briefings",
            transcript_cache_dir=None,
        )
        assert result is not None
        assert result.status == BriefingStatus.OK
        assert (tmp_path / "briefings" / "2026-04-09-shuka-vid123XYZ45.json").exists()

    def test_transient_transcript_failure_returns_none(self, tmp_path: Path, fake_summarizer, monkeypatch):
        def raise_transient(*a, **kw):
            raise TransientTranscriptFailure("vid123XYZ45", "network timeout")

        monkeypatch.setattr(run, "extract_transcript", raise_transient)

        result = run.process_video(
            meta=_make_meta("vid123XYZ45"),
            summarizer=fake_summarizer,
            briefings_dir=tmp_path / "briefings",
            transcript_cache_dir=None,
        )
        # Transient: no write, no briefing returned
        assert result is None
        assert not list((tmp_path / "briefings").glob("*.json")) if (tmp_path / "briefings").exists() else True

    def test_permanent_transcript_failure_writes_placeholder(self, tmp_path: Path, fake_summarizer, monkeypatch):
        def raise_permanent(*a, **kw):
            raise PermanentTranscriptFailure("vid123XYZ45", "members only", "members_only")

        monkeypatch.setattr(run, "extract_transcript", raise_permanent)

        result = run.process_video(
            meta=_make_meta("vid123XYZ45"),
            summarizer=fake_summarizer,
            briefings_dir=tmp_path / "briefings",
            transcript_cache_dir=None,
        )
        assert result is not None
        assert result.status == BriefingStatus.FAILED
        assert result.failure_reason.value == "members_only"

    def test_transient_summarizer_failure_returns_none(self, tmp_path: Path, fake_summarizer, monkeypatch):
        monkeypatch.setattr(
            run,
            "extract_transcript",
            lambda vid, cache_dir: TranscriptResult(text="트랜스크립트 " * 100, source="transcript_api_stenographer"),
        )
        fake_summarizer.summarize.side_effect = TransientSummarizerError("rate limit")

        result = run.process_video(
            meta=_make_meta("vid123XYZ45"),
            summarizer=fake_summarizer,
            briefings_dir=tmp_path / "briefings",
            transcript_cache_dir=None,
        )
        assert result is None

    def test_permanent_summarizer_failure_writes_placeholder(self, tmp_path: Path, fake_summarizer, monkeypatch):
        monkeypatch.setattr(
            run,
            "extract_transcript",
            lambda vid, cache_dir: TranscriptResult(text="트랜스크립트 " * 100, source="transcript_api_stenographer"),
        )
        fake_summarizer.summarize.side_effect = PermanentSummarizerError(
            "wrong language", failure_code="wrong_language"
        )

        result = run.process_video(
            meta=_make_meta("vid123XYZ45"),
            summarizer=fake_summarizer,
            briefings_dir=tmp_path / "briefings",
            transcript_cache_dir=None,
        )
        assert result is not None
        assert result.status == BriefingStatus.FAILED
        assert result.failure_reason.value == "wrong_language"


class TestRunOrchestrator:
    def test_happy_path_writes_all_videos(self, tmp_path: Path, fake_summarizer, monkeypatch):
        config_path = _write_config(
            tmp_path,
            channels=[{"id": "UCsT0YIqwnpJCM-mx7-gSA4Q", "name": "슈카월드", "slug": "shuka"}],
        )
        briefings_dir = tmp_path / "briefings"

        videos = [_make_meta(f"vid{i}XYZ") for i in range(3)]
        monkeypatch.setattr(run, "discover_new_videos", lambda **kw: videos)
        monkeypatch.setattr(
            run,
            "extract_transcript",
            lambda vid, cache_dir: TranscriptResult(text="트랜스크립트 " * 100, source="transcript_api_stenographer"),
        )

        exit_code = run.run(config_path=config_path, briefings_dir=briefings_dir)
        assert exit_code == 0
        assert len(list(briefings_dir.glob("*.json"))) == 3

    def test_no_new_videos_succeeds(self, tmp_path: Path, fake_summarizer, monkeypatch):
        config_path = _write_config(
            tmp_path,
            channels=[{"id": "UCsT0YIqwnpJCM-mx7-gSA4Q", "name": "슈카월드", "slug": "shuka"}],
        )
        briefings_dir = tmp_path / "briefings"

        monkeypatch.setattr(run, "discover_new_videos", lambda **kw: [])

        exit_code = run.run(config_path=config_path, briefings_dir=briefings_dir)
        assert exit_code == 0

    def test_all_channels_fail_returns_exit_2(self, tmp_path: Path, fake_summarizer, monkeypatch):
        config_path = _write_config(
            tmp_path,
            channels=[
                {"id": "UCsT0YIqwnpJCM-mx7-gSA4Q", "name": "슈카월드", "slug": "shuka"},
                {"id": "UCxxxxxxxxxxxxxxxxxxxxxx", "name": "언더스탠딩", "slug": "understanding"},
            ],
        )
        briefings_dir = tmp_path / "briefings"

        def raise_discovery(**kw):
            raise DiscoveryFailure("boom")

        monkeypatch.setattr(run, "discover_new_videos", raise_discovery)

        exit_code = run.run(config_path=config_path, briefings_dir=briefings_dir)
        assert exit_code == 2

    # ---------------------------------------------------------------------
    # MANDATORY REGRESSION TESTS (iron rule from /plan-eng-review)
    # ---------------------------------------------------------------------

    def test_REGRESSION_permanent_failure_on_video_3_does_not_halt_others(
        self, tmp_path: Path, fake_summarizer, monkeypatch
    ):
        """REGRESSION: video 3 permanent transcript fail → videos 4, 5 still processed."""
        config_path = _write_config(
            tmp_path,
            channels=[{"id": "UCsT0YIqwnpJCM-mx7-gSA4Q", "name": "슈카월드", "slug": "shuka"}],
        )
        briefings_dir = tmp_path / "briefings"

        videos = [_make_meta(f"vid0{i}XYZ") for i in range(5)]
        monkeypatch.setattr(run, "discover_new_videos", lambda **kw: videos)

        call_count = {"n": 0}

        def transcript_side_effect(vid, cache_dir):
            call_count["n"] += 1
            if call_count["n"] == 3:
                raise PermanentTranscriptFailure(vid, "members only", "members_only")
            return TranscriptResult(text="트랜스크립트 " * 100, source="transcript_api_stenographer")

        monkeypatch.setattr(run, "extract_transcript", transcript_side_effect)

        exit_code = run.run(config_path=config_path, briefings_dir=briefings_dir)
        assert exit_code == 0

        # All 5 videos should have produced a briefing file (4 ok + 1 failed placeholder)
        files = list(briefings_dir.glob("*.json"))
        assert len(files) == 5

    def test_REGRESSION_unhandled_exception_on_video_3_does_not_halt_others(
        self, tmp_path: Path, fake_summarizer, monkeypatch
    ):
        """REGRESSION: RuntimeError in summarize for video 3 → videos 4, 5 still processed."""
        config_path = _write_config(
            tmp_path,
            channels=[{"id": "UCsT0YIqwnpJCM-mx7-gSA4Q", "name": "슈카월드", "slug": "shuka"}],
        )
        briefings_dir = tmp_path / "briefings"

        videos = [_make_meta(f"vid0{i}XYZ") for i in range(5)]
        monkeypatch.setattr(run, "discover_new_videos", lambda **kw: videos)
        monkeypatch.setattr(
            run,
            "extract_transcript",
            lambda vid, cache_dir: TranscriptResult(text="트랜스크립트 " * 100, source="transcript_api_stenographer"),
        )

        call_count = {"n": 0}

        def summarize_side_effect(transcript, meta):
            call_count["n"] += 1
            if call_count["n"] == 3:
                raise RuntimeError("totally unexpected library bug")
            return SummarizerResult(
                summary="파월 의장의 발언에서 주목할 점은 표면적 인하 신호가 아니라 그 이면의 조건부 단서들이다. "
                * 15,
                provider="gemini",
                model="gemini-2.5-flash",
                prompt_version="v1",
            )

        fake_summarizer.summarize.side_effect = summarize_side_effect

        exit_code = run.run(config_path=config_path, briefings_dir=briefings_dir)
        assert exit_code == 0

        # 4 successful briefings (video 3 was skipped via outer try/except)
        files = list(briefings_dir.glob("*.json"))
        assert len(files) == 4

    def test_REGRESSION_channel_rss_fail_does_not_halt_other_channels(
        self, tmp_path: Path, fake_summarizer, monkeypatch
    ):
        """REGRESSION: channel 2's discovery fails → channels 1, 3, 4, 5 still run."""
        config_path = _write_config(
            tmp_path,
            channels=[
                {"id": "UCsT0YIqwnpJCM-mx7-gSA4Q", "name": "CH1", "slug": "ch1"},
                {"id": "UCfailedxxxxxxxxxxxxxxxx", "name": "CH2", "slug": "ch2"},
                {"id": "UC3xxxxxxxxxxxxxxxxxxxxx", "name": "CH3", "slug": "ch3"},
                {"id": "UC4xxxxxxxxxxxxxxxxxxxxx", "name": "CH4", "slug": "ch4"},
                {"id": "UC5xxxxxxxxxxxxxxxxxxxxx", "name": "CH5", "slug": "ch5"},
            ],
        )
        briefings_dir = tmp_path / "briefings"

        def discover_side_effect(channel_id, channel_slug, channel_name, known_video_ids, max_new_videos=None):
            if channel_slug == "ch2":
                raise DiscoveryFailure(f"[{channel_slug}] RSS 404")
            return [_make_meta(f"{channel_slug}_v1", slug=channel_slug, channel_name=channel_name)]

        monkeypatch.setattr(run, "discover_new_videos", discover_side_effect)
        monkeypatch.setattr(
            run,
            "extract_transcript",
            lambda vid, cache_dir: TranscriptResult(text="트랜스크립트 " * 100, source="transcript_api_stenographer"),
        )

        exit_code = run.run(config_path=config_path, briefings_dir=briefings_dir)
        assert exit_code == 0

        # 4 briefings (one per channel except ch2)
        files = list(briefings_dir.glob("*.json"))
        assert len(files) == 4
        filenames = [f.name for f in files]
        assert not any("ch2" in name for name in filenames)
        for slug in ("ch1", "ch3", "ch4", "ch5"):
            assert any(slug in name for name in filenames), f"missing {slug}"
