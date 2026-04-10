"""Tests for the Summarizer abstraction + GeminiFlashSummarizer.

Network-hitting paths are mocked. We test:
  - Prompt construction (format, caps, content)
  - Response validation (length, language, truncation)
  - Retry policy (short-output retry)
  - Exception classification (transient vs permanent)
  - Factory load_summarizer
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from pipeline.models import DiscoverySource, VideoMeta
from pipeline.summarizers.base import (
    PermanentSummarizerError,
    Summarizer,
    SummarizerResult,
    TransientSummarizerError,
    _is_hangul,
    load_summarizer,
)
from pipeline.summarizers.gemini_flash import (
    GeminiFlashSummarizer,
    PROMPT_TEMPLATE_V1,
    _classify_gemini_exception,
)


def _make_video_meta() -> VideoMeta:
    return VideoMeta(
        video_id="abc123XYZ45",
        channel_id="UCsT0YIqwnpJCM-mx7-gSA4Q",
        channel_slug="shuka",
        channel_name="슈카월드",
        title="美 연준 금리인하 시그널",
        published_at=datetime(2026, 4, 9, 3, 0, 0, tzinfo=timezone.utc),
        discovery_source=DiscoverySource.RSS,
    )


def _korean_summary(length: int = 600) -> str:
    """Generate a mock summary that's mostly Korean and the right length."""
    base = "파월 의장의 발언에서 주목할 점은 표면적 인하 신호가 아니라 그 이면의 조건부 단서들이다. 슈카월드는 이번 영상에서 시장 반응의 역설을 지적한다. "
    result = ""
    while len(result) < length:
        result += base
    return result[:length]


class FakeSummarizer(Summarizer):
    """Test double for base Summarizer policy without needing a real API."""

    provider = "fake"
    model = "fake-model"
    prompt_version = "v1"

    def __init__(self, responses: list[str]):
        self.responses = list(responses)
        self.call_count = 0

    def _build_prompt(self, transcript: str, meta: VideoMeta) -> str:
        return f"FAKE PROMPT for {meta.title}: {transcript[:100]}"

    def _call_api(self, prompt: str) -> str:
        self.call_count += 1
        if not self.responses:
            raise RuntimeError("no more fake responses")
        return self.responses.pop(0)


class TestBaseSummarizerPolicy:
    def test_happy_path_returns_result(self):
        # 900 is safely above the 700 min_chars floor
        s = FakeSummarizer(responses=[_korean_summary(900)])
        result = s.summarize("가" * 500, _make_video_meta())

        assert isinstance(result, SummarizerResult)
        # Truncation may trim to last sentence boundary so length is <= fixture size
        assert 700 <= len(result.summary) <= 900
        assert result.provider == "fake"

    def test_empty_transcript_raises_permanent(self):
        s = FakeSummarizer(responses=[])
        with pytest.raises(PermanentSummarizerError) as exc_info:
            s.summarize("", _make_video_meta())
        assert exc_info.value.failure_code == "empty_transcript"

    def test_tiny_transcript_raises_permanent(self):
        s = FakeSummarizer(responses=[])
        with pytest.raises(PermanentSummarizerError) as exc_info:
            s.summarize("짧음", _make_video_meta())
        assert exc_info.value.failure_code == "empty_transcript"

    def test_short_output_triggers_retry(self):
        """First response below 700; second meets the floor."""
        s = FakeSummarizer(responses=[_korean_summary(400), _korean_summary(900)])
        result = s.summarize("가" * 500, _make_video_meta())

        assert s.call_count == 2
        assert 700 <= len(result.summary) <= 900

    def test_persistently_short_output_relaxed_floor(self):
        """Both attempts below min_chars but above the relaxed floor (300) → accept."""
        short = _korean_summary(500)  # below 700 min, above 300 relaxed
        s = FakeSummarizer(responses=[short, short])
        result = s.summarize("가" * 500, _make_video_meta())
        assert 300 <= len(result.summary) <= 500

    def test_way_too_short_raises_permanent(self):
        """Both attempts below relaxed floor (300) → permanent failure."""
        tiny = _korean_summary(150)
        s = FakeSummarizer(responses=[tiny, tiny])
        with pytest.raises(PermanentSummarizerError) as exc_info:
            s.summarize("가" * 500, _make_video_meta())
        assert exc_info.value.failure_code == "summarizer_refused"

    def test_non_korean_response_raises_permanent(self):
        """English-only response → wrong_language classification."""
        english = "This is an English summary that Gemini should not have returned because we asked for Korean content summarization."
        s = FakeSummarizer(responses=[english])
        with pytest.raises(PermanentSummarizerError) as exc_info:
            s.summarize("가" * 500, _make_video_meta())
        assert exc_info.value.failure_code == "wrong_language"

    def test_empty_response_raises_permanent(self):
        s = FakeSummarizer(responses=["   \n  "])
        with pytest.raises(PermanentSummarizerError) as exc_info:
            s.summarize("가" * 500, _make_video_meta())
        assert exc_info.value.failure_code == "summarizer_refused"


