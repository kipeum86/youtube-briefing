"""Integration tests for pipeline/run.py — full orchestrator with all externals mocked.

Includes the 3 mandatory regression tests from the eng review:
  1. Permanent transcript failure on video 3 does not halt videos 4+
  2. Unhandled exception on video 3 does not halt videos 4+
  3. Channel RSS fetch failure does not halt other channels
"""

from __future__ import annotations

import threading
import time
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


def _write_config(
    tmp_path: Path,
    channels: list[dict],
    blogs: list[dict] | None = None,
    *,
    max_discovery_concurrency: int = 1,
    max_processing_concurrency: int = 1,
) -> Path:
    config = {
        "pipeline": {
            "summarizer": {"provider": "gemini", "model": "gemini-2.5-flash", "prompt_version": "v1"},
            "summary_min_chars": 500,
            "summary_max_chars": 1000,
            "transcript_cache_dir": str(tmp_path / "transcripts"),
            "log_dir": str(tmp_path / "logs"),
            "max_discovery_concurrency": max_discovery_concurrency,
            "max_processing_concurrency": max_processing_concurrency,
        },
        "channels": channels,
        "blogs": blogs or [],
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
        assert config.pipeline.summarizer.provider == "gemini"
        assert len(config.channels) == 1

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

    def test_discovery_can_run_sources_concurrently(self, tmp_path: Path, fake_summarizer, monkeypatch):
        config_path = _write_config(
            tmp_path,
            channels=[
                {"id": "UCsT0YIqwnpJCM-mx7-gSA4Q", "name": "CH1", "slug": "ch1"},
                {"id": "UCxxxxxxxxxxxxxxxxxxxxxx", "name": "CH2", "slug": "ch2"},
            ],
            max_discovery_concurrency=2,
        )
        briefings_dir = tmp_path / "briefings"
        lock = threading.Lock()
        release = threading.Event()
        active = 0
        max_active = 0

        def discover_side_effect(**kwargs):
            nonlocal active, max_active
            with lock:
                active += 1
                max_active = max(max_active, active)
                if max_active == 2:
                    release.set()
            release.wait(timeout=1)
            with lock:
                active -= 1
            return []

        monkeypatch.setattr(run, "discover_new_videos", discover_side_effect)

        exit_code = run.run(config_path=config_path, briefings_dir=briefings_dir)

        assert exit_code == 0
        assert max_active == 2

    def test_processing_can_run_items_concurrently(self, tmp_path: Path, fake_summarizer, monkeypatch):
        config_path = _write_config(
            tmp_path,
            channels=[{"id": "UCsT0YIqwnpJCM-mx7-gSA4Q", "name": "슈카월드", "slug": "shuka"}],
            max_processing_concurrency=2,
        )
        briefings_dir = tmp_path / "briefings"
        videos = [_make_meta(f"vid{i}XYZ") for i in range(4)]
        monkeypatch.setattr(run, "discover_new_videos", lambda **kw: videos)

        lock = threading.Lock()
        release = threading.Event()
        active = 0
        max_active = 0

        def process_side_effect(**kwargs):
            nonlocal active, max_active
            with lock:
                active += 1
                max_active = max(max_active, active)
                if max_active == 2:
                    release.set()
            release.wait(timeout=1)
            time.sleep(0.01)
            with lock:
                active -= 1
            return object()

        monkeypatch.setattr(run, "process_video", process_side_effect)

        exit_code = run.run(config_path=config_path, briefings_dir=briefings_dir)

        assert exit_code == 0
        assert max_active == 2

    def test_blog_only_config_writes_new_posts(self, tmp_path: Path, fake_summarizer, monkeypatch):
        config_path = _write_config(
            tmp_path,
            channels=[],
            blogs=[{"blog_id": "ranto28", "name": "메르의 블로그", "slug": "mer"}],
        )
        briefings_dir = tmp_path / "briefings"

        blog_meta = VideoMeta(
            video_id="224250228854",
            channel_id="ranto28",
            channel_slug="mer",
            channel_name="메르의 블로그",
            title="트럼프, 호르무즈해협 완전 봉쇄 선언",
            published_at=datetime(2026, 4, 13, 0, 0, tzinfo=timezone.utc),
            discovery_source=DiscoverySource.NAVER_BLOG_RSS,
            source_type="naver_blog",
            source_url="https://blog.naver.com/ranto28/224250228854",
            thumbnail_url="https://ssl.pstatic.net/static/blog/icon/favicon.ico",
            duration_seconds=0,
        )

        monkeypatch.setattr(run, "discover_new_videos", lambda **kw: [])
        monkeypatch.setattr(run, "discover_new_blog_posts", lambda **kw: [blog_meta])
        monkeypatch.setattr(
            run,
            "extract_blog_post_text",
            lambda url, item_id: TranscriptResult(
                text="본문 " * 200,
                source="naver_blog_html",
                # Within +/-2d of RSS pubDate, so the page timestamp can refine it.
                published_at=datetime(2026, 4, 12, 22, 5, 47, tzinfo=timezone.utc),
            ),
        )

        exit_code = run.run(config_path=config_path, briefings_dir=briefings_dir)
        assert exit_code == 0
        files = list(briefings_dir.glob("*.json"))
        assert len(files) == 1
        assert files[0].name == "2026-04-13-mer-224250228854.json"
        loaded = files[0].read_text(encoding="utf-8")
        assert '"source_type": "naver_blog"' in loaded
        assert '"video_url": "https://blog.naver.com/ranto28/224250228854"' in loaded
        assert '"published_at": "2026-04-12T22:05:47Z"' in loaded

    def test_blog_published_at_override_rejected_when_drift_exceeds_two_days(
        self, tmp_path: Path, fake_summarizer, monkeypatch
    ):
        """REGRESSION: Naver page extractor returns a date >2d from RSS pubDate
        (e.g. it picked up a content date from og:description). The pipeline must
        keep the trustworthy RSS pubDate and reject the override.

        Original incident: ranto28/224263266592 → page yielded 2026-05-15 (a date
        the post mentions), RSS said 2026-04-24. ~21d drift → reject."""
        config_path = _write_config(
            tmp_path,
            channels=[],
            blogs=[{"blog_id": "ranto28", "name": "메르의 블로그", "slug": "mer"}],
        )
        briefings_dir = tmp_path / "briefings"

        rss_published = datetime(2026, 4, 24, 7, 55, tzinfo=timezone.utc)
        contaminated_page_date = datetime(2026, 5, 15, 0, 0, tzinfo=timezone.utc)

        blog_meta = VideoMeta(
            video_id="224263266592",
            channel_id="ranto28",
            channel_slug="mer",
            channel_name="메르의 블로그",
            title="미국 국가부채를 29만 4,117번 갚을 수 있다는 프시케 소행성 근황",
            published_at=rss_published,
            discovery_source=DiscoverySource.NAVER_BLOG_RSS,
            source_type="naver_blog",
            source_url="https://blog.naver.com/ranto28/224263266592",
            thumbnail_url="https://ssl.pstatic.net/static/blog/icon/favicon.ico",
            duration_seconds=0,
        )

        monkeypatch.setattr(run, "discover_new_videos", lambda **kw: [])
        monkeypatch.setattr(run, "discover_new_blog_posts", lambda **kw: [blog_meta])
        monkeypatch.setattr(
            run,
            "extract_blog_post_text",
            lambda url, item_id: TranscriptResult(
                text="본문 " * 200,
                source="naver_blog_html",
                published_at=contaminated_page_date,
            ),
        )

        exit_code = run.run(config_path=config_path, briefings_dir=briefings_dir)
        assert exit_code == 0
        files = list(briefings_dir.glob("*.json"))
        assert len(files) == 1
        # Filename uses RSS pubDate (KST), not the contaminated page date.
        assert files[0].name == "2026-04-24-mer-224263266592.json"
        loaded = files[0].read_text(encoding="utf-8")
        assert '"published_at": "2026-04-24T07:55:00Z"' in loaded

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

    def test_REGRESSION_per_channel_known_set_not_cross_contaminated(
        self, tmp_path: Path, fake_summarizer, monkeypatch
    ):
        """REGRESSION: each channel sees only ITS OWN known video IDs.

        The old code passed a global cross-channel known set, which caused
        false-positive RSS saturation: any channel with zero history would
        fail the "known ID in RSS?" check against another channel's IDs,
        triggering needless yt-dlp catchup and English metadata.

        We verify the wiring by asserting the known_video_ids each channel
        sees matches only briefings from that channel that already exist on
        disk.
        """
        config_path = _write_config(
            tmp_path,
            channels=[
                {"id": "UCsT0YIqwnpJCM-mx7-gSA4Q", "name": "슈카월드", "slug": "shuka"},
                {"id": "UCOB62fKRT7b73X7tRxMuN2g", "name": "박종훈", "slug": "parkjonghoon"},
                {"id": "UCIUni4ScRp4mqPXsxy62L5w", "name": "언더스탠딩", "slug": "understanding"},
            ],
        )
        briefings_dir = tmp_path / "briefings"
        briefings_dir.mkdir(parents=True)

        # Seed disk with one existing shuka briefing and one parkjonghoon briefing.
        # understanding has zero history.
        from pipeline.writers.json_store import write_briefing
        from pipeline.models import Briefing, BriefingStatus, DiscoverySource

        def _seed(video_id, slug, name):
            write_briefing(
                Briefing(
                    video_id=video_id,
                    channel_slug=slug,
                    channel_name=name,
                    title="seeded",
                    published_at=datetime(2026, 4, 1, tzinfo=timezone.utc),
                    video_url=f"https://www.youtube.com/watch?v={video_id}",
                    thumbnail_url=f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg",
                    duration_seconds=1000,
                    discovery_source=DiscoverySource.RSS,
                    status=BriefingStatus.OK,
                    summary="seeded summary " * 50,
                    failure_reason=None,
                    generated_at=datetime(2026, 4, 1, tzinfo=timezone.utc),
                    provider="gemini",
                    model="gemini-2.5-flash",
                    prompt_version="v1",
                ),
                briefings_dir,
            )

        _seed("shukaold001", "shuka", "슈카월드")
        _seed("parkold0001", "parkjonghoon", "박종훈")

        # Capture what each channel's discovery call sees.
        seen_known: dict[str, set[str]] = {}

        def discover_side_effect(channel_id, channel_slug, channel_name, known_video_ids, max_new_videos=None, min_duration_seconds=None):
            seen_known[channel_slug] = set(known_video_ids)
            return []  # Pretend no new videos, we only care about the known set

        monkeypatch.setattr(run, "discover_new_videos", discover_side_effect)

        run.run(config_path=config_path, briefings_dir=briefings_dir)

        # Each channel saw only ITS own ids — no cross-contamination.
        assert seen_known["shuka"] == {"shukaold001"}
        assert seen_known["parkjonghoon"] == {"parkold0001"}
        assert seen_known["understanding"] == set()

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

        def discover_side_effect(channel_id, channel_slug, channel_name, known_video_ids, max_new_videos=None, min_duration_seconds=None):
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
