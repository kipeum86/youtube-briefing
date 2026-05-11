"""Tests for discovery — RSS primary + yt-dlp catchup fallback."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from pipeline.fetchers import discovery
from pipeline.fetchers.discovery import (
    DiscoveryFailure,
    _filter_shorts,
    _is_rss_saturated,
    _parse_rss_timestamp,
    _parse_ytdlp_output,
    _parse_ytdlp_publish_date,
    _probe_durations,
    _probe_publish_dates,
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

    def test_six_field_format_uses_release_timestamp_when_upload_date_na(self):
        """yt-dlp --flat-playlist often returns NA for upload_date. The 5th
        field (release_timestamp, unix epoch) is the reliable fallback."""
        # 1712534400 = 2024-04-08T00:00:00 UTC
        stdout = "vid123XYZ45|Valid title|NA|1000|1712534400|1712534400\n"
        videos = list(_parse_ytdlp_output(
            stdout, channel_id=VALID_CHANNEL_ID, channel_slug="shuka", channel_name="슈"
        ))
        assert len(videos) == 1
        v = videos[0]
        assert v.published_at.year == 2024
        assert v.published_at.month == 4
        assert v.published_at.day == 8

    def test_six_field_format_timestamp_fallback_when_release_ts_na(self):
        """Falls all the way through to the 6th field (timestamp)."""
        stdout = "vid123XYZ45|Valid title|NA|1000|NA|1712534400\n"
        videos = list(_parse_ytdlp_output(
            stdout, channel_id=VALID_CHANNEL_ID, channel_slug="shuka", channel_name="슈"
        ))
        assert videos[0].published_at.year == 2024

    def test_all_na_triggers_per_video_probe(self, monkeypatch):
        """When flat-playlist returns all-NA for dates, _parse_ytdlp_output
        probes per-video and stamps the probed date on the VideoMeta.

        Regression: parkjonghoon / globelab / jisik-inside consistently
        returned NA for upload_date, release_timestamp, AND timestamp in
        flat-playlist mode. The old code collapsed every such video to the
        same `now()` timestamp, producing identical-to-the-microsecond
        published_at values across unrelated videos."""
        stdout = (
            "vid00001XYZ|첫 영상|NA|1200|NA|NA\n"
            "vid00002XYZ|둘째 영상|NA|900|NA|NA\n"
        )
        real = datetime(2026, 4, 15, 12, 0, 0, tzinfo=timezone.utc)

        def fake_probe(video_ids):
            return {vid: real for vid in video_ids}

        monkeypatch.setattr(discovery, "_probe_publish_dates", fake_probe)

        videos = list(_parse_ytdlp_output(
            stdout, channel_id=VALID_CHANNEL_ID, channel_slug="globelab", channel_name="지"
        ))
        assert len(videos) == 2
        assert all(v.published_at == real for v in videos)

    def test_probe_failure_falls_back_to_now_per_video(self, monkeypatch):
        """If the probe itself raises, each unresolved video falls back to
        now() so the pipeline still progresses — fail open, don't drop."""
        stdout = "vid00001XYZ|영상|NA|1200|NA|NA\n"

        def boom(video_ids):
            raise RuntimeError("yt-dlp exploded")

        monkeypatch.setattr(discovery, "_probe_publish_dates", boom)

        before = datetime.now(timezone.utc)
        videos = list(_parse_ytdlp_output(
            stdout, channel_id=VALID_CHANNEL_ID, channel_slug="shuka", channel_name="슈"
        ))
        after = datetime.now(timezone.utc)
        assert len(videos) == 1
        assert before <= videos[0].published_at <= after

    def test_probe_partial_success_other_videos_fall_back(self, monkeypatch):
        """Probe resolves some videos but not others — only the missing ones
        get now(), resolved ones get the probed date."""
        stdout = (
            "vid00001XYZ|첫|NA|1200|NA|NA\n"
            "vid00002XYZ|둘|NA|900|NA|NA\n"
        )
        real = datetime(2026, 4, 15, 12, 0, 0, tzinfo=timezone.utc)
        monkeypatch.setattr(
            discovery, "_probe_publish_dates",
            lambda ids: {"vid00001XYZ": real},
        )

        before = datetime.now(timezone.utc)
        videos = list(_parse_ytdlp_output(
            stdout, channel_id=VALID_CHANNEL_ID, channel_slug="shuka", channel_name="슈"
        ))
        after = datetime.now(timezone.utc)

        by_id = {v.video_id: v for v in videos}
        assert by_id["vid00001XYZ"].published_at == real
        assert before <= by_id["vid00002XYZ"].published_at <= after


