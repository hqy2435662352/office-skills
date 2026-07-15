#!/usr/bin/env python3
"""
scripts/layer_gate.py - 跨层执行门控

检查前置层的产物文件是否存在，不存在则阻断并输出纠正性指令。
针对 Layer 3/4 还检查映射表是否包含追溯表（物理条件）。
用法: python scripts/layer_gate.py --target <N> --workdir <路径>
退出码: 0=通过, 1=前置产物缺失(致命), 3=追溯表缺失(可重试)
"""

import os
import sys
import argparse
from pathlib import Path
from glob import glob

LAYER_REQUIREMENTS = {
    1: [],
    2: ["*_展平.csv"],
    3: ["*_元数据.yaml"],
    4: ["*_元数据.yaml", "*_映射表.md"],
}

LAYER_NAMES = {
    1: "Layer 1 - Flatten",
    2: "Layer 2 - Classify",
    3: "Layer 3 - Map",
    4: "Layer 4 - Fill",
}


def check_requirements(target: int, workdir: Path) -> tuple[bool, list[str]]:
    patterns = LAYER_REQUIREMENTS.get(target, [])
    missing = []
    for pattern in patterns:
        matches = list(workdir.glob(pattern))
        if not matches:
            missing.append(pattern)
    return len(missing) == 0, missing


def check_traceability_in_mapping(workdir: Path) -> tuple[bool, str]:
    """Verify {name}_映射表.md contains a traceability section.
    
    Layer 3 must embed one of three traceability table headers
    (直接迁移 / 数据聚合 / 数据清洗) per HUMAN_GATE_TEMPLATES.md.
    Returns (has_trace, mapping_file_path).
    """
    mapping_files = list(workdir.glob("*_映射表.md"))
    if not mapping_files:
        return False, ""
    
    trace_headers = [
        "## 追溯表：直接迁移",
        "## 追溯表：数据聚合", 
        "## 追溯表：数据清洗",
    ]
    
    for mf in mapping_files:
        content = mf.read_text(encoding="utf-8")
        for header in trace_headers:
            if header in content:
                return True, str(mf)
    
    return False, str(mapping_files[0])


def main():
    parser = argparse.ArgumentParser(description="跨层执行门控 - 检查前置产物")
    parser.add_argument("--target", type=int, required=False, choices=[1, 2, 3, 4])
    parser.add_argument("--workdir", type=Path, required=True, help="展平元数据输出目录")
    parser.add_argument("--set-gate", type=int, choices=[1, 2, 3], help="设置 Human Gate 待确认标记")
    parser.add_argument("--confirm-gate", type=int, choices=[1, 2, 3], help="确认 Human Gate 通过")
    args = parser.parse_args()

    if not args.workdir.exists():
        print(f"[LAYER_GATE_ERROR] Work directory not found: {args.workdir}", file=sys.stderr)
        sys.exit(1)

    # Human Gate management
    if args.set_gate:
        gate_file = args.workdir / f".gate{args.set_gate}_pending"
        gate_file.write_text(f"Human Gate {args.set_gate} pending confirmation\nset_at=...\n")
        print(f"[GATE_SET] Human Gate {args.set_gate} marked as PENDING. File: {gate_file}")
        print(f"[GATE_SET] Present Layer {args.set_gate} output to user. DO NOT proceed until confirmed.")
        sys.exit(0)

    if args.confirm_gate:
        gate_file = args.workdir / f".gate{args.confirm_gate}_pending"
        if gate_file.exists():
            gate_file.unlink()
            print(f"[GATE_CONFIRMED] Human Gate {args.confirm_gate} confirmed. Gate file removed.")
        else:
            print(f"[GATE_INFO] No pending gate file for Human Gate {args.confirm_gate}.")
        sys.exit(0)

    # Gate check during layer transitions
    if args.target and args.target > 1:
        prev_gate = args.target - 1
        gate_file = args.workdir / f".gate{prev_gate}_pending"
        if gate_file.exists():
            print(f"[LAYER_GATE_ERROR] {LAYER_NAMES[args.target]} BLOCKED.", file=sys.stderr)
            print(f"[LAYER_GATE_ERROR] Human Gate {prev_gate} is PENDING confirmation.", file=sys.stderr)
            print(f"[LAYER_GATE_ERROR] Gate file exists: {gate_file}", file=sys.stderr)
            print(f"[LAYER_GATE_ERROR] CORRECTIVE: Present Layer {prev_gate} output to user, then run:", file=sys.stderr)
            print(f"[LAYER_GATE_ERROR]   python scripts/layer_gate.py --confirm-gate {prev_gate} --workdir {args.workdir}", file=sys.stderr)
            sys.exit(1)

    if args.target is None:
        print("[LAYER_GATE] No action specified. Use --target, --set-gate, or --confirm-gate.")
        sys.exit(1)

    if args.target == 1:
        print(f"[LAYER_GATE_OK] Layer 1 has no prerequisites. Proceed.")
        sys.exit(0)

    ok, missing = check_requirements(args.target, args.workdir)

    if not ok:
        prev_layer = args.target - 1
        print(f"[LAYER_GATE_ERROR] {LAYER_NAMES[args.target]} BLOCKED.", file=sys.stderr)
        print(f"[LAYER_GATE_ERROR] Missing prerequisite outputs in {args.workdir}:", file=sys.stderr)
        for m in missing:
            print(f"  - {m}", file=sys.stderr)
        print(f"[LAYER_GATE_ERROR] Root cause: {LAYER_NAMES[prev_layer]} was not completed or output files missing.", file=sys.stderr)
        print(f"[LAYER_GATE_ERROR] CORRECTIVE ACTION:", file=sys.stderr)
        print(f"[LAYER_GATE_ERROR]   1. Complete {LAYER_NAMES[prev_layer]} first.", file=sys.stderr)
        print(f"[LAYER_GATE_ERROR]   2. Ensure output files exist in {args.workdir}.", file=sys.stderr)
        print(f"[LAYER_GATE_ERROR]   3. Re-run: python scripts/layer_gate.py --target {args.target} --workdir {args.workdir}", file=sys.stderr)
        existing = list(args.workdir.glob("*"))
        print(f"[LAYER_GATE_ERROR] Current files in workdir: {[p.name for p in existing]}", file=sys.stderr)
        sys.exit(1)

    print(f"[LAYER_GATE_OK] {LAYER_NAMES[args.target]} prerequisites satisfied.")

    # Traceability content check (Layer 4: mapping must contain traceability section before fill)
    if args.target == 4:
        has_trace, mapping_path = check_traceability_in_mapping(args.workdir)
        if not has_trace:
            print(f"[LAYER_GATE_ERROR] Traceability table MISSING in mapping file.", file=sys.stderr)
            print(f"[LAYER_GATE_ERROR] File: {mapping_path if mapping_path else '(not found)'}", file=sys.stderr)
            print(f"[LAYER_GATE_ERROR] Layer 3 must embed a traceability table in the mapping file.", file=sys.stderr)
            print(f"[LAYER_GATE_ERROR] Required: one of '## 追溯表：直接迁移', '## 追溯表：数据聚合', or '## 追溯表：数据清洗'", file=sys.stderr)
            print(f"[LAYER_GATE_ERROR] See references/HUMAN_GATE_TEMPLATES.md for the templates.", file=sys.stderr)
            print(f"[LAYER_GATE_ERROR] CORRECTIVE ACTION: add the appropriate traceability table to the mapping file.", file=sys.stderr)
            sys.exit(3)

    sys.exit(0)


if __name__ == "__main__":
    main()
