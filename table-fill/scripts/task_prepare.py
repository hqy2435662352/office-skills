#!/usr/bin/env python3
"""
scripts/task_prepare.py — Task 级编排（issue 02 + issue 03，spec S4/S5/S6）。

职责（Task Layer = 共享准备 + run 创建 + 阶段调度挂载；Run Layer 契约
零改动）：
  - 任务级一次性 staging：唯一输入文件 → <task_root>/staged/（ASCII 命名，
    确定性、可复现）；
  - 任务级 outline：每文件一次（officecli view outline），sheet 存在性在
    此验证（task_schema 静态校验只查文件存在）；
  - eager 预展平需求收集：source.sheets[] + target.sheet → (file, sheet) 对，
    按缓存键去重（唯一需求数 = cache/ 目录数）；
  - 阶段 1（source_prepare）：每缓存键恰好一个 worker 展平入库（命中零
    officecli），并发默认 2 —— 禁止 run 内 lazy flatten，缓存写冲突从结构
    上消除；
  - 阶段 2（run_prepare）：物化（staged 复制 + 缓存产物物化 + candidates/
    digest 再生成）+ run 级 prepare_manifest.json 组装（compile-facing 字段
    与单 run 同构，flattened 条目仅多 cache_key/sha256），并发默认 2；
  - 阶段 3–5（compile / execute / gate）：subprocess 调用现有脚本
    （compile_fill.py / execute_batch.py / execution_gate.py），零改动；
  - barrier 调度 + 并发默认值 + 单一写者由 task_scheduler 承载：本模块只
    提供 worker（薄适配层）与阶段编排（run_staged_pipeline），worker 只
    回报结果，task_status.json 在阶段边界由主进程统一写盘一次。

契约测试 seam（spec Testing Decision #3）：
  - staged_name_for / collect_demands / assemble_run_manifest 是可 import 的
    纯函数（无 Office、可单测）；
  - prepare_task_level（任务级串行预演）、cache_build_worker /
    run_prepare_worker（阶段 1/2 worker）、run_staged_pipeline（阶段编排）
    复用 prepare_run / flatten_cache 的底层函数，prepare_run.py 本体零改动；
    run 级缺陷以 StageError 抛出（不阻断同阶段其他 run，失败清单在阶段
    边界汇总，issue 03）；任务级缺陷仍走 fail()（fail-closed）。
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # 套件导入纪律

from _officecli import (  # noqa: E402
    fail, record_timing as _record_timing, sha256_file,
)

import flatten_cache  # noqa: E402
import preflight  # noqa: E402
import stage_files  # noqa: E402
import task_scheduler  # noqa: E402
from flatten_table import officecli_outline  # noqa: E402
from prepare_run import (  # noqa: E402 —— 复用单 run 底层函数（命名/指纹/决策事实）
    ascii_slug, collect_style_granularity, facts_sha256, structure_facts,
)
from task_schema import _resolve, TASK_STATUS_NAME  # noqa: E402

RUN_MANIFEST_NAME = "prepare_manifest.json"
STAGED_DIR_NAME = "staged"
OUTLINES_DIR_NAME = "outlines"
RUNS_DIR_NAME = "runs"

# 需求角色（collect_demands 的 kind 字段；task 级指纹与物化按角色分支）
KIND_SOURCE = "source"
KIND_TARGET = "target"

# run 生命周期状态的 prepare 语义（与 task_schema.RUN_STATES 同一集合）：
# prepare 阶段只允许重建 planned/prepared 的 run；superseded 是保留证据的
# 终止分支（跳过）；其余状态（compiled+）由 issue 04 的 resume 语义承担。
PREPARE_ALLOWED_STATES = ("planned", "prepared")
SUPERSEDED_STATE = "superseded"

# --prepare 与 --run 的阶段子集（顺序即调度顺序，见 spec S6 表格）
PREPARE_STAGES = ("source_prepare", "run_prepare")
RUN_STAGES = ("source_prepare", "run_prepare", "compile", "execute", "gate")


def staged_name_for(path: Path, taken: set) -> str | None:
    """确定性 staged 命名：basename；冲突加 _2/_3… 后缀。

    非 ASCII basename → None（officecli 在中文路径失败；调用方转为
    STAGED_NAME_NON_ASCII 缺陷，fail-closed）。
    """
    name = path.name
    try:
        name.encode("ascii")
    except UnicodeEncodeError:
        return None
    if name not in taken:
        return name
    stem, _, suffix = name.rpartition(".")
    s = 2
    while True:
        cand = f"{stem}_{s}" + (f".{suffix}" if suffix else "")
        if cand not in taken:
            return cand
        s += 1


def entry_name(staged: str, sheet: str) -> str:
    """单 run 展平条目命名约定: <staged_stem>_<ascii_slug(sheet)>."""
    return f"{Path(staged).stem}_{ascii_slug(sheet)}"


def collect_demands(task: dict, resolved_to_staged: dict, task_root: Path) -> list[dict]:
    """eager 预展平需求收集（纯函数）：每条 run 的 source.sheets[] 与
    target.sheet → (staged, sheet) 对，附 kind（source|target）、单 run 条目
    名与所属 run。返回按 task.yaml 声明顺序排列的完整需求清单（去重按缓存键
    在缓存构建阶段做）。"""
    demands = []
    for run in task["runs"]:
        rid = run["id"]
        src = run["source"]
        staged_src = resolved_to_staged[_resolve(task_root, src["file"])]
        for sheet in src["sheets"]:
            demands.append({
                "run": rid, "kind": KIND_SOURCE, "staged": staged_src,
                "sheet": sheet, "name": entry_name(staged_src, sheet),
            })
        tgt = run["target"]
        staged_tgt = resolved_to_staged[_resolve(task_root, tgt["template"])]
        demands.append({
            "run": rid, "kind": KIND_TARGET, "staged": staged_tgt,
            "sheet": tgt["sheet"], "name": entry_name(staged_tgt, tgt["sheet"]),
        })
    return demands


def assemble_run_manifest(workdir, task_label, files, outlines, flattened,
                          target_entry, fingerprints, row_gaps=None,
                          style_granularity=None) -> dict:
    """run 级 prepare_manifest.json 组装（纯函数）。

    compile-facing 顶层形态与 prepare_run.py 的单 run manifest 同构
    （schema_version 2 / workdir / task / files / outlines / flattened /
    target / fingerprints / row_gaps / style_granularity）；flattened 条目只
    比单 run 多 cache_key（provenance metadata）与 sha256（物化 CSV = run
    业务身份）两个字段。
    """
    return {
        "schema_version": 2,
        "workdir": workdir,
        "task": task_label,
        "files": files,
        "outlines": outlines,
        "flattened": flattened,
        "target": target_entry,
        "fingerprints": fingerprints,
        "row_gaps": row_gaps or {},
        "style_granularity": style_granularity or {},
    }


# ── 任务级串行预演（issue 02 步骤 1–7 + RUN_STATE_GUARD） ───────────────

def prepare_task_level(root: Path, task: dict, manifest: dict,
                       status: dict) -> dict:
    """串行任务级输入事实确定（prelude）：staging + outline + 需求收集 +
    sheet 校验 + 缓存键计算 + 指纹 + 冻结校验 + 快照补全 + RUN_STATE_GUARD。

    全部为任务级 fail-closed 检查（缺陷走 fail()）；返回 ctx 供阶段 1/2
    worker 使用（stage worker 绝不触碰 task_status.json —— 单一写者）。
    """
    task_id = task["task"]["id"]

    # ── 1. 唯一输入文件（source + template，按解析路径去重）→ staged 命名 ──
    refs = []
    seen_paths = set()
    for run in task["runs"]:
        for ref in (run["source"]["file"], run["target"]["template"]):
            p = _resolve(root, ref)
            if p not in seen_paths:
                seen_paths.add(p)
                refs.append(p)
    staged_by_resolved = {}
    taken = set()
    non_ascii = []
    for p in refs:
        name = staged_name_for(p, taken)
        if name is None:
            non_ascii.append(str(p))
            continue
        taken.add(name)
        staged_by_resolved[p] = name
    if non_ascii:
        fail("STAGED_NAME_NON_ASCII",
             f"{len(non_ascii)} 个输入文件无法 ASCII staging: {non_ascii[:5]}",
             "把文件名改为 ASCII（officecli 在中文路径失败）后重试",
             defects=[{"code": "STAGED_NAME_NON_ASCII", "at": p}
                      for p in non_ascii])

    # ── 2. 任务级 staging（一次性；幂等：同 size+mtime 跳过） ──
    stage_dir = root / STAGED_DIR_NAME
    stage_dir.mkdir(parents=True, exist_ok=True)
    records = stage_files.stage_files(
        stage_dir, [(str(p), name) for p, name in staged_by_resolved.items()])
    errors = [r for r in records if r["status"] == "ERROR"]
    if errors:
        fail("STAGE_FAILED", f"{len(errors)} 个文件 staging 失败",
             "检查源路径与 staged 命名后重试", exit_code=1)
    staged_files = [
        {"staged": name, "source": str(p),
         "sha256": sha256_file(stage_dir / name)}
        for p, name in staged_by_resolved.items()
    ]
    sha_by_staged = {f["staged"]: f["sha256"] for f in staged_files}

    prev_staged = manifest.get("staged_files") or []
    if prev_staged and prev_staged != staged_files:
        fail("SOURCE_HASH_DRIFT",
             "staged 文件登记与已封存快照不一致（源文件内容或路径变化）",
             "失败二分：输入事实改变 → supersede（issue 04）；尚无 run 产物时"
             "删除 task_manifest.json/task_status.json 重新 --init")

    # ── 3. 任务级 outline（每文件一次；已登记且文件未变 → 零 officecli 复用） ──
    outlines_dir = root / OUTLINES_DIR_NAME
    outlines_dir.mkdir(parents=True, exist_ok=True)
    prev_outlines = manifest.get("outlines") or {}
    outlines = {}
    for f in staged_files:
        name = f["staged"]
        outline_file = f"{Path(name).stem}_outline.txt"
        outline_path = outlines_dir / outline_file
        if prev_outlines.get(name) == outline_file and outline_path.is_file():
            outlines[name] = outline_file  # 缓存命中：不再探测
            continue
        proc = officecli_outline(str(stage_dir / name))  # officecli view outline
        outline_path.write_text(json.dumps(proc, ensure_ascii=False, indent=1),
                                encoding="utf-8")
        outlines[name] = outline_file

    # ── 4. eager 需求收集 + sheet 存在性验证（outline 证据） ──
    demands = collect_demands(task, staged_by_resolved, root)
    sheet_of = {}
    for f in staged_files:
        name = f["staged"]
        try:
            outline = json.loads(
                (outlines_dir / f"{Path(name).stem}_outline.txt").read_text(
                    encoding="utf-8"))
            sheet_of[name] = [s.get("name") for s in
                              outline.get("data", {}).get("sheets", [])]
        except (OSError, ValueError, AttributeError):
            sheet_of[name] = []
    sheet_defects = []
    for d in demands:
        if d["sheet"] not in sheet_of.get(d["staged"], []):
            sheet_defects.append({
                "code": "SHEET_NOT_FOUND",
                "at": f"runs/{d['run']}",
                "message": f"sheet {d['sheet']!r} 不在工作簿 {d['staged']} 中",
                "corrective_action": "对照 outline 修正 task.yaml 的 "
                                     "source.sheets / target.sheet",
            })
    if sheet_defects:
        fail("SHEET_NOT_FOUND", f"{len(sheet_defects)} 条 sheet 引用不存在",
             "按缺陷清单修正 task.yaml 后重试", defects=sheet_defects)

    # ── 5. eager 展平需求去重（每缓存键恰好一个 worker；命中零 officecli） ──
    preflight.check_resident_cleanup()
    oc_version = flatten_cache.officecli_version()
    schema_v = flatten_cache.FLATTEN_SCHEMA_VERSION
    key_of = {}
    unique_keys = []
    for d in demands:
        key = flatten_cache.cache_key(sha_by_staged[d["staged"]], d["sheet"],
                                      schema_v, oc_version)
        d["key"] = key
        if key not in key_of:
            key_of[key] = d
            unique_keys.append(key)

    cache_refs = {
        key: {
            "file": key_of[key]["staged"], "sheet": key_of[key]["sheet"],
            "source_hash": sha_by_staged[key_of[key]["staged"]],
            "flatten_schema_version": schema_v, "officecli_version": oc_version,
        }
        for key in unique_keys
    }

    # ── RUN_STATE_GUARD（任务级前置）：prepare 只允许重建 planned/prepared ──
    status_runs = status["runs"]
    guard_defects = []
    for rid, entry_state in status_runs.items():
        state = entry_state.get("state")
        if state not in PREPARE_ALLOWED_STATES + (SUPERSEDED_STATE,):
            guard_defects.append({
                "code": "RUN_STATE_GUARD",
                "at": f"runs/{rid}",
                "message": f"run {rid} 已推进到 {state}（prepare 只允许 "
                           f"{'/'.join(PREPARE_ALLOWED_STATES)} 的 run 重建）",
                "corrective_action": "保留现有产物；恢复语义由 resume_task"
                                     "（issue 04）承担",
            })
    if guard_defects:
        fail("RUN_STATE_GUARD", f"{len(guard_defects)} 条 run 超出 prepare 允许状态",
             "不要重跑 --prepare 覆盖已推进的 run；使用 resume 路径",
             defects=guard_defects)

    return {
        "root": root,
        "task_id": task_id,
        "manifest": manifest,
        "status_runs": status_runs,
        "stage_dir": stage_dir,
        "outlines_dir": outlines_dir,
        "runs_dir": root / RUNS_DIR_NAME,
        "staged_files": staged_files,
        "sha_by_staged": sha_by_staged,
        "outlines": outlines,
        "demands": demands,
        "key_of": key_of,
        "unique_keys": unique_keys,
        "cache_refs": cache_refs,
        "eligible_runs": [rid for rid, e in status_runs.items()
                          if e.get("state") in PREPARE_ALLOWED_STATES],
        "superseded_runs": [rid for rid, e in status_runs.items()
                            if e.get("state") == SUPERSEDED_STATE],
    }


def finalize_cache_facts(ctx: dict) -> None:
    """阶段 1（缓存构建）之后的串行固化：指纹计算 + 漂移校验 + 封存快照
    补全。必须等 stage 1 barrier 结束（fingerprints 依赖缓存 meta，与 issue
    02 的「先展平、后指纹」相对顺序一致）；失败走 fail()（任务级）。"""
    root = ctx["root"]
    manifest = ctx["manifest"]
    unique_keys = ctx["unique_keys"]
    key_of = ctx["key_of"]

    def _meta_of(d: dict) -> dict:
        cache_dir = flatten_cache.cache_entry_dir(root, d["key"])
        return json.loads((cache_dir / "meta.json").read_text(encoding="utf-8"))

    src_keys = [d for d in (key_of[k] for k in unique_keys)
                if d["kind"] == KIND_SOURCE]
    tgt_keys = [d for d in (key_of[k] for k in unique_keys)
                if d["kind"] == KIND_TARGET]
    fingerprints = {
        "source_structure": facts_sha256(
            [structure_facts(_meta_of(d)) for d in src_keys]),
        "target_structure": facts_sha256(
            [structure_facts(_meta_of(d)) for d in tgt_keys]),
    }
    prev_refs = manifest.get("flatten_cache_refs") or {}
    prev_fps = manifest.get("fingerprints") or {}
    if prev_refs and prev_refs != ctx["cache_refs"]:
        fail("CACHE_REF_DRIFT",
             "缓存键引用与已封存快照不一致（officecli 升级或源 hash 漂移）",
             "失败二分：输入事实改变 → supersede（issue 04）；否则删除缓存与"
             "快照后重新 --init/--prepare")
    if prev_fps and prev_fps != fingerprints:
        fail("FINGERPRINT_DRIFT",
             "结构指纹与已封存快照不一致",
             "输入事实改变 → supersede（issue 04）")

    # 封存快照补全（--init 骨架 → prepare 事实；只授权一次写）
    if not (manifest.get("staged_files") or []):
        manifest["staged_files"] = ctx["staged_files"]
        manifest["outlines"] = ctx["outlines"]
        manifest["flatten_cache_refs"] = ctx["cache_refs"]
        manifest["fingerprints"] = fingerprints
        (root / "task_manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


# ── 阶段 worker（薄适配层：回报结果，不写 task_status.json） ────────────

def cache_build_worker(ctx: dict, key: str) -> dict:
    """阶段 1 worker：每缓存键恰好一次展平入库（hit 直接跳过，零 officecli）。
    同一缓存键不会有两个 worker 并发 —— 并发写冲突从结构上不存在（spec S6）。"""
    entry_dir = flatten_cache.cache_entry_dir(ctx["root"], key)
    if flatten_cache.cache_hit(entry_dir):
        return {"status": "ok",
                "artifacts": {"key": key, "hit": True, "file": None,
                              "sheet": None}}
    d = ctx["key_of"][key]
    try:
        flatten_cache.build_cache_entry(ctx["root"], ctx["stage_dir"] / d["staged"],
                                        d["sheet"], key)
    except SystemExit:
        # 内部 fail() 的结构化错误已落到 stderr；这里只归一并带 key 上下文
        raise task_scheduler.StageError(
            "CACHE_BUILD_FAILED",
            f"缓存键 {key[:12]}… 的展平构建失败（细节见上方 stderr）",
            "修复 officecli 环境后重试（缓存键不变时确定性重展平）") from None
    return {"status": "ok",
            "artifacts": {"key": key, "hit": False, "file": d["staged"],
                          "sheet": d["sheet"]}}


def run_prepare_worker(ctx: dict, rid: str) -> dict:
    """阶段 2 worker：单 run 物化 + prepare_manifest 组装（只写本 run 目录，
    并行安全；run 内 shell 约束与单 run 语义一致）。

    run 级缺陷以 StageError 抛出（不阻断同阶段其他 run）；任务级缺陷已在
    prelude fail()。"""
    runs_dir = ctx["runs_dir"]
    runs_dir.mkdir(parents=True, exist_ok=True)
    run_dir = runs_dir / rid
    run_dir.mkdir(parents=True, exist_ok=True)

    demands = ctx["demands"]
    run_demands = [d for d in demands if d["run"] == rid]
    run_staged = []
    for d in run_demands:
        if d["staged"] not in run_staged:
            run_staged.append(d["staged"])

    # 8a. staged 文件物化（幂等复制；compile 的 input_hashes 绑定对象）
    recs = stage_files.stage_files(
        run_dir, [(str(ctx["stage_dir"] / name), name) for name in run_staged])
    if any(r["status"] == "ERROR" for r in recs):
        raise task_scheduler.StageError(
            "STAGE_FAILED", f"run {rid} staging 失败",
            "检查 staged 文件后重试该 run")
    files = [{"staged": name,
              "source": sha_src_by_staged(name, ctx["staged_files"]),
              "sha256": sha256_file(run_dir / name)} for name in run_staged]
    for name in run_staged:
        shutil.copyfile(ctx["outlines_dir"] / f"{Path(name).stem}_outline.txt",
                        run_dir / f"{Path(name).stem}_outline.txt")
    outlines_map = {name: f"{Path(name).stem}_outline.txt"
                    for name in run_staged}

    # 8b. 缓存产物物化（命名与单 run 约定一致）
    names = [d["name"] for d in run_demands]
    if len(set(names)) != len(names):
        raise task_scheduler.StageError(
            "ENTRY_NAME_DUPLICATE",
            f"run {rid} 的展平条目名重复: {names}",
            "同名 (file, sheet) 既做源又做目标时，分开声明或改 staging 名")
    entries = [
        flatten_cache.materialize_entry(
            ctx["root"], d["key"], run_dir, staged_name=d["staged"],
            sheet=d["sheet"], name=d["name"],
            is_target=(d["kind"] == KIND_TARGET))
        for d in run_demands
    ]
    target_demand = next(d for d in run_demands if d["kind"] == KIND_TARGET)
    target_entry = next(e for e in entries
                        if e["name"] == target_demand["name"])

    # 8c. run 级指纹 + 决策事实（与 prepare_run 的扁平逻辑同构）
    metas = {e["name"]: json.loads(
        (run_dir / e["meta"]).read_text(encoding="utf-8"))
        for e in entries}
    run_source_facts = [
        structure_facts(metas[e["name"]])
        for e in entries if e["name"] != target_entry["name"]
    ]
    run_fps = {
        "source_structure": facts_sha256(run_source_facts),
        "target_structure": facts_sha256([structure_facts(
            metas[target_entry["name"]])]),
    }
    row_gaps = {name: m["row_gaps"] for name, m in metas.items()
                if m.get("row_gaps")}
    run_manifest = assemble_run_manifest(
        str(run_dir), ctx["task_id"], files, outlines_map, entries,
        target_entry, run_fps, row_gaps=row_gaps,
        style_granularity=collect_style_granularity(metas))
    (run_dir / RUN_MANIFEST_NAME).write_text(
        json.dumps(run_manifest, ensure_ascii=False, indent=2),
        encoding="utf-8")
    _record_timing(run_dir, "task_prepare")

    return {"run": rid, "status": "ok",
            "artifacts": {"manifest": str(run_dir / RUN_MANIFEST_NAME)}}


def _run_child(script: str, args: list, fail_code: str, fail_action: str,
               *, timeout: int = 600):
    """以独立进程调用同目录脚本（现有脚本零改动的 subprocess 入口；
    失败 → StageError，由调度器归并为 failed 结果）。"""
    script_path = Path(__file__).resolve().parent / script
    try:
        r = subprocess.run(
            [sys.executable, str(script_path), *args],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout)
    except subprocess.TimeoutExpired:
        raise task_scheduler.StageError(
            fail_code, f"{script} 超时 ({timeout}s)", fail_action) from None
    if r.returncode != 0:
        tail = (r.stderr or r.stdout or "").strip()[-500:]
        raise task_scheduler.StageError(
            fail_code, f"{script} exit {r.returncode}: {tail}", fail_action)
    return r


def compile_worker(ctx: dict, rid: str) -> dict:
    """阶段 3 worker：compile_fill.py（纯文本，并发 4 安全）。"""
    run_dir = ctx["runs_dir"] / rid
    spec = run_dir / "fill_spec.yaml"
    if not spec.is_file():
        raise task_scheduler.StageError(
            "FILL_SPEC_MISSING", f"run {rid} 缺 fill_spec.yaml",
            "由 MOD Resolution 撰写（映射确认后）后重试该 run")
    _run_child("compile_fill.py",
               ["--spec", str(spec), "--workdir", str(run_dir)],
               "COMPILE_FAILED",
               "修复 fill_spec 后重试该 run（失败二分：输入事实改变 → supersede）")
    return {"run": rid, "status": "ok",
            "artifacts": {"fill_spec": str(spec),
                          "plan": "execution_plan.json", "mapping": "mapping.md"}}


def execute_worker(ctx: dict, rid: str) -> dict:
    """阶段 4 worker：execute_batch.py（Office 密集，并发默认 2 是实证安全
    上限；模板取本 run manifest 的目标条目 — 自包含 run 目录）。"""
    run_dir = ctx["runs_dir"] / rid
    plan = run_dir / "execution_plan.json"
    if not plan.is_file():
        raise task_scheduler.StageError(
            "PLAN_MISSING", f"run {rid} 缺 execution_plan.json",
            "先完成 compile 阶段（或重试该 run）")
    try:
        manifest = json.loads(
            (run_dir / RUN_MANIFEST_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise task_scheduler.StageError(
            "RUN_MANIFEST_MISSING", f"run {rid} 的 prepare_manifest.json "
            "缺失或损坏", "重新 prepare 该 run") from None
    template = run_dir / manifest["target"]["file"]
    if not template.is_file():
        raise task_scheduler.StageError(
            "TEMPLATE_MISSING", f"run {rid} 的目标模板缺失: {template}",
            "重新 prepare 该 run")
    _run_child("execute_batch.py",
               ["--plan", str(plan), "--template", str(template),
                "--workdir", str(run_dir)],
               "EXECUTE_FAILED",
               "读取失败明细修复后重试该 run（失败二分：输入事实改变 → "
               "supersede）")
    return {"run": rid, "status": "ok",
            "artifacts": {"receipt": "draft_receipt.json",
                          "draft": "validated_draft.*"}}


def gate_worker(ctx: dict, rid: str) -> dict:
    """阶段 5 worker：execution_gate.py --set（记录呈现的哈希三元组；
    串行一次人机交互 —— 确认展开与 gate_summary 聚合在 issue 05）。"""
    run_dir = ctx["runs_dir"] / rid
    _run_child("execution_gate.py", ["--set", "--workdir", str(run_dir)],
               "GATE_SET_FAILED",
               "检查该 run 的 gate 前置产物（spec/plan/draft）后重试")
    return {"run": rid, "status": "ok", "artifacts": {"gate": ".gate3_pending"}}


def _stage_worker(ctx: dict, stage: str):
    """stage → worker（闭包绑定 ctx；worker 只回报结果，绝不写 status）。"""
    if stage == "source_prepare":
        return lambda key: cache_build_worker(ctx, key)
    if stage == "run_prepare":
        return lambda rid: run_prepare_worker(ctx, rid)
    if stage == "compile":
        return lambda rid: compile_worker(ctx, rid)
    if stage == "execute":
        return lambda rid: execute_worker(ctx, rid)
    if stage == "gate":
        return lambda rid: gate_worker(ctx, rid)
    raise ValueError(f"unknown stage worker: {stage!r}")


# ── 阶段编排（--prepare / --run 共用；issue 03 barrier + 单一写者） ─────

def run_staged_pipeline(root: Path, task: dict, manifest: dict, status: dict,
                        *, stages=RUN_STAGES, progress=print) -> dict:
    """barrier 式阶段编排：prelude（串行任务级事实）→ 各阶段依次执行。

    - 阶段内并行（task_scheduler.run_stage，并发默认值 = implementation
      constant）；阶段间 barrier；无跨阶段流水线；
    - 单一写者：worker 只回报结果，task_status.json 在阶段边界由本进程
      统一写盘一次（apply_stage_status 批量推进）；
    - 失败传播：任一 run 失败不阻断同阶段其他 run；阶段结束汇总失败清单
      （report['failures']）。阶段 1（缓存构建）是任务级阶段：任一键失败
      → fail-closed 提前停（后续物化必然失败）；
    - 进度报告：阶段边界输出 `阶段 x/y 开始/完成` 摘要（progress 默认
      print，可注入捕获）。
    """
    ctx = prepare_task_level(root, task, manifest, status)
    stage_reports = []
    total = len(stages)
    prev_ok = None
    for idx, stage in enumerate(stages, 1):
        if stage == "source_prepare":
            items = list(ctx["unique_keys"])
        elif stage == "run_prepare":
            items = list(ctx["eligible_runs"])
        else:
            items = list(prev_ok or [])
        progress(task_scheduler.stage_start_line(idx, total, stage, len(items)))
        res = task_scheduler.run_stage(stage, items, _stage_worker(ctx, stage))
        # 阶段边界：批量推进 + 单独写盘一次（单一写者）
        status = task_scheduler.apply_stage_status(status, stage, res["results"])
        (root / TASK_STATUS_NAME).write_text(
            json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
        if stage == "source_prepare":
            _record_timing(root, "task_cache_build")
        elif stage == "run_prepare":
            _record_timing(root, "task_prepare")
        stage_reports.append(res)
        progress(task_scheduler.stage_end_line(idx, total, stage, res))
        for f in res["failed"]:
            progress(f"  失败: run={f.get('run')} [{f['code']}] {f['message']}")
        if stage == "source_prepare":
            if res["failed"]:
                break  # 任务级阶段失败：fail-closed 停（物化依赖全部缓存条目）
            finalize_cache_facts(ctx)  # 指纹/漂移/封存补全（依赖缓存 meta）
        prev_ok = [r["run"] for r in res["results"]
                   if r["status"] == "ok" and r.get("run")]

    # ── 汇总报告（阶段报告 + 跨阶段失败清单 + prepare 形态产物索引） ──
    sr1 = next((sr for sr in stage_reports if sr["stage"] == "source_prepare"),
               None)
    sr2 = next((sr for sr in stage_reports if sr["stage"] == "run_prepare"),
               None)
    cache_report = {"unique_keys": len(ctx["unique_keys"]), "hits": 0,
                    "misses": 0}
    if sr1 is not None:
        cache_report["hits"] = sum(
            1 for r in sr1["ok"] if r["artifacts"].get("hit"))
        cache_report["misses"] = sum(
            1 for r in sr1["ok"] if not r["artifacts"].get("hit"))
    prepared = {}
    if sr2 is not None:
        for r in sr2["ok"]:
            prepared[r["run"]] = r["artifacts"].get("manifest")
    return {
        "stages": stage_reports,
        "failures": task_scheduler.aggregate_failures(stage_reports),
        "cache": cache_report,
        "prepared": prepared,
        "superseded": list(ctx["superseded_runs"]),
    }


def run_prepare(root: Path, task: dict, manifest: dict, status: dict,
                *, progress=print) -> dict:
    """--prepare 挂载（spec S2/S4/S5；issue 02 契约保持）：阶段 1+2 的既有
    入口，report 形态兼容（cache / runs / superseded），另附失败清单。

    行为变化（issue 03）：run 级失败不再 fail-fast —— 同阶段其他 run 不受
    影响，失败清单在 report['failures'] 汇总，由调用方决定退出语义；任务级
    缺陷仍在 prelude fail()（fail-closed 不变）。"""
    report = run_staged_pipeline(root, task, manifest, status,
                                 stages=PREPARE_STAGES, progress=progress)
    return {
        "cache": report["cache"],
        "runs": report["prepared"],
        "superseded": report["superseded"],
        "failures": report["failures"],
        "stages": report["stages"],
    }


def sha_src_by_staged(name: str, staged_files: list[dict]) -> str:
    """staged 名 → 源路径（files 条目的 source 字段）。"""
    return next(f["source"] for f in staged_files if f["staged"] == name)