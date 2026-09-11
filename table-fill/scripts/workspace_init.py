#!/usr/bin/env python3
"""
scripts/workspace_init.py — Workspace Init: the single Job-level entry point.

table-fill v3 convergence (ticket 01, spec "8 阶段管线" first stage). 一次完成:
  复制 (中文名源文件 → ASCII staged 名) + ASCII 命名 + 哈希 + 环境预检 +
  outline + 展平 (role-neutral) + premod evidence, 产出 workspace_manifest.json
  —— 工作区的唯一事实空间, 每 Job 一次 (单 run / multi-run 相同); 此后所有
  环节只认 manifest。已有准备事实可**显式继承**。

角色中立 (ADR 0018): 本脚本**不知道 source/target 角色** — 无 --target 参数,
manifest 无 target/kind/二元 fingerprints, 不写 prepare_manifest.json
(run-local compiler view 由 materialize_run.py 在 Topology 后投影)。
Selective Flatten Invariant: --sheets 只展平本 Job 业务 sheet 并集,
角色中立 ≠ 全簿展平。

三个模式 (互斥):
  --init          建立事实空间: preflight → stage (copy + ASCII 命名) → 哈希
                  → outline → 展平 → classify → role-neutral premod evidence
                  → 写 workspace_manifest.json
                    --files  "源路径|ascii名, 源路径|ascii名"  (源路径可为中文)
                    --sheets "ascii名.xlsx:S1,S2;ascii名2.xlsx:S3"  (业务 sheet 并集)
  --verify        哈希核对: 读 manifest, 重算全部 staged 输入的 SHA-256 与
                  manifest 记录的哈希身份比对。漂移 → exit 3 + 提示「输入已变化,
                  请重新初始化」; 一致 → exit 0 + 结构化 PASS。fail-closed:
                  绝不静默沿用旧结果, 也不做任何 copy/探测。
  --inherit-from <dir>  显式继承另一个已 init 工作区的事实空间: 从该目录的
                  workspace_manifest.json 读取 staged 输入/哈希/outline/展平
                  记录, 逐条按哈希核对后复制对应的产物文件到本工作区, 只补写
                  本工作区自己的 workspace_manifest.json (记录 inherited_from)。
                  哈希不匹配 → exit 3 fail-closed (绝不手工快照推演、绝不静默继
                  承改过的文件)。

Exit codes: 0=pass, 1=fatal (env/file), 3=retryable (verify 漂移 / 继承哈希不匹配)。

实现约束 (环境指示):
  - officecli 子进程一律经 scripts/_officecli.py 既有适配器 (officecli / clean_residents);
  - flatten/classify/digest 复用既有脚本 (flatten_workbook.py / classify_columns.py /
    structure_digest.py), 与 prepare_run.py 的纯函数同构;
  - 不裸 subprocess 直调 officecli (flatten_workbook.py 内部已走适配器)。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _officecli import (  # noqa: E402
    clean_residents,
    ensure_utf8_stdio as _utf8_stdio,
    fail,
    sha256_file,
)

import preflight  # noqa: E402
import stage_files  # noqa: E402
from flatten_table import officecli_outline  # noqa: E402
from prepare_run import (  # noqa: E402
    WORKSPACE_SCHEMA_VERSION as SCHEMA_VERSION,  # canonical home: prepare_run
    # (writer 与 materialize/--verify 的消费端校验共用同一常量, 防漂移)
    _entry_for as _prepare_entry_for,  # 私有但同套件纪律 (T12: pptx 条目形态对齐)
    ascii_slug,
    facts_sha256,
    flatten_pptx_table,
    structure_facts,
    validate_workspace_manifest_shape,
    verify_workspace_facts,
)

MANIFEST_NAME = "workspace_manifest.json"

# 展平条目 digest 延后契约沿用 prepare_run 的 deferred 约定 (Business Reasoning
# Barrier/ticket 02): xlsx 条目只写 premod_evidence, 完整 digest 延后到 MOD 解锁。
DIGEST_DEFERRED = "deferred"

# 展平条目的产物键 (compile-facing, 与 prepare_run._entry_for 对齐)
_ENTRY_PROD_KEYS = ("csv", "meta", "evidence", "candidates")


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _parse_files_arg(files_arg: str) -> list[tuple[str, str]]:
    """'src|name , src|name' → [(src, name)]。src 可为中文路径, name 必须 ASCII。

    与 stage_files / prepare_run 的 --files 语法一致: '|' 分隔源路径与 staged 名;
    无 '|' 时回退为 basename (basename 非 ASCII 会在此失败 —— 中文源必须显式
    给 ASCII 名, 这是「零手工复制/改名」的核心: 命名自动化在脚本内完成)。"""
    entries = []
    for chunk in files_arg.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "|" in chunk:
            src, _, name = chunk.partition("|")
            entries.append((src.strip(), name.strip()))
        else:
            p = Path(chunk)
            entries.append((chunk, p.name))
    if not entries:
        fail("NO_FILES", "--files is empty",
             "Provide '源路径|ascii名' pairs (源路径可为中文, ascii 名必填)")
    return entries


def _ascii_name_ok(name: str) -> bool:
    return bool(name) and _ascii_safe(name)


def _ascii_safe(s: str) -> bool:
    try:
        s.encode("ascii")
        return True
    except UnicodeEncodeError:
        return False


def _workdir_ascii_check(workdir: Path) -> None:
    try:
        str(workdir).encode("ascii")
    except UnicodeEncodeError:
        fail("NON_ASCII_PATH",
             f"workdir 含非 ASCII 字符: {workdir} — officecli batch/set 在中文路径失败",
             "使用 ASCII workdir (如 C:/Temp/tablefill/<task>/)。文件名的中文由 "
             "--files 的 ASCII staged 名映射消化, 不进入目录路径", exit_code=1)


def _preflight_or_fail() -> None:
    r = preflight.check_officecli()
    if r:
        fail(r["code"], r["message"], r["corrective_action"], exit_code=1)
    preflight.check_resident_cleanup()


def _run_child(script: str, args: list, fail_code: str, fail_action: str,
               timeout: int = 600) -> None:
    """以独立进程调用同目录脚本 (复用 prepare_run 的同一 subprocess 纪律)。

    timeout 是最终保险: 脚本内部的 officecli 调用自带 timeout (适配器
    归一化为 rc=124), 这里兜底纯 Python 处理 (JSON/csv/classify/digest)
    的死循环。超时 → kill 子进程 + 清理可能残留的 resident officecli。"""
    script_path = Path(__file__).resolve().parent / script
    try:
        r = subprocess.run(
            [sys.executable, "-X", "utf8", str(script_path), *args],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout)
    except subprocess.TimeoutExpired:
        clean_residents()  # 被 kill 的 python 若正挂着 officecli, 孙进程可能残留
        fail(fail_code,
             f"{script} timed out after {timeout}s (已终止子进程并清理 resident)",
             fail_action, exit_code=3)
    if r.returncode != 0:
        tail = (r.stderr or r.stdout or "").strip()[-800:]
        fail(fail_code, f"{script} failed (exit {r.returncode}): {tail}",
             fail_action)


def _flatten_entry(fname: str, sheet: str, name: str) -> dict:
    """xlsx 展平条目 (role-neutral: 无 kind/target 角色 — ADR 0018)。

    每条目携带自己的 entry-level structure fingerprint (structure_sha256,
    由 meta 的结构事实确定性计算); 源/目标角色与聚合指纹属于 run-local
    派生 (materialize_run), 不进 workspace_manifest。"""
    return {
        "file": fname,
        "sheet": sheet,
        "name": name,
        "csv": f"{name}_flat.csv",
        "meta": f"{name}_meta.json",
        "evidence": f"{name}_premod_evidence.md",
        "digest": DIGEST_DEFERRED,
        "candidates": f"{name}_candidates.yaml",
    }


def _entry_structure_sha256(workdir: Path, e: dict) -> str:
    """entry-level structure fingerprint: 该 entry meta 的结构事实确定性哈希
    (与 prepare_run.facts_sha256(structure_facts(meta)) 同口径)。"""
    facts = structure_facts(json.loads(
        (workdir / e["meta"]).read_text(encoding="utf-8")))
    return facts_sha256([facts])


def _outline_is_usable(p: Path) -> bool:
    """只信任真正携带 sheet 数据的 outline 缓存 — officecli 的错误 JSON
    ({"success": false, "error": ...}) 不得被缓存成事实 (quote_fill 复盘:
    9/6 的错误 outline 被静默复用, S0 带着垃圾 outline 继续跑到 flatten)。"""
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(d, dict) and (d.get("success") is True or "data" in d)


def _resolve_staged_ref(staged_names: set, fname: str) -> str:
    """--sheets 文件引用对 staged 名的解析: 先精确匹配, 再容忍漏写扩展名
    ('source_guili' 命中 staged 'source_guili.xlsx' — staging 对无后缀的 ascii
    名会自动补源文件扩展名, 因为 officecli 只按扩展名开文件)。"""
    if fname in staged_names:
        return fname
    cands = [s for s in staged_names if Path(s).stem == fname]
    return cands[0] if len(cands) == 1 else ""


def _flatten_stage(workdir: Path, manifest: dict, sheets_arg: str) -> None:
    """同一套展平/classify/digest, 一次 --init 内完成 (不复用 prepare_run 的
    run_flatten_stage 是因为 init 是单次原子入口, 不需要增量 merge 状态)。

    role-neutral (ADR 0018): 不区分 source/target — 无 --target, 无 kind,
    无二元 fingerprints; 每个展平条目记录自己的 entry-level structure_sha256。
    """
    staged_names = {f["staged"] for f in manifest["inputs"]}
    parsed = []
    for chunk in sheets_arg.split(";"):
        chunk = chunk.strip()
        if not chunk:
            continue
        if ":" not in chunk:
            fail("SHEETS_ARG_INVALID",
                 f"sheets 条目必须 'file:SheetA,SheetB', 得到 {chunk!r}",
                 "修正 --sheets 语法")
        file_part, _, sheets_part = chunk.partition(":")
        fname = _resolve_staged_ref(staged_names, file_part.strip())
        if not fname:
            fail("FILE_NOT_STAGED", f"{file_part.strip()} 未暂存 (可用: "
                 f"{sorted(staged_names)})",
                 "在 --files 中列出它; staged 名无扩展名时会自动补源文件扩展名 "
                 "(如 source_guili → source_guili.xlsx), --sheets 引用可省略扩展名")
        sheets = [s.strip() for s in sheets_part.split(",") if s.strip()]
        if not sheets:
            fail("SHEETS_ARG_INVALID", f"no sheets listed for {fname!r}",
                 "List at least one sheet per file")
        parsed.append((fname, sheets))

    by_file: dict[str, list[tuple[str, str]]] = {}
    for fname, sheets in parsed:
        for s in sheets:
            name = f"{Path(fname).stem}_{ascii_slug(s)}"
            by_file.setdefault(fname, []).append((s, name))

    flattened = []
    # (file, slug) 名称全局唯一性防御 (展平条目 name 是 manifest 主键)
    seen_names: set[str] = set()
    for fname, targets in by_file.items():
        staged = workdir / fname
        if staged.suffix.lower() == ".pptx":
            # PPTX 源/目标 (T12): flatten_pptx_table 每表直展 (officecli get
            # depth 2, 自带 minimal digest); 条目走 _entry_for xlsx=False 形态
            # (真实 digest 文件, 无 evidence; candidates 形态与 prepare_run 一致)。
            for s, n in targets:
                if n in seen_names:
                    fail("ENTRY_NAME_DUPLICATE",
                         f"展平条目名重复: {n} (不同 sheet slug 收敛到同名)",
                         "为源文件提供更可区分的 ASCII staged 名后重跑 --init")
                seen_names.add(n)
                if not s.startswith("slide["):
                    fail("PPTX_SHEET_ARG_INVALID",
                         f"pptx flatten needs 'slide[N]/table[@id=M]' targets, got: {s!r}",
                         "Use the table id from the outline (e.g. slide[5]/table[@id=3])")
                flatten_pptx_table(staged, s, n, workdir)
                entry = _prepare_entry_for(fname, s, n, xlsx=False)
                entry = {k: v for k, v in entry.items() if k != "kind"}
                entry["structure_sha256"] = _entry_structure_sha256(workdir, entry)
                flattened.append(entry)
            continue
        plan = workdir / f"_ws_plan_{Path(fname).stem}.json"
        plan.write_text(json.dumps(
            {"targets": [{"sheet": s, "name": n} for s, n in targets]},
            ensure_ascii=False), encoding="utf-8")
        _run_child(
            "flatten_workbook.py",
            ["--input", str(staged), "--plan", str(plan),
             "--out-dir", str(workdir)],
            "FLATTEN_FAILED", "读 stderr 并重跑 --init")
        for s, n in targets:
            if n in seen_names:
                fail("ENTRY_NAME_DUPLICATE",
                     f"展平条目名重复: {n} (不同 sheet slug 收敛到同名)",
                     "为源文件提供更可区分的 ASCII staged 名后重跑 --init")
            seen_names.add(n)
            entry = _flatten_entry(fname, s, n)
            meta_path = workdir / entry["meta"]
            cand_path = workdir / entry["candidates"]
            _run_child(
                "classify_columns.py",
                ["--meta", str(meta_path), "--output", str(cand_path)],
                "CLASSIFY_FAILED", "读 stderr 并重跑 --init")
            _run_child(
                "structure_digest.py",
                ["--meta", str(meta_path), "--csv", str(workdir / entry["csv"]),
                 "--candidates", str(cand_path), "--pre-mod",
                 "--out", str(workdir / entry["evidence"])],
                "DIGEST_FAILED", "读 stderr 并重跑 --init")
            entry["structure_sha256"] = _entry_structure_sha256(workdir, entry)
            flattened.append(entry)

    manifest["flattened"] = flattened


def _stamp_derived(workdir: Path, manifest: dict) -> None:
    """给全部派生产物 (outline/flatten 条目产物) 打 SHA-256 身份。

    清单记录「全部输入与派生产物的哈希身份」(验收 2): inputs[].sha256 +
    derived[].sha256 (outline txt + flattened 的 csv/meta/evidence/candidates)。"""
    derived = []
    outlines = manifest.get("outlines") or {}
    for _, outline_name in outlines.items():
        p = workdir / outline_name
        derived.append({"path": outline_name, "kind": "outline",
                        "sha256": sha256_file(p) if p.is_file() else None})
    for e in manifest.get("flattened") or []:
        for key in _ENTRY_PROD_KEYS:
            fname = e.get(key)
            if not fname:
                continue
            p = workdir / fname
            derived.append({
                "path": fname, "kind": f"flatten/{e['name']}/{key}",
                "sha256": sha256_file(p) if p.is_file() else None,
            })
    manifest["derived"] = derived


def run_init(workdir: Path, files_arg: str, sheets_arg: str,
             task: str) -> None:
    workdir.mkdir(parents=True, exist_ok=True)
    _workdir_ascii_check(workdir)
    _preflight_or_fail()

    entries = _parse_files_arg(files_arg)
    bad = [n for _, n in entries if not _ascii_name_ok(n)]
    if bad:
        fail("NON_ASCII_STAGED_NAME",
             f"staged 名必须 ASCII 且非空, 得到: {bad!r}",
             "中文源文件用 --files 显式给出 ASCII staged 名 (零手工复制/改名)")

    records = stage_files.stage_files(workdir, entries)
    errors = [r for r in records if r["status"] == "ERROR"]
    if errors:
        fail("STAGE_FAILED", f"{len(errors)} file(s) failed staging",
             "检查源路径与 ASCII staged 名", exit_code=1)

    inputs = []
    outlines = {}
    for rec in records:
        name = Path(rec["dst"]).name
        staged = workdir / name
        outline_txt = workdir / f"{Path(name).stem}_outline.txt"
        if (not outline_txt.is_file()
                or rec["status"] != "SKIPPED"
                or not _outline_is_usable(outline_txt)):
            try:
                proc = officecli_outline(str(staged))
            except OSError as exc:
                fail("OUTLINE_FAILED", str(exc),
                     "staged 名必须带 officecli 可识别扩展名 (.xlsx/.xlsm/.docx/"
                     ".pptx) — 无后缀的 ascii 名会自动补源文件后缀; 扩展名正确仍失败 "
                     "则核对源文件本身是否可被 officecli 打开", exit_code=3)
            outline_txt.write_text(
                json.dumps(proc, ensure_ascii=False, indent=1), encoding="utf-8")
        inputs.append({
            "staged": name,
            "source": rec["src"],
            "sha256": sha256_file(staged),
        })
        outlines[name] = outline_txt.name

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "kind": "workspace_init",
        "workdir": str(workdir),
        "task": task,
        "inputs": inputs,
        "outlines": outlines,
        "flattened": [],
        "derived": [],
        "inherited_from": None,
    }

    _flatten_stage(workdir, manifest, sheets_arg)
    _stamp_derived(workdir, manifest)

    _save_manifest(workdir, manifest)
    print(json.dumps({
        "status": "PASS",
        "code": "WORKSPACE_INIT_DONE",
        "manifest": MANIFEST_NAME,
        "inputs": [f["staged"] for f in inputs],
        "flattened": [e["name"] for e in manifest["flattened"]],
        "structure_sha256": {
            e["name"]: e.get("structure_sha256")
            for e in manifest["flattened"]
        },
        "note": "role-neutral manifest (ADR 0018) — run-local prepare_manifest "
                "由 materialize_run.py 投影",
    }, ensure_ascii=False, indent=2))
    sys.exit(0)


def _load_manifest(workdir: Path) -> dict:
    p = workdir / MANIFEST_NAME
    if not p.is_file():
        fail("MANIFEST_NOT_FOUND",
             f"{MANIFEST_NAME} 不在 {workdir} — 先运行 workspace_init --init",
             "python scripts/workspace_init.py --workdir <dir> --init ...")
    try:
        m = json.loads(p.read_text(encoding="utf-8"))
    except ValueError as e:
        fail("MANIFEST_INVALID", f"manifest 损坏: {e}",
             "删除 manifest 后重新 --init")
    if not isinstance(m, dict):
        fail("MANIFEST_INVALID", "manifest 顶层必须是 object", "重新 --init")
    return m


def _save_manifest(workdir: Path, manifest: dict) -> None:
    """原子落盘: tmp + fsync + os.replace — 任何失败都不会半写覆盖 canonical
    事实空间 (旧 manifest 完整保留; WORKDIR_POLLUTED 之外的落盘保险)。"""
    dst = workdir / MANIFEST_NAME
    tmp = workdir / f".{MANIFEST_NAME}.tmp"
    text = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")
    try:
        with open(tmp, "wb") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, dst)
    except OSError as e:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        fail("MANIFEST_WRITE_FAILED",
             f"cannot atomically write {MANIFEST_NAME}: {e}",
             "检查 workdir 权限/占用后重跑 --init (旧 manifest 未被覆盖)")


def _verify_inputs(workdir: Path, manifest: dict) -> list[dict]:
    """重算全部 staged 输入的 SHA-256 并与 manifest 身份比对。

    返回漂移清单 [{staged, expected, actual}] (空 = 无漂移)。"""
    drift = []
    for it in manifest.get("inputs") or []:
        staged = it.get("staged")
        expected = it.get("sha256")
        p = workdir / staged if staged else None
        actual = sha256_file(p) if (p and p.is_file()) else None
        if not expected or actual != expected:
            drift.append({
                "staged": staged, "expected": (expected or "")[:16] + "...",
                "actual": (actual or "<missing>")[:16] + ("..." if actual else ""),
            })
    return drift


def run_verify(workdir: Path) -> None:
    """--verify: workspace integrity verify (trust boundary) — 形状 + 全部
    声明事实 (inputs/derived/引用) 与物理文件核对。名字终于配得上行为:
    --init 建立事实, --verify 验证事实仍成立。"""
    manifest = _load_manifest(workdir)
    problems = validate_workspace_manifest_shape(manifest)
    if problems:
        fail("MANIFEST_INVALID", "; ".join(problems),
             "删除损坏/旧版本 manifest 后重新 workspace_init --init",
             exit_code=3)
    drift = verify_workspace_facts(workdir, manifest)
    if drift:
        # fail-closed: 明确拒绝 + 「重新初始化」提示 (验收 3); 代码沿用
        # INPUT_DRIFT (既有 CLI 契约), 覆盖面扩到 derived/引用漂移
        kinds = sorted({d["kind"] for d in drift})
        paths = sorted({d["path"] for d in drift})
        fail("INPUT_DRIFT",
             f"{len(drift)} 项事实漂移 (kinds: {', '.join(kinds)}): "
             f"{paths} — 与 workspace_manifest.json 记录的哈希身份不一致",
             "事实已变化, 请重新初始化: python scripts/workspace_init.py "
             "--workdir <dir> --init ... (绝不静默沿用旧结果)",
             defects=[{k: d[k] for k in ("kind", "path", "message")}
                      for d in drift], exit_code=3)
    print(json.dumps({
        "status": "PASS",
        "code": "WORKSPACE_VERIFIED",
        "input_count": len(manifest.get("inputs") or []),
        "derived_count": len(manifest.get("derived") or []),
        "message": "全部输入与派生产物哈希 + 引用均与 manifest 一致",
    }, ensure_ascii=False, indent=2))
    sys.exit(0)


def run_inherit(workdir: Path, inherit_from: Path) -> None:
    """显式继承一个已 init 工作区的事实空间 (验收 4 的「显式继承」语法)。

    从 inherit_from 目录的 workspace_manifest.json 读取 inputs/outlines/flattened/
    fingerprints, 逐条按哈希核对后把对应产物文件复制到本工作区, 只写本工作区
    自己的 workspace_manifest.json (inherited_from 记录来源)。哈希不匹配 →
    exit 3 fail-closed (绝不手工快照推演、绝不静默继承改过的文件)。
    """
    workdir.mkdir(parents=True, exist_ok=True)
    _workdir_ascii_check(workdir)
    src_manifest = _load_manifest(inherit_from)

    drift = _verify_inputs(inherit_from, src_manifest)
    if drift:
        fail("INHERIT_INPUT_DRIFT",
             f"来源工作区 {inherit_from} 的输入已漂移, 无法继承: "
             f"{[d['staged'] for d in drift]}",
             "先在来源工作区重新 --init, 或在本工作区直接 --init", exit_code=3)

    import shutil
    # 复制 staged 输入 (按源工作区记录的 staged 名)
    copied_inputs = []
    for it in src_manifest.get("inputs") or []:
        staged = it.get("staged")
        src_p = inherit_from / staged
        if not src_p.is_file():
            fail("INHERIT_FILE_MISSING", f"来源 staged 文件缺失: {staged}",
                 "来源工作区不完整, 无法继承; 直接 --init", exit_code=3)
        shutil.copy2(src_p, workdir / staged)
        stage_files._force_writable(workdir / staged)  # noqa: 私有但同套件纪律
        copied_inputs.append(it)

    # 复制 outline (按 outlines 映射)
    outlines = {}
    for staged, outline_name in (src_manifest.get("outlines") or {}).items():
        src_o = inherit_from / outline_name
        if not src_o.is_file():
            fail("INHERIT_OUTLINE_MISSING", f"来源 outline 缺失: {outline_name}",
                 "来源工作区不完整, 无法继承; 直接 --init", exit_code=3)
        shutil.copyfile(src_o, workdir / outline_name)
        outlines[staged] = outline_name

    # 复制 flatten 条目产物 (csv/meta/evidence/candidates)
    flattened = src_manifest.get("flattened") or []
    for e in flattened:
        for key in _ENTRY_PROD_KEYS:
            fname = e.get(key)
            if not fname:
                continue
            src_f = inherit_from / fname
            if not src_f.is_file():
                fail("INHERIT_PRODUCT_MISSING", f"来源展平产物缺失: {fname}",
                     "来源工作区不完整, 无法继承; 直接 --init", exit_code=3)
            shutil.copyfile(src_f, workdir / fname)

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "kind": "workspace_init",
        "workdir": str(workdir),
        "task": src_manifest.get("task"),
        "inputs": copied_inputs,
        "outlines": outlines,
        "flattened": flattened,
        "derived": [],
        "inherited_from": str(inherit_from),
    }
    _stamp_derived(workdir, manifest)

    _save_manifest(workdir, manifest)
    print(json.dumps({
        "status": "PASS",
        "code": "WORKSPACE_INHERITED",
        "inherited_from": str(inherit_from),
        "inputs": [f["staged"] for f in copied_inputs],
        "flattened": [e["name"] for e in flattened],
        "note": "role-neutral manifest (ADR 0018) — run-local prepare_manifest "
                "由 materialize_run.py 投影",
    }, ensure_ascii=False, indent=2))
    sys.exit(0)


def _strip_shim(workdir: Path) -> None:
    """清理 --init 内部中间 _ws_plan_*.json (不写入 manifest, 仅工作区临时物)。"""
    for p in workdir.glob("_ws_plan_*.json"):
        try:
            p.unlink()
        except OSError:
            pass


def main() -> None:
    _utf8_stdio()
    parser = argparse.ArgumentParser(
        description="Workspace Init: 复制/ASCII 命名/哈希/预检/outline/展平/"
                    "digest 一次完成, 产出 workspace_manifest.json (唯一事实空间)")
    parser.add_argument("--workdir", type=Path, required=True, help="ASCII workdir")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--init", action="store_true",
                      help="建立事实空间 (一次完成全部准备)")
    mode.add_argument("--verify", action="store_true",
                      help="哈希核对: 输入漂移 → 拒绝 + 提示重新初始化")
    mode.add_argument("--inherit-from", type=Path, metavar="DIR",
                      help="显式继承另一已 init 工作区的事实空间")
    parser.add_argument("--files", type=str, default="",
                        help='--init: "源路径|ascii名, 源路径|ascii名" (中文源给 ASCII 名)')
    parser.add_argument("--sheets", type=str, default="",
                        help='--init: 业务 sheet 并集 "ascii名.xlsx:S1,S2;ascii名2.xlsx:S3" '
                             '(Selective Flatten — 只展平本 Job 业务 scope, 角色中立)')
    parser.add_argument("--task", type=str, default="", help="任务文本 (记录于 manifest)")
    args = parser.parse_args()

    if args.verify:
        run_verify(args.workdir)
    elif args.inherit_from is not None:
        run_inherit(args.workdir, args.inherit_from)
    else:  # --init
        if not args.files:
            fail("NO_FILES", "--init 需要 --files",
                 "提供 '源路径|ascii名' 条目")
        if not args.sheets:
            fail("NO_SHEETS", "--init 需要 --sheets (业务 sheet 并集)",
                 "提供 'file:SheetA,SheetB' 对; 无 --target — 角色由 materialize_run 投影")
        try:
            run_init(args.workdir, args.files, args.sheets, args.task)
        finally:
            _strip_shim(args.workdir)


if __name__ == "__main__":
    main()
