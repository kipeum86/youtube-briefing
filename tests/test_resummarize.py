"""Tests for scripts/re-summarize-from-cache.py."""

from __future__ import annotations

import importlib.util
import sys
from datetime import datetime, timezone
from pathlib import Path

from pipeline.fetchers.transcript_extractor import TranscriptResult, TransientTranscriptFailure
from pipeline.models import (
    Briefing,
    BriefingStatus,
    DiscoverySource,
    FailureReason,
    SourceType,
)
from pipeline.summarizers.base import SummarizerResult
from pipeline.writers.json_store import write_briefing


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "re-summarize-from-cache.py"
SPEC = importlib.util.spec_from_file_location("resummarize_from_cache", SCRIPT)
assert SPEC and SPEC.loader
resummarize_from_cache = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = resummarize_from_cache
SPEC.loader.exec_module(resummarize_from_cache)


FIXED_NOW = datetime(2026, 4, 27, 12, 0, 0, tzinfo=timezone.utc)


class FakeSummarizer:
    provider = "fake"
    model = "fake-model"
    prompt_version = "v2"

    def __init__(self):
        self.calls: list[tuple[str, str]] = []

    def summarize(self, transcript, meta):  # noqa: ANN001
        self.calls.append((transcript, meta.video_id))
        return SummarizerResult(
            summary=(
                "**재요약 결과**\n\n"
                "캐시된 트랜스크립트를 기반으로 새 핵심 주장을 안정적으로 작성했다. "
                "이 문장은 테스트용으로 충분히 긴 본문을 구성한다.\n\n"
                "근거 단락에는 숫자와 정책명, 회사명 같은 구체 요소가 포함된 것으로 가정한다. "
                "기존 JSON은 백업된 뒤 이 내용으로 교체된다.\n\n"
                "함의 단락은 다음 관전 포인트와 운영상 의미를 설명한다. "
                "실패하면 기존 파일을 그대로 보존해야 한다."
            ),
            provider=self.provider,
            model=self.model,
            prompt_version=self.prompt_version,
        )


def _make_ok_briefing(video_id="abc123XYZ45", slug="shuka", **overrides) -> Briefing:
    data = dict(
        video_id=video_id,
        channel_slug=slug,
        channel_name="슈카월드",
        title="테스트 영상",
        published_at=datetime(2026, 4, 9, 3, 0, 0, tzinfo=timezone.utc),
        video_url=f"https://www.youtube.com/watch?v={video_id}",
        thumbnail_url=f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg",
        duration_seconds=1800,
        discovery_source=DiscoverySource.RSS,
        status=BriefingStatus.OK,
        summary=(
            "기존 요약 문장입니다. 테스트를 위해 충분히 긴 문자열로 작성해 Pydantic 검증을 통과합니다. "
            "재요약 이후에는 이 문장이 새 요약으로 교체되어야 합니다."
        ),
        failure_reason=None,
        generated_at=datetime(2026, 4, 9, 21, 15, 0, tzinfo=timezone.utc),
        provider="gemini",
        model="gemini-2.5-flash",
        prompt_version="v1",
    )
    data.update(overrides)
    return Briefing(**data)


def _make_failed_briefing(video_id="failed12345", slug="shuka") -> Briefing:
    data = _make_ok_briefing(video_id=video_id, slug=slug).model_dump()
    data.update(
        status=BriefingStatus.FAILED,
        summary=None,
        failure_reason=FailureReason.MEMBERS_ONLY,
    )
    return Briefing(**data)


def _write_transcript(cache_dir: Path, video_id: str, text: str = "트랜스크립트 " * 100) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / f"{video_id}.txt").write_text(text, encoding="utf-8")


def _select(briefings_dir: Path, transcripts_dir: Path, **kwargs):
    return resummarize_from_cache.select_targets(
        briefings_dir=briefings_dir,
        transcript_cache_dir=transcripts_dir,
        **kwargs,
    )