class TestFilterShorts:
    def test_drops_videos_below_threshold(self):
        videos = [
            _make_meta("long0001", duration_seconds=1800),
            _make_meta("short001", duration_seconds=45),
            _make_meta("long0002", duration_seconds=2400),
        ]
        kept = _filter_shorts(videos, 600)
        assert {v.video_id for v in kept} == {"long0001", "long0002"}

    def test_drops_videos_with_unknown_duration_when_filter_enabled(self):
        """Unknown duration is skipped when the duration filter is enabled."""
        videos = [
            _make_meta("known001", duration_seconds=1800),
            _make_meta("unknown1", duration_seconds=None),
            _make_meta("shortvid", duration_seconds=45),
        ]
        kept = _filter_shorts(videos, 600)
        assert {v.video_id for v in kept} == {"known001"}

    def test_none_threshold_keeps_all(self):
        videos = [
            _make_meta("short001", duration_seconds=10),
            _make_meta("long0001", duration_seconds=1800),
        ]
        assert _filter_shorts(videos, None) == videos
        assert _filter_shorts(videos, 0) == videos

    def test_exactly_at_threshold_is_kept(self):
        videos = [_make_meta("border01", duration_seconds=600)]
        assert _filter_shorts(videos, 600) == videos


class TestProbeDurations:
    def test_empty_input_returns_empty_dict(self):
        assert _probe_durations([]) == {}

    def test_parses_yt_dlp_output(self, monkeypatch):
        from unittest.mock import MagicMock
        import subprocess

        fake_result = MagicMock()
        fake_result.returncode = 0
        fake_result.stdout = "abc123|1234\ndef456|45\n"
        fake_result.stderr = ""
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: fake_result)

        out = _probe_durations(["abc123", "def456"])
        assert out == {"abc123": 1234, "def456": 45}

    def test_handles_na_duration(self, monkeypatch):
        from unittest.mock import MagicMock
        import subprocess

        fake_result = MagicMock()
        fake_result.returncode = 0
        fake_result.stdout = "abc123|NA\n"
        fake_result.stderr = ""
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: fake_result)

        out = _probe_durations(["abc123"])
        assert out == {"abc123": None}

    def test_nonzero_exit_with_no_stdout_raises(self, monkeypatch):
        from unittest.mock import MagicMock
        import subprocess

        fake_result = MagicMock()
        fake_result.returncode = 2
        fake_result.stdout = ""
        fake_result.stderr = "boom"
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: fake_result)

        with pytest.raises(RuntimeError, match="duration probe exit"):
            _probe_durations(["abc123"])

    def test_partial_success_with_ignore_errors(self, monkeypatch):
        """yt-dlp --ignore-errors returns rc!=0 but still prints good lines.
        We should accept what we got, not fail the batch."""
        from unittest.mock import MagicMock
        import subprocess

        fake_result = MagicMock()
        fake_result.returncode = 1
        fake_result.stdout = "good123|1800\n"
        fake_result.stderr = "WARNING: bad456 unavailable\n"
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: fake_result)

        out = _probe_durations(["good123", "bad456"])
        assert out == {"good123": 1800}


