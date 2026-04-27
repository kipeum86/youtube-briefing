"""Tests for summary shape parsing and validation."""

from __future__ import annotations

from pipeline.summarizers.summary_contract import (
    SummaryContract,
    issue_codes,
    parse_markdown_summary,
    validate_summary_contract,
)


TEST_CONTRACT = SummaryContract(
    min_chars=50,
    max_chars=1000,
    headline_max_chars=12,
    body_paragraphs=3,
)


def _summary(
    *,
    headline: str = "연준 인하 신호",
    body: list[str] | None = None,
) -> str:
    paragraphs = body or [
        "파월 의장의 발언은 시장의 금리 기대를 흔들었다. 장기 금리는 완화 신호와 반대로 움직였다.",
        "도트 플롯과 국채금리의 괴리는 투자자들이 물가 재상승 가능성을 반영하고 있음을 보여준다.",
        "이번 사례는 통화정책 신호가 시장 기대를 통해 스스로 효과를 약화시킬 수 있음을 시사한다.",
    ]
    return "\n\n".join([f"**{headline}**", *paragraphs])


def _codes(summary: str) -> list[str]:
    return issue_codes(validate_summary_contract(summary, TEST_CONTRACT))


def test_parse_markdown_summary_extracts_headline_and_body():
    parsed = parse_markdown_summary(_summary())

    assert parsed.headline == "연준 인하 신호"
    assert len(parsed.paragraphs) == 3
    assert len(parsed.raw_blocks) == 4


def test_valid_summary_has_no_issues():
    assert _codes(_summary()) == []


def test_missing_headline_is_reported():
    summary = _summary().replace("**연준 인하 신호**", "연준 인하 신호")

    assert "missing_headline" in _codes(summary)


def test_headline_too_long_is_reported():
    summary = _summary(headline="연준 인하 신호와 장기금리 역설")

    assert "headline_too_long" in _codes(summary)


def test_wrong_block_count_is_reported():
    summary = _summary(body=["첫 단락입니다.", "둘째 단락입니다."])

    assert "wrong_block_count" in _codes(summary)


def test_section_label_is_reported():
    summary = _summary(
        body=[
            "파월 의장의 발언은 시장의 금리 기대를 흔들었다.",
            "**핵심 주장**",
            "이번 사례는 통화정책 신호의 역설을 보여준다.",
        ]
    )

    assert "section_label" in _codes(summary)


def test_body_bold_is_reported():
    summary = _summary(
        body=[
            "파월 의장의 발언은 시장의 금리 기대를 흔들었다.",
            "도트 플롯에서 **정책금리 전망**은 낮아졌지만 장기 금리는 올랐다.",
            "이번 사례는 통화정책 신호의 역설을 보여준다.",
        ]
    )

    assert "body_bold" in _codes(summary)


def test_markdown_header_is_reported():
    summary = _summary(
        body=[
            "파월 의장의 발언은 시장의 금리 기대를 흔들었다.",
            "## 근거\n도트 플롯과 국채금리가 엇갈렸다.",
            "이번 사례는 통화정책 신호의 역설을 보여준다.",
        ]
    )

    assert "markdown_header" in _codes(summary)


def test_bullet_list_is_reported():
    summary = _summary(
        body=[
            "파월 의장의 발언은 시장의 금리 기대를 흔들었다.",
            "- 도트 플롯은 낮아졌다.\n- 국채금리는 올랐다.",
            "이번 사례는 통화정책 신호의 역설을 보여준다.",
        ]
    )

    assert "bullet_list" in _codes(summary)


def test_meta_narration_is_reported():
    summary = _summary(
        body=[
            "이 영상은 금리 문제를 설명합니다. 시장 기대가 어떻게 바뀌었는지도 다룹니다.",
            "도트 플롯과 국채금리의 괴리가 핵심 근거다.",
            "이번 사례는 통화정책 신호의 역설을 보여준다.",
        ]
    )

    assert "meta_narration" in _codes(summary)


def test_length_bounds_are_reported():
    low = validate_summary_contract(_summary(), SummaryContract(min_chars=1000, max_chars=2000))
    high = validate_summary_contract(_summary(), SummaryContract(min_chars=1, max_chars=10))

    assert "length_below_min" in issue_codes(low)
    assert "length_above_max" in issue_codes(high)
