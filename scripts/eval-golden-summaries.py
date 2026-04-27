#!/usr/bin/env python3
"""Evaluate prompt versions against a fixed transcript golden set."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Callable

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import yaml

from pipeline.config import validate_config_dict  # noqa: E402
from pipeline.models import DiscoverySource, SourceType, VideoMeta  # noqa: E402
from pipeline.summarizers.base import (  # noqa: E402
    Summarizer,
    load_summarizer,
)
from pipeline.summarizers.summary_contract import (  # noqa: E402
    SummaryContract,
    issue_codes,
    parse_markdown_summary,
    validate_summary_contract,
)


SummarizerFactory = Callable[[str], Summarizer]


def main() -> int:
    args = parse_args()
    config = load_eval_config(args.config)
    prompt_versions = [part.strip() for part in args.prompt_versions.split(",") if part.strip()]

    contract = SummaryContract(
        min_chars=config["pipeline"].get("summary_min_chars", args.min_chars),
        max_chars=config["pipeline"].get("summary_max_chars", args.max_chars),
        headline_max_chars=config["pipeline"].get(
            "summary_headline_max_chars",
            args.headline_max_chars,
        ),
    )
    factory = build_summarizer_factory(config)
    result = evaluate_golden_set(
        manifest_path=args.manifest,
        prompt_versions=prompt_versions,
        contract=contract,
        summarizer_factory=factory,
    )

    if args.output_json:
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    if args.output_md:
        args.output_md.parent.mkdir(parents=True, exist_ok=True)
        args.output_md.write_text(render_markdown_report(result), encoding="utf-8")

    print_text_report(result)
    return 0 if all(row["failed"] == 0 for row in result["aggregate"].values()) else 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate and score summaries for a transcript golden set.",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=REPO_ROOT / "tests" / "eval" / "transcripts" / "manifest.json",
    )
    parser.add_argument("--config", type=Path, default=REPO_ROOT / "config.yaml")
    parser.add_argument("--prompt-versions", default="v1,v2")
    parser.add_argument("--min-chars", type=int, default=700)
    parser.add_argument("--max-chars", type=int, default=1200)
    parser.add_argument("--headline-max-chars", type=int, default=24)
    parser.add_argument("--output-json", type=Path)
    parser.add_argument("--output-md", type=Path)
    return parser.parse_args()


def load_eval_config(config_path: Path) -> dict[str, Any]:
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"config root must be a mapping: {config_path}")
    validate_config_dict(raw)
    return raw


def build_summarizer_factory(config: dict[str, Any]) -> SummarizerFactory:
    pipeline_cfg = config["pipeline"]
    summarizer_cfg = pipeline_cfg["summarizer"]

    def factory(prompt_version: str) -> Summarizer:
        summarizer = load_summarizer(
            provider=summarizer_cfg["provider"],
            model=summarizer_cfg["model"],
            prompt_version=prompt_version,
            repair_model=summarizer_cfg.get("repair_model"),
            output_format=summarizer_cfg.get("output_format", "free"),
            temperature=summarizer_cfg.get("temperature"),
            max_output_tokens=summarizer_cfg.get("max_output_tokens", 1600),
            request_timeout_seconds=summarizer_cfg.get("request_timeout_seconds", 90),
            transient_retries=summarizer_cfg.get("transient_retries", 2),
            transient_backoff_seconds=summarizer_cfg.get("transient_backoff_seconds", 5),
        )
        summarizer.min_chars = pipeline_cfg.get("summary_min_chars", 700)
        summarizer.max_chars = pipeline_cfg.get("summary_max_chars", 1200)
        summarizer.headline_max_chars = pipeline_cfg.get("summary_headline_max_chars", 24)
        summarizer.max_retries_on_short = summarizer_cfg.get("short_output_retries", 1)
        summarizer.max_format_repair_attempts = summarizer_cfg.get("repair_attempts", 1)
        summarizer.max_full_retries = summarizer_cfg.get("full_retries", 1)
        summarizer.context_max_chars = pipeline_cfg.get("context_max_chars", 30_000)
        return summarizer

    return factory


def evaluate_golden_set(
    *,
    manifest_path: Path,
    prompt_versions: list[str],
    contract: SummaryContract,
    summarizer_factory: SummarizerFactory,
) -> dict[str, Any]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    items: list[dict[str, Any]] = []
    aggregate = {
        prompt_version: {
            "ok": 0,
            "failed": 0,
            "issue_files": 0,
            "issue_total": 0,
            "avg_summary_chars": 0.0,
            "issues": {},
        }
        for prompt_version in prompt_versions
    }
    char_totals = Counter()
    issue_counts: dict[str, Counter[str]] = {
        prompt_version: Counter() for prompt_version in prompt_versions
    }

    for item in manifest["items"]:
        transcript_path = resolve_manifest_path(item["transcript_path"])
        transcript = transcript_path.read_text(encoding="utf-8")
        row = {
            "video_id": item["video_id"],
            "channel_slug": item.get("channel_slug", "unknown"),
            "source_type": item.get("source_type", "youtube"),
            "chars": item.get("chars", len(transcript)),
            "length_bucket": item.get("length_bucket"),
            "results": {},
        }
        meta = make_video_meta(item)

        for prompt_version in prompt_versions:
            summarizer = summarizer_factory(prompt_version)
            try:
                result = summarizer.summarize(transcript, meta)
                parsed = parse_markdown_summary(result.summary)
                issues = validate_summary_contract(result.summary, contract)
                codes = issue_codes(issues)
                row["results"][prompt_version] = {
                    "status": "ok",
                    "summary_chars": len(result.summary),
                    "body_blocks": len(parsed.paragraphs),
                    "headline_chars": len(parsed.headline or ""),
                    "issues": codes,
                }
                aggregate[prompt_version]["ok"] += 1
                char_totals[prompt_version] += len(result.summary)
                if codes:
                    aggregate[prompt_version]["issue_files"] += 1
                for code in codes:
                    issue_counts[prompt_version][code] += 1
                    aggregate[prompt_version]["issue_total"] += 1
            except Exception as exc:  # noqa: BLE001
                aggregate[prompt_version]["failed"] += 1
                row["results"][prompt_version] = {
                    "status": "failed",
                    "error": f"{type(exc).__name__}: {exc}",
                }

        items.append(row)

    for prompt_version in prompt_versions:
        ok_count = aggregate[prompt_version]["ok"]
        if ok_count:
            aggregate[prompt_version]["avg_summary_chars"] = (
                char_totals[prompt_version] / ok_count
            )
        aggregate[prompt_version]["issues"] = dict(issue_counts[prompt_version])

    return {
        "manifest": str(manifest_path),
        "prompt_versions": prompt_versions,
        "item_count": len(items),
        "contract": {
            "min_chars": contract.min_chars,
            "max_chars": contract.max_chars,
            "headline_max_chars": contract.headline_max_chars,
            "body_paragraphs": contract.body_paragraphs,
        },
        "aggregate": aggregate,
        "items": items,
    }


def resolve_manifest_path(path_text: str) -> Path:
    path = Path(path_text)
    if path.is_absolute():
        return path
    return REPO_ROOT / path


def make_video_meta(item: dict[str, Any]) -> VideoMeta:
    source_type = item.get("source_type", "youtube")
    return VideoMeta(
        video_id=item["video_id"],
        channel_id=item.get("channel_id") or item.get("channel_slug") or "unknown",
        channel_slug=item.get("channel_slug", "unknown"),
        channel_name=item.get("channel_name", item.get("channel_slug", "Unknown")),
        title=item.get("title", item["video_id"]),
        published_at="2026-01-01T00:00:00Z",
        discovery_source=(
            DiscoverySource.NAVER_BLOG_RSS
            if source_type == SourceType.NAVER_BLOG
            else DiscoverySource.RSS
        ),
        source_type=source_type,
    )


def print_text_report(result: dict[str, Any]) -> None:
    print(f"items: {result['item_count']}")
    for prompt_version, row in result["aggregate"].items():
        print(
            f"{prompt_version}: ok={row['ok']} failed={row['failed']} "
            f"issue_files={row['issue_files']} issue_total={row['issue_total']} "
            f"avg_chars={row['avg_summary_chars']:.1f}"
        )
        for code, count in sorted(row["issues"].items()):
            print(f"  {code}: {count}")


def render_markdown_report(result: dict[str, Any]) -> str:
    lines = [
        "# Golden Summary Evaluation",
        "",
        f"- Items: {result['item_count']}",
        f"- Prompt versions: {', '.join(result['prompt_versions'])}",
        "",
        "## Aggregate",
        "",
        "| prompt | ok | failed | issue files | issue total | avg chars |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for prompt_version, row in result["aggregate"].items():
        lines.append(
            f"| {prompt_version} | {row['ok']} | {row['failed']} | "
            f"{row['issue_files']} | {row['issue_total']} | "
            f"{row['avg_summary_chars']:.1f} |"
        )

    lines.extend(["", "## Items", ""])
    for item in result["items"]:
        lines.append(f"### {item['channel_slug']} / {item['video_id']}")
        for prompt_version in result["prompt_versions"]:
            row = item["results"][prompt_version]
            if row["status"] == "ok":
                issues = ", ".join(row["issues"]) or "none"
                lines.append(
                    f"- `{prompt_version}`: {row['summary_chars']} chars, "
                    f"{row['body_blocks']} body blocks, issues: {issues}"
                )
            else:
                lines.append(f"- `{prompt_version}`: failed, {row['error']}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