class TestProbePublishDates:
    def test_empty_input_returns_empty_dict(self):
        assert _probe_publish_dates([]) == {}

    def test_parses_upload_date(self, monkeypatch):
        import subprocess

        fake_result = MagicMock()
        fake_result.returncode = 0
        fake_result.stdout = "abc123|20260408|NA|NA\ndef456|20260407|NA|NA\n"
        fake_result.stderr = ""
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: fake_result)

        out = _probe_publish_dates(["abc123", "def456"])
        assert out["abc123"] == datetime(2026, 4, 8, tzinfo=timezone.utc)
        assert out["def456"] == datetime(2026, 4, 7, tzinfo=timezone.utc)

    def test_falls_through_to_release_timestamp(self, monkeypatch):
        """Non-flat mode still leaves upload_date NA for some videos — the
        chain to release_timestamp / timestamp must still work."""
        import subprocess

        fake_result = MagicMock()
        fake_result.returncode = 0
        # 1712534400 = 2024-04-08 UTC
        fake_result.stdout = "abc123|NA|1712534400|NA\n"
        fake_result.stderr = ""
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: fake_result)

        out = _probe_publish_dates(["abc123"])
        assert out["abc123"].year == 2024
        assert out["abc123"].month == 4
        assert out["abc123"].day == 8

    def test_unresolvable_video_omitted_from_result(self, monkeypatch):
        """Videos where every field is NA are omitted — the caller's
        responsibility to decide a fallback."""
        import subprocess

        fake_result = MagicMock()
        fake_result.returncode = 0
        fake_result.stdout = "abc123|20260408|NA|NA\nbad456|NA|NA|NA\n"
        fake_result.stderr = ""
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: fake_result)

        out = _probe_publish_dates(["abc123", "bad456"])
        assert "abc123" in out
        assert "bad456" not in out

    def test_nonzero_exit_with_no_stdout_raises(self, monkeypatch):
        import subprocess

        fake_result = MagicMock()
        fake_result.returncode = 2
        fake_result.stdout = ""
        fake_result.stderr = "boom"
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: fake_result)

        with pytest.raises(RuntimeError, match="publish date probe exit"):
            _probe_publish_dates(["abc123"])

    def test_partial_success_with_ignore_errors(self, monkeypatch):
        import subprocess

        fake_result = MagicMock()
        fake_result.returncode = 1
        fake_result.stdout = "good123|20260408|NA|NA\n"
        fake_result.stderr = "WARNING: bad456 unavailable\n"
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: fake_result)

        out = _probe_publish_dates(["good123", "bad456"])
        assert out == {"good123": datetime(2026, 4, 8, tzinfo=timezone.utc)}


