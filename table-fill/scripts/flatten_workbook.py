#!/usr/bin/env python3
"""Flatten several XLSX sheets with one shared outline discovery.

The ordinary ``flatten_table.py`` entry point remains available for one sheet.
Use this planner when a workbook contributes multiple sheets so outline probing,
range sizing, and process startup are not repeated for every sheet.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from _officecli import (  # noqa: E402
    ensure_utf8_stdio as _utf8_stdio, fail, record_timing as _record_timing,
    sha256_file,
)

from flatten_table import flatten_xlsx_file, officecli_outline


def main() -> int:
    parser = argparse.ArgumentParser(description="Flatten multiple XLSX sheets with shared outline")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True,
                        help='JSON: {"targets":[{"sheet":"Sheet","name":"source_home"}]}')
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    if not args.input.is_file():
        fail("INPUT_NOT_FOUND", f"input file not found: {args.input}", "Stage the XLSX before Layer 1")
    if not args.plan.is_file():
        fail("PLAN_NOT_FOUND", f"plan file not found: {args.plan}", "Create the ASCII-safe sheet plan")
    try:
        plan = json.loads(args.plan.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        fail("PLAN_INVALID", str(exc), "Fix the JSON plan and retry", exit_code=3)
    targets = plan.get("targets") if isinstance(plan, dict) else None
    if not isinstance(targets, list) or not targets:
        fail("PLAN_INVALID", "plan must contain non-empty targets[]", "Add sheet/name target entries", exit_code=3)
    names = [item.get("name") for item in targets]
    sheets = [item.get("sheet") for item in targets]
    if any(not isinstance(v, str) or not v for v in names + sheets) or len(set(names)) != len(names):
        fail("PLAN_INVALID", "each target needs a unique sheet and name", "Correct targets[]", exit_code=3)

    try:
        outline = officecli_outline(str(args.input))
    except Exception as exc:
        fail("OUTLINE_FAILED", str(exc), "Read officecli stderr and retry once", exit_code=3)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    results = []
    for item in targets:
        name = item["name"]
        csv_path = args.out_dir / f"{name}_flat.csv"
        meta_path = args.out_dir / f"{name}_meta.json"
        rows, elapsed = flatten_xlsx_file(
            str(args.input), item["sheet"], csv_path, meta_path, outline_data=outline
        )
        results.append({"name": name, "sheet": item["sheet"], "rows": rows, "elapsed_s": elapsed})

    print(json.dumps({"status": "PASS", "shared_outline": True, "results": results}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
