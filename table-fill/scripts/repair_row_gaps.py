#!/usr/bin/env python3
"""
scripts/repair_row_gaps.py — input-repair CLI (ADR 0021).

Row-number gaps (`row` element r-values discontinuous in sheet XML) break
officecli `add ... after: /row[N]` anchor chains permanently. This CLI repairs a
COPY of a workbook and produces a NEW repaired input snapshot — input-version
repair, NOT workspace mutation.

**You usually do not need this.** `workspace_init --init` repairs row gaps inside
its own staging step by default (detect → repair the staged copy → then hash /
outline / flatten), so the ordinary flow is a single pass. This CLI remains for
the two cases init must decline:

  * `--no-repair` was used (byte-fidelity of the source was requested), or
  * the caller's source path IS the staged path, so repairing in place would
    mutate the caller's own file (init reports `reason: source-is-staged-file`).

It is also the way to repair a workbook WITHOUT building a workspace.

Detection and mutation are NOT implemented here — they live in `flatten_table`
(the single home for xlsx structural facts: prefix-agnostic readers + the
`SheetNotFound` fail-closed path). This file is only CLI + copy-to-snapshot +
verify + report. (Recorded 2026-09: this script used to carry a private copy of
the row regex written as `<row\\b`, which matches 0 rows on any officecli-touched
workbook — officecli writes `<x:row>` — so it answered NO_ROW_GAPS for sheets
that still had gaps, and it resolved the worksheet by "first sheet wins",
ignoring --sheet entirely.)

It performs NO: manifest refresh, incremental flatten, fingerprint patching,
spec patching, recompile, or any next-state derivation.

Usage:
  python scripts/repair_row_gaps.py --input <staged.xlsx> (--sheet <Name> | --all)
                                     [--out <repaired.xlsx>]

Exit codes: 0=pass (repaired or no gaps), 1=fatal (env/file/typo), 3=retryable.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _officecli import ensure_utf8_stdio as _utf8_stdio, fail  # noqa: E402
from flatten_table import (  # noqa: E402  (xlsx 结构事实唯一实现地)
    SheetNotFound, all_row_gaps, flush_workbook, materialize_rows, row_gaps,
    row_gaps_in_xml, sheet_names, sheet_rel_path,
)

# 兼容别名 (旧内部 helper 名): 语义不变, 实现统一在 flatten_table。
sheet_target_path = sheet_rel_path


def row_gaps_in(book: Path, sheet_path: str) -> list:
    """给定 worksheet 路径读 row 空洞 (前缀无关)."""
    with zipfile.ZipFile(book) as z:
        try:
            xml = z.read(sheet_path).decode("utf-8", errors="replace")
        except KeyError:
            fail("SHEET_XML_MISSING", f"{sheet_path} 不存在于 {book}",
                 "检查 sheet 路径解析", exit_code=1)
    return row_gaps_in_xml(xml)


def verify_no_gaps(book: Path, sheet_path: str) -> bool:
    return not row_gaps_in(book, sheet_path)


def _plan(book: Path, sheet, do_all: bool) -> dict:
    """{sheet: gaps} 限定在请求的 scope 内 (拼错 fail-closed)."""
    try:
        available = sheet_names(book)
    except (KeyError, OSError, zipfile.BadZipFile):
        fail("NOT_XLSX", f"{book} 不是标准 xlsx (缺 workbook.xml)",
             "用 officecli 或 Excel 保存后重试", exit_code=1)
    if do_all:
        return all_row_gaps(book)
    if sheet not in available:
        # 拼错绝不能退化成"在别的 sheet 上静默 no-op"。
        fail("SHEET_NOT_FOUND",
             f"sheet {sheet!r} 不在 {book.name} (可用: {available})",
             "用 outline 文件确认 sheet 名, 或用 --all 修复全部 sheet",
             exit_code=1)
    gaps = row_gaps(book, sheet)
    return {sheet: gaps} if gaps else {}


def repair_copy(input_path: Path, out_path: Path, sheet, do_all: bool) -> dict:
    """副本上物化缺失行元素 → close 刷盘 → 逐 sheet 复核 → 输出 snapshot.

    fails closed: 任一步失败都不产生"半修复被当作快照"的状态 — 调用方应删除
    out 并重试 (幂等: 已修复的副本再跑 → NO_ROW_GAPS)。"""
    if input_path.suffix.lower() not in (".xlsx", ".xlsm", ".xltx", ".xltm"):
        return {"status": "PASS", "code": "NO_ROW_GAPS", "repaired": [],
                "sheets": [],
                "reason": f"{input_path.suffix}: 无行元素空洞域 (pptx/docx)"}

    plan = _plan(input_path, sheet, do_all)
    if not plan:
        return {"status": "PASS", "code": "NO_ROW_GAPS", "repaired": [],
                "sheets": [],
                "reason": "row r 值连续" if not do_all
                          else "全部 sheet row r 值连续"}

    out_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(input_path, out_path)
    try:
        out_path.chmod(0o644)  # stage_files 会设只读; 副本需可写
    except OSError:
        pass

    per_sheet, total = [], []
    for sh, gaps in plan.items():
        try:
            fixed = materialize_rows(out_path, sh, gaps)
        except RuntimeError as e:
            fail("REPAIR_OP_FAILED", str(e), "检查 sheet 名 / 副本文件", exit_code=3)
        per_sheet.append({"sheet": sh, "gaps_before": gaps,
                          "repaired": fixed, "gaps_after": None})
        total.extend(fixed)

    # 强制刷盘: resident 延迟写未落盘时, 紧跟的验证会复见空洞 (2026-08-13 实测)。
    try:
        flush_workbook(out_path)
    except RuntimeError as e:
        fail("REPAIR_FLUSH_FAILED", f"{e} — 行元素已物化但可能未刷盘",
             "删除 out 后重跑 (幂等)", exit_code=3)

    # 逐 sheet 复核 — 用同一前缀无关检测器 (旧的验证同样失明)
    residual = {}
    for rec in per_sheet:
        after = row_gaps(out_path, rec["sheet"])
        rec["gaps_after"] = after
        if after:
            residual[rec["sheet"]] = after
    if residual:
        fail("REPAIR_VERIFY_FAILED", f"副本 {out_path} 仍含行号空洞: {residual}",
             "删除 out 并重跑 (幂等); 若持续, 检查是否 officecli close 未刷盘",
             exit_code=3)

    return {"status": "PASS", "code": "ROW_GAPS_REPAIRED",
            "repaired": total, "sheets": per_sheet,
            "input": str(input_path), "output_snapshot": str(out_path),
            "next": "以该 repaired snapshot 为输入重新 workspace_init --init "
                    "(canonical re-entry, ADR 0021) → materialize_run → 刷新 "
                    "FillSpec 指纹 → Compile Clean → Spec Review → Execute"}


def main() -> None:
    _utf8_stdio()
    parser = argparse.ArgumentParser(
        description="input-repair CLI: 在副本上物化行号空洞, 产出 repaired "
                    "input snapshot (ADR 0021)")
    parser.add_argument("--input", type=Path, required=True,
                        help="待修复的 Office 输入 (staged 名, ASCII)")
    scope = parser.add_mutually_exclusive_group(required=True)
    scope.add_argument("--sheet", type=str, default=None,
                       help="sheet 名 (如 11_FRESH本土)")
    scope.add_argument("--all", action="store_true", dest="do_all",
                       help="修复该工作簿全部含空洞的 sheet (单一快照)")
    parser.add_argument("--out", type=Path, default=None,
                        help="repaired snapshot 路径 (默认: <input>.repaired<ext> 同目录)")
    parser.add_argument("--workdir", type=Path, default=None,
                        help="(兼容) 旧 CLI 占位 — 新语义不需要; 保留以拒绝误用")
    args = parser.parse_args()

    if args.workdir is not None:
        fail("LEGACY_CLI_REJECTED",
             "repair_row_gaps 已收缩为 input-repair CLI (ADR 0021): "
             "--workdir/--target/--patch-spec 不再存在",
             "用法: repair_row_gaps.py --input <staged.xlsx> (--sheet <Name> | "
             "--all) [--out <repaired.xlsx>] — 不触碰 manifest/spec/编译",
             exit_code=3)

    if not args.input.is_file():
        fail("INPUT_NOT_FOUND", f"input not found: {args.input}",
             "确认 staged 输入路径", exit_code=1)
    out = args.out or args.input.with_name(
        f"{args.input.stem}.repaired{args.input.suffix}")
    print(json.dumps(repair_copy(args.input, out, args.sheet, args.do_all),
                     ensure_ascii=False, indent=2))
    sys.exit(0)


if __name__ == "__main__":
    main()
