"""Tests for scripts/eval-golden-summaries.py."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

from pipeline.summarizers.base import SummarizerResult
from pipeline.summarizers.summary_contract import SummaryContract


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "eval-golden-summaries.py"
SPEC = importlib.util.spec_from_file_location("eval_golden_summaries", SCRIPT)
assert SPEC and SPEC.loader
eval_golden_summaries = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = eval_golden_summaries
SPEC.loader.exec_module(eval_golden_summaries)


def _valid_summary() -> str:
    paragraphs = [
        "파월 의장의 발언은 단순한 금리 인하 신호가 아니라 시장 기대와 정책 제약이 동시에 드러난 사건으로 해석된다. 시장은 완화 가능성을 먼저 반영했지만 장기 금리의 움직임은 물가 기대가 쉽게 꺾이지 않는다는 점을 보여준다. 원문은 이 충돌을 통해 중앙은행 메시지와 금융시장 가격이 서로 영향을 주고받는 구조를 강조한다. 특히 인하 신호가 위험자산 선호를 자극하면 정책 완화의 의도와 다른 결과가 나타날 수 있다는 점을 핵심 논지로 제시한다.",
        "근거로는 정책금리 전망, 10년물 국채금리, 달러 인덱스의 엇갈린 움직임이 제시된다. 정책금리 전망이 낮아졌는데도 장기 금리가 반등했다는 점은 투자자들이 단순한 완화보다 인플레이션 재가속 가능성을 가격에 넣고 있음을 뜻한다. 과거 연착륙 국면에서도 인하 기대가 빠르게 커진 뒤 중앙은행이 다시 신중한 태도로 돌아선 사례가 있었다. 이러한 지표 조합은 시장이 기준금리 경로뿐 아니라 성장률, 물가, 재정 여건까지 함께 재평가하고 있음을 보여준다.",
        "따라서 이번 국면은 기준금리 인하 여부 하나로 판단하기 어렵다. 다음 관전 포인트는 물가 지표와 점도표 변화, 그리고 장기 금리가 완화 신호를 얼마나 받아들이는지다. 투자자는 단기적인 낙관보다 금리와 달러, 주식시장이 같은 방향으로 움직이는지 확인해야 하며, 정책 기대가 과도하게 앞서갈 때 생기는 변동성에도 대비해야 한다. 결국 시장의 확신이 강해질수록 작은 지표 변화가 가격을 크게 흔들 수 있다.",
    ]
    return "**연준 인하 신호**\n\n" + "\n\n".join(paragraphs)


class FakeSummarizer:
    provider = "fake"
    model = "fake-model"

    def __init__(self, prompt_version: str, summary: str | None = None):
        self.prompt_version = prompt_version
        self.summary = summary or _valid_summary()

    def summarize(self, transcript, meta):  # noqa: ANN001
        return SummarizerResult(
            summary=self.summary,
            provider=self.provider,
            model=self.model,
            prompt_version=self.prompt_version,
        )


def _write_manifest(tmp_path: Path) -> Path:
    transcript = tmp_path / "transcript.txt"
    transcript.write_text("트랜스크립트 " * 100, encoding="utf-8")
    manifest = {
        "items": [
            {
                "video_id": "abc123XYZ45",
                "channel_slug": "shuka",
                "channel_name": "슈카월드",
                "source_type": "youtube",
                "title": "테스트 영상",
                "transcript_path": str(transcript),
                "chars": len(transcript.read_text(encoding="utf-8")),
                "length_bucket": "<10k",
                "sha256": "x" * 64,
            }
        ]
    }
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    return path


def test_evaluate_golden_set_aggregates_contract_results(tmp_path: Path):
    manifest_path = _write_manifest(tmp_path)

    result = eval_golden_summaries.evaluate_golden_set(
        manifest_path=manifest_path,
        prompt_versions=["v1", "v2"],
        contract=SummaryContract(min_chars=700, max_chars=1200),
        summarizer_factory=lambda prompt_version: FakeSummarizer(prompt_version),
    )

    assert result["item_count"] == 1
    assert result["aggregate"]["v1"]["ok"] == 1
    assert result["aggregate"]["v1"]["failed"] == 0
    assert result["aggregate"]["v1"]["issue_total"] == 0
    assert result["items"][0]["results"]["v2"]["body_blocks"] == 3


def test_evaluate_golden_set_records_contract_issues(tmp_path: Path):
    manifest_path = _write_manifest(tmp_path)
    invalid = _valid_summary().replace("**연준 인하 신호**", "**아주 긴 헤드라인이 계약보다 훨씬 길게 생성된 사례**")

    result = eval_golden_summaries.evaluate_golden_set(
        manifest_path=manifest_path,
        prompt_versions=["v2"],
        contract=SummaryContract(min_chars=700, max_chars=1200, headline_max_chars=8),
        summarizer_factory=lambda prompt_version: FakeSummarizer(prompt_version, invalid),
    )

    assert result["aggregate"]["v2"]["ok"] == 1
    assert result["aggregate"]["v2"]["issue_files"] == 1
    assert result["aggregate"]["v2"]["issues"]["headline_too_long"] == 1


def test_render_markdown_report_contains_aggregate(tmp_path: Path):
    manifest_path = _write_manifest(tmp_path)
    result = eval_golden_summaries.evaluate_golden_set(
        manifest_path=manifest_path,
        prompt_versions=["v1"],
        contract=SummaryContract(min_chars=700, max_chars=1200),
        summarizer_factory=lambda prompt_version: FakeSummarizer(prompt_version),
    )

    report = eval_golden_summaries.render_markdown_report(result)

    assert "Golden Summary Evaluation" in report
    assert "| v1 | 1 | 0 | 0 | 0 |" in report
