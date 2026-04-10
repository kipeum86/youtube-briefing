"""Pydantic models — single source of truth for the briefing schema.

These models are validated on every write to data/briefings/*.json so a field
mismatch in pipeline/run.py fails immediately instead of corrupting the corpus.

The JSON Schema derived from `Briefing` is also exported to
`src/content/briefing.schema.json` at build time so Astro's content collection
Zod schema can stay in sync with one source of truth (this file).
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Annotated

from pydantic import BaseModel, Field, HttpUrl, field_validator, model_validator


class BriefingStatus(str, Enum):
    """Deterministic outcome of processing a single video."""

    OK = "ok"
    FAILED = "failed"


class FailureReason(str, Enum):
    """Stable enum of permanent failure causes.

    Transient failures (network timeouts, rate limits) do NOT write placeholder
    JSONs and therefore do not appear here. See
    pipeline/fetchers/transcript_extractor.py's classification logic.
    """

    SESSION_EXPIRED = "session_expired"
    VIDEO_REMOVED = "video_removed"
    MEMBERS_ONLY = "members_only"
    AGE_RESTRICTED = "age_restricted"
    EMPTY_TRANSCRIPT = "empty_transcript"
    TRANSCRIPTS_DISABLED = "transcripts_disabled"
    SUMMARIZER_REFUSED = "summarizer_refused"
    WRONG_LANGUAGE = "wrong_language"


class DiscoverySource(str, Enum):
    """How a video was discovered by the pipeline."""

    RSS = "rss"
    YTDLP_CATCHUP = "ytdlp_catchup"


class VideoMeta(BaseModel):
    """Lightweight video metadata returned from discovery.

    Used by pipeline/run.py between fetchers/discovery.py and the transcript +
    summarize steps. Contains just enough to decide whether this video needs
    processing.
    """

    video_id: Annotated[str, Field(min_length=5, max_length=20)]
    channel_id: Annotated[str, Field(pattern=r"^UC[A-Za-z0-9_-]{22}$")]
    channel_slug: str
    channel_name: str
    title: str
    published_at: datetime
    discovery_source: DiscoverySource
    duration_seconds: int | None = None


class Briefing(BaseModel):
    """Complete briefing record — one per file in data/briefings/.

    Invariants enforced by cross-field validators:
      - status=OK requires a non-empty summary
      - status=FAILED requires failure_reason to be populated
      - FAILED briefings leave summary null
    """

    # Identity
    video_id: Annotated[str, Field(min_length=5, max_length=20)]
    channel_slug: str
    channel_name: str

    # Source metadata
    title: str
    published_at: datetime
    video_url: HttpUrl
    thumbnail_url: HttpUrl
    duration_seconds: int
    discovery_source: DiscoverySource

    # Outcome
    status: BriefingStatus
    summary: str | None = None
    failure_reason: FailureReason | None = None

    # Provenance
    generated_at: datetime
    provider: str  # e.g. "gemini"
    model: str  # e.g. "gemini-2.5-flash"
    prompt_version: str  # e.g. "v1"

    @field_validator("channel_slug")
    @classmethod
    def slug_is_lowercase(cls, v: str) -> str:
        if not v.islower() or " " in v:
            raise ValueError(f"channel_slug must be lowercase with no spaces: {v!r}")
        return v

    @model_validator(mode="after")
    def check_status_invariants(self) -> Briefing:
        if self.status == BriefingStatus.OK:
            if not self.summary or len(self.summary) < 50:
                raise ValueError(
                    f"status=ok requires a non-empty summary (got {len(self.summary or '')} chars)"
                )
            if self.failure_reason is not None:
                raise ValueError("status=ok must not have a failure_reason")
        elif self.status == BriefingStatus.FAILED:
            if self.failure_reason is None:
                raise ValueError("status=failed requires failure_reason to be set")
            if self.summary is not None:
                raise ValueError("status=failed must have summary=None")
        return self
