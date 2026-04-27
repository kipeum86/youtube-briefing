"""Build the transcript text inserted into summary prompts."""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class SummaryContext:
    """Transcript slice used for one summarization prompt."""

    text: str
    original_chars: int
    included_chars: int
    strategy: str


def build_summary_context(transcript: str, max_chars: int = 30_000) -> SummaryContext:
    """Return prompt-ready transcript text within a character budget.

    Short transcripts are passed through unchanged. Long transcripts are cleaned
    line-by-line, deduped, and then compressed by preserving the beginning and
    more of the ending, where conclusions often appear in economics/current
    affairs content.
    """

    if max_chars < 1:
        raise ValueError("max_chars must be >= 1")

    text = transcript.strip()
    original_chars = len(text)
    if original_chars <= max_chars:
        return SummaryContext(
            text=text,
            original_chars=original_chars,
            included_chars=original_chars,
            strategy="full",
        )

    lines = _dedupe_lines(_normalize_lines(text))
    compact = "\n".join(lines)
    if len(compact) <= max_chars:
        return SummaryContext(
            text=compact,
            original_chars=original_chars,
            included_chars=len(compact),
            strategy="dedupe",
        )

    if len(lines) <= 1:
        compressed = _front_tail_chars(compact, max_chars)
    else:
        compressed = _front_tail_lines(lines, max_chars)

    return SummaryContext(
        text=compressed,
        original_chars=original_chars,
        included_chars=len(compressed),
        strategy="front_tail",
    )


def _normalize_lines(text: str) -> list[str]:
    return [
        re.sub(r"\s+", " ", line).strip()
        for line in text.splitlines()
        if line.strip()
    ]


def _dedupe_lines(lines: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for line in lines:
        if line in seen:
            continue
        seen.add(line)
        deduped.append(line)
    return deduped


def _front_tail_chars(text: str, max_chars: int) -> str:
    front_chars = max(1, int(max_chars * 0.3))
    tail_chars = max_chars - front_chars
    return (text[:front_chars] + text[-tail_chars:]).strip()


def _front_tail_lines(lines: list[str], max_chars: int) -> str:
    front_budget = max(1, int(max_chars * 0.3))
    front_lines, next_index = _take_prefix(lines, front_budget)

    remaining = max_chars - _joined_len(front_lines)
    if front_lines and remaining > 0:
        remaining -= 1  # newline between front and tail groups

    tail_lines = _take_suffix(lines[next_index:], max(0, remaining))
    combined = "\n".join(front_lines + tail_lines).strip()
    if len(combined) <= max_chars:
        return combined
    return combined[:max_chars].rstrip()


def _take_prefix(lines: list[str], budget: int) -> tuple[list[str], int]:
    selected: list[str] = []
    used = 0
    for index, line in enumerate(lines):
        separator = 1 if selected else 0
        projected = used + separator + len(line)
        if projected <= budget:
            selected.append(line)
            used = projected
            continue

        remaining = budget - used - separator
        if remaining > 0:
            selected.append(line[:remaining].rstrip())
            return selected, index + 1
        return selected, index

    return selected, len(lines)


def _take_suffix(lines: list[str], budget: int) -> list[str]:
    if budget <= 0:
        return []

    selected_reversed: list[str] = []
    used = 0
    for line in reversed(lines):
        separator = 1 if selected_reversed else 0
        projected = used + separator + len(line)
        if projected <= budget:
            selected_reversed.append(line)
            used = projected
            continue

        remaining = budget - used - separator
        if remaining > 0:
            selected_reversed.append(line[-remaining:].lstrip())
        break

    return list(reversed(selected_reversed))


def _joined_len(lines: list[str]) -> int:
    if not lines:
        return 0
    return sum(len(line) for line in lines) + len(lines) - 1