def test_dry_run_lists_cached_targets_without_writing(tmp_path: Path):
    briefings_dir = tmp_path / "briefings"
    transcripts_dir = tmp_path / "transcripts"
    original = _make_ok_briefing()
    path = write_briefing(original, briefings_dir)
    _write_transcript(transcripts_dir, original.video_id)

    selection = _select(briefings_dir, transcripts_dir)
    result = resummarize_from_cache.resummarize_selection(
        selection=selection,
        briefings_dir=briefings_dir,
        summarizer=None,
        dry_run=True,
        now_fn=lambda: FIXED_NOW,
    )

    loaded = Briefing.model_validate_json(path.read_text(encoding="utf-8"))
    assert result["target_count"] == 1
    assert result["written"] == 0
    assert result["backup_dir"] is None
    assert loaded.summary == original.summary


def test_resummarize_writes_backup_and_updates_briefing(tmp_path: Path):
    briefings_dir = tmp_path / "briefings"
    transcripts_dir = tmp_path / "transcripts"
    original = _make_ok_briefing()
    path = write_briefing(original, briefings_dir)
    _write_transcript(transcripts_dir, original.video_id)

    summarizer = FakeSummarizer()
    selection = _select(briefings_dir, transcripts_dir)
    result = resummarize_from_cache.resummarize_selection(
        selection=selection,
        briefings_dir=briefings_dir,
        summarizer=summarizer,
        now_fn=lambda: FIXED_NOW,
    )

    loaded = Briefing.model_validate_json(path.read_text(encoding="utf-8"))
    backup_path = Path(result["backup_dir"]) / path.name
    backed_up = Briefing.model_validate_json(backup_path.read_text(encoding="utf-8"))

    assert result["written"] == 1
    assert summarizer.calls == [("트랜스크립트 " * 100, original.video_id)]
    assert loaded.summary != original.summary
    assert loaded.provider == "fake"
    assert loaded.prompt_version == "v2"
    assert loaded.generated_at == FIXED_NOW
    assert backed_up.summary == original.summary


def test_select_targets_skips_missing_cache_and_honors_channel_filter(tmp_path: Path):
    briefings_dir = tmp_path / "briefings"
    transcripts_dir = tmp_path / "transcripts"
    shuka = _make_ok_briefing(video_id="shuka12345", slug="shuka")
    mer = _make_ok_briefing(video_id="mer1234567", slug="mer", channel_name="메르의 블로그")
    write_briefing(shuka, briefings_dir)
    write_briefing(mer, briefings_dir)
    _write_transcript(transcripts_dir, shuka.video_id)

    selection = _select(
        briefings_dir,
        transcripts_dir,
        status_filter="ok",
        only_channel="shuka",
    )

    assert [target.briefing.video_id for target in selection.targets] == [shuka.video_id]
    assert selection.skipped_channel == 1
    assert selection.skipped_missing_cache == 0


def test_status_all_can_retry_failed_placeholder_with_cache(tmp_path: Path):
    briefings_dir = tmp_path / "briefings"
    transcripts_dir = tmp_path / "transcripts"
    failed = _make_failed_briefing()
    path = write_briefing(failed, briefings_dir)
    _write_transcript(transcripts_dir, failed.video_id)

    selection = _select(
        briefings_dir,
        transcripts_dir,
        status_filter="all",
    )
    result = resummarize_from_cache.resummarize_selection(
        selection=selection,
        briefings_dir=briefings_dir,
        summarizer=FakeSummarizer(),
        now_fn=lambda: FIXED_NOW,
    )

    loaded = Briefing.model_validate_json(path.read_text(encoding="utf-8"))
    assert result["written"] == 1
    assert loaded.status == BriefingStatus.OK
    assert loaded.failure_reason is None
    assert loaded.summary is not None


