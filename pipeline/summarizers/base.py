"""Summarizer abstraction — swappable LLM provider interface.

Every concrete summarizer implements `Summarizer.summarize(transcript, meta)`,
returning a `SummarizerResult` with the summary text plus provenance fields
that get written into the briefing JSON (provider, model, prompt_version).

This abstraction exists so swapping from Gemini to Sonnet or GPT is a one-line
config change in `config.yaml` (`pipeline.summarizer.provider`). The orchestrator
loads the right subclass by name via `load_summarizer()`.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass

from pipeline.models import VideoMeta
from pipeline.summarizers.summary_contract import (
    ParsedSummary,
    SummaryContract,
    SummaryValidationIssue,
    issue_codes,
    parse_markdown_summary,
    validate_summary_contract,
)

logger = logging.getLogger(__name__)


FORMAT_ONLY_REPAIR_CODES = {
    "headline_too_long",
    "body_bold",
    "section_label",
    "markdown_header",
    "bullet_list",
    "meta_narration",
    "length_above_max",
}

FULL_RETRY_CODES = {
    "length_below_min",
    "wrong_block_count",
}


class SummarizerError(Exception):
    """Base exception for summarizer failures."""


class TransientSummarizerError(SummarizerError):
    """Retry next pipeline run (network, 5xx, rate limits)."""


class PermanentSummarizerError(SummarizerError):
    """Write failed placeholder (4xx, wrong language, refused)."""

    def __init__(self, message: str, failure_code: str):
        super().__init__(message)
        self.failure_code = failure_code  # matches FailureReason enum value


@dataclass
class SummarizerResult:
    """Return value from Summarizer.summarize().

    Contains everything needed to populate the corresponding fields on the
    Briefing model.
    """

    summary: str
    provider: str
    model: str
    prompt_version: str


class Summarizer(ABC):
    """Abstract base class for all summarizer backends.

    Concrete subclasses:
      - pipeline/summarizers/gemini_flash.py :: GeminiFlashSummarizer
      - (future) pipeline/summarizers/claude_sonnet.py
      - (future) pipeline/summarizers/openai_gpt.py

    Subclasses MUST:
      1. Override provider/model/prompt_version class attributes
      2. Implement _build_prompt(transcript, meta) → str
      3. Implement _call_api(prompt) → str (raw response text)
      4. Raise Transient/PermanentSummarizerError on failures
    """

    provider: str = "abstract"
    model: str = "abstract"
    prompt_version: str = "v1"

    # Hard constraints enforced after _call_api returns
    min_chars: int = 700
    max_chars: int = 1200
    headline_max_chars: int = 24
    max_retries_on_short: int = 1
    max_format_repair_attempts: int = 1
    max_full_retries: int = 1
    context_max_chars: int = 30_000

    def summarize(self, transcript: str, meta: VideoMeta) -> SummarizerResult:
        """Produce a Korean deep-analysis summary of the transcript.

        High-level flow:
          1. Build the prompt from the transcript + metadata
          2. Call the API
          3. Validate language and summary shape
          4. Try one format-only repair when the issue is structural
          5. Try one full retry when the output needs a fresh generation
          6. Return SummarizerResult on success, raise on failure

        This method is NOT abstract — it implements the policy. Subclasses
        override _build_prompt and _call_api.
        """
        if not transcript or len(transcript) < 100:
            raise PermanentSummarizerError(
                f"transcript too short to summarize ({len(transcript or '')} chars)",
                failure_code="empty_transcript",
            )

        prompt = self._build_prompt(transcript, meta)
        contract = self._summary_contract()

        format_repairs = 0
        full_retries = 0
        last_issues: list[SummaryValidationIssue] = []

        while True:
            raw = self._call_api(prompt)
            self._validate_language(raw)

            summary = raw.strip()
            retry_summary = summary
            issues = validate_summary_contract(summary, contract)
            if not issues:
                return self._build_result(summary)

            last_issues = issues
            logger.info(
                "[%s] summary contract failed: %s",
                meta.channel_slug,
                ", ".join(issue_codes(issues)),
            )

            if (
                format_repairs < self.max_format_repair_attempts
                and _can_format_repair(issues, summary, contract)
            ):
                format_repairs += 1
                repaired = self._repair_response(summary, issues, contract).strip()
                self._validate_language(repaired)

                repaired_issues = validate_summary_contract(repaired, contract)
                if not repaired_issues:
                    return self._build_result(repaired)

                last_issues = repaired_issues
                retry_summary = repaired
                logger.info(
                    "[%s] summary contract failed after repair: %s",
                    meta.channel_slug,
                    ", ".join(issue_codes(repaired_issues)),
                )

                if not _needs_full_retry(repaired_issues, repaired, contract):
                    break

            if (
                full_retries < self._effective_full_retry_limit()
                and _needs_full_retry(last_issues, retry_summary, contract)
            ):
                full_retries += 1
                logger.info(
                    "[%s] retrying full summary generation after contract failure",
                    meta.channel_slug,
                )
                continue

            break

        codes = ", ".join(issue_codes(last_issues)) or "unknown"
        raise PermanentSummarizerError(
            f"summarizer output failed summary contract after repair/retry: {codes}",
            failure_code="summarizer_refused",
        )

    @abstractmethod
    def _build_prompt(self, transcript: str, meta: VideoMeta) -> str:
        """Construct the LLM prompt from the transcript and video metadata."""

    @abstractmethod
    def _call_api(self, prompt: str) -> str:
        """Send the prompt to the LLM provider and return the raw response text."""

    def _repair_response(
        self,
        raw_response: str,
        issues: list[SummaryValidationIssue],
        contract: SummaryContract,
    ) -> str:
        """Repair a structurally invalid response without re-reading transcript."""
        raise PermanentSummarizerError(
            "summary repair is not implemented for this summarizer",
            failure_code="summarizer_refused",
        )

    # ------------------------------------------------------------------
    # Shared validation helpers
    # ------------------------------------------------------------------

    def _summary_contract(self) -> SummaryContract:
        return SummaryContract(
            min_chars=self.min_chars,
            max_chars=self.max_chars,
            headline_max_chars=self.headline_max_chars,
        )

    def _build_result(self, summary: str) -> SummarizerResult:
        return SummarizerResult(
            summary=summary,
            provider=self.provider,
            model=self.model,
            prompt_version=self.prompt_version,
        )

    def _effective_full_retry_limit(self) -> int:
        # max_retries_on_short is retained for compatibility with existing
        # tests/config; full retries now cover short output and block-count drift.
        return max(self.max_retries_on_short, self.max_full_retries)

    def _validate_language(self, text: str) -> None:
        """Raise PermanentSummarizerError if the response is not Korean.

        Heuristic: at least 30% of non-whitespace characters must be Hangul
        (Unicode blocks U+AC00-U+D7A3 and U+1100-U+11FF).
        """
        non_space = [c for c in text if not c.isspace()]
        if not non_space:
            raise PermanentSummarizerError(
                "summarizer returned empty response",
                failure_code="summarizer_refused",
            )
        hangul_count = sum(1 for c in non_space if _is_hangul(c))
        ratio = hangul_count / len(non_space)
        if ratio < 0.3:
            raise PermanentSummarizerError(
                f"summarizer returned non-Korean output (hangul ratio: {ratio:.0%})",
                failure_code="wrong_language",
            )

    def _truncate_to_limit(self, text: str) -> str:
        """Truncate to max_chars, trying to end on a sentence boundary."""
        text = text.strip()
        if len(text) <= self.max_chars:
            return text

        # Find the last sentence-ending punctuation before max_chars
        candidates = []
        for punct in ("다.", "요.", "음.", ".", "!", "?"):
            idx = text.rfind(punct, 0, self.max_chars)
            if idx != -1:
                candidates.append(idx + len(punct))

        if candidates:
            return text[: max(candidates)].strip()

        # No sentence boundary found — hard truncate with ellipsis
        return text[: self.max_chars - 1] + "…"


def _is_hangul(char: str) -> bool:
    """True if the character is in a Hangul Unicode block."""
    if not char:
        return False
    code = ord(char)
    return (
        0xAC00 <= code <= 0xD7A3  # Hangul syllables
        or 0x1100 <= code <= 0x11FF  # Hangul Jamo
        or 0x3130 <= code <= 0x318F  # Hangul compatibility Jamo
    )


def _can_format_repair(
    issues: list[SummaryValidationIssue],
    summary: str,
    contract: SummaryContract,
) -> bool:
    codes = set(issue_codes(issues))
    if not codes:
        return False

    allowed = set(FORMAT_ONLY_REPAIR_CODES)
    if "missing_headline" in codes:
        if _missing_headline_is_repairable(parse_markdown_summary(summary), contract):
            allowed.add("missing_headline")
        else:
            return False

    return codes.issubset(allowed)


def _needs_full_retry(
    issues: list[SummaryValidationIssue],
    summary: str,
    contract: SummaryContract,
) -> bool:
    codes = set(issue_codes(issues))
    if codes.intersection(FULL_RETRY_CODES):
        return True
    if "missing_headline" in codes:
        return not _missing_headline_is_repairable(
            parse_markdown_summary(summary),
            contract,
        )
    return False


def _missing_headline_is_repairable(
    parsed: ParsedSummary,
    contract: SummaryContract,
) -> bool:
    return len(parsed.raw_blocks) >= contract.body_paragraphs


def load_summarizer(
    provider: str,
    model: str,
    prompt_version: str = "v1",
    repair_model: str | None = None,
    output_format: str = "free",
    temperature: float | None = None,
    max_output_tokens: int | None = 1600,
    request_timeout_seconds: float | None = 90,
    transient_retries: int = 2,
    transient_backoff_seconds: float = 5,
) -> Summarizer:
    """Factory: load the right Summarizer subclass by provider name.

    Keeps the orchestrator free of import-time dependencies on specific
    provider libraries (google-genai, anthropic, openai).

    Raises:
        ValueError: unknown provider
    """
    if provider == "gemini":
        from pipeline.summarizers.gemini_flash import GeminiFlashSummarizer

        return GeminiFlashSummarizer(
            model=model,
            prompt_version=prompt_version,
            repair_model=repair_model,
            output_format=output_format,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
            request_timeout_seconds=request_timeout_seconds,
            transient_retries=transient_retries,
            transient_backoff_seconds=transient_backoff_seconds,
        )

    raise ValueError(
        f"unknown summarizer provider: {provider!r}. "
        f"Supported: gemini"
    )
