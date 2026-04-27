"""Tests for the summary-shape audit script."""

from __future__ import annotations

import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path

from pipeline.summarizers.summary_contract import SummaryContract


SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "audit-summary-shape.py"


def _load_script_module():
    spec = importlib.util.spec_from_file_location("audit_summary_shape", SCRIPT_PATH)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _write_briefing(path: Path, *, summary: str | None, status: str = "ok") -> None:
    payload = {
        "video_id": "abc123XYZ45",
        "channel_slug": "shuka",
        "channel_name": "슈카월드",
        "title": "테스트",
        "published_at": datetime(2026, 4, 9, tzinfo=timezone.utc).isoformat(),
        "video_url": "https://www.youtube.com/watch?v=abc123XYZ45",
        "thumbnail_url": "https://i.ytimg.com/vi/abc123XYZ45/hqdefault.jpg",
        "duration_seconds": 1000,
        "discovery_source": "rss",
        "source_type": "youtube",
        "status": status,
        "summary": summary,
        "failure_reason": None if status == "ok" else "empty_transcript",
        "generated_at": datetime(2026, 4, 9, tzinfo=timezone.utc).isoformat(),
        "provider": "gemini",
        "model": "gemini-2.5-flash",
        "prompt_version": "v1",
    }
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_audit_briefings_counts_issue_files(tmp_path: Path):
    module = _load_script_module()
    valid_summary = "\n\n".join(
        [
            "**연준 인하 신호**",
            "파월 의장의 발언은 시장의 금리 기대를 흔들었다.",
            "도트 플롯과 국채금리의 괴리가 핵심 근거다.",
            "이번 사례는 통화정책 신호의 역설을 보여준다.",
        ]
    )
    invalid_summary = valid_summary.replace("**연준 인하 신호**", "연준 인하 신호")

    _write_briefing(tmp_path / "2026-04-09-shuka-valid1.json", summary=valid_summary)
    _write_briefing(tmp_path / "2026-04-09-shuka-invalid1.json", summary=invalid_summary)
    _write_briefing(tmp_path / "2026-04-09-shuka-failed1.json", summary=None, status="failed")

    result = module.audit_briefings(
        briefings_dir=tmp_path,
        contract=SummaryContract(min_chars=10, max_chars=1000, headline_max_chars=24),
        example_limit=1,
    )

    assert result["briefings"] == 3
    assert result["ok"] == 2
    assert result["failed"] == 1
    assert result["issue_files"] == 1
    assert result["issues"]["missing_headline"] == 1
    assert result["examples"]["missing_headline"] == ["2026-04-09-shuka-invalid1.json"]
