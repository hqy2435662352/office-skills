#!/usr/bin/env python3
"""
scripts/classify_columns.py — deterministic candidate classification for Layer 2.

Consumes the flatten_table.py --meta JSON and produces a candidate
classification YAML (DIMENSION / MEASURE_AGGREGABLE / MEASURE_DERIVED /
METADATA) plus an explicit `uncertain_columns` list for LLM review.

Why: column typing is mostly deterministic (numeric ratio, cardinality).
The script computes the boring 80%; the LLM reviews only the boundary
cases and the business semantics (which column is the price, which is the
model number). This replaces reading the whole flattened table into
context for a from-scratch classification.

Classification signals (self-contained):
  nonempty == 0                     → SKIP (empty column)
  numeric_ratio >= 0.90:
    unique <= 5                     → MEASURE_AGGREGABLE (high confidence)
    unique/nonempty >= 0.8          → MEASURE_DERIVED candidate (medium)
    else                            → MEASURE_AGGREGABLE (medium)
  0.50 <= numeric_ratio < 0.90      → uncertain (mixed text+numbers)
  numeric_ratio < 0.50 (text):
    unique/nonempty <= 0.30         → DIMENSION (high)
    0.30 < unique/nonempty <= 0.80  → uncertain (DIMENSION/METADATA)
    unique/nonempty > 0.80          → METADATA (high: codes/model/Z)

Exit codes:
  0 — Pass (candidates written)
  1 — Fatal (meta missing / unreadable)

Usage:
  python scripts/classify_columns.py --meta <meta.json> --output <candidates.yaml>
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def write_metadata_skeleton(meta: dict, classifications: list, output: Path) -> None:
    """Write {name}_元数据.yaml skeleton with pre-classified columns.

    Layer 2 LLM work shrinks to: (1) resolve UNCERTAIN columns with business
    context, (2) confirm/correct block boundaries, (3) fill data_quality.
    Previously the LLM hand-wrote the whole YAML (~40 lines per sheet).
    """
    lines = []
    lines.append(f"# {meta.get('sheet', '?')}_元数据.yaml (skeleton — LLM completes)")
    lines.append(f"source_file: {json.dumps(meta.get('file', '?'), ensure_ascii=False)}")
    lines.append(f"sheet: {json.dumps(meta.get('sheet', '?'), ensure_ascii=False)}")
    lines.append("")
    lines.append("# 数据块 — LLM 确认/修正边界 (block_type: main_kpi|observation|detail|pivot)")
    lines.append("blocks:")
    blocks = meta.get("blocks", []) or []
    if blocks:
        for b in blocks:
            lines.append(f"  - block_id: {blocks.index(b) + 1}")
            lines.append(f"    block_type: \"detail\"   # LLM 判定")
            lines.append(f"    row_range: [{b.get('start')}, {b.get('end')}]")
            lines.append(f"    description: \"\"        # LLM 填写")
            lines.append(f"    boundary_marker: \"\"    # LLM 填写")
    else:
        lines.append("  # 无候选块 — LLM 根据展平 CSV 手动标注行范围")
    lines.append("")
    lines.append("# 列定义 (确定性分类已填充; UNCERTAIN 列由 LLM 用业务上下文判定; SKIP 空列已省略)")
    lines.append("columns:")
    for entry, (cls, conf, reason) in classifications:
        if cls == "SKIP":
            continue  # empty columns add noise — the LLM doesn't need them
        col = entry.get("col", "?")
        name_hint = (entry.get("samples") or [""])[0][:20]
        lines.append(f"  - {{col_index: {col}, col_name: \"{name_hint}\", "
                     f"classification: \"{cls}\", confidence: \"{conf}\", "
                     f"unit: \"\", note: \"\"}}")
    lines.append("")
    lines.append("# 数据质量 — LLM 填写 (missing_values / error_values / unit_notes)")
    lines.append("data_quality:")
    lines.append("  missing_values: []")
    lines.append("  error_values: []")
    lines.append("  unit_notes: \"\"")
    output.parent.mkdir(parents=True, exist_ok=True)
    with open(output, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"[CLASSIFY_COLUMNS] metadata skeleton written {output}")


def classify_column(entry: dict) -> tuple[str, str, str]:
    """Returns (classification, confidence, reason)."""
    col = entry.get("col", "?")
    nonempty = entry.get("nonempty", 0)
    if nonempty == 0:
        return "SKIP", "high", "empty column"
    nr = entry.get("numeric_ratio", 0.0)
    unique = entry.get("unique", 0)
    card = unique / nonempty if nonempty else 0

    if nr >= 0.90:
        if unique <= 5:
            return "MEASURE_AGGREGABLE", "high", \
                f"numeric ratio {nr:.2f}, few unique values ({unique})"
        if card >= 0.80:
            return "MEASURE_DERIVED", "medium", \
                f"numeric ratio {nr:.2f}, high cardinality ({card:.2f}) — unit price / ratio pattern"
        return "MEASURE_AGGREGABLE", "medium", \
            f"numeric ratio {nr:.2f}, cardinality {card:.2f}"
    if nr >= 0.50:
        return "UNCERTAIN", "low", \
            f"mixed text+numbers (numeric ratio {nr:.2f}) — needs LLM business check"
    # text column
    if card <= 0.30:
        return "DIMENSION", "high", \
            f"text, low cardinality ({card:.2f}) — repeated group labels"
    if card <= 0.80:
        return "UNCERTAIN", "low", \
            f"text, mid cardinality ({card:.2f}) — DIMENSION vs METADATA"
    return "METADATA", "high", \
        f"text, high cardinality ({card:.2f}) — codes / model / IDs"


def main() -> None:
    parser = argparse.ArgumentParser(description="Layer 2 candidate classification")
    parser.add_argument("--meta", type=Path, required=True, help="flatten --meta JSON")
    parser.add_argument("--output", type=Path, required=True, help="candidates YAML")
    parser.add_argument("--metadata", type=Path, default=None,
                        help="also write a {name}_元数据.yaml SKELETON (columns pre-classified, "
                             "LLM only fills uncertain columns + block boundaries + data quality)")
    args = parser.parse_args()

    if not args.meta.is_file():
        print(json.dumps({
            "code": "META_NOT_FOUND",
            "message": f"meta file not found: {args.meta}",
            "corrective_action": "Run flatten_table.py with --meta first.",
        }, ensure_ascii=False), file=sys.stderr)
        sys.exit(1)

    with open(args.meta, "r", encoding="utf-8") as f:
        meta = json.load(f)

    lines = []
    lines.append(f"source_file: {meta.get('file', '?')}")
    lines.append(f"source_sheet: {meta.get('sheet', '?')}")
    dims = meta.get("dimensions", {})
    lines.append("dimensions:")
    for k in ("rows", "cols", "formulas", "errorCells", "tables", "charts", "oleObjects"):
        if k in dims and dims[k] is not None:
            lines.append(f"  {k}: {dims[k]}")
    lines.append("")
    lines.append("# Candidate data blocks — LLM may correct boundaries")
    lines.append("block_candidates:")
    for b in meta.get("blocks", []):
        lines.append(f"  - {{start: {b['start']}, end: {b['end']}, score: {b['score']}, "
                     f"title: {json.dumps(b['title'], ensure_ascii=False)}}}")
    lines.append("")
    lines.append("# Candidate column classifications")
    lines.append("column_classifications:")
    uncertain = []
    classified = []  # (entry, (cls, conf, reason)) for skeleton reuse
    for entry in meta.get("columns", []):
        cls, conf, reason = classify_column(entry)
        if cls == "UNCERTAIN":
            uncertain.append((entry, reason))
            cls = "UNCERTAIN"
        classified.append((entry, (cls, conf, reason)))
        lines.append(f"  - {{col: {entry['col']}, classification: {cls}, "
                     f"confidence: {conf}, nonempty: {entry['nonempty']}, "
                     f"numeric_ratio: {entry.get('numeric_ratio', 0.0)}, "
                     f"unique: {entry.get('unique', 0)}, "
                     f"samples: {json.dumps(entry.get('samples', []), ensure_ascii=False)}}}")
    lines.append("")
    lines.append("# Boundary cases — LLM must review these with business context")
    lines.append("uncertain_columns:")
    for entry, reason in uncertain:
        lines.append(f"  - {{col: {entry['col']}, signals: {json.dumps(entry, ensure_ascii=False)}, "
                     f"question: \"{reason}\"}}")
    lines.append("")
    lines.append("# LLM 审查要点: (1) 修正 uncertain 列 (2) 确认业务语义(哪列是价格/型号/报价) "
                 "(3) 修正块边界 (4) 判定场景(直接迁移/数据聚合/数据清洗) (5) 追踪 DERIVED 公式链")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"[CLASSIFY_COLUMNS] {len(meta.get('columns', []))} columns → "
          f"{len(uncertain)} uncertain, written {args.output}")

    if args.metadata:
        write_metadata_skeleton(meta, classified, args.metadata)

    sys.exit(0)


if __name__ == "__main__":
    main()
