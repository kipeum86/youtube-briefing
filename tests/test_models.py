"""Pydantic model validation tests.

Covers the deterministic failure contract: status=ok requires summary,
status=failed requires failure_reason, no mixing.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from pipeline.models import (
    Briefing,
    BriefingStatus,
    DiscoverySource,
    FailureReason,
    SummarySections,
    VideoMeta,
)


def _minimal_briefing_kwargs(**overrides):
    base = dict(
        video_id="abc123XYZ45",
        channel_slug="shuka",
        channel_name="슈카월드",
        title="美 연준 금리인하 시그널",
        published_at=datetime(2026, 4, 9, 3, 0, 0, tzinfo=timezone.utc),
        video_url="https://www.youtube.com/watch?v=abc123XYZ45",
        thumbnail_url="https://i.ytimg.com/vi/abc123XYZ45/hqdefault.jpg",
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
    base.update(overrides)
    return base


class TestBriefingHappyPath:
    def test_valid_ok_briefing_constructs(self):
        b = Briefing(**_minimal_briefing_kwargs())
        assert b.status == BriefingStatus.OK
        assert b.summary is not None
        assert b.failure_reason is None

    def test_valid_ok_briefing_accepts_summary_sections(self):
        sections = SummarySections(
            headline="연준 인하 신호",
            thesis="핵심 주장이 들어간 단락입니다.",
            evidence="근거와 숫자가 들어간 단락입니다.",
            implication="함의와 관전 포인트가 들어간 단락입니다.",
        )
        b = Briefing(**_minimal_briefing_kwargs(summary_sections=sections))
        assert b.summary_sections == sections

    def test_valid_failed_briefing_constructs(self):
        b = Briefing(
            **_minimal_briefing_kwargs(
                status=BriefingStatus.FAILED,
                summary=None,
                failure_reason=FailureReason.MEMBERS_ONLY,
            )
        )
        assert b.status == BriefingStatus.FAILED
        assert b.summary is None
        assert b.failure_reason == FailureReason.MEMBERS_ONLY


class TestBriefingInvariants:
    def test_ok_status_requires_summary(self):
        with pytest.raises(ValidationError, match="non-empty summary"):
            Briefing(**_minimal_briefing_kwargs(summary=None))

    def test_ok_status_rejects_short_summary(self):
        with pytest.raises(ValidationError, match="non-empty summary"):
            Briefing(**_minimal_briefing_kwargs(summary="너무 짧음"))

    def test_ok_status_rejects_failure_reason(self):
        with pytest.raises(ValidationError, match="must not have a failure_reason"):
            Briefing(
                **_minimal_briefing_kwargs(
                    failure_reason=FailureReason.VIDEO_REMOVED,
                )
            )

    def test_failed_status_requires_failure_reason(self):
        with pytest.raises(ValidationError, match="requires failure_reason"):
            Briefing(
                **_minimal_briefing_kwargs(
                    status=BriefingStatus.FAILED,
                    summary=None,
                    failure_reason=None,
                )
            )

    def test_failed_status_rejects_summary(self):
        with pytest.raises(ValidationError, match="must have summary=None"):
            Briefing(
                **_minimal_briefing_kwargs(
                    status=BriefingStatus.FAILED,
                    failure_reason=FailureReason.VIDEO_REMOVED,
                    # summary stays default from _minimal_briefing_kwargs (populated)
                )
            )

    def test_failed_status_rejects_summary_sections(self):
        with pytest.raises(ValidationError, match="summary_sections=None"):
            Briefing(
                **_minimal_briefing_kwargs(
                    status=BriefingStatus.FAILED,
                    summary=None,
                    failure_reason=FailureReason.VIDEO_REMOVED,
                    summary_sections={
                        "headline": "연준 인하 신호",
                        "thesis": "핵심 주장이 들어간 단락입니다.",
                        "evidence": "근거와 숫자가 들어간 단락입니다.",
                        "implication": "함의와 관전 포인트가 들어간 단락입니다.",
                    },
                )
            )


class TestBriefingFieldValidation:
    def test_slug_must_be_lowercase(self):
        with pytest.raises(ValidationError, match="lowercase"):
            Briefing(**_minimal_briefing_kwargs(channel_slug="Shuka"))

    def test_slug_rejects_spaces(self):
        with pytest.raises(ValidationError, match="lowercase"):
            Briefing(**_minimal_briefing_kwargs(channel_slug="shuka world"))

    def test_invalid_url_rejected(self):
        with pytest.raises(ValidationError):
            Briefing(**_minimal_briefing_kwargs(video_url="not-a-url"))


class TestVideoMeta:
    def test_valid_video_meta_constructs(self):
        vm = VideoMeta(
            video_id="abc123XYZ45",
            channel_id="UCsT0YIqwnpJCM-mx7-gSA4Q",
            channel_slug="shuka",
            channel_name="슈카월드",
            title="test",
            published_at=datetime.now(timezone.utc),
            discovery_source=DiscoverySource.RSS,
        )
        assert vm.duration_seconds is None  # default

    def test_invalid_channel_id_format_rejected(self):
        with pytest.raises(ValidationError):
            VideoMeta(
                video_id="abc",
                channel_id="not-a-channel-id",
                channel_slug="shuka",
                channel_name="슈카월드",
                title="test",
                published_at=datetime.now(timezone.utc),
                discovery_source=DiscoverySource.RSS,
            )
