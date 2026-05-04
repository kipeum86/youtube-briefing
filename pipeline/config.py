"""Typed configuration validation for the briefing pipeline."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator


class SummarizerConfig(BaseModel):
    """LLM provider and generation policy."""

    model_config = ConfigDict(extra="forbid")

    provider: str = "gemini"
    model: str = "gemini-2.5-flash"
    repair_model: str | None = None
    prompt_version: str = "v1"
    output_format: Literal["free", "json"] = "json"
    temperature: float | None = Field(default=None, ge=0, le=2)
    max_output_tokens: int | None = Field(default=1600, ge=1)
    request_timeout_seconds: float | None = Field(default=90, gt=0)
    repair_attempts: int = Field(default=1, ge=0)
    full_retries: int = Field(default=1, ge=0)
    short_output_retries: int = Field(default=1, ge=0)
    transient_retries: int = Field(default=2, ge=1)
    transient_backoff_seconds: float = Field(default=5, ge=0)


class PipelineConfig(BaseModel):
    """Top-level pipeline behavior and local paths."""

    model_config = ConfigDict(extra="ignore")

    summarizer: SummarizerConfig
    summary_min_chars: int = Field(default=700, ge=1)
    summary_max_chars: int = Field(default=1200, ge=1)
    summary_headline_max_chars: int = Field(default=24, ge=1)
    max_new_videos_per_channel: int = Field(default=10, ge=1)
    min_duration_seconds: int | None = Field(default=600, ge=0)
    transcript_cache_dir: str = "data/transcripts"
    log_dir: str = "logs"
    context_max_chars: int = Field(default=30_000, ge=1)
    max_discovery_concurrency: int = Field(default=4, ge=1)
    max_processing_concurrency: int = Field(default=2, ge=1)
    cron: str | None = None

    @model_validator(mode="after")
    def check_summary_bounds(self) -> PipelineConfig:
        if self.summary_max_chars < self.summary_min_chars:
            raise ValueError("summary_max_chars must be >= summary_min_chars")
        return self


class ChannelConfig(BaseModel):
    """One YouTube channel source."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    slug: str = Field(min_length=1)


class BlogConfig(BaseModel):
    """One Naver blog source."""

    model_config = ConfigDict(extra="forbid")

    blog_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    slug: str = Field(min_length=1)


class AppConfig(BaseModel):
    """Complete YAML config shape."""

    model_config = ConfigDict(extra="ignore")

    pipeline: PipelineConfig
    channels: list[ChannelConfig] = Field(default_factory=list)
    blogs: list[BlogConfig] = Field(default_factory=list)

    @model_validator(mode="after")
    def require_at_least_one_source(self) -> AppConfig:
        if not self.channels and not self.blogs:
            raise ValueError("config must include at least one channel or blog")
        return self


def validate_config_dict(config: dict[str, Any]) -> AppConfig:
    """Validate a raw YAML config dict and raise ValueError with stable paths."""

    try:
        return AppConfig.model_validate(config)
    except ValidationError as exc:
        raise ValueError(format_config_errors(exc)) from exc


def format_config_errors(exc: ValidationError) -> str:
    """Render Pydantic validation errors with dotted field paths."""

    rendered: list[str] = []
    for error in exc.errors():
        location = ".".join(str(part) for part in error["loc"]) or "config"
        rendered.append(f"{location}: {error['msg']}")
    return "invalid config: " + "; ".join(rendered)
