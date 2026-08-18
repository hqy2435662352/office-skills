#!/usr/bin/env python3
"""
scripts/repair_row_gaps.py — 物化目标 sheet 的行号空洞 (row elements).

行号空洞 = sheet XML 中 row 元素 r 值不连续 (如 1..21, 23..52 → 缺 22)。
officecli 的 `add ... after: /row[N]` 锚点要求 N 元素真实存在; 空洞存在时
插入行会落在空洞之后的 r 值、锚点链永久断裂 (2026-08-12 埃及复盘)。

修复方式 (已实证): 对缺失行执行 `set <sheet>/A<r> numberformat=0.00` —
officecli 会物化空行元素且不留单元格内容。注意 `value:null` 只在 officecli
已规范化 (保存过) 的文件上物化, WPS/Excel 原始 XML 上无效 — 一律用
style-only (numberformat) 写入。写完必须 `officecli close <file>` 强制刷盘
(否则 resident 延迟写未落盘, 紧跟的重 flatten 仍读到旧 XML, 2026-08-13 实测)。

用法:
  python scripts/repair_row_gaps.py --workdir <dir> [--target <staged 文件>]
                                     [--patch-spec <fill_spec.yaml>]

读 prepare_manifest.json 的 row_gaps (prepare_run.py 阶段 B 输出),
在 staged 副本上物化缺失行元素。

修复成功后脚本自动重跑 prepare_run.py --flatten (仅目标 sheet),
同步 prepare_manifest.json 的结构指纹 — 行洞修复 = staged 文件修改 =
指纹必然变化 (机械事实), 手工同步已取消。

修复后唯一动作 (flatten 已自动):
  1. 更新 fill_spec.yaml 的 target_structure 指纹 — 抄输出 JSON 里的
     fingerprints.target_structure, 或 --patch-spec 一步改写;
  2. 重编译 (compile_fill.py) → 重执行 (execute_batch.py)。

Exit codes: 0=pass, 1=fatal, 3=retryable.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from _officecli import (  # noqa: E402
    ensure_utf8_stdio as _utf8_stdio, fail, officecli,
)

MANIFEST_NAME = "prepare_manifest.json"

try:
    import yaml  # 仅 --patch-spec 的改写后回读校验
except ImportError:  # pragma: no cover
    yaml = None


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


def resync_flatten(workdir: Path) -> dict:
    """重跑 prepare_run.py --flatten (仅目标 sheet), 返回新指纹 dict.

    行洞修复 = staged 文件修改 = 指纹必然变化 (机械事实)。repair 后由脚本
    自动同步 manifest 指纹, Agent 不再手工重 flatten。指纹以
    prepare_run.py 的 FLATTEN_STAGE_DONE 输出为准 (同一管线, 必然一致)。
    """
    manifest = load_manifest(workdir)
    target_entry = manifest.get("target")
    if not target_entry:
        fail("NO_TARGET", "manifest has no target entry",
             "Run prepare_run.py --flatten with --target <staged file>")
    proc = subprocess.run(
        [sys.executable, str(Path(__file__).resolve().parent / "prepare_run.py"),
         "--workdir", str(workdir), "--flatten",
         "--sheets", f"{target_entry['file']}:{target_entry['sheet']}",
         "--target", target_entry["file"]],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=900)
    if proc.returncode != 0:
        fail("RESYNC_FLATTEN_FAILED",
             f"auto re-flatten after repair failed: {proc.stderr[-400:]}",
             "The staged file was repaired but the manifest is stale — re-run "
             "repair_row_gaps.py (idempotent) or run prepare_run.py --flatten "
             "manually, then update the spec fingerprint", exit_code=3)
    try:
        out = json.loads(proc.stdout)
    except ValueError:
        fail("RESYNC_FLATTEN_INVALID", "auto re-flatten produced unparsable output",
             "Re-run repair_row_gaps.py or prepare_run.py --flatten manually")
    fps = out.get("fingerprints")
    if not isinstance(fps, dict) or "target_structure" not in fps:
        fail("RESYNC_FLATTEN_NO_FINGERPRINTS",
             "auto re-flatten reported no fingerprints",
             "Re-run repair_row_gaps.py or prepare_run.py --flatten manually")
    return fps


def patch_spec_fingerprint(spec_path: Path, target_fp: str) -> None:
    """把新 target_structure 指纹写进 fill_spec.yaml (外科手术式行替换,
    保留注释与其余内容; 改写后 yaml 回读校验)。"""
    if not spec_path.is_file():
        fail("SPEC_NOT_FOUND", f"fill_spec.yaml not found: {spec_path}",
             "Provide the --patch-spec path")
    if yaml is None:
        fail("DEP_MISSING", "PyYAML is required for --patch-spec",
             "pip install pyyaml")
    text = spec_path.read_text(encoding="utf-8")
    lines = text.splitlines()
    out: list[str] = []
    fp_indent: int | None = None
    patched = False
    for line in lines:
        stripped = line.lstrip()
        indent = len(line) - len(stripped)
        if fp_indent is None:
            if stripped == "fingerprints:":
                fp_indent = indent
            out.append(line)
            continue
        if not stripped or indent <= fp_indent:
            # 块结束 (空行 / 同级或更外缩进) — 后续行原样保留
            fp_indent = None
            out.append(line)
            continue
        if stripped.startswith("target_structure:"):
            head = line[:line.index("target_structure:") + len("target_structure:")]
            comment = ""
            rest = line[line.index("target_structure:") + len("target_structure:"):]
            if "#" in rest:
                comment = rest[rest.index("#"):].rstrip()
            out.append(f"{head} {target_fp}" + (f" {comment}" if comment else ""))
            patched = True
        else:
            out.append(line)
    if not patched:
        fail("SPEC_FINGERPRINT_NOT_FOUND",
             "no `fingerprints.target_structure` key found in the spec",
             "Add the fingerprints block (see assets/fill_spec_template.yaml) "
             "or use make_probe_spec.py --workdir <dir>")
    new_text = "\n".join(out)
    try:
        spec = yaml.safe_load(new_text)
    except yaml.YAMLError as e:
        fail("SPEC_PATCH_INVALID", f"patched spec does not parse: {e}",
             "Restore the spec and fix the fingerprints block manually")
    if spec.get("fingerprints", {}).get("target_structure") != target_fp:
        fail("SPEC_PATCH_VERIFY_FAILED",
             "patched spec fingerprint mismatch after write",
             "Restore the spec and update the fingerprint manually")
    spec_path.write_text(new_text + "\n", encoding="utf-8")


def repair(workdir: Path, target: str | None, patch_spec: Path | None) -> list[dict]:
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
                              "repaired": [], "fingerprints_synced": False},
                             ensure_ascii=False, indent=2))
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
        # 强制刷盘: resident 延迟写未落盘时, 紧跟的重 flatten 仍读到旧 XML
        # (2026-08-13 实测: set 返回后立即重 flatten 会复见空洞)。
        proc = officecli("close", str(staged))
        if proc.returncode != 0:
            fail("REPAIR_FLUSH_FAILED",
                 f"officecli close failed: {proc.stderr[-400:]}",
                 "The row elements were materialized but may not be flushed — "
                 "re-run repair_row_gaps.py (idempotent)", exit_code=3)
        fps = resync_flatten(workdir)
        result = {"status": "PASS", "code": "ROW_GAPS_REPAIRED",
                  "sheet": sheet, "repaired": fixed,
                  "fingerprints_synced": True,
                  "fingerprints": fps,
                  "next": "flatten 已自动同步; 唯一动作 = 更新 spec 指纹 "
                          "(抄本输出 fingerprints.target_structure 或 "
                          "--patch-spec 一步完成) + 重编译 (compile_fill.py)"}
        # 先输出指纹: --patch-spec 失败 (exit 3) 时, 新指纹也已可见可抄
        # (重跑 repair 只会得到 NO_ROW_GAPS, 不再带指纹 — 2026-08-13 复盘)。
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if patch_spec is not None:
            patch_spec_fingerprint(patch_spec, fps["target_structure"])
            print(json.dumps({"status": "PASS", "code": "SPEC_PATCHED",
                              "spec_patched": str(patch_spec)},
                             ensure_ascii=False, indent=2))
        return fixed
    print(json.dumps({"status": "PASS", "code": "NO_ROW_GAPS", "repaired": [],
                      "fingerprints_synced": False},
                     ensure_ascii=False, indent=2))
    return []


def main() -> None:
    _utf8_stdio()
    parser = argparse.ArgumentParser(
        description="Materialize missing row elements in the staged target "
                    "(row-number gaps that break add-after anchors); auto "
                    "re-syncs prepare_manifest fingerprints")
    parser.add_argument("--workdir", type=Path, required=True, help="ASCII workdir")
    parser.add_argument("--target", type=str, default=None,
                        help="Staged target file name (default: manifest target)")
    parser.add_argument("--patch-spec", type=Path, default=None,
                        help="fill_spec.yaml to rewrite its "
                             "fingerprints.target_structure with the new hash "
                             "(one-step spec sync)")
    args = parser.parse_args()
    repair(args.workdir, args.target, args.patch_spec)


if __name__ == "__main__":
    main()