class TestTruncation:
    def test_long_response_truncated_at_sentence_boundary(self):
        s = FakeSummarizer(responses=[])
        long_text = "첫 문장이 여기 있습니다. 두 번째 문장도 있습니다. " * 50  # ~2250 chars
        truncated = s._truncate_to_limit(long_text)
        assert len(truncated) <= 1200
        # Should end at a sentence boundary (period)
        assert truncated.endswith(".") or truncated.endswith("…")

    def test_no_sentence_boundary_falls_back_to_hard_truncate(self):
        s = FakeSummarizer(responses=[])
        no_punct = "한국어" * 600
        truncated = s._truncate_to_limit(no_punct)
        assert len(truncated) <= 1200
        assert truncated.endswith("…")

    def test_short_response_untouched(self):
        s = FakeSummarizer(responses=[])
        short = "짧은 요약."
        assert s._truncate_to_limit(short) == short


class TestIsHangul:
    def test_hangul_syllable(self):
        assert _is_hangul("가") is True
        assert _is_hangul("한") is True
        assert _is_hangul("글") is True

    def test_latin_is_not_hangul(self):
        assert _is_hangul("a") is False
        assert _is_hangul("Z") is False

    def test_digit_is_not_hangul(self):
        assert _is_hangul("0") is False

    def test_punctuation_is_not_hangul(self):
        assert _is_hangul(".") is False
        assert _is_hangul("—") is False


class TestLoadSummarizer:
    def test_load_gemini(self):
        s = load_summarizer("gemini", "gemini-2.5-flash", "v1")
        assert isinstance(s, GeminiFlashSummarizer)
        assert s.model == "gemini-2.5-flash"

    def test_unknown_provider_raises(self):
        with pytest.raises(ValueError, match="unknown summarizer provider"):
            load_summarizer("openai", "gpt-5", "v1")


class TestGeminiFlashPromptBuild:
    def test_prompt_contains_title_and_channel(self):
        s = GeminiFlashSummarizer(api_key="fake-key")
        meta = _make_video_meta()
        prompt = s._build_prompt("테스트 트랜스크립트 내용 " * 20, meta)
        assert meta.title in prompt
        assert meta.channel_name in prompt
        assert "700~1,200자" in prompt  # Length constraint

    def test_long_transcript_is_capped(self):
        s = GeminiFlashSummarizer(api_key="fake-key")
        long_text = "가" * 100_000
        prompt = s._build_prompt(long_text, _make_video_meta())
        # Prompt includes the template + capped transcript
        assert len(prompt) < 90_000

    def test_unknown_prompt_version_raises(self):
        s = GeminiFlashSummarizer(api_key="fake-key", prompt_version="v99")
        with pytest.raises(ValueError, match="unknown prompt_version"):
            s._build_prompt("test", _make_video_meta())


