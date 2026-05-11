"""Summary shape parsing and validation.

This module defines the contract expected by the briefing UI. Runtime
enforcement lives in the Summarizer policy, where invalid generations can be
repaired or retried before the pipeline writes a placeholder.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class SummaryContract:
    """Structural expectations for one rendered briefing summary."""

    min_chars: int = 700
    max_chars: int = 1200
    headline_max_chars: int = 24
    body_paragraphs: int = 3


@dataclass(frozen=True)
class SummaryValidationIssue:
    """One contract violation found in a summary."""

    code: str
    message: str


@dataclass(frozen=True)
class ParsedSummary:
    """A minimal parse of the markdown-ish summary format."""

    headline: str | None
    paragraphs: list[str]
    raw_blocks: list[str]


HEADLINE_RE = re.compile(r"^\*\*(.+?)\*\*\s*$", re.DOTALL)
SECTION_LABEL_RE = re.compile(
    r"^\*{0,2}\s*(\d+\.\s*)?"
    r"(주제\s*한\s*줄|핵심\s*주장|근거\s*/?\s*데이터|함의|결론|요약)"
    r"\s*\*{0,2}\s*$"
)
BODY_BOLD_RE = re.compile(r"\*\*[^*]+\*\*")
MARKDOWN_HEADER_RE = re.compile(r"^\s*#+\s+", re.MULTILINE)
BULLET_LIST_RE = re.compile(r"^\s*[-*]\s+", re.MULTILINE)
META_NARRATION_RE = re.compile(
    r"^\s*(이\s*(글|영상|콘텐츠)|원문)은\s+.+(다룬다|다룹니다|이야기한다|이야기합니다|설명한다|설명합니다)"
)
SENTENCE_END_RE = re.compile(r"[.!?。！？](?:[\"'”’)\]}]*)$")


def parse_markdown_summary(text: str) -> ParsedSummary:
    """Parse the current summary string format into headline + body blocks."""

    raw_blocks = _split_blocks(text)
    headline: str | None = None
    paragraphs = raw_blocks

    if raw_blocks:
        match = HEADLINE_RE.match(raw_blocks[0])
        if match and "\n" not in match.group(1):
            headline = match.group(1).strip()
            paragraphs = raw_blocks[1:]

    return ParsedSummary(
        headline=headline,
        paragraphs=paragraphs,
        raw_blocks=raw_blocks,
    )


def validate_summary_contract(
    text: str,
    contract: SummaryContract | None = None,
) -> list[SummaryValidationIssue]:
    """Return all shape issues for a summary."""

    contract = contract or SummaryContract()
    summary = text.strip()
    parsed = parse_markdown_summary(summary)
    issues: list[SummaryValidationIssue] = []

    if len(summary) < contract.min_chars:
        issues.append(
            SummaryValidationIssue(
                "length_below_min",
                f"summary has {len(summary)} chars, below {contract.min_chars}",
            )
        )

    if len(summary) > contract.max_chars:
        issues.append(
            SummaryValidationIssue(
                "length_above_max",
                f"summary has {len(summary)} chars, above {contract.max_chars}",
            )
        )

    if parsed.headline is None:
        issues.append(
            SummaryValidationIssue(
                "missing_headline",
                "first block must be a single **headline** line",
            )
        )
    elif len(parsed.headline) > contract.headline_max_chars:
        issues.append(
            SummaryValidationIssue(
                "headline_too_long",
                f"headline has {len(parsed.headline)} chars, above {contract.headline_max_chars}",
            )
        )

    if len(parsed.paragraphs) != contract.body_paragraphs:
        issues.append(
            SummaryValidationIssue(
                "wrong_block_count",
                f"summary has {len(parsed.paragraphs)} body blocks, expected {contract.body_paragraphs}",
            )
        )

    for paragraph in parsed.paragraphs:
        stripped = paragraph.strip()
        if SECTION_LABEL_RE.match(stripped):
            issues.append(
                SummaryValidationIssue(
                    "section_label",
                    "body must not include standalone section labels",
                )
            )
            break

    if any(BODY_BOLD_RE.search(paragraph) for paragraph in parsed.paragraphs):
        issues.append(
            SummaryValidationIssue(
                "body_bold",
                "body paragraphs must not include **bold** markdown",
            )
        )

    if any(MARKDOWN_HEADER_RE.search(paragraph) for paragraph in parsed.paragraphs):
        issues.append(
            SummaryValidationIssue(
                "markdown_header",
                "body paragraphs must not include markdown headers",
            )
        )

    if any(BULLET_LIST_RE.search(paragraph) for paragraph in parsed.paragraphs):
        issues.append(
            SummaryValidationIssue(
                "bullet_list",
                "body paragraphs must not include bullet lists",
            )
        )

    for paragraph in parsed.paragraphs:
        if paragraph.strip() and not SENTENCE_END_RE.search(paragraph.strip()):
            issues.append(
                SummaryValidationIssue(
                    "incomplete_sentence",
                    "body paragraphs must end with sentence-ending punctuation",
                )
            )
            break

    first_body = parsed.paragraphs[0] if parsed.paragraphs else ""
    if first_body and META_NARRATION_RE.search(first_body):
        issues.append(
            SummaryValidationIssue(
                "meta_narration",
                "first body paragraph must not be a meta description",
            )
        )

    return issues


def is_summary_contract_valid(
    text: str,
    contract: SummaryContract | None = None,
) -> bool:
    """True when the summary has no contract issues."""

    return not validate_summary_contract(text, contract)


def issue_codes(issues: Iterable[SummaryValidationIssue]) -> list[str]:
    """Return issue codes in encounter order."""

    return [issue.code for issue in issues]


def _split_blocks(text: str) -> list[str]:
    stripped = text.strip()
    if not stripped:
        return []
    return [block.strip() for block in re.split(r"\n\s*\n+", stripped) if block.strip()]
