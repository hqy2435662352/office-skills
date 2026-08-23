#!/usr/bin/env python3
"""
scripts/task_prepare.py — Task 级 Prepare 编排（issue 02，spec S4/S5 物化消费）。

职责（Task Layer = 共享准备 + run 创建；Run Layer 契约零改动）：
  - 任务级一次性 staging：唯一输入文件 → <task_root>/staged/（ASCII 命名，
    确定性、可复现）；
  - 任务级 outline：每文件一次（officecli view outline），sheet 存在性在
    此验证（task_schema 静态校验只查文件存在）；
  - eager 预展平需求收集：source.sheets[] + target.sheet → (file, sheet) 对，
    按缓存键去重（唯一需求数 = cache/ 目录数）；
  - run 创建：物化（staged 复制 + 缓存产物物化 + candidates/digest 再生成）
    + run 级 prepare_manifest.json 组装（compile-facing 字段与单 run 同构，
    flattened 条目仅多 cache_key/sha256 元数据）。

契约测试 seam（spec Testing Decision #3）：
  - staged_name_for / collect_demands / assemble_run_manifest 是可 import 的
    纯函数（无 Office、可单测）；
  - run_prepare 是 CLI 编排胶水（prepare_task.py --prepare 调用），复用
    prepare_run / flatten_cache 的底层函数，prepare_run.py 本体零改动。
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # 套件导入纪律

from _officecli import (  # noqa: E402
    fail, record_timing as _record_timing, sha256_file,
)

import flatten_cache  # noqa: E402
import preflight  # noqa: E402
import stage_files  # noqa: E402
from flatten_table import officecli_outline  # noqa: E402
from prepare_run import (  # noqa: E402 —— 复用单 run 底层函数（命名/指纹/决策事实）
    ascii_slug, collect_style_granularity, facts_sha256, structure_facts,
)
from task_schema import _resolve, utc_now_iso  # noqa: E402

RUN_MANIFEST_NAME = "prepare_manifest.json"
STAGED_DIR_NAME = "staged"
OUTLINES_DIR_NAME = "outlines"
RUNS_DIR_NAME = "runs"

# 需求角色（collect_demands 的 kind 字段；task 级指纹与物化按角色分支）
KIND_SOURCE = "source"
KIND_TARGET = "target"

# run 生命周期状态的 prepare 语义（与 task_schema.RUN_STATES 同一集合）：
# --prepare 只允许重建 planned/prepared 的 run；superseded 是保留证据的
# 终止分支（跳过）；其余状态（compiled+）由 issue 04 的 resume 语义承担。
PREPARE_ALLOWED_STATES = ("planned", "prepared")
SUPERSEDED_STATE = "superseded"


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


# ── CLI 编排胶水（prepare_task.py --prepare 调用；无 Office 不测本函数） ──

def run_prepare(root: Path, task: dict, manifest: dict, status: dict) -> dict:
    """Task 级 Prepare：staging + outline + eager 缓存构建 + 物化 + run
    manifest 组装 + 状态推进（planned→prepared）。"""
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

    # ── 5. eager 预展平（每缓存键恰好一次；命中零 officecli） ──
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

    preflight.check_resident_cleanup()
    hits = misses = 0
    cache_refs = {}
    for key in unique_keys:
        d = key_of[key]
        if flatten_cache.cache_hit(flatten_cache.cache_entry_dir(root, key)):
            hits += 1
        else:
            misses += 1
            flatten_cache.build_cache_entry(root, stage_dir / d["staged"],
                                            d["sheet"], key)
        cache_refs[key] = {
            "file": d["staged"], "sheet": d["sheet"],
            "source_hash": sha_by_staged[d["staged"]],
            "flatten_schema_version": schema_v, "officecli_version": oc_version,
        }
    _record_timing(root, "task_cache_build")

    # ── 6. task 级指纹（缓存 metas 的 source/target 结构事实） + 冻结校验 ──
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
    if prev_refs and prev_refs != cache_refs:
        fail("CACHE_REF_DRIFT",
             "缓存键引用与已封存快照不一致（officecli 升级或源 hash 漂移）",
             "失败二分：输入事实改变 → supersede（issue 04）；否则删除缓存与"
             "快照后重新 --init/--prepare")
    if prev_fps and prev_fps != fingerprints:
        fail("FINGERPRINT_DRIFT",
             "结构指纹与已封存快照不一致",
             "输入事实改变 → supersede（issue 04）")

    # ── 7. 封存快照补全（--init 骨架 → prepare 事实；只授权一次写） ──
    if not prev_staged:
        manifest["staged_files"] = staged_files
        manifest["outlines"] = outlines
        manifest["flatten_cache_refs"] = cache_refs
        manifest["fingerprints"] = fingerprints
        (root / "task_manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    # ── 8. 逐 run 创建：物化 + run manifest 组装 ──
    runs_dir = root / RUNS_DIR_NAME
    runs_dir.mkdir(parents=True, exist_ok=True)
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

    prepared_runs = {}
    superseded_runs = []
    for rid, entry_state in status_runs.items():
        state = entry_state.get("state")
        if state == SUPERSEDED_STATE:
            superseded_runs.append(rid)
            continue
        run_dir = runs_dir / rid
        run_dir.mkdir(parents=True, exist_ok=True)

        # 8a. staged 文件物化（幂等复制；compile 的 input_hashes 绑定对象）
        run_demands = [d for d in demands if d["run"] == rid]
        run_staged = []
        for d in run_demands:
            if d["staged"] not in run_staged:
                run_staged.append(d["staged"])
        recs = stage_files.stage_files(
            run_dir, [(str(stage_dir / name), name) for name in run_staged])
        if any(r["status"] == "ERROR" for r in recs):
            fail("STAGE_FAILED", f"run {rid} staging 失败",
                 "检查 staged 文件后重试", exit_code=1)
        files = [{"staged": name, "source": sha_src_by_staged(name, staged_files),
                  "sha256": sha256_file(run_dir / name)} for name in run_staged]
        for name in run_staged:
            shutil.copyfile(outlines_dir / f"{Path(name).stem}_outline.txt",
                            run_dir / f"{Path(name).stem}_outline.txt")
        outlines_map = {name: f"{Path(name).stem}_outline.txt"
                        for name in run_staged}

        # 8b. 缓存产物物化（命名与单 run 约定一致）
        names = [d["name"] for d in run_demands]
        if len(set(names)) != len(names):
            fail("ENTRY_NAME_DUPLICATE",
                 f"run {rid} 的展平条目名重复: {names}",
                 "同名 (file, sheet) 既做源又做目标时，分开声明或改 staging 名")
        entries = [
            flatten_cache.materialize_entry(
                root, d["key"], run_dir, staged_name=d["staged"],
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
            str(run_dir), task_id, files, outlines_map, entries, target_entry,
            run_fps, row_gaps=row_gaps,
            style_granularity=collect_style_granularity(metas))
        (run_dir / RUN_MANIFEST_NAME).write_text(
            json.dumps(run_manifest, ensure_ascii=False, indent=2),
            encoding="utf-8")
        _record_timing(run_dir, "task_prepare")
        prepared_runs[rid] = str(run_dir / RUN_MANIFEST_NAME)

    # ── 9. status 推进（单一写者 = prepare_task 进程） ──
    for rid, entry_state in status_runs.items():
        if entry_state.get("state") in PREPARE_ALLOWED_STATES:
            entry_state["state"] = "prepared"
    status["updated_at"] = utc_now_iso()
    (root / "task_status.json").write_text(
        json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
    _record_timing(root, "task_prepare")

    return {
        "cache": {"unique_keys": len(unique_keys), "hits": hits, "misses": misses},
        "runs": prepared_runs,
        "superseded": superseded_runs,
    }


def sha_src_by_staged(name: str, staged_files: list[dict]) -> str:
    """staged 名 → 源路径（files 条目的 source 字段）。"""
    return next(f["source"] for f in staged_files if f["staged"] == name)