class TestGeminiClassification:
    def test_429_is_transient(self):
        result = _classify_gemini_exception(Exception("429 rate limit"))
        assert result.transient is True

    def test_5xx_is_transient(self):
        result = _classify_gemini_exception(Exception("HTTP 503 service unavailable"))
        assert result.transient is True

    def test_timeout_is_transient(self):
        result = _classify_gemini_exception(Exception("Request timeout after 30s"))
        assert result.transient is True

    def test_auth_failure_is_permanent(self):
        result = _classify_gemini_exception(Exception("401 unauthorized"))
        assert result.transient is False

    def test_invalid_request_is_permanent(self):
        result = _classify_gemini_exception(Exception("400 bad request"))
        assert result.transient is False

    def test_unknown_defaults_to_transient(self):
        result = _classify_gemini_exception(RuntimeError("mysterious"))
        assert result.transient is True


class TestGeminiCallApi:
    def test_no_api_key_raises_permanent(self):
        s = GeminiFlashSummarizer(api_key="")
        with pytest.raises(PermanentSummarizerError, match="GEMINI_API_KEY"):
            s._call_api("prompt")

    def test_mocked_happy_path(self, monkeypatch):
        """Mock out the Gemini client to return a Korean string."""
        s = GeminiFlashSummarizer(api_key="fake-key")

        expected_text = _korean_summary(600)
        fake_response = MagicMock()
        fake_response.text = expected_text

        fake_client = MagicMock()
        fake_client.models.generate_content.return_value = fake_response
        s._client = fake_client

        result = s._call_api("fake prompt")
        # _call_api returns the raw response, no truncation at this layer
        assert result == expected_text

    def test_mocked_transient_then_success(self, monkeypatch):
        """First call raises 429, second succeeds."""
        s = GeminiFlashSummarizer(api_key="fake-key")

        # Avoid the 5s sleep in test
        monkeypatch.setattr("pipeline.summarizers.gemini_flash.time.sleep", lambda _: None)

        expected_text = _korean_summary(600)
        fake_response_ok = MagicMock()
        fake_response_ok.text = expected_text

        fake_client = MagicMock()
        fake_client.models.generate_content.side_effect = [
            Exception("429 rate limit exceeded"),
            fake_response_ok,
        ]
        s._client = fake_client

        result = s._call_api("fake prompt")
        assert result == expected_text
        assert fake_client.models.generate_content.call_count == 2

    def test_mocked_persistent_transient_raises(self, monkeypatch):
        s = GeminiFlashSummarizer(api_key="fake-key")
        monkeypatch.setattr("pipeline.summarizers.gemini_flash.time.sleep", lambda _: None)

        fake_client = MagicMock()
        fake_client.models.generate_content.side_effect = Exception("429 rate limit")
        s._client = fake_client

        with pytest.raises(TransientSummarizerError):
            s._call_api("fake prompt")

    def test_mocked_auth_failure_raises_permanent(self, monkeypatch):
        s = GeminiFlashSummarizer(api_key="fake-key")

        fake_client = MagicMock()
        fake_client.models.generate_content.side_effect = Exception("401 unauthorized")
        s._client = fake_client

        with pytest.raises(PermanentSummarizerError):
            s._call_api("fake prompt")


class TestGeminiFullFlow:
    def test_end_to_end_mocked(self, monkeypatch):
        """Full summarize() path with a mocked Gemini response."""
        s = GeminiFlashSummarizer(api_key="fake-key")

        fake_response = MagicMock()
        fake_response.text = _korean_summary(1000)

        fake_client = MagicMock()
        fake_client.models.generate_content.return_value = fake_response
        s._client = fake_client

        result = s.summarize("트랜스크립트 " * 100, _make_video_meta())
        assert isinstance(result, SummarizerResult)
        # Truncation may trim to the last sentence boundary; bounds reflect new 700-1200 target
        assert 700 <= len(result.summary) <= 1000
        assert result.provider == "gemini"
        assert result.model == "gemini-2.5-flash"
        assert result.prompt_version == "v1"
