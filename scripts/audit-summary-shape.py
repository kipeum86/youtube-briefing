#!/usr/bin/env python3
"""Audit generated briefing summaries against the UI summary contract."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pipeline.summarizers.summary_contract import (  # noqa: E402
    SummaryContract,
    issue_codes,
    validate_summary_contract,
)


ISSUE_ORDER = [
    "length_below_min",
    "length_above_max",
    "missing_headline",
    "headline_too_long",
    "wrong_block_count",
    "section_label",
    "body_bold",
    "markdown_header",
    "bullet_list",
    "meta_narration",
    "incomplete_sentence",
]


def main() -> int:
    args = parse_args()
    contract = SummaryContract(
        min_chars=args.min_chars,
        max_chars=args.max_chars,
        headline_max_chars=args.headline_max_chars,
        body_paragraphs=args.body_paragraphs,
    )

    result = audit_briefings(
        briefings_dir=args.briefings_dir,
        contract=contract,
        example_limit=args.show_examples,
    )

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print_text_report(result)

    if args.fail_on_issues and result["issue_total"] > 0:
        return 1
    if args.threshold is not None and result["issue_rate"] > args.threshold:
        return 1
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Audit data/briefings summaries for structural drift.",
    )
    parser.add_argument(
        "--briefings-dir",
        type=Path,
        default=REPO_ROOT / "data" / "briefings",
        help="Directory containing briefing JSON files.",
    )
    parser.add_argument("--min-chars", type=int, default=700)
    parser.add_argument("--max-chars", type=int, default=1200)
    parser.add_argument("--headline-max-chars", type=int, default=24)
    parser.add_argument("--body-paragraphs", type=int, default=3)
    parser.add_argument(
        "--show-examples",
        type=int,
        default=0,
        help="Show up to N example files for each issue code.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON.",
    )
    parser.add_argument(
        "--fail-on-issues",
        action="store_true",
        help="Exit nonzero if any issue is found.",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=None,
        help="Exit nonzero if issue_rate exceeds this fraction, e.g. 0.02.",
    )
    return parser.parse_args()


def audit_briefings(
    *,
    briefings_dir: Path,
    contract: SummaryContract,
    example_limit: int = 0,
) -> dict[str, Any]:
    files = sorted(briefings_dir.glob("*.json"))
    issue_counts: Counter[str] = Counter()
    examples: dict[str, list[str]] = defaultdict(list)
    ok_count = 0
    failed_count = 0
    issue_file_count = 0
    corrupt_count = 0

    for path in files:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            corrupt_count += 1
            continue

        if data.get("status") != "ok":
            failed_count += 1
            continue

        ok_count += 1
        summary = data.get("summary") or ""
        issues = validate_summary_contract(summary, contract)
        codes = issue_codes(issues)
        if codes:
            issue_file_count += 1
        for code in codes:
            issue_counts[code] += 1
            if example_limit > 0 and len(examples[code]) < example_limit:
                examples[code].append(path.name)

    ordered_counts = {code: issue_counts.get(code, 0) for code in ISSUE_ORDER}
    for code, count in sorted(issue_counts.items()):
        if code not in ordered_counts:
            ordered_counts[code] = count

    issue_rate = issue_file_count / ok_count if ok_count else 0.0

    return {
        "briefings": len(files),
        "ok": ok_count,
        "failed": failed_count,
        "corrupt": corrupt_count,
        "issue_files": issue_file_count,
        "issue_rate": issue_rate,
        "issue_total": sum(issue_counts.values()),
        "issues": ordered_counts,
        "examples": {code: examples.get(code, []) for code in ordered_counts},
        "contract": {
            "min_chars": contract.min_chars,
            "max_chars": contract.max_chars,
            "headline_max_chars": contract.headline_max_chars,
            "body_paragraphs": contract.body_paragraphs,
        },
    }


def print_text_report(result: dict[str, Any]) -> None:
    print(f"briefings: {result['briefings']}")
    print(f"ok: {result['ok']}")
    print(f"failed: {result['failed']}")
    print(f"corrupt: {result['corrupt']}")
    print(f"issue_files: {result['issue_files']}")
    print(f"issue_rate: {result['issue_rate']:.2%}")
    print(f"issue_total: {result['issue_total']}")
    print("")
    for code, count in result["issues"].items():
        print(f"{code}: {count}")
        examples = result["examples"].get(code) or []
        for example in examples:
            print(f"  - {example}")


if __name__ == "__main__":
    raise SystemExit(main())
