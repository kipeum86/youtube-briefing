"""Unit tests for transcript_extractor.

Covers pure helper functions (VTT parsing, overlap ratio, classification) and
the exception hierarchy. Network-hitting paths (_try_transcript_api,
_try_notebooklm, _try_ytdlp) are tested in test_transcript_extractor_integration.py
with mocks (not yet written — v1 plan).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pipeline.fetchers import transcript_extractor as tx
from pipeline.fetchers.transcript_extractor import (
    PermanentTranscriptFailure,
    TranscriptFailure,
    TranscriptResult,
    TransientTranscriptFailure,
    _cache_transcript,
    _classify_transcript_api_exception,
    _overlap_ratio,
    _parse_vtt,
    _transcript_to_text,
    extract_transcript,
)


class TestExceptionHierarchy:
    def test_transient_is_transcript_failure(self):
        exc = TransientTranscriptFailure("vid123", "network hiccup")
        assert isinstance(exc, TranscriptFailure)
        assert exc.transient is True
        assert exc.video_id == "vid123"

    def test_permanent_has_failure_code(self):
        exc = PermanentTranscriptFailure("vid123", "Members-only", "members_only")
        assert isinstance(exc, TranscriptFailure)
        assert exc.transient is False
        assert exc.failure_code == "members_only"

    def test_exception_str_includes_video_id(self):
        exc = TransientTranscriptFailure("vid123", "timeout")
        assert "vid123" in str(exc)
        assert "timeout" in str(exc)


class TestClassifier:
    def test_video_unavailable_is_permanent(self):
        class VideoUnavailable(Exception):
            pass

        classified = _classify_transcript_api_exception(VideoUnavailable("video unavailable"), "vid")
        assert classified.transient is False
        assert classified.code == "video_removed"

    def test_transcripts_disabled_is_permanent(self):
        class TranscriptsDisabled(Exception):
            pass

        classified = _classify_transcript_api_exception(TranscriptsDisabled("subtitles are disabled"), "vid")
        assert classified.transient is False
        assert classified.code == "transcripts_disabled"

    def test_timeout_is_transient(self):
        exc = Exception("request timeout after 30s")
        classified = _classify_transcript_api_exception(exc, "vid")
        assert classified.transient is True
        assert classified.code == "timeout"

    def test_rate_limit_is_transient(self):
        exc = Exception("429 rate limit exceeded")
        classified = _classify_transcript_api_exception(exc, "vid")
        assert classified.transient is True
        assert classified.code == "rate_limit"

    def test_unknown_error_defaults_to_transient(self):
        """Unknown errors are transient — safer to retry than lose data permanently."""
        exc = RuntimeError("mysterious failure nobody has seen before")
        classified = _classify_transcript_api_exception(exc, "vid")
        assert classified.transient is True
        assert classified.code == "unknown"


class TestOverlapRatio:
    def test_identical_strings_return_1(self):
        assert _overlap_ratio("hello", "hello") == 1.0

    def test_no_overlap_returns_0(self):
        # All different chars at shared positions
        assert _overlap_ratio("abcde", "xyzwv") == 0.0

    def test_partial_overlap(self):
        assert _overlap_ratio("hello", "help!") == 0.6  # h, e, l match; l, o differ

    def test_empty_strings_return_0(self):
        assert _overlap_ratio("", "abc") == 0.0
        assert _overlap_ratio("abc", "") == 0.0
        assert _overlap_ratio("", "") == 0.0


class TestTranscriptToText:
    def test_list_of_dicts_joins_distinct_lines(self):
        """Use distinct content so the >80% overlap dedup (inherited from parlawatch) doesn't drop them."""
        fake = [
            {"text": "안녕하세요 여러분"},
            {"text": "오늘의 주제는 경제입니다"},
            {"text": "파월 의장이 말했습니다"},
        ]
        result = _transcript_to_text(fake)
        assert result == "안녕하세요 여러분\n오늘의 주제는 경제입니다\n파월 의장이 말했습니다"

    def test_deduplicates_exactly_identical_consecutive_lines(self):
        fake = [
            {"text": "같은 줄"},
            {"text": "같은 줄"},
            {"text": "완전히 다른 내용"},
        ]
        result = _transcript_to_text(fake)
        assert result == "같은 줄\n완전히 다른 내용"

    def test_drops_highly_overlapping_consecutive_lines(self):
        """This is the inherited parlawatch behavior for noisy auto-captions."""
        fake = [
            {"text": "파월 의장이 금리를 인하"},
            {"text": "파월 의장이 금리를 인하한"},  # >80% overlap
        ]
        result = _transcript_to_text(fake)
        # Only the first should survive
        assert result == "파월 의장이 금리를 인하"

    def test_skips_empty_strings(self):
        fake = [
            {"text": "안녕하세요"},
            {"text": ""},
            {"text": "   "},
            {"text": "오늘의 주제"},
        ]
        result = _transcript_to_text(fake)
        assert result == "안녕하세요\n오늘의 주제"

    def test_returns_none_for_all_empty(self):
        assert _transcript_to_text([{"text": ""}, {"text": "   "}]) is None
        assert _transcript_to_text([]) is None