def test_select_targets_can_sort_by_published_at_not_filename(tmp_path: Path):
    briefings_dir = tmp_path / "briefings"
    transcripts_dir = tmp_path / "transcripts"
    older = _make_ok_briefing(
        video_id="older12345",
        slug="zzz",
        published_at=datetime(2026, 4, 9, 1, 0, 0, tzinfo=timezone.utc),
    )
    newer = _make_ok_briefing(
        video_id="newer12345",
        slug="aaa",
        published_at=datetime(2026, 4, 9, 10, 0, 0, tzinfo=timezone.utc),
    )
    write_briefing(older, briefings_dir)
    write_briefing(newer, briefings_dir)
    _write_transcript(transcripts_dir, older.video_id)
    _write_transcript(transcripts_dir, newer.video_id)

    selection = _select(
        briefings_dir,
        transcripts_dir,
        sort_key="published_at",
        limit=2,
    )

    assert [target.briefing.video_id for target in selection.targets] == [
        newer.video_id,
        older.video_id,
    ]


def test_fetch_missing_naver_blog_text_is_cached_and_resummarized(
    tmp_path: Path,
    monkeypatch,
):
    briefings_dir = tmp_path / "briefings"
    transcripts_dir = tmp_path / "transcripts"
    original = _make_ok_briefing(
        video_id="224263266592",
        slug="mer",
        channel_name="메르의 블로그",
        discovery_source=DiscoverySource.NAVER_BLOG_RSS,
        source_type=SourceType.NAVER_BLOG,
        video_url="https://blog.naver.com/ranto28/224263266592",
        thumbnail_url="https://ssl.pstatic.net/static/blog/icon/favicon.ico",
    )
    path = write_briefing(original, briefings_dir)
    fetched_text = "블로그 본문 " * 100

    def fake_extract_blog_post_text(post_url, item_id):  # noqa: ANN001
        assert post_url == str(original.video_url)
        assert item_id == original.video_id
        return TranscriptResult(text=fetched_text, source="naver_blog_html")

    monkeypatch.setattr(
        resummarize_from_cache,
        "extract_blog_post_text",
        fake_extract_blog_post_text,
    )

    selection = _select(
        briefings_dir,
        transcripts_dir,
        fetch_missing=True,
    )
    summarizer = FakeSummarizer()
    result = resummarize_from_cache.resummarize_selection(
        selection=selection,
        briefings_dir=briefings_dir,
        summarizer=summarizer,
        fetch_missing=True,
        now_fn=lambda: FIXED_NOW,
    )

    loaded = Briefing.model_validate_json(path.read_text(encoding="utf-8"))
    assert result["written"] == 1
    assert summarizer.calls == [(fetched_text, original.video_id)]
    cached = transcripts_dir / f"{original.video_id}.txt"
    assert cached.read_text(encoding="utf-8") == fetched_text
    assert loaded.summary != original.summary
    assert loaded.model == "fake-model"


def test_fetch_missing_failure_preserves_existing_briefing(
    tmp_path: Path,
    monkeypatch,
):
    briefings_dir = tmp_path / "briefings"
    transcripts_dir = tmp_path / "transcripts"
    original = _make_ok_briefing(
        video_id="224263266592",
        slug="mer",
        channel_name="메르의 블로그",
        discovery_source=DiscoverySource.NAVER_BLOG_RSS,
        source_type=SourceType.NAVER_BLOG,
        video_url="https://blog.naver.com/ranto28/224263266592",
        thumbnail_url="https://ssl.pstatic.net/static/blog/icon/favicon.ico",
    )
    path = write_briefing(original, briefings_dir)

    def fake_extract_blog_post_text(post_url, item_id):  # noqa: ANN001
        raise TransientTranscriptFailure(item_id, "network")

    monkeypatch.setattr(
        resummarize_from_cache,
        "extract_blog_post_text",
        fake_extract_blog_post_text,
    )

    selection = _select(
        briefings_dir,
        transcripts_dir,
        fetch_missing=True,
    )
    result = resummarize_from_cache.resummarize_selection(
        selection=selection,
        briefings_dir=briefings_dir,
        summarizer=FakeSummarizer(),
        fetch_missing=True,
        now_fn=lambda: FIXED_NOW,
    )

    loaded = Briefing.model_validate_json(path.read_text(encoding="utf-8"))
    assert result["written"] == 0
    assert result["failed"] == 1
    assert loaded.summary == original.summary
    assert not (transcripts_dir / f"{original.video_id}.txt").exists()
