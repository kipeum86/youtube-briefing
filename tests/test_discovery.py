"""Tests for discovery — RSS primary + yt-dlp catchup fallback."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from pipeline.fetchers import discovery
from pipeline.fetchers.discovery import (
    DiscoveryFailure,
    _is_rss_saturated,
    _parse_rss_timestamp,
    _parse_ytdlp_output,
    discover_new_videos,
)
from pipeline.models import DiscoverySource, VideoMeta


VALID_CHANNEL_ID = "UCsT0YIqwnpJCM-mx7-gSA4Q"  # 슈카월드 (real, used only as fixture value)


def _make_meta(video_id: str, **overrides) -> VideoMeta:
    # Pad short test IDs to meet VideoMeta's min_length=5
    padded = video_id if len(video_id) >= 5 else video_id + "_test"
    defaults = dict(
        video_id=padded,
        channel_id=VALID_CHANNEL_ID,
        channel_slug="shuka",
        channel_name="슈카월드",
        title=f"Test video {padded}",
        published_at=datetime(2026, 4, 9, tzinfo=timezone.utc),
        discovery_source=DiscoverySource.RSS,
        duration_seconds=None,
    )
    defaults.update(overrides)
    return VideoMeta(**defaults)


class TestRssSaturation:
    def test_empty_rss_not_saturated(self):
        assert _is_rss_saturated([], set()) is False

    def test_first_run_not_saturated(self):
        """No known videos yet — take what RSS gives us, no need to catchup."""
        videos = [_make_meta(f"vid{i:05d}") for i in range(15)]
        assert _is_rss_saturated(videos, known_video_ids=set()) is False

    def test_known_video_in_rss_not_saturated(self):
        """At least one known video is in the 15-window — we're caught up."""
        videos = [_make_meta(f"vid{i:05d}") for i in range(15)]
        known = {"vid00005"}  # Exists in the middle
        assert _is_rss_saturated(videos, known) is False

    def test_rss_full_window_no_known_is_saturated(self):
        """15 items, none match our known set → we fell behind, need catchup."""
        videos = [_make_meta(f"vid{i:05d}") for i in range(15)]
        known = {"very_old_video", "ancient_video"}
        assert _is_rss_saturated(videos, known) is True

    def test_partial_rss_window_not_saturated(self):
        """Channel with <15 videos total — not saturated even if nothing matches."""
        videos = [_make_meta(f"vid{i:05d}") for i in range(5)]
        known = {"other_video"}
        assert _is_rss_saturated(videos, known) is False


class TestParseRssTimestamp:
    def test_iso_with_offset(self):
        result = _parse_rss_timestamp("2026-04-09T03:00:00+00:00")
        assert result == datetime(2026, 4, 9, 3, 0, 0, tzinfo=timezone.utc)

    def test_iso_with_z_suffix(self):
        result = _parse_rss_timestamp("2026-04-09T03:00:00Z")
        assert result == datetime(2026, 4, 9, 3, 0, 0, tzinfo=timezone.utc)

    def test_empty_falls_back_to_now(self):
        before = datetime.now(timezone.utc)
        result = _parse_rss_timestamp("")
        after = datetime.now(timezone.utc)
        assert before <= result <= after

    def test_malformed_falls_back_to_now(self):
        result = _parse_rss_timestamp("not a timestamp")
        assert isinstance(result, datetime)
        assert result.tzinfo is not None


class TestParseYtdlpOutput:
    def test_valid_line_parses(self):
        stdout = "vid123XYZ45|Test video title|20260409|1847\n"
        videos = list(_parse_ytdlp_output(
            stdout,
            channel_id=VALID_CHANNEL_ID,
            channel_slug="shuka",
            channel_name="슈카월드",
        ))
        assert len(videos) == 1
        v = videos[0]
        assert v.video_id == "vid123XYZ45"
        assert v.title == "Test video title"
        assert v.duration_seconds == 1847
        assert v.discovery_source == DiscoverySource.YTDLP_CATCHUP
        assert v.published_at.year == 2026 and v.published_at.month == 4 and v.published_at.day == 9

    def test_multiple_lines_parse(self):
        stdout = (
            "vid00001XYZ|영상 첫 번째|20260409|1200\n"
            "vid00002XYZ|영상 두 번째|20260408|900\n"
            "vid00003XYZ|영상 세 번째|20260407|2400\n"
        )
        videos = list(_parse_ytdlp_output(
            stdout,
            channel_id=VALID_CHANNEL_ID,
            channel_slug="shuka",
            channel_name="슈카월드",
        ))
        assert len(videos) == 3
        assert videos[0].video_id == "vid00001XYZ"
        assert videos[1].duration_seconds == 900

    def test_empty_stdout_yields_nothing(self):
        assert list(_parse_ytdlp_output("", channel_id=VALID_CHANNEL_ID, channel_slug="shuka", channel_name="슈")) == []

    def test_missing_duration_is_none(self):
        stdout = "vid123XYZ45|Title|20260409|NA\n"
        videos = list(_parse_ytdlp_output(
            stdout, channel_id=VALID_CHANNEL_ID, channel_slug="shuka", channel_name="슈"
        ))
        assert videos[0].duration_seconds is None

    def test_malformed_line_is_skipped(self):
        stdout = "malformed-line\nvid123XYZ45|Valid|20260409|1000\n"
        videos = list(_parse_ytdlp_output(
            stdout, channel_id=VALID_CHANNEL_ID, channel_slug="shuka", channel_name="슈"
        ))
        assert len(videos) == 1
        assert videos[0].video_id == "vid123XYZ45"