class TestParseVTT:
    def test_parses_simple_stenographer_vtt(self, tmp_path: Path):
        vtt = tmp_path / "test.vtt"
        vtt.write_text(
            "WEBVTT\n"
            "Kind: captions\n"
            "Language: ko\n"
            "\n"
            "1\n"
            "00:00:00.000 --> 00:00:02.500\n"
            "안녕하세요 여러분\n"
            "\n"
            "2\n"
            "00:00:02.500 --> 00:00:05.000\n"
            "오늘 이야기할 주제는\n",
            encoding="utf-8",
        )
        result = _parse_vtt(vtt, is_auto=False)
        assert "안녕하세요 여러분" in result
        assert "오늘 이야기할 주제는" in result
        # Metadata stripped
        assert "WEBVTT" not in result
        assert "00:00:00" not in result

    def test_strips_html_tags(self, tmp_path: Path):
        vtt = tmp_path / "test.vtt"
        vtt.write_text(
            "WEBVTT\n"
            "\n"
            "00:00:00.000 --> 00:00:02.500\n"
            "<c.color00FF00>초록색 텍스트</c>\n",
            encoding="utf-8",
        )
        result = _parse_vtt(vtt, is_auto=False)
        assert "초록색 텍스트" in result
        assert "<c" not in result

    def test_auto_caption_dedup(self, tmp_path: Path):
        vtt = tmp_path / "test.vtt"
        vtt.write_text(
            "WEBVTT\n"
            "\n"
            "00:00:00.000 --> 00:00:02.500\n"
            "반복되는 텍스트\n"
            "\n"
            "00:00:02.500 --> 00:00:05.000\n"
            "반복되는 텍스트\n"
            "\n"
            "00:00:05.000 --> 00:00:07.500\n"
            "새로운 텍스트\n",
            encoding="utf-8",
        )
        result = _parse_vtt(vtt, is_auto=True)
        lines = [ln for ln in result.split("\n") if ln]
        assert lines.count("반복되는 텍스트") == 1
        assert "새로운 텍스트" in lines


class TestCache:
    def test_cache_transcript_writes_file(self, tmp_path: Path):
        _cache_transcript("테스트 내용", "vid123", tmp_path)
        cached = tmp_path / "vid123.txt"
        assert cached.exists()
        assert cached.read_text(encoding="utf-8") == "테스트 내용"

    def test_cache_transcript_none_dir_is_noop(self, tmp_path: Path):
        # Should not raise — None means caching is disabled
        _cache_transcript("content", "vid123", None)


class TestExtractTranscript:
    def test_cache_hit_bypasses_tiers(self, tmp_path: Path):
        # Pre-populate the cache
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        (cache_dir / "vid123.txt").write_text("캐시된 트랜스크립트 내용 " * 20, encoding="utf-8")

        result = extract_transcript("vid123", cache_dir=cache_dir)
        assert isinstance(result, TranscriptResult)
        assert result.text.startswith("캐시된 트랜스크립트 내용")

    def test_no_cache_no_tiers_raises_permanent(self, tmp_path: Path, monkeypatch):
        """When all three tiers return None and no cache, raise PermanentTranscriptFailure."""
        # Monkey-patch all three tier helpers to return None
        monkeypatch.setattr(tx, "_try_notebooklm", lambda vid: None)
        monkeypatch.setattr(tx, "_try_transcript_api", lambda vid: None)
        monkeypatch.setattr(tx, "_try_ytdlp", lambda vid: None)

        with pytest.raises(PermanentTranscriptFailure) as exc_info:
            extract_transcript("vid999", cache_dir=tmp_path)
        assert exc_info.value.failure_code == "empty_transcript"

    def test_notebooklm_tier_1_succeeds_bypasses_other_tiers(self, tmp_path: Path, monkeypatch):
        """NotebookLM (tier 1) succeeds → transcript-api and yt-dlp are never called."""
        call_log = []

        def notebooklm_ok(vid):
            call_log.append("notebooklm")
            return TranscriptResult(text="한국어 트랜스크립트 " * 20, source="notebooklm")

        def transcript_api_called(vid):
            call_log.append("transcript_api")
            return TranscriptResult(text="should not be reached", source="transcript_api_auto")

        def ytdlp_called(vid):
            call_log.append("ytdlp")
            return TranscriptResult(text="should not be reached", source="ytdlp_auto")

        monkeypatch.setattr(tx, "_try_notebooklm", notebooklm_ok)
        monkeypatch.setattr(tx, "_try_transcript_api", transcript_api_called)
        monkeypatch.setattr(tx, "_try_ytdlp", ytdlp_called)

        result = extract_transcript("vid001", cache_dir=tmp_path)
        assert result.source == "notebooklm"
        assert call_log == ["notebooklm"]  # tier 2 and 3 never reached