class TestParseYtdlpPublishDate:
    """_parse_ytdlp_publish_date three-tier fallback.

    Regression: when all yt-dlp fields came back NA or unparseable,
    every catchup video got `now()` as its published_at, which corrupted
    filename dates and the newest-first sort."""

    def test_upload_date_yyyymmdd_is_primary(self):
        dt = _parse_ytdlp_publish_date("20260408", "", "", "vid1")
        assert dt == datetime(2026, 4, 8, tzinfo=timezone.utc)

    def test_falls_through_to_release_timestamp(self):
        # 1712534400 = 2024-04-08 UTC
        dt = _parse_ytdlp_publish_date("NA", "1712534400", "", "vid1")
        assert dt.year == 2024 and dt.month == 4 and dt.day == 8
        assert dt.tzinfo is not None

    def test_falls_through_to_timestamp(self):
        dt = _parse_ytdlp_publish_date("NA", "NA", "1712534400", "vid1")
        assert dt.year == 2024 and dt.month == 4 and dt.day == 8

    def test_all_na_returns_none(self):
        """All-NA returns None so the caller can probe per-video instead of
        collapsing every such video to the same `now()` timestamp."""
        assert _parse_ytdlp_publish_date("NA", "NA", "NA", "vid1") is None

    def test_empty_strings_return_none(self):
        assert _parse_ytdlp_publish_date("", "", "", "vid1") is None

    def test_malformed_upload_date_falls_through(self):
        """Bad YYYYMMDD falls through to the timestamp chain, not None."""
        dt = _parse_ytdlp_publish_date("not-a-date", "1712534400", "", "vid1")
        assert dt is not None
        assert dt.year == 2024


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

    def test_min_duration_filters_shorts_on_rss_path(self, monkeypatch):
        """RSS videos arrive with duration=None, probe populates them,
        Shorts (< min_duration_seconds) get dropped before capping."""
        rss_videos = [
            _make_meta("shortv01"),  # will be probed as 45s
            _make_meta("realv001"),  # will be probed as 1800s
            _make_meta("shortv02"),  # will be probed as 30s
            _make_meta("realv002"),  # will be probed as 2400s
        ]
        monkeypatch.setattr(discovery, "_fetch_rss", lambda *a, **kw: rss_videos)
        monkeypatch.setattr(
            discovery,
            "_probe_durations",
            lambda ids: {"shortv01": 45, "realv001": 1800, "shortv02": 30, "realv002": 2400},
        )

        new = discover_new_videos(
            channel_id=VALID_CHANNEL_ID,
            channel_slug="shuka",
            channel_name="슈카월드",
            known_video_ids=set(),
            min_duration_seconds=600,
        )
        assert {v.video_id for v in new} == {"realv001", "realv002"}
        assert all(v.duration_seconds >= 600 for v in new)

    def test_min_duration_none_disables_filter(self, monkeypatch):
        """min_duration_seconds=None means no probe, no filter."""
        rss_videos = [_make_meta("shortv01"), _make_meta("realv001")]
        probe_calls = {"n": 0}

        def fake_probe(ids):
            probe_calls["n"] += 1
            return {}

        monkeypatch.setattr(discovery, "_fetch_rss", lambda *a, **kw: rss_videos)
        monkeypatch.setattr(discovery, "_probe_durations", fake_probe)

        new = discover_new_videos(
            channel_id=VALID_CHANNEL_ID,
            channel_slug="shuka",
            channel_name="슈카월드",
            known_video_ids=set(),
            min_duration_seconds=None,
        )
        assert len(new) == 2
        assert probe_calls["n"] == 0  # probe skipped when filter disabled

    def test_min_duration_zero_disables_filter(self, monkeypatch):
        """min_duration_seconds=0 disables the filter too."""
        rss_videos = [_make_meta("shortv01"), _make_meta("realv001")]
        monkeypatch.setattr(discovery, "_fetch_rss", lambda *a, **kw: rss_videos)
        monkeypatch.setattr(discovery, "_probe_durations", lambda ids: {})

        new = discover_new_videos(
            channel_id=VALID_CHANNEL_ID,
            channel_slug="shuka",
            channel_name="슈카월드",
            known_video_ids=set(),
            min_duration_seconds=0,
        )
        assert len(new) == 2

    def test_duration_probe_failure_keeps_unverified_candidates(self, monkeypatch):
        """If yt-dlp duration probing fails, keep RSS candidates.

        GitHub Actions YouTube metadata probes can be blocked as bot traffic.
        Failing open keeps a whole source from starving; transcript extraction
        and summarization can still reject thin videos later.
        """
        rss_videos = [_make_meta("vidA00001"), _make_meta("vidB00001")]
        monkeypatch.setattr(discovery, "_fetch_rss", lambda *a, **kw: rss_videos)

        def raise_probe(ids):
            raise RuntimeError("yt-dlp network down")

        monkeypatch.setattr(discovery, "_probe_durations", raise_probe)

        new = discover_new_videos(
            channel_id=VALID_CHANNEL_ID,
            channel_slug="shuka",
            channel_name="슈카월드",
            known_video_ids=set(),
            min_duration_seconds=600,
        )
        assert [v.video_id for v in new] == ["vidA00001", "vidB00001"]
        assert all(v.duration_seconds is None for v in new)

    def test_min_duration_filters_catchup_path(self, monkeypatch):
        """yt-dlp catchup videos already carry duration — filter directly,
        no probe call needed."""
        rss_videos = [_make_meta(f"rssv{i:03d}") for i in range(15)]  # saturated
        catchup_videos = [
            _make_meta("shortcv01", duration_seconds=45),
            _make_meta("realcv001", duration_seconds=1800),
            _make_meta("shortcv02", duration_seconds=30),
        ]
        monkeypatch.setattr(discovery, "_fetch_rss", lambda *a, **kw: rss_videos)
        monkeypatch.setattr(discovery, "_fetch_ytdlp_catchup", lambda *a, **kw: catchup_videos)
        # Guard: probe must NOT be called on catchup path (videos already have durations)
        probe_called = {"n": 0}
        monkeypatch.setattr(
            discovery,
            "_probe_durations",
            lambda ids: (probe_called.__setitem__("n", probe_called["n"] + 1), {})[1],
        )

        new = discover_new_videos(
            channel_id=VALID_CHANNEL_ID,
            channel_slug="shuka",
            channel_name="슈카월드",
            known_video_ids={"ancient999"},  # forces saturation → catchup path
            min_duration_seconds=600,
        )
        assert {v.video_id for v in new} == {"realcv001"}
        assert probe_called["n"] == 0  # no probe on catchup path

    def test_duration_filter_runs_before_cap(self, monkeypatch):
        """If a channel drops 8 shorts and 3 real videos, cap=5 must not
        waste slots on shorts and return 2 real videos. Filter first, then cap."""
        rss_videos = [_make_meta(f"vid{i:05d}") for i in range(11)]
        # First 8 are shorts, last 3 are real
        durations = {
            f"vid{i:05d}": 30 if i < 8 else 1800 for i in range(11)
        }
        monkeypatch.setattr(discovery, "_fetch_rss", lambda *a, **kw: rss_videos)
        monkeypatch.setattr(discovery, "_probe_durations", lambda ids: durations)

        new = discover_new_videos(
            channel_id=VALID_CHANNEL_ID,
            channel_slug="shuka",
            channel_name="슈카월드",
            known_video_ids=set(),
            max_new_videos=5,
            min_duration_seconds=600,
        )
        # All 3 real videos survive, not 5 shorts
        assert len(new) == 3
        assert {v.video_id for v in new} == {"vid00008", "vid00009", "vid00010"}

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
