#!/usr/bin/env python3
"""Build a one-pass SKU inheritance index from an XLSX workbook.

The index replaces the expensive pattern of querying matching SKU cells and then
reading the whole workbook again to recover adjacent D/F/X fields. It uses one
officecli ``view text`` call and emits only the structured candidates needed by
Layer 3. No workbook library is used.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

from _officecli import (  # noqa: E402
    ensure_utf8_stdio as _utf8_stdio, fail, officecli,
    record_timing as _record_timing, sha256_file,
)


ROW_RE = re.compile(r"^\[/([^/\]]+)/row\[(\d+)\]\]\s?(.*)$")
CELL_RE = re.compile(r"(?:^|\t)([A-Z]{1,3})(\d+)=(.*?)(?=\t[A-Z]{1,3}\d+=|$)", re.DOTALL)
# `view text` (non-JSON) separates sheets with a banner line; a DOTALL cell
# value that reaches end-of-string would swallow it. Treat it as a terminator.
SHEET_BANNER_RE = re.compile(r"^==+\s*Sheet\b")
SKU_RE = re.compile(r"^Z[A-Z0-9]+$")
ROLE_HEADERS = {
    "C": "订单明细",
    "D": "工厂型号",
    "F": "压缩机",
    "X": "铜管规格",
}
FIELDS = {
    "A": "category",
    "B": "product_category",
    "C": "sku",
    "D": "factory_model",
    "E": "configuration",
    "F": "compressor",
    "X": "copper_spec",
}


def normalize(value: str) -> str:
    return value.replace("\u00a0", " ").strip()


def parse_cells(raw: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for match in CELL_RE.finditer(raw):
        values[match.group(1)] = normalize(match.group(3))
    return values


def parse_view_text(text: str) -> list[dict]:
    """Parse officecli text rows without interpreting business semantics.

    A trailing DOTALL cell value must not swallow the sheet banner
    (`=== Sheet: N ===`) that `view text` (non-JSON) prints between sheets:
    strip banner content from the last cell of every sheet's last row.
    """
    rows: list[dict] = []
    current: dict | None = None

    def flush() -> None:
        nonlocal current
        if current is None:
            return
        current["cells"] = parse_cells(current.pop("raw"))
        rows.append(current)
        current = None

    banner_seen = False
    for line in text.splitlines():
        if SHEET_BANNER_RE.match(line):
            banner_seen = True
            continue
        match = ROW_RE.match(line)
        if match:
            flush()
            banner_seen = False
            current = {
                "sheet": match.group(1),
                "row": int(match.group(2)),
                "raw": match.group(3),
            }
        elif current is not None and not banner_seen:
            current["raw"] += "\n" + line
    flush()
    return rows


def is_schema_header(cells: dict[str, str]) -> bool:
    return all(cells.get(col) == value for col, value in ROLE_HEADERS.items())


def candidate_rows(rows: list[dict], allowed_sheets: set[str] | None) -> tuple[list[dict], dict]:
    by_sheet: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        if allowed_sheets is None or row["sheet"] in allowed_sheets:
            by_sheet[row["sheet"]].append(row)

    candidates: list[dict] = []
    sheet_meta: dict[str, dict] = {}
    for sheet, sheet_rows in by_sheet.items():
        headers = [r["row"] for r in sheet_rows if is_schema_header(r["cells"])]
        sheet_meta[sheet] = {"header_rows": headers, "candidate_rows": 0}
        if not headers:
            continue
        for row in sheet_rows:
            prior_headers = [header for header in headers if header < row["row"]]
            if not prior_headers:
                continue
            sku = normalize(row["cells"].get("C", ""))
            if not SKU_RE.fullmatch(sku):
                continue
            record = {
                "sheet": sheet,
                "row": row["row"],
                "header_row": max(prior_headers),
            }
            for col, field in FIELDS.items():
                value = normalize(row["cells"].get(col, ""))
                record[field] = value
            candidates.append(record)
            sheet_meta[sheet]["candidate_rows"] += 1
    return candidates, sheet_meta


def consensus(candidates: list[dict], field: str) -> dict:
    values = sorted({item[field] for item in candidates if item.get(field)})
    if not values:
        return {"status": "missing", "values": [], "value": None}
    if len(values) == 1:
        return {"status": "unique", "values": values, "value": values[0]}
    return {"status": "conflict", "values": values, "value": None}


def build_index(candidates: list[dict]) -> dict:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for item in candidates:
        grouped[item["sku"]].append(item)

    result: dict[str, dict] = {}
    for sku, entries in sorted(grouped.items()):
        result[sku] = {
            "candidate_count": len(entries),
            "candidates": entries,
            "field_consensus": {
                field: consensus(entries, field)
                for field in ("factory_model", "compressor", "copper_spec")
            },
        }
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a one-pass XLSX inheritance index")
    parser.add_argument("--input", type=Path, required=True, help="staged XLSX input")
    parser.add_argument("--output", type=Path, required=True, help="JSON index output")
    parser.add_argument("--sheet", action="append", default=None, help="limit to a sheet; repeatable")
    args = parser.parse_args()
    if not args.input.is_file():
        fail("INPUT_NOT_FOUND", f"input file not found: {args.input}", "Stage the XLSX before Layer 1")

    try:
        proc = officecli("view", str(args.input), "text", timeout=600)
    except OSError as exc:
        fail("OFFICECLI_ERROR", str(exc), "Check officecli installation and PATH")
    if proc.returncode != 0:
        fail(
            "OFFICECLI_VIEW_FAILED",
            proc.stderr,
            "Read the structured stderr and retry once after the standard fix",
            exit_code=3,
        )

    rows = parse_view_text(proc.stdout)
    candidates, sheet_meta = candidate_rows(rows, set(args.sheet) if args.sheet else None)
    payload = {
        "schema": "table-fill-inheritance-index-v1",
        "input": str(args.input),
        "source": "officecli view text (one pass)",
        "sheets": sheet_meta,
        "candidate_rows": len(candidates),
        "index": build_index(candidates),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        f"[INHERITANCE_INDEX] PASS sheets={len(sheet_meta)} "
        f"candidate_rows={len(candidates)} skus={len(payload['index'])} output={args.output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
