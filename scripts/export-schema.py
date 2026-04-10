#!/usr/bin/env python3
"""Export Pydantic Briefing model's JSON Schema to src/content/briefing.schema.json.

Run this whenever pipeline/models.py changes. The Astro Zod schema in
src/content/config.ts reads this file to stay in sync.

Usage:
    python scripts/export-schema.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Add repo root to path so "pipeline" is importable regardless of where this runs
REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

from pipeline.models import Briefing  # noqa: E402


def main():
    schema = Briefing.model_json_schema()
    output_path = REPO_ROOT / "src" / "content" / "briefing.schema.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(schema, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote {output_path} ({len(json.dumps(schema))} bytes)")


if __name__ == "__main__":
    main()