class TestDiscoverNewVideos:
    def test_rss_success_returns_only_new_videos(self, monkeypatch):
        rss_videos = [_make_meta("new01"), _make_meta("new02"), _make_meta("old01")]
        monkeypatch.setattr(discovery, "_fetch_rss", lambda *a, **kw: rss_videos)

        new = discover_new_videos(
            channel_id=VALID_CHANNEL_ID,
            channel_slug="shuka",
            channel_name="슈카월드",
            known_video_ids={"old01"},
        )
        assert {v.video_id for v in new} == {"new01", "new02"}

    def test_max_new_videos_caps_rss_path(self, monkeypatch):
        """max_new_videos caps the RSS-returned new list to the N newest items."""
        # 15 RSS items, none known → all 15 are "new"
        rss_videos = [_make_meta(f"rssv{i:03d}") for i in range(15)]
        monkeypatch.setattr(discovery, "_fetch_rss", lambda *a, **kw: rss_videos)

        new = discover_new_videos(
            channel_id=VALID_CHANNEL_ID,
            channel_slug="shuka",
            channel_name="슈카월드",
            known_video_ids=set(),
            max_new_videos=10,
        )
        # Should return the 10 newest (first 10 from the already-sorted list)
        assert len(new) == 10
        assert [v.video_id for v in new] == [f"rssv{i:03d}" for i in range(10)]

    def test_max_new_videos_none_means_no_cap(self, monkeypatch):
        """None (default) returns all new videos unfiltered."""
        rss_videos = [_make_meta(f"rssv{i:03d}") for i in range(15)]
        monkeypatch.setattr(discovery, "_fetch_rss", lambda *a, **kw: rss_videos)

        new = discover_new_videos(
            channel_id=VALID_CHANNEL_ID,
            channel_slug="shuka",
            channel_name="슈카월드",
            known_video_ids=set(),
            max_new_videos=None,
        )
        assert len(new) == 15

    def test_max_new_videos_caps_catchup_path(self, monkeypatch):
        """The cap also applies when the catchup path runs."""
        rss_videos = [_make_meta(f"rssv{i:03d}") for i in range(15)]  # saturated
        catchup_videos = [_make_meta(f"catch{i:03d}") for i in range(30)]
        monkeypatch.setattr(discovery, "_fetch_rss", lambda *a, **kw: rss_videos)
        monkeypatch.setattr(discovery, "_fetch_ytdlp_catchup", lambda *a, **kw: catchup_videos)

        new = discover_new_videos(
            channel_id=VALID_CHANNEL_ID,
            channel_slug="shuka",
            channel_name="슈카월드",
            known_video_ids={"ancient999"},  # forces saturation → catchup path
            max_new_videos=10,
        )
        assert len(new) == 10

    def test_rss_saturation_triggers_ytdlp_catchup(self, monkeypatch):
        # RSS returns 15 videos, none match known_video_ids → saturated
        rss_videos = [_make_meta(f"rssv{i:03d}") for i in range(15)]
        catchup_videos = [_make_meta("catch01"), _make_meta("catch02"), _make_meta("ancient999")]

        monkeypatch.setattr(discovery, "_fetch_rss", lambda *a, **kw: rss_videos)
        monkeypatch.setattr(discovery, "_fetch_ytdlp_catchup", lambda *a, **kw: catchup_videos)

        new = discover_new_videos(
            channel_id=VALID_CHANNEL_ID,
            channel_slug="shuka",
            channel_name="슈카월드",
            known_video_ids={"ancient999"},  # Forces saturation
        )
        # Catchup path — returns catchup videos minus ancient999
        assert {v.video_id for v in new} == {"catch01", "catch02"}

    def test_rss_failure_falls_back_to_ytdlp(self, monkeypatch):
        def raise_rss(*a, **kw):
            raise RuntimeError("RSS 500")

        catchup_videos = [_make_meta("cvid01"), _make_meta("cvid02")]
        monkeypatch.setattr(discovery, "_fetch_rss", raise_rss)
        monkeypatch.setattr(discovery, "_fetch_ytdlp_catchup", lambda *a, **kw: catchup_videos)

        new = discover_new_videos(
            channel_id=VALID_CHANNEL_ID,
            channel_slug="shuka",
            channel_name="슈카월드",
            known_video_ids=set(),
        )
        assert len(new) == 2

    def test_both_tiers_fail_raises(self, monkeypatch):
        def raise_both(*a, **kw):
            raise RuntimeError("boom")

        monkeypatch.setattr(discovery, "_fetch_rss", raise_both)
        monkeypatch.setattr(discovery, "_fetch_ytdlp_catchup", raise_both)

        with pytest.raises(DiscoveryFailure):
            discover_new_videos(
                channel_id=VALID_CHANNEL_ID,
                channel_slug="shuka",
                channel_name="슈카월드",
                known_video_ids=set(),
            )

    def test_rss_catchup_on_saturation_but_catchup_fails_returns_rss(self, monkeypatch):
        """Saturation triggers catchup; if catchup fails, we still return the RSS new videos."""
        rss_videos = [_make_meta(f"rssv{i:03d}") for i in range(15)]
        monkeypatch.setattr(discovery, "_fetch_rss", lambda *a, **kw: rss_videos)
        monkeypatch.setattr(
            discovery,
            "_fetch_ytdlp_catchup",
            lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("yt-dlp down")),
        )

        # Saturated: known doesn't match the 15 rss items
        new = discover_new_videos(
            channel_id=VALID_CHANNEL_ID,
            channel_slug="shuka",
            channel_name="슈카월드",
            known_video_ids={"ancient999"},
        )
        # All 15 rss videos are new → returned
        assert len(new) == 15
