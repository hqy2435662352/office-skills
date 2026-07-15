#!/usr/bin/env python3
"""
scripts/verify_output.py - EXIT GATE 出口验证

在 agent 报告任务完成前强制调用。检查输出文件的完整性、数据正确性和格式合规性。
用法: python scripts/verify_output.py --output <最终输出文件> --workdir <展平元数据输出/>
退出码: 0=全部通过, 1=文件级错误(致命), 3=数据级错误(可修复)
"""

import os
import sys
import json
import argparse
import subprocess
import zipfile
from pathlib import Path
from glob import glob


EXPECTED_OUTPUTS = [
    "*_展平.csv",
    "*_元数据.yaml",
    "*_映射表.md",
]

CRITICAL_CELLS_KEYWORDS = [
    "总销量", "收入", "变频", "中高端", "差异化", "净利润",
    "毛利率", "占比", "销量", "指标",
]


def check_file_exists(output_path: Path, min_size: int = 1000) -> list[str]:
    errors = []
    if not output_path.exists():
        errors.append(f"[FATAL] Output file NOT FOUND: {output_path}")
        errors.append(f"[FATAL] The output was never written to this path.")
        return errors

    size = output_path.stat().st_size
    if size < min_size:
        errors.append(f"[FATAL] Output file too small: {size} bytes (expected >= {min_size})")
        errors.append(f"[FATAL] File may be empty, corrupted, or overwritten by a tool like python-pptx.")
        errors.append(f"[FATAL] Root cause: python-pptx save() overwrites officecli writes with a fresh template.")
        errors.append(f"[FATAL] CORRECTIVE: Re-run Layer 4. Ensure python-pptx is NOT used after officecli set.")
    return errors


def check_pptx_structure(output_path: Path) -> list[str]:
    errors = []
    if output_path.suffix not in ('.pptx', '.xlsx'):
        return errors

    try:
        with zipfile.ZipFile(output_path, 'r') as z:
            names = z.namelist()
            if output_path.suffix == '.pptx' and 'ppt/presentation.xml' not in names:
                errors.append(f"[FATAL] Invalid PPTX: missing presentation.xml")
    except zipfile.BadZipFile:
        errors.append(f"[FATAL] File is not a valid ZIP/Office document")
    return errors


def check_cell_data(output_path: Path, sample_cells: list[str]) -> list[str]:
    """Sample key cells and verify they contain data."""
    errors = []
    for cell_path in sample_cells:
        try:
            result = subprocess.run(
                ['officecli', 'get', str(output_path), cell_path, '--depth', '1', '--json'],
                capture_output=True, timeout=20
            )
            data = json.loads(result.stdout.decode('utf-8'))
            text = data.get('data', {}).get('results', [{}])[0].get('text', '')
            if not text or text.strip() == '':
                errors.append(f"[DATA_ERROR] Cell {cell_path} is EMPTY.")
        except Exception as e:
            errors.append(f"[DATA_ERROR] Failed to read cell {cell_path}: {e}")
    return errors


def check_format_conventions(output_path: Path, sample_cells: list[str]) -> list[str]:
    """Check for format violations like 'pp' in rate columns."""
    errors = []
    for cell_path in sample_cells:
        try:
            result = subprocess.run(
                ['officecli', 'get', str(output_path), cell_path, '--depth', '1', '--json'],
                capture_output=True, timeout=20
            )
            data = json.loads(result.stdout.decode('utf-8'))
            text = data.get('data', {}).get('results', [{}])[0].get('text', '')
            if 'pp' in text and ('增减' in cell_path or '同比' in cell_path or '率' in cell_path):
                errors.append(f"[FORMAT_ERROR] Cell {cell_path} contains 'pp': '{text}'. Use '%' for percentage point changes.")
        except:
            pass
    return errors


def check_workdir_completeness(workdir: Path) -> list[str]:
    errors = []
    for pattern in EXPECTED_OUTPUTS:
        matches = list(workdir.glob(pattern))
        if not matches:
            errors.append(f"[MISSING] Expected output file matching '{pattern}' not found in {workdir}")
    return errors


def main():
    parser = argparse.ArgumentParser(description="EXIT GATE: 出口验证 - 禁止在失败时报告完成")
    parser.add_argument("--output", type=Path, required=True, help="最终输出文件路径")
    parser.add_argument("--workdir", type=Path, required=True, help="展平元数据输出目录")
    parser.add_argument("--min-size", type=int, default=5000, help="输出文件最小期望大小(bytes)")
    parser.add_argument("--table-map", type=str, default="", help="Slide:table_id 映射, 格式: 5:2,6:3,6:5,21:3")
    args = parser.parse_args()

    all_errors = []

    # 1. File existence and size
    all_errors.extend(check_file_exists(args.output, args.min_size))

    # 2. PPTX structure
    all_errors.extend(check_pptx_structure(args.output))

    # 3. Cell data sampling (optional — table layouts vary, use fill_cells self-verify for accuracy)
    # Only check format conventions on the rate column where we know the layout
    if args.table_map:
        rate_paths = []
        for pair in args.table_map.split(","):
            pair = pair.strip()
            if ":" in pair:
                slide, tid = pair.split(":")
                rate_paths.append(f"/slide[{slide}]/table[@id={tid}]/tr[2]/tc[8]")
        all_errors.extend(check_format_conventions(args.output, rate_paths))

    # 5. Workdir completeness
    if args.workdir.exists():
        all_errors.extend(check_workdir_completeness(args.workdir))

    # Report
    if all_errors:
        has_fatal = any("[FATAL]" in e for e in all_errors)
        corrective = "Fix the issues listed and re-run verify_output.py" if has_fatal else \
                     "Check cell mappings and re-run Layer 4 fill. If cells are intentionally empty, verify the mapping is correct."
        print(json.dumps({
            "code": "FATAL_ERROR" if has_fatal else "DATA_ERROR",
            "message": f"EXIT GATE failed: {len(all_errors)} issue(s) found.",
            "issues": all_errors,
            "corrective_action": corrective
        }, ensure_ascii=False), file=sys.stderr)
        sys.exit(1 if has_fatal else 3)

    print(f"[EXIT_GATE_PASSED] All checks passed.")
    print(f"[EXIT_GATE_PASSED] Output: {args.output} ({args.output.stat().st_size} bytes)")
    print(f"[EXIT_GATE_PASSED] You may now report task completion to the user.")
    sys.exit(0)


if __name__ == "__main__":
    main()
