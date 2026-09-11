#!/usr/bin/env python3
"""
scripts/repair_row_gaps.py — input-repair utility (ADR 0021).

Row-number gaps (`row` element r-values discontinuous in sheet XML) break
officecli `add ... after: /row[N]` anchor chains permanently. This utility
repairs a COPY of the affected workbook and produces a NEW repaired input
snapshot. It is input-version repair, NOT workspace mutation:

  input workbook → copy to <out> → materialize missing row elements on the
  copy → close/flush → verify gaps repaired → output repaired snapshot

The current workspace stays immutable; once the repaired snapshot is adopted
it re-enters through the canonical `workspace_init --init` path (re-init with
the repaired file among --files), then materialize_run re-projects run views,
FillSpec fingerprints are rebound, and Compile → Spec Review → Execute follow.

This script performs NO: manifest refresh, incremental flatten, fingerprint
patching, spec patching, recompile, or any next-state derivation. It answers
one question only: "produce a repaired copy of this workbook".

Gap detection reads the workbook XML directly (allowed structural parsing —
invariant 6); materialization uses the officecli adapter (`set ... numberformat
=0.00` materializes an empty row element without cell content; `value:null`
fails on un-normalized WPS/Excel XML). `officecli close` forces the flush that
a resident-deferred write would otherwise lose.

Usage:
  python scripts/repair_row_gaps.py --input <staged.xlsx> --sheet <SheetName>
                                     [--out <repaired.xlsx>]

Exit codes: 0=pass (repaired or no gaps), 1=fatal (env/file), 3=retryable.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _officecli import (  # noqa: E402
    ensure_utf8_stdio as _utf8_stdio, fail, officecli,
)

ROW_RE = re.compile(r"<row\b[^>]*\br=\"(\d+)\"", re.IGNORECASE)

# 与 prepare_run 的 files 参数同构: officecli 对中文路径失败, staged 名必须 ASCII
# (不改文件名 — repair 只修内容, 命名由 workspace_init --files 映射消化)。


def sheet_target_path(book: Path, sheet_name: str) -> str:
    """sheet 名 → xl/<sheet file> 路径 (经 workbook.xml + rels 解析).

    Rel 元素属性顺序不定 (Target 可能在 Id 前), 分别捕获后组合, 不依赖顺序。"""
    with zipfile.ZipFile(book) as z:
        try:
            wb = z.read("xl/workbook.xml").decode("utf-8")
            rels = z.read("xl/_rels/workbook.xml.rels").decode("utf-8")
        except KeyError:
            fail("NOT_XLSX", f"{book} 不是标准 xlsx (缺 workbook.xml)",
                 "用 officecli 或 Excel 保存后重试", exit_code=1)
    rid_m = re.search(
        r'<(?:\w+:)?sheet\b[^>]*name="([^"]*)"[^>]*r:id="(rId\d+)"', wb)
    if not rid_m:
        fail("SHEET_NOT_FOUND", f"sheet {sheet_name!r} 未在 workbook.xml 找到",
             "用 outline 文件确认 sheet 名", exit_code=1)
    r_id = rid_m.group(2)
    rel_m = re.search(
        rf'<Relationship\b[^>]*Id="{re.escape(r_id)}"[^>]*/?>', rels)
    if not rel_m:
        fail("SHEET_REL_MISSING",
             f"sheet {r_id} 的 Relationship 未在 rels 找到",
             "检查 xl/_rels/workbook.xml.rels", exit_code=1)
    target_m = re.search(r'Target="([^"]+)"', rel_m.group(0))
    if not target_m:
        fail("SHEET_REL_MISSING",
             f"sheet {r_id} 的 Relationship 缺 Target",
             "检查 xl/_rels/workbook.xml.rels", exit_code=1)
    target = target_m.group(1)
    if not target.startswith("/"):
        target = "xl/" + target.lstrip("/")
    # 规范化相对路径 (../) 与重复前缀 (/xl/xl/...)
    parts = []
    for seg in target.split("/"):
        if seg == "..":
            if parts:
                parts.pop()
        elif seg and seg != ".":
            parts.append(seg)
    norm = "/".join(parts)
    if not norm.startswith("xl/"):
        norm = "xl/" + norm.lstrip("xl/")
    return norm


def row_gaps_in(book: Path, sheet_path: str) -> list[int]:
    """读取 sheet XML 的 row r 值, 返回不连续处缺失的行号 (升序)."""
    with zipfile.ZipFile(book) as z:
        try:
            xml = z.read(sheet_path).decode("utf-8")
        except KeyError:
            fail("SHEET_XML_MISSING", f"{sheet_path} 不存在于 {book}",
                 "检查 sheet 路径解析", exit_code=1)
    r_vals = sorted({int(m) for m in ROW_RE.findall(xml)})
    if not r_vals:
        return []
    gaps = [expected for expected in range(min(r_vals) + 1, max(r_vals))
            if expected not in set(r_vals)]
    return gaps


def verify_no_gaps(book: Path, sheet_path: str) -> bool:
    return not row_gaps_in(book, sheet_path)


def repair_copy(input_path: Path, out_path: Path, sheet_name: str) -> dict:
    """在副本上物化缺失行元素 → close 刷盘 → 验证无洞 → 输出 repaired snapshot.

    只做 Office 手术。fails closed: 任何一步失败都不产生"半修复被当作快照"的
    状态 — 调用方应删除 out 并重试 (脚本幂等: 已修复的副本再跑 → NO_ROW_GAPS)。"""
    sheet_path = sheet_target_path(input_path, sheet_name)
    # 只修 xlsx (pptx 无 row 元素概念); slide[...] 目标由调用方自行跳过 —
    # 这里按 sheet XML 存在性判, 不存在即 NO_ROW_GAPS (pptx 没有行空洞域)。
    if not sheet_path.endswith(".xml") or not sheet_path.startswith("xl/"):
        return {"status": "PASS", "code": "NO_ROW_GAPS",
                "repaired": [], "reason": "pptx/no-row sheet: 无行元素空洞域"}
    gaps = row_gaps_in(input_path, sheet_path)
    if not gaps:
        return {"status": "PASS", "code": "NO_ROW_GAPS",
                "repaired": [], "reason": "row r 值连续"}

    out_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(input_path, out_path)
    # 副本可写 (stage_files 会设只读, 复制后 officecli 需要写)
    try:
        out_path.chmod(0o644)
    except OSError:
        pass

    fixed = []
    for r in gaps:
        path = f"/{sheet_name}/A{r}"
        proc = officecli("set", str(out_path), path,
                         "--prop", "numberformat=0.00")
        if proc.returncode != 0:
            fail("REPAIR_OP_FAILED",
                 f"officecli set {path} failed: {proc.stderr[-400:]}",
                 "检查 sheet 名 / 副本文件", exit_code=3)
        fixed.append(r)
    # 强制刷盘: resident 延迟写未落盘时, 紧跟的验证会复见空洞
    # (2026-08-13 实测: set 返回后立即重读会复见空洞)。
    proc = officecli("close", str(out_path))
    if proc.returncode != 0:
        fail("REPAIR_FLUSH_FAILED",
             f"officecli close failed: {proc.stderr[-400:]}",
             "行元素已物化但可能未刷盘 — 删除 out 后重跑 (幂等)",
             exit_code=3)

    if not verify_no_gaps(out_path, sheet_path):
        fail("REPAIR_VERIFY_FAILED",
             f"副本 {out_path} 仍含行号空洞 (sheet {sheet_name})",
             "删除 out 并重跑 (幂等); 若持续, 检查是否 officecli close 未刷盘",
             exit_code=3)

    return {"status": "PASS", "code": "ROW_GAPS_REPAIRED",
            "sheet": sheet_name, "repaired": fixed,
            "input": str(input_path),
            "output_snapshot": str(out_path),
            "next": "以该 repaired snapshot 为输入重新 workspace_init --init "
                    "(canonical re-entry, ADR 0021) → materialize_run → 刷新 "
                    "FillSpec 指纹 → Compile Clean → Spec Review → Execute"}


def main() -> None:
    _utf8_stdio()
    parser = argparse.ArgumentParser(
        description="input-repair utility: 在副本上物化行号空洞, 产出 repaired "
                    "input snapshot (ADR 0021)")
    parser.add_argument("--input", type=Path, required=True,
                        help="待修复的 Office 输入 (staged 名, ASCII)")
    parser.add_argument("--sheet", type=str, required=True,
                        help="sheet 名 (如 11_FRESH本土)")
    parser.add_argument("--out", type=Path, default=None,
                        help="repaired snapshot 路径 (默认: <input>.repaired<ext> "
                             "同目录)")
    parser.add_argument("--workdir", type=Path, default=None,
                        help="(兼容) 旧 CLI 占位 — 新语义不需要; 保留以拒绝误用")
    args = parser.parse_args()

    if args.workdir is not None:
        fail("LEGACY_CLI_REJECTED",
             "repair_row_gaps 已收缩为 input-repair utility (ADR 0021): "
             "--workdir/--target/--patch-spec 不再存在",
             "用法: repair_row_gaps.py --input <staged.xlsx> --sheet <Name> "
             "[--out <repaired.xlsx>] — 不触碰 manifest/spec/编译", exit_code=3)

    if not args.input.is_file():
        fail("INPUT_NOT_FOUND", f"input not found: {args.input}",
             "确认 staged 输入路径", exit_code=1)
    out = args.out or args.input.with_name(
        f"{args.input.stem}.repaired{args.input.suffix}")
    result = repair_copy(args.input, out, args.sheet)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0)


if __name__ == "__main__":
    main()