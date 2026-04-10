"""Three-tier YouTube transcript extraction.

Strategy (inherited from parlawatch/pipeline/subtitle_extractor.py, adapted for
the youtube-briefing deterministic failure contract):

  1. youtube-transcript-api  — primary, pure HTTP, no browser
  2. notebooklm-py           — optional, only if NOTEBOOKLM_AUTH_JSON env var is set
  3. yt-dlp VTT download     — last resort, subprocess-based

Each tier returns the full transcript text on success. On failure, the next tier
is tried. If all three fail, we classify the result as either TransientFailure
(retry next run) or PermanentFailure (write failed placeholder per deterministic
failure contract in the design doc).

Caches successful transcripts to {transcript_cache_dir}/{video_id}.txt so
re-summarization after prompt changes does not require re-fetching.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

logger = logging.getLogger(__name__)


Source = Literal["transcript_api_stenographer", "transcript_api_auto", "notebooklm", "ytdlp_stenographer", "ytdlp_auto"]


class TranscriptFailure(Exception):
    """Base exception for transcript extraction failures."""

    def __init__(self, video_id: str, reason: str, transient: bool):
        super().__init__(f"[{video_id}] {reason}")
        self.video_id = video_id
        self.reason = reason
        self.transient = transient


class TransientTranscriptFailure(TranscriptFailure):
    """Temporary failure — retry on next pipeline run, do not write placeholder."""

    def __init__(self, video_id: str, reason: str):
        super().__init__(video_id, reason, transient=True)


class PermanentTranscriptFailure(TranscriptFailure):
    """Permanent failure — write placeholder JSON with status=failed, never retried."""

    def __init__(self, video_id: str, reason: str, failure_code: str):
        super().__init__(video_id, reason, transient=False)
        self.failure_code = failure_code  # stable enum string for failure_reason field


@dataclass
class TranscriptResult:
    text: str
    source: Source


def extract_transcript(video_id: str, cache_dir: Path | str | None = None) -> TranscriptResult:
    """Extract the Korean transcript for a YouTube video.

    Tries three tiers in order. First success wins. All three failing raises
    TranscriptFailure (either Transient or Permanent depending on classification).

    Args:
        video_id: 11-char YouTube video ID (e.g. "abc123XYZ45")
        cache_dir: Optional path to cache transcripts as {video_id}.txt

    Returns:
        TranscriptResult with the full transcript text and the source tier that won.

    Raises:
        TransientTranscriptFailure: retry next run (network hiccups, rate limits)
        PermanentTranscriptFailure: write failed placeholder (members-only, removed, empty)
    """
    # Check cache first
    if cache_dir is not None:
        cache_dir = Path(cache_dir)
        cached = cache_dir / f"{video_id}.txt"
        if cached.exists() and cached.stat().st_size > 100:
            logger.info("transcript cache hit: %s", video_id)
            text = cached.read_text(encoding="utf-8")
            return TranscriptResult(text=text, source="transcript_api_stenographer")

    # Tier 1: youtube-transcript-api
    try:
        result = _try_transcript_api(video_id)
        if result is not None:
            _cache_transcript(result.text, video_id, cache_dir)
            return result
    except _ClassifiedError as e:
        if not e.transient:
            raise PermanentTranscriptFailure(video_id, e.reason, e.code)
        logger.info("tier 1 transient: %s — trying tier 2", e.reason)

    # Tier 2: notebooklm-py (optional)
    if os.environ.get("NOTEBOOKLM_AUTH_JSON", "").strip():
        try:
            result = _try_notebooklm(video_id)
            if result is not None:
                _cache_transcript(result.text, video_id, cache_dir)
                return result
        except _ClassifiedError as e:
            if not e.transient:
                raise PermanentTranscriptFailure(video_id, e.reason, e.code)
            logger.info("tier 2 transient: %s — trying tier 3", e.reason)
    else:
        logger.debug("NOTEBOOKLM_AUTH_JSON not set — skipping tier 2")

    # Tier 3: yt-dlp VTT
    try:
        result = _try_ytdlp(video_id)
        if result is not None:
            _cache_transcript(result.text, video_id, cache_dir)
            return result
    except _ClassifiedError as e:
        if not e.transient:
            raise PermanentTranscriptFailure(video_id, e.reason, e.code)
        logger.info("tier 3 transient: %s", e.reason)

    # All three tiers returned None without raising classified errors.
    # This means "no transcript available" → permanent failure.
    raise PermanentTranscriptFailure(
        video_id,
        "No transcript available from any source (transcript-api, notebooklm, yt-dlp)",
        failure_code="empty_transcript",
    )


# ---------------------------------------------------------------------------
# Internal classification helper
# ---------------------------------------------------------------------------


@dataclass
class _ClassifiedError(Exception):
    reason: str
    transient: bool
    code: str  # stable failure enum value


def _classify_transcript_api_exception(exc: Exception, video_id: str) -> _ClassifiedError:
    """Map youtube-transcript-api exceptions to transient/permanent classification."""
    name = type(exc).__name__
    msg = str(exc).lower()

    # Permanent classifications
    if "videounavailable" in name.lower() or "video unavailable" in msg:
        return _ClassifiedError("Video removed or unavailable", transient=False, code="video_removed")
    if "transcriptsdisabled" in name.lower() or "subtitles are disabled" in msg:
        return _ClassifiedError("Transcripts disabled for this video", transient=False, code="transcripts_disabled")
    if "notranslationsavailable" in name.lower() or "nosubtitleavailable" in name.lower():
        return _ClassifiedError("No Korean subtitles available", transient=False, code="empty_transcript")
    if "agebanned" in name.lower() or "age restricted" in msg:
        return _ClassifiedError("Age-restricted video", transient=False, code="age_restricted")
    if "members-only" in msg or "membersonly" in name.lower():
        return _ClassifiedError("Members-only video", transient=False, code="members_only")

    # Transient classifications
    if "timeout" in name.lower() or "timeout" in msg:
        return _ClassifiedError(f"transcript-api timeout: {exc}", transient=True, code="timeout")
    if "connection" in name.lower() or "network" in msg:
        return _ClassifiedError(f"transcript-api network: {exc}", transient=True, code="network")
    if "429" in msg or "rate" in msg and "limit" in msg:
        return _ClassifiedError("transcript-api rate limited", transient=True, code="rate_limit")

    # Unknown → treat as transient (safer: retries next run)
    return _ClassifiedError(f"transcript-api unknown error: {name}: {exc}", transient=True, code="unknown")


# ---------------------------------------------------------------------------
# Tier 1: youtube-transcript-api
# ---------------------------------------------------------------------------


def _try_transcript_api(video_id: str) -> TranscriptResult | None:
    """Attempt Korean transcript via youtube-transcript-api.

    Returns TranscriptResult on success, None if library is not installed.
    Raises _ClassifiedError on failures that can be mapped to transient/permanent.
    """
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
    except ImportError:
        logger.warning("youtube-transcript-api not installed — skipping tier 1")
        return None

    try:
        ytt_api = YouTubeTranscriptApi()
        transcript = ytt_api.fetch(video_id, languages=["ko"])
    except Exception as exc:  # noqa: BLE001 — library raises many types
        raise _classify_transcript_api_exception(exc, video_id) from exc

    text = _transcript_to_text(transcript)
    if text is None or len(text) < 100:
        return None  # Let tier 2/3 try before declaring empty

    # Detect whether the Korean track was auto-generated
    is_generated = False
    try:
        for t in ytt_api.list(video_id):
            if getattr(t, "language_code", None) == "ko":
                is_generated = bool(getattr(t, "is_generated", False))
                break
    except Exception:  # noqa: BLE001
        pass

    source: Source = "transcript_api_auto" if is_generated else "transcript_api_stenographer"
    logger.info("tier 1 transcript-api: %d chars (%s)", len(text), source)
    return TranscriptResult(text=text, source=source)


def _transcript_to_text(transcript) -> str | None:
    """Convert a FetchedTranscript (or snippet list) to de-duplicated plain text."""
    snippets = getattr(transcript, "snippets", None) or transcript
    lines: list[str] = []
    prev = ""

    for snippet in snippets:
        text = getattr(snippet, "text", None)
        if text is None:
            text = snippet.get("text", "") if isinstance(snippet, dict) else str(snippet)
        text = text.strip()
        if not text or text == prev:
            continue
        if prev and _overlap_ratio(prev, text) > 0.8:
            continue
        lines.append(text)
        prev = text

    return "\n".join(lines) if lines else None


# ---------------------------------------------------------------------------
# Tier 2: notebooklm-py (optional)
# ---------------------------------------------------------------------------


def _try_notebooklm(video_id: str) -> TranscriptResult | None:
    """Attempt transcript via NotebookLM unofficial API.

    Only runs when NOTEBOOKLM_AUTH_JSON env var is set. The caller is expected
    to gate this tier on env var presence — we still guard here for safety.
    """
    try:
        from notebooklm import NotebookLMClient
    except ImportError:
        logger.warning("notebooklm-py not installed — skipping tier 2")
        return None

    async def _extract() -> str | None:
        async with await NotebookLMClient.from_storage() as client:
            nb = await client.notebooks.create(f"yb-temp-{video_id}")
            try:
                url = f"https://www.youtube.com/watch?v={video_id}"
                source = await client.sources.add_url(nb.id, url, wait=True, wait_timeout=180.0)
                fulltext = await client.sources.get_fulltext(nb.id, source.id)
                return fulltext.content
            finally:
                try:
                    await client.notebooks.delete(nb.id)
                except Exception:  # noqa: BLE001
                    pass

    try:
        text = asyncio.run(_extract())
    except Exception as exc:  # noqa: BLE001
        # NotebookLM unofficial API — classify conservatively
        msg = str(exc).lower()
        if "timeout" in msg:
            raise _ClassifiedError(f"notebooklm timeout: {exc}", transient=True, code="timeout") from exc
        if "auth" in msg or "unauthorized" in msg or "401" in msg:
            raise _ClassifiedError("NotebookLM session expired", transient=False, code="session_expired") from exc
        return None  # Let tier 3 try

    if text and len(text) >= 100:
        logger.info("tier 2 notebooklm: %d chars", len(text))
        return TranscriptResult(text=text, source="notebooklm")

    return None  # Let tier 3 try


# ---------------------------------------------------------------------------
# Tier 3: yt-dlp VTT download
# ---------------------------------------------------------------------------


def _try_ytdlp(video_id: str) -> TranscriptResult | None:
    """Attempt transcript via yt-dlp subtitle download.

    Returns None if yt-dlp is not installed or no subtitles are found.
    Raises _ClassifiedError on hard failures (timeout, permanent unavailability).
    """
    url = f"https://www.youtube.com/watch?v={video_id}"

    with tempfile.TemporaryDirectory() as tmpdir:
        template = str(Path(tmpdir) / video_id)
        cmd = [
            "yt-dlp",
            "--write-sub",
            "--write-auto-sub",
            "--sub-lang", "ko",
            "--sub-format", "vtt",
            "--skip-download",
            "-o", template,
            url,
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        except FileNotFoundError:
            logger.warning("yt-dlp binary not found — skipping tier 3")
            return None
        except subprocess.TimeoutExpired:
            raise _ClassifiedError("yt-dlp timeout (120s)", transient=True, code="timeout") from None

        if result.returncode != 0:
            stderr = result.stderr.lower()
            if "members-only" in stderr or "private" in stderr:
                raise _ClassifiedError("Members-only or private", transient=False, code="members_only")
            if "video unavailable" in stderr:
                raise _ClassifiedError("Video removed", transient=False, code="video_removed")
            logger.warning("yt-dlp exit %d: %s", result.returncode, result.stderr[:200])

        # Look for any .vtt file produced
        steno_path = Path(tmpdir) / f"{video_id}.ko.vtt"
        auto_path = Path(tmpdir) / f"{video_id}.ko.auto.vtt"

        if steno_path.exists():
            text = _parse_vtt(steno_path, is_auto=False)
            if text:
                logger.info("tier 3 yt-dlp: %d chars (stenographer)", len(text))
                return TranscriptResult(text=text, source="ytdlp_stenographer")
        if auto_path.exists():
            text = _parse_vtt(auto_path, is_auto=True)
            if text:
                logger.info("tier 3 yt-dlp: %d chars (auto)", len(text))
                return TranscriptResult(text=text, source="ytdlp_auto")

        # Glob fallback for alternate filename patterns yt-dlp sometimes uses
        for vtt_file in Path(tmpdir).glob("*.vtt"):
            is_auto = "auto" in vtt_file.name.lower()
            text = _parse_vtt(vtt_file, is_auto=is_auto)
            if text:
                source: Source = "ytdlp_auto" if is_auto else "ytdlp_stenographer"
                logger.info("tier 3 yt-dlp: %d chars (%s)", len(text), source)
                return TranscriptResult(text=text, source=source)

    return None


def _parse_vtt(vtt_path: Path, is_auto: bool = False) -> str:
    """Parse a WebVTT file to clean plain text."""
    content = vtt_path.read_text(encoding="utf-8")
    lines = []
    prev = ""

    for raw in content.split("\n"):
        line = raw.strip()
        if not line or line.startswith(("WEBVTT", "Kind:", "Language:", "NOTE")):
            continue
        if re.match(r"^\d{2}:\d{2}:\d{2}\.\d{3}\s*-->", line):
            continue
        if re.match(r"^\d+$", line):
            continue

        cleaned = re.sub(r"<[^>]+>", "", line).strip()
        if not cleaned:
            continue

        if is_auto:
            if cleaned == prev:
                continue
            if prev and _overlap_ratio(prev, cleaned) > 0.8:
                continue

        lines.append(cleaned)
        prev = cleaned

    return "\n".join(lines)


def _overlap_ratio(a: str, b: str) -> float:
    """Rough character-position overlap ratio for duplicate-line detection."""
    if not a or not b:
        return 0.0
    shorter = min(len(a), len(b))
    matches = sum(1 for ca, cb in zip(a, b) if ca == cb)
    return matches / shorter


def _cache_transcript(text: str, video_id: str, cache_dir: Path | str | None) -> None:
    """Persist the transcript to disk for re-summarization."""
    if cache_dir is None:
        return
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / f"{video_id}.txt").write_text(text, encoding="utf-8")
