"""Tests for transcript context building."""

from __future__ import annotations

import pytest

from pipeline.summarizers.context_builder import build_summary_context


def test_short_transcript_passes_through_unchanged():
    text = "첫 줄입니다.\n\n둘째 줄입니다."

    context = build_summary_context(text, max_chars=100)

    assert context.text == text
    assert context.original_chars == len(text)
    assert context.included_chars == len(text)
    assert context.strategy == "full"


def test_dedupe_can_reduce_below_budget():
    text = "\n".join(["중복 라인"] * 20)

    context = build_summary_context(text, max_chars=20)

    assert context.text == "중복 라인"
    assert context.strategy == "dedupe"
    assert context.included_chars <= 20


def test_long_context_preserves_beginning_and_ending():
    lines = [f"초반 내용 {i}" for i in range(20)]
    lines += [f"중반 내용 {i}" for i in range(100)]
    lines += [f"후반 결론 {i}" for i in range(20)]
    text = "\n".join(lines)

    context = build_summary_context(text, max_chars=220)

    assert context.strategy == "front_tail"
    assert context.included_chars <= 220
    assert "초반 내용 0" in context.text
    assert "후반 결론 19" in context.text
    assert context.original_chars > context.included_chars


def test_single_long_line_preserves_prefix_and_suffix():
    text = "앞" * 500 + "중" * 500 + "뒤" * 500

    context = build_summary_context(text, max_chars=300)

    assert context.strategy == "front_tail"
    assert context.included_chars <= 300
    assert context.text.startswith("앞")
    assert context.text.endswith("뒤")


def test_max_chars_must_be_positive():
    with pytest.raises(ValueError, match="max_chars"):
        build_summary_context("본문", max_chars=0)
