#!/usr/bin/env python3
"""
scripts/repair_row_gaps.py — 物化目标 sheet 的行号空洞 (row elements).

行号空洞 = sheet XML 中 row 元素 r 值不连续 (如 1..21, 23..52 → 缺 22)。
officecli 的 `add ... after: /row[N]` 锚点要求 N 元素真实存在; 空洞存在时
插入行会落在空洞之后的 r 值、锚点链永久断裂 (2026-08-12 埃及复盘)。

修复方式 (已实证): 对缺失行执行 `set <sheet>/A<r> numberformat=0.00` —
officecli 会物化空行元素且不留单元格内容。注意 `value:null` 只在 officecli
已规范化 (保存过) 的文件上物化, WPS/Excel 原始 XML 上无效 — 一律用
style-only (numberformat) 写入。

用法:
  python scripts/repair_row_gaps.py --workdir <dir> [--target <staged 文件>]

读 prepare_manifest.json 的 row_gaps (prepare_run.py 阶段 B 输出),
在 staged 副本上物化缺失行元素。

修复后必须:
  1. 重跑 prepare_run.py --flatten (指纹变化, row_gaps 消失)
  2. 更新 fill_spec.yaml 的 target_structure 指纹
  3. 重编译 (compile_fill.py) → 重执行 (execute_batch.py)

Exit codes: 0=pass, 1=fatal, 3=retryable.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from _officecli import (  # noqa: E402
    ensure_utf8_stdio as _utf8_stdio, fail, officecli,
)

MANIFEST_NAME = "prepare_manifest.json"


def load_manifest(workdir: Path) -> dict:
    p = workdir / MANIFEST_NAME
    if not p.is_file():
        fail("MANIFEST_NOT_FOUND",
             f"prepare_manifest.json not found in {workdir} — run the flatten stage first",
             "Run: python scripts/prepare_run.py --workdir <dir> --flatten ...")
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except ValueError as e:
        fail("MANIFEST_INVALID", f"corrupt manifest: {e}",
             "Delete the manifest and re-run prepare_run.py")


def repair(workdir: Path, target: str | None) -> list[dict]:
    manifest = load_manifest(workdir)
    target_entry = manifest.get("target")
    if not target_entry:
        fail("NO_TARGET", "manifest has no target entry",
             "Run prepare_run.py --flatten with --target <staged file>")
    staged = workdir / (target or target_entry["file"])
    if not staged.is_file():
        fail("STAGED_NOT_FOUND", f"staged target not found: {staged}",
             "Stage the target file first (prepare_run.py --outline)")
    sheet = target_entry["sheet"]
    if not sheet.startswith("slide["):  # pptx 无行元素概念, 无空洞可修
        gaps = sorted(set((manifest.get("row_gaps") or {}).get(
            target_entry["name"], [])))
        if not gaps:
            print(json.dumps({"status": "PASS", "code": "NO_ROW_GAPS",
                              "repaired": []}, ensure_ascii=False, indent=2))
            return []
        fixed = []
        for r in gaps:
            path = f"/{sheet}/A{r}"
            proc = officecli("set", str(staged), path,
                             "--prop", "numberformat=0.00")
            if proc.returncode != 0:
                fail("REPAIR_OP_FAILED",
                     f"officecli set {path} failed: {proc.stderr[-400:]}",
                     "Check the sheet name / staged file", exit_code=3)
            fixed.append(r)
        print(json.dumps({"status": "PASS", "code": "ROW_GAPS_REPAIRED",
                          "sheet": sheet, "repaired": fixed,
                          "next": "re-run prepare_run.py --flatten, update the "
                                  "spec target_structure fingerprint, recompile"},
                         ensure_ascii=False, indent=2))
        return fixed
    print(json.dumps({"status": "PASS", "code": "NO_ROW_GAPS", "repaired": []},
                     ensure_ascii=False, indent=2))
    return []


def main() -> None:
    _utf8_stdio()
    parser = argparse.ArgumentParser(
        description="Materialize missing row elements in the staged target "
                    "(row-number gaps that break add-after anchors)")
    parser.add_argument("--workdir", type=Path, required=True, help="ASCII workdir")
    parser.add_argument("--target", type=str, default=None,
                        help="Staged target file name (default: manifest target)")
    args = parser.parse_args()
    repair(args.workdir, args.target)


if __name__ == "__main__":
    main()
