#!/usr/bin/env python3
"""
scripts/materialize_run.py — Run Materialization: Topology decision → run-local compiler view.

The single lowering boundary between a resolved Run Definition and the run-local
`prepare_manifest.json` (compile-facing view). It is NOT a prepare lifecycle:
it discovers nothing, flattens nothing, renders nothing beyond a target routing
view, and never mutates the workspace.

Two front-end forms, normalized into one internal RunDefinition:
  single-run (ephemeral, no task.yaml):
    python scripts/materialize_run.py --workdir <dir> \
      --sources source_HOME,source_MULTI --target customer_SPEC
  multi-run (persistent run list):
    python scripts/materialize_run.py --workdir <task-root> --task task.yaml

Four actions, and only four:
  1. Resolve   sources/target entry names against workspace_manifest
  2. Validate  referenced entries exist in the initialized workspace scope;
               otherwise RUN_ENTRY_NOT_IN_WORKSPACE (exit 3) — never silently
               incremental-flatten a missing sheet; corrective action is a
               full re-init with the complete business-sheet union.
  3. Project   derive the run-local prepare_manifest.json: run-level
               source_structure / target_structure fingerprints aggregated
               from entry-level structure facts (aggregation is run-local —
               the workspace stores no role aggregates), row_gaps and
               style_granularity decision facts.
  4. Render    produce the target routing view (placeholder/clone-source style
               segments) from the target entry's EXISTING meta/csv/candidates
               via structure_digest --pre-mod --target — a pure presentation
               projection: 0 probing, 0 flatten, 0 extraction. Written as
               <target>_target_view.md (distinct name — never overwrites the
               workspace's role-neutral <target>_premod_evidence.md).

Forbidden: topology reasoning, task-shape reasoning, MOD nomination, sheet
discovery, incremental flatten, workspace modification, cache/reuse discovery,
business mapping, FillSpec generation, writing back to workspace_manifest,
creating any run-definition artifact (single-run CLI input stays ephemeral;
multi-run identity lives in task.yaml).

Run answers:
  single-run → <workdir>/prepare_manifest.json (flat workdir, no Task container)
  multi-run  → <task-root>/runs/<id>/prepare_manifest.json per run (plain loop,
               no scheduler/status/retry; any invalid run fails closed).

Exit codes: 0=pass, 1=fatal (env/file), 3=retryable (apply corrective_action).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _officecli import ensure_utf8_stdio as _utf8_stdio, fail  # noqa: E402
from prepare_run import (  # noqa: E402 —— 复用既有纯函数, 不搬不重构 (ADR 0020)
    collect_style_granularity,
    facts_sha256,
    structure_facts,
    validate_workspace_manifest_shape,
    verify_workspace_facts,
    workdir_pollution_defect,
)
from task_prepare import assemble_run_manifest  # noqa: E402 —— 既有 run-manifest 组装 (Q4 冻结: 唯一复用点)

WORKSPACE_MANIFEST_NAME = "workspace_manifest.json"
RUN_MANIFEST_NAME = "prepare_manifest.json"


# ── 内部 RunDefinition (两种输入形态归于同一最小结构) ─────────────────────

def _run_def(id: str, sources: list[str], target: str, output: str | None) -> dict:
    return {"id": id, "sources": sources, "target": target, "output": output}


# ── workspace_manifest 读取 (canonical physical facts, role-neutral) ───────

def load_workspace(workdir: Path) -> dict:
    """读取 workspace_manifest 并完成 trust-boundary 校验。

    校验三件事 (fail-closed):
      1. 形状 — validate_workspace_manifest_shape (schema_version/必需键)
      2. 事实 — verify_workspace_facts (inputs/derived 哈希 + flattened 引用
         与路径 containment 逐条核对)
    此后 manifest 声明的任何 artifact 才是可消费的。TOCTOU 不变量:
    校验保证 load 瞬间的一致; 之后对 workdir 的任何写入都视为越界, 除非
    走 canonical 工具。"""
    p = workdir / WORKSPACE_MANIFEST_NAME
    if not p.is_file():
        fail("WORKSPACE_MANIFEST_NOT_FOUND",
             f"{WORKSPACE_MANIFEST_NAME} not found in {workdir} — run "
             "workspace_init --init first (it is the Job-level entry, "
             "identical for single-run and multi-run)",
             "python scripts/workspace_init.py --workdir <dir> --init "
             "--files ... --sheets ... --task ...", exit_code=1)
    try:
        m = json.loads(p.read_text(encoding="utf-8"))
    except ValueError as e:
        fail("WORKSPACE_MANIFEST_INVALID",
             f"{WORKSPACE_MANIFEST_NAME} corrupt: {e}",
             "Delete the manifest and re-run workspace_init --init", exit_code=1)
    problems = validate_workspace_manifest_shape(m)
    if problems:
        fail("WORKSPACE_MANIFEST_INVALID",
             f"{WORKSPACE_MANIFEST_NAME} shape invalid: {'; '.join(problems)}",
             "Re-run workspace_init --init (canonical fact space, "
             "role-neutral schema v4)", exit_code=1)
    drift = verify_workspace_facts(workdir, m)
    if drift:
        kinds = sorted({d["kind"] for d in drift})
        fail("WORKSPACE_DRIFT",
             f"{len(drift)} 项 workspace 事实与物理文件不一致 "
             f"(kinds: {', '.join(kinds)}): {drift[0]['message']}",
             "重新 workspace_init --init — canonical fact space 与物理文件 "
             "不一致时绝不静默沿用旧结果",
             defects=[{k: d[k] for k in ("kind", "path", "message")}
                      for d in drift], exit_code=3)
    return m


def workspace_staged_names(workspace: dict) -> set[str]:
    return {(it.get("staged") or "") for it in workspace.get("inputs") or []}


def entry_by_name(workspace: dict, name: str) -> dict | None:
    for e in workspace.get("flattened") or []:
        if e.get("name") == name:
            return e
    return None


def entries_for_sheet(workspace: dict, staged: str, sheet: str) -> list[dict]:
    return [e for e in workspace.get("flattened") or []
            if e.get("file") == staged and e.get("sheet") == sheet]


def _resolve_sources(workspace: dict, staged: str,
                     sheets: list[str]) -> list[str]:
    """task.yaml 形态: 按 staged + sheet 解析源 entry 名 (不存在 → fail-closed)."""
    out = []
    for s in sheets:
        hits = entries_for_sheet(workspace, staged, s)
        if not hits:
            fail("RUN_ENTRY_NOT_IN_WORKSPACE",
                 f"run 引用 {staged}:{s} 不在已初始化的 workspace scope 内",
                 "以完整业务 sheet 并集重新 workspace_init --init (绝不偷偷"
                 "增量 flatten 缺失 sheet)", exit_code=3)
        out.append(hits[0]["name"])
    return out


def _resolve_target(workspace: dict, staged: str, sheet: str) -> str:
    hits = entries_for_sheet(workspace, staged, sheet)
    if not hits:
        fail("RUN_ENTRY_NOT_IN_WORKSPACE",
             f"run 引用目标 {staged}:{sheet} 不在已初始化的 workspace scope 内",
             "以完整业务 sheet 并集重新 workspace_init --init", exit_code=3)
    return hits[0]["name"]


# ── 前置校验: workspace 需未进入 compile+ 生命周期 (WORKDIR_POLLUTED) ────

def _guard_out_dir(workdir: Path, out_dir: Path) -> None:
    """materialize 是 run 生命周期的 lowering 入口: 对已推进到 compile+ 的
    目录重物化会先读旧产物再覆写 → fail-closed (strict=False: fill_spec.yaml
    豁免 — spec 撰写先于或后于 materialize 均合法, plan/receipt 永不进入)。"""
    defect = workdir_pollution_defect(out_dir, strict=False)
    if defect is not None:
        fail(defect["code"], defect["message"], defect["corrective_action"])


# ── 单 run 投影 (含 Render: target routing view) ──────────────────────────

def render_target_view(workspace: Path, target_entry: dict, out_dir: Path) -> str:
    """从 target entry 既有 meta/csv/candidates 渲染 run-local target routing
    view (占位/克隆源样式段)。纯重渲染: 0 probing · 0 flatten · 0 extraction。
    输出 <target>_target_view.md — 与 workspace role-neutral
    <target>_premod_evidence.md 不同名, 不覆盖 workspace 产物。"""
    view_name = f"{target_entry['name']}_target_view.md"
    meta = workspace / (target_entry.get("meta") or "")
    csv = workspace / (target_entry.get("csv") or "")
    cands = workspace / (target_entry.get("candidates") or "")
    args = ["structure_digest.py", "--meta", str(meta), "--csv", str(csv),
            "--candidates", str(cands), "--pre-mod", "--target",
            "--out", str(out_dir / view_name)]
    r = subprocess.run(
        [sys.executable, "-X", "utf8",
         str(Path(__file__).resolve().parent / args[0]), *args[1:]],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        tail = (r.stderr or r.stdout or "").strip()[-500:]
        fail("TARGET_VIEW_RENDER_FAILED",
             f"structure_digest --pre-mod --target failed for "
             f"{target_entry['name']}: {tail}",
             "Read stderr and re-run materialize_run (idempotent)", exit_code=3)
    return view_name


def materialize_one(workdir: Path, workspace: dict, run: dict,
                    out_dir: Path) -> dict:
    """把一个 RunDefinition 投影为 out_dir/prepare_manifest.json.

    run: {"id", "sources": [entry names], "target": entry name,
          "output": str|None}
    """
    _guard_out_dir(workdir, out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    source_names = list(run["sources"])
    target_name = run["target"]
    # self-fill 合法 (同 entry 同时作 source 与 target — pptx 机制测试等):
    # run 视图中条目只投影一次, 源/目标角色不冲突; 除此之外的重复仍拒绝。
    all_names = source_names + [target_name]
    is_self_fill = source_names.count(target_name) >= 1
    if len(set(all_names)) != len(all_names) and not is_self_fill:
        fail("ENTRY_NAME_DUPLICATE",
             f"run {run['id']} 的展平条目名重复: {all_names}",
             "同名 (file, sheet) 既做源又做目标时分开声明或改 staging 名",
             exit_code=3)

    entries = []
    for name in all_names:
        if any(e["name"] == name for e in entries):
            continue  # self-fill: 同名条目只投影一次
        e = entry_by_name(workspace, name)
        if e is None:
            fail("RUN_ENTRY_NOT_IN_WORKSPACE",
                 f"run 引用 entry {name!r} 不在已初始化的 workspace scope 内",
                 "以完整业务 sheet 并集重新 workspace_init --init (绝不偷偷"
                 "增量 flatten 缺失 sheet)", exit_code=3)
        entries.append(e)

    target_entry = next(e for e in entries if e["name"] == target_name)

    # files[] / outlines: 本 run 引用的 staged 输入 (来自 workspace inputs,
    # 不逐字节复制 —— 哈希引用共享)。
    run_staged = sorted({e["file"] for e in entries})
    by_staged = {it["staged"]: it for it in workspace.get("inputs") or []}
    files = [{"staged": st,
              "source": (by_staged.get(st) or {}).get("source"),
              "sha256": (by_staged.get(st) or {}).get("sha256")}
             for st in run_staged if st in by_staged]
    outlines_map = {st: (workspace.get("outlines") or {}).get(st)
                    for st in run_staged}
    outlines_map = {k: v for k, v in outlines_map.items() if v}

    # 投影 flattened 条目: 源 entry 复用 workspace evidence; target entry 的
    # evidence 指向 run-local target routing view (此步骤写入 canonical 之外
    # 的 run-local 文件, 不触碰 workspace_manifest 的 derived 记录)。
    view_name = render_target_view(workdir, target_entry, out_dir)
    flat = []
    for e in entries:
        item = {k: v for k, v in e.items() if k != "kind"}
        if e["name"] == target_name:
            item["evidence"] = view_name
        flat.append(item)

    # run 级指纹 (聚合发生在 run 层 — workspace 只存 entry-level 事实)。
    metas = {}
    for e in entries:
        meta_p = workdir / e["meta"]
        metas[e["name"]] = json.loads(meta_p.read_text(encoding="utf-8"))
    target_facts = [structure_facts(metas[target_name])]
    source_entries = [e for e in entries if e["name"] != target_name]
    if not source_entries and is_self_fill:
        # self-fill: 源与目标是同一 entry — source_structure 与 target 同指纹
        # (同一物理事实; compile 的 source_csv 读取与 target 相同)
        source_facts = target_facts
    else:
        source_facts = [structure_facts(metas[e["name"]])
                        for e in source_entries]
    fingerprints = {
        "source_structure": facts_sha256(source_facts),
        "target_structure": facts_sha256(target_facts),
    }
    row_gaps = {name: m["row_gaps"] for name, m in metas.items()
                if m.get("row_gaps")}

    target_item = next(e for e in flat if e["name"] == target_name)
    manifest = assemble_run_manifest(
        str(out_dir), run.get("task_label") or workspace.get("task") or "",
        files, outlines_map, flat, target_item, fingerprints,
        row_gaps=row_gaps,
        style_granularity=collect_style_granularity(metas))
    (out_dir / RUN_MANIFEST_NAME).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "run": run["id"],
        "prepare_manifest": str(out_dir / RUN_MANIFEST_NAME),
        "target_view": str(out_dir / view_name),
        "fingerprints": fingerprints,
    }


# ── 输入归一: 单 run CLI / 多 run task.yaml ───────────────────────────────

def _load_task_yaml(task_root: Path, task_path: str) -> dict:
    import yaml  # PyYAML (与其它脚本一致)
    p = task_root / task_path
    if not p.is_file():
        fail("TASK_YAML_NOT_FOUND", f"task.yaml not found: {p}",
             "Provide --task <task.yaml> relative to --workdir", exit_code=1)
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
    except ValueError as e:
        fail("TASK_YAML_INVALID", f"task.yaml corrupt: {e}",
             "Fix the YAML syntax", exit_code=1)
    return data or {}


def _run_defs_from_task(workspace: dict, task_root: Path,
                        task_yaml: dict) -> list[dict]:
    import task_schema  # noqa: PLC0415 —— task.yaml 静态校验 (既有, 保留)
    defects = task_schema.validate_task_yaml(task_yaml, task_root)
    if defects:
        fail("TASK_YAML_INVALID", f"{len(defects)} task.yaml defect(s)",
             "Fix task.yaml per task_schema validation",
             defects=[{k: v for k, v in d.items() if k in
                       ("code", "message", "corrective_action", "at")}
                      for d in defects], exit_code=3)

    staged_names = workspace_staged_names(workspace)

    def staged_for(ref: str) -> str:
        """task.yaml 的文件引用 → workspace staged 名 (先原文匹配, 再 basename)."""
        base = Path(ref).name
        if ref in staged_names:
            return ref
        for it in workspace.get("inputs") or []:
            src = it.get("source") or ""
            if Path(src).name == base or src == ref:
                return it["staged"]
        fail("RUN_ENTRY_NOT_IN_WORKSPACE",
             f"task.yaml 引用文件 {ref!r} 未在 workspace inputs 中 (staged: "
             f"{sorted(staged_names)})",
             "workspace_init --files 与 task.yaml 必须引用同一批输入文件",
             exit_code=3)

    runs = []
    tid = (task_yaml.get("task") or {}).get("id", "task")
    for r in task_yaml.get("runs") or []:
        rid = r["id"]
        src = r.get("source") or {}
        tgt = r.get("target") or {}
        staged_src = staged_for(src.get("file", ""))
        sources = _resolve_sources(workspace, staged_src, src.get("sheets") or [])
        staged_tgt = staged_for(tgt.get("template", ""))
        target = _resolve_target(workspace, staged_tgt, tgt.get("sheet", ""))
        runs.append(_run_def(rid, sources, target,
                             tgt.get("output") or f"out_{rid}.xlsx"))
    return runs


def main() -> None:
    _utf8_stdio()
    parser = argparse.ArgumentParser(
        description="Run Materialization: topology decision → run-local prepare_manifest.json")
    parser.add_argument("--workdir", type=Path, required=True,
                        help="workspace dir (single-run: flat workdir; multi-run: task root)")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--sources", type=str, default="",
                      help="single-run: 逗号分隔的源 entry 名 (workspace flattened[].name)")
    mode.add_argument("--task", type=str, default="",
                      help="multi-run: task.yaml 路径 (相对 --workdir); 一次 materialize 全部 runs")
    parser.add_argument("--target", type=str, default="",
                      help="single-run: target entry 名 (workspace flattened[].name)")
    parser.add_argument("--run-id", type=str, default="",
                      help="single-run 可选 run id (默认 'run', 仅用于路径命名)")
    args = parser.parse_args()

    workdir = args.workdir

    if args.task:
        # multi-run: run 级污染由 materialize_one 逐 run 守卫 (out_dir =
        # task-root/runs/<id>, 与 workspace 根目录不同物理层); workspace
        # 读取含 trust-boundary 校验。
        workspace = load_workspace(workdir)
        task_root = workdir
        task_yaml = _load_task_yaml(task_root, args.task)
        runs = _run_defs_from_task(workspace, task_root, task_yaml)
        results = []
        for run in runs:
            out_dir = task_root / "runs" / run["id"]
            results.append(materialize_one(workdir, workspace, run, out_dir))
        print(json.dumps({"status": "PASS", "code": "MATERIALIZED_TASK",
                          "runs": results}, ensure_ascii=False, indent=2))
    else:
        if not args.sources or not args.target:
            fail("NO_RUN_DEF", "--sources/--target 必填 (单 run CLI 形态)",
                 "single-run: --sources a,b --target c; multi-run: --task task.yaml",
                 exit_code=3)
        rid = args.run_id or "run"
        run = _run_def(rid,
                       [s.strip() for s in args.sources.split(",") if s.strip()],
                       args.target.strip(), None)
        # 污染守卫先行 (单 run out_dir == workdir): 已推进 compile+ 的目录在
        # 读取 workspace 之前拒绝 — 不留任何「先读旧产物再覆写」的路径
        # (守卫顺序契约: WORKDIR_POLLUTED 先于 WORKSPACE_DRIFT 触发)。
        _guard_out_dir(workdir, workdir)
        workspace = load_workspace(workdir)
        result = materialize_one(workdir, workspace, run, workdir)
        print(json.dumps({"status": "PASS", "code": "MATERIALIZED_RUN",
                          "run": result}, ensure_ascii=False, indent=2))
    sys.exit(0)


if __name__ == "__main__":
    main()