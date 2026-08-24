#!/usr/bin/env python3
"""
scripts/task_resume.py — Run Lifecycle / Resume / Supersede（issue 04，spec S7）。

status 是生命周期索引，不是真值源：断点判定 = artifact 存在性 + hash 校验
（无 Office 的纯文件系统 + hash 逻辑，可 import、可单测 —— spec Testing
Decision #2/4 的 seam）。本模块是三块的核心：

1. 断点判定（checkpoint determination）：
   gather_run_facts 收集 run 目录的产物证据（manifest/spec/plan/receipt/
   draft/gate markers/final_receipt 的存在性与 hash），classify_run_facts
   按 spec S7 矩阵给出判定：

   - final_receipt 存在                    → promoted（跳过）
   - status 标 superseded（无 --rebuild）  → superseded（跳过）
   - .gate3_pending hash 三元组有效       → gated（等待确认，不绕过）
   - .gate3_confirmed hash 三元组有效     → confirmed（等待 gate_task 的
                                           promote；resume 不自动 promote）
   - draft 存在 + receipt.draft_sha256 匹配 → drafted（直接进 gate）
   - draft 存在但 receipt 缺失/不匹配
     （execute crash window）              → execute_retry（重跑 execute）
   - draft 存在但 receipt 的 spec/plan 绑定
     与当前不符（execute 后输入事实改变） → blocked（建议 supersede）
   - plan 的 fill_spec_sha256 匹配 +
     input_hashes 绑定有效                → compiled（跳过 compile）
   - manifest 有效 + 物化产物 hash 匹配    → prepared（跳过 run_prepare）
   - 其余（无产物 / 产物破损）            → planned（阶段 1 起）

2. 恢复编排（resume pipeline）：resume_with_ctx 按判定结果调度剩余阶段
   （compile barrier → execute barrier → gate；barrier 顺序 + 单一写者 +
   失败隔离复用 task_scheduler）；计划 schedule_resume 是纯函数
   （判定 → {stage: [runs]}，空阶段不执行）。不自动跳过 Gate、不自动
   promote（fail-closed 不变）。

3. 失败二分的边界（spec S7 / 设计点 3）：
   - execute 之前（compile 周期内）的 spec/plan 改动 = 输入事实未变的
     REPAIR 循环：判定降级到 prepared（重 compile）—— 与单 run 的
     “修 spec → 重编译”修复语义一致（编出来的 draft 还没落地）；
   - execute 之后 spec/plan 与 receipt 绑定漂移 = 输入事实改变（MOD/映射
     裁决变化已落地到产物）：blocked → supersede；
   - task.yaml 变化（MANIFEST_STALE）与源文件 hash 漂移
     （SOURCE_HASH_DRIFT）在 prelude 阻塞 → supersede。

4. Supersede（失败二分：输入事实改变 → supersede 该 run，run 级非 task 级）：
   - validate_supersede：mapping 形状校验（old ∈ status、new 已在 task.yaml
     声明、无环/重复、旧 run 必须保留在 task.yaml、声明被改动的 run 必须
     mapping —— 防“改了声明但不标记废弃”的静默错误）；
   - supersede_status：旧 run 标 superseded + superseded_by 链接新版本，
     新增 run 初始化 planned，其余 run 状态不动（不重置）—— 纯函数；
   - resume_task.py --supersede 是唯一被授权在 task.yaml 变化后重派生
     manifest 快照的入口（issue 01 的 MANIFEST_STALE fail-closed 由此显式
     解除，不静默重派生）。

证据语义说明（SKILL.md 权威模型：gate marker 与 final_receipt 是流程
证据，不是可写状态）：gated/confirmed 判据 = marker 的 hash 三元组与当前
产物相等（marker 只引导调度，从不授权）；promoted 判据 = final_receipt
存在（ticket 矩阵字面语义；promote 的最终副本 hash 一致性由
promote_output.py 写入时校验，resume 只按存在性跳过）。
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # 套件导入纪律

from _officecli import record_timing as _record_timing, sha256_file  # noqa: E402

import task_prepare  # noqa: E402
import task_scheduler  # noqa: E402
from execution_gate import gate_hashes  # noqa: E402 —— 呈现态 hash 三元组复用
from task_schema import (  # noqa: E402
    RUN_STATES, TASK_STATUS_NAME, utc_now_iso,
)


# ── 断点判定：产物证据收集（纯文件系统 + hash，无 Office） ──────────────

def _try_json(path: Path):
    """读取 JSON 产物；缺失或损坏 → None（损坏按缺失处理，分类自动降级）。"""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return None


def gather_run_facts(run_dir) -> dict:
    """收集单个 run 目录的全部断点证据（纯文件系统 + SHA-256，无 Office）。

    返回的 facts 只含存在性/哈希/解析结果，不做出任何判定 —— classify 是
    唯一的判定者（判定逻辑与证据收集分离，便于逐层单测）。

    物化 hash 校验范围（与 run manifest 的登记一致）：files[].sha256 与
    flattened[].sha256（物化 CSV）逐字节比对；meta/digest 只查存在（物化
    完整性的廉价证据，不带 hash 登记）。"""
    run_dir = Path(run_dir)
    facts = {"run_dir": str(run_dir)}

    # ── prepare_manifest.json：files（物化 staged 输入）+ flattened（物化
    #    展平产物）的登记与 sha256 —— “物化产物 hash 匹配”的证据 ──
    manifest = _try_json(run_dir / task_prepare.RUN_MANIFEST_NAME)
    facts["manifest"] = manifest
    files_match = None
    flattened_match = None
    if isinstance(manifest, dict) and isinstance(manifest.get("files"), list):
        files = manifest["files"]
        if files:
            files_match = all(
                isinstance(f, dict) and isinstance(f.get("staged"), str)
                and f["staged"] and (run_dir / f["staged"]).is_file()
                and sha256_file(run_dir / f["staged"]) == f.get("sha256")
                for f in files)
        else:
            files_match = False  # 无 files 登记的 manifest 视为无效
    if isinstance(manifest, dict) and isinstance(manifest.get("flattened"), list):
        flat = manifest["flattened"]
        if flat:
            flattened_match = all(
                isinstance(e, dict)
                and e.get("csv") and (run_dir / e["csv"]).is_file()
                and sha256_file(run_dir / e["csv"]) == e.get("sha256")
                and e.get("meta") and (run_dir / e["meta"]).is_file()
                and e.get("digest") and (run_dir / e["digest"]).is_file()
                for e in flat)
        else:
            flattened_match = False
    facts["manifest_valid"] = bool(files_match and flattened_match)

    # ── fill_spec.yaml + execution_plan.json：compiled 断点的证据 ──
    spec_path = run_dir / "fill_spec.yaml"
    facts["spec_sha256"] = sha256_file(spec_path) if spec_path.is_file() else None
    plan_path = run_dir / "execution_plan.json"
    plan = _try_json(plan_path)
    facts["plan"] = plan
    plan_fill_match = (
        isinstance(plan, dict)
        and facts["spec_sha256"] is not None
        and plan.get("fill_spec_sha256") == facts["spec_sha256"])
    input_hashes_valid = False
    if isinstance(plan, dict) and isinstance(plan.get("input_hashes"), dict):
        ih = plan["input_hashes"]
        if ih:
            input_hashes_valid = all(
                isinstance(v, str) and v and (run_dir / k).is_file()
                and sha256_file(run_dir / k) == v
                for k, v in ih.items())
    facts["plan_valid"] = bool(plan_fill_match and input_hashes_valid)

    # ── draft_receipt.json + draft：drafted / crash window 的证据 ──
    receipt = _try_json(run_dir / "draft_receipt.json")
    facts["receipt"] = receipt
    draft_path = None
    if isinstance(receipt, dict) and receipt.get("draft_path"):
        p = Path(receipt["draft_path"])
        if p.is_file():
            draft_path = p
    if draft_path is None:
        drafts = sorted(run_dir.glob("validated_draft.*"))
        draft_path = drafts[0] if drafts else None
    facts["draft_path"] = str(draft_path) if draft_path else None
    facts["draft_sha256"] = sha256_file(draft_path) if draft_path else None
    facts["receipt_draft_match"] = bool(
        isinstance(receipt, dict)
        and isinstance(receipt.get("draft_sha256"), str)
        and facts["draft_sha256"] is not None
        and receipt["draft_sha256"] == facts["draft_sha256"])

    # receipt 对 spec/plan 的 execute 期绑定：与当前文件不符 = execute 之后
    # 输入事实改变（失败二分 → supersede），区别于 crash window（重试）。
    binding = {"ok": True, "issue": None, "detail": None}
    if isinstance(receipt, dict):
        r_spec = receipt.get("fill_spec_sha256")
        r_plan = receipt.get("execution_plan_sha256")
        if facts["spec_sha256"] is None:
            binding = {"ok": False, "issue": "spec_missing",
                       "detail": "fill_spec.yaml 缺失（receipt 存在但竞态删除）"}
        elif r_spec != facts["spec_sha256"]:
            binding = {"ok": False, "issue": "spec_changed",
                       "detail": "fill_spec.yaml 在 execute 后被修改"}
        elif not plan_path.is_file():
            binding = {"ok": False, "issue": "plan_missing",
                       "detail": "execution_plan.json 缺失"}
        elif r_plan != sha256_file(plan_path):
            binding = {"ok": False, "issue": "plan_changed",
                       "detail": "execution_plan.json 在 execute 后被替换"}
    facts["receipt_binding"] = binding

    # ── Gate markers + final_receipt：gated / confirmed / promoted 的证据 ──
    pending = _try_json(run_dir / ".gate3_pending")
    confirmed = _try_json(run_dir / ".gate3_confirmed")
    facts["pending_hashes"] = (
        pending.get("hashes")
        if isinstance(pending, dict) and isinstance(pending.get("hashes"), dict)
        else None)
    facts["confirmed_hashes"] = (
        confirmed.get("hashes")
        if isinstance(confirmed, dict)
        and isinstance(confirmed.get("hashes"), dict)
        else None)
    facts["trio_current"] = gate_hashes(run_dir)  # 当前呈现态 hash 三元组
    facts["final_receipt_exists"] = (
        run_dir / "final_receipt.json").is_file()
    return facts


def _evidence(facts: dict) -> dict:
    """判定报告的 evidence：裁决基于哪些产物事实（人读诊断用）。"""
    return {
        "manifest_valid": facts["manifest_valid"],
        "plan_valid": facts["plan_valid"],
        "receipt": facts["receipt"] is not None,
        "receipt_draft_match": facts["receipt_draft_match"],
        "draft": facts["draft_sha256"] is not None,
        "pending": facts["pending_hashes"] is not None,
        "confirmed": facts["confirmed_hashes"] is not None,
        "final_receipt": facts["final_receipt_exists"],
    }


def classify_run_facts(facts: dict, *, status_state: str | None = None,
                       rebuild: bool = False) -> dict:
    """断点判定（纯函数，spec S7 场景矩阵；无 Office 可单测）。

    返回 {"status", "needs", "reason", "evidence"}；status ∈
    planned/prepared/compiled/drafted/execute_retry/gated/confirmed/promoted/
    superseded/blocked。needs = 恢复后仍需执行的阶段子序列（STAGES 顺序）。
    """
    if not rebuild and status_state == "superseded":
        return {
            "status": "superseded", "needs": [],
            "reason": "run 已标 superseded（保留证据的终止分支）；"
                      "显式 --rebuild 才重新进入主路径",
            "evidence": _evidence(facts),
        }
    if facts["final_receipt_exists"]:
        return {
            "status": "promoted", "needs": [],
            "reason": "final_receipt.json 存在 — 已交付，跳过",
            "evidence": _evidence(facts),
        }
    trio = facts["trio_current"]
    if trio is not None and facts["pending_hashes"] == trio:
        return {
            "status": "gated", "needs": [],
            "reason": ".gate3_pending 有效（hash 三元组匹配当前产物）— "
                      "等待人工确认，resume 不绕过 Gate、不自动 promote",
            "evidence": _evidence(facts),
        }
    if trio is not None and facts["confirmed_hashes"] == trio:
        return {
            "status": "confirmed", "needs": [],
            "reason": ".gate3_confirmed 有效且未 promote — 确认已绑定，"
                      "promote 由 gate_task 展开（resume 不自动 promote）",
            "evidence": _evidence(facts),
        }
    if facts["draft_sha256"] is not None:
        if facts["receipt"] is None:
            return {
                "status": "execute_retry", "needs": ["execute", "gate"],
                "reason": "draft 存在但 receipt 缺失（execute crash window）— "
                          "重跑 execute 重建 draft+receipt",
                "evidence": _evidence(facts),
            }
        if not facts["receipt_draft_match"]:
            return {
                "status": "execute_retry", "needs": ["execute", "gate"],
                "reason": "draft 与 receipt.draft_sha256 不匹配"
                          "（execute crash window / draft 被改动）— 重跑 execute",
                "evidence": _evidence(facts),
            }
        binding = facts["receipt_binding"]
        if not binding["ok"]:
            return {
                "status": "blocked", "needs": [],
                "reason": f"execute 后输入事实改变（{binding['detail']}）— "
                          "禁止在旧 run 上继续修补",
                "evidence": _evidence(facts),
                "blocked": {
                    "code": "RUN_INPUT_CHANGED",
                    "message": f"run 的 draft/receipt 与当前 spec/plan 绑定不符"
                               f"（{binding['detail']}）",
                    "corrective_action": "失败二分：输入事实改变 → 用 "
                        "resume_task.py --supersede --map <run>=<run>_v2 标记"
                        "废弃并链接新版本，新 run 从新输入重新编译执行",
                    "issue": binding["issue"],
                },
            }
        return {
            "status": "drafted", "needs": ["gate"],
            "reason": "draft + receipt.draft_sha256 匹配 — 直接进 gate 呈现",
            "evidence": _evidence(facts),
        }
    if facts["plan_valid"]:
        return {
            "status": "compiled", "needs": ["execute", "gate"],
            "reason": "plan 的 fill_spec_sha256 匹配 + input_hashes 绑定有效 — "
                      "跳过 compile",
            "evidence": _evidence(facts),
        }
    if facts["manifest_valid"]:
        return {
            "status": "prepared", "needs": ["compile", "execute", "gate"],
            "reason": "manifest 有效 + 物化产物 hash 匹配 — 跳过 run_prepare",
            "evidence": _evidence(facts),
        }
    return {
        "status": "planned", "needs": [
            "source_prepare", "run_prepare", "compile", "execute", "gate"],
        "reason": "无产物（或产物无效）— 从阶段 1 起",
        "evidence": _evidence(facts),
    }


# ── 恢复调度：判定 → 剩余阶段计划（纯函数） ──────────────────────────────

NEEDS_BY_STATUS = {
    "planned": ("source_prepare", "run_prepare", "compile", "execute", "gate"),
    "prepared": ("compile", "execute", "gate"),
    "compiled": ("execute", "gate"),
    "execute_retry": ("execute", "gate"),
    "drafted": ("gate",),
    # 终态 / 等待态：不再调度任何阶段（--rebuild 由调用方改写 status 后重判）
    "gated": (),
    "confirmed": (),
    "promoted": (),
    "superseded": (),
    "blocked": (),
}


def schedule_resume(decisions: dict) -> dict:
    """判定集合 → 阶段计划（纯函数）：{stage: [run ids]}，按 STAGES 顺序，
    空阶段不出现。barrier 顺序由 STAGES 本身保证（无跨阶段流水线）。"""
    schedule = {stage: [] for stage in task_scheduler.STAGES}
    for rid, decision in decisions.items():
        for stage in NEEDS_BY_STATUS.get(decision["status"], ()):
            schedule[stage].append(rid)
    return {stage: items for stage, items in schedule.items() if items}


# ── 恢复编排（barrier 调度 + 单一写者；worker 可注入供无 Office 单测） ────

# --rebuild 时 superseded run 的状态索引重置表（status 是索引不是真值源：
# 产物证据决定实际断点，索引只复位到与本 run 判定对齐的合法状态，使
# apply_stage_status 的阶段推进能继续工作）。confirmed 无对应状态，
# 复位到 drafted（等待 gate_task 的 confirm/promote 展开）。
_INDEX_STATE_FOR = {
    "planned": "planned",
    "prepared": "prepared",
    "compiled": "compiled",
    "execute_retry": "compiled",
    "drafted": "drafted",
    "gated": "gated",
    "confirmed": "drafted",
    "promoted": "promoted",
    "blocked": None,      # 内部不一致（绑定漂移）：保持 superseded，不重置
    "superseded": "superseded",
}


def resume_with_ctx(ctx: dict, status: dict, *, rebuild: bool = False,
                    worker_of=None, progress=print) -> dict:
    """按断点判定继续剩余阶段（compile barrier → execute barrier → gate）。

    - 判定：逐 run gather_run_facts + classify_run_facts（status 只提供
      superseded 标记，其余全部由产物证据裁决 —— status 不是真值源）；
    - --rebuild：superseded run 的状态索引先复位到与判定对齐的合法状态
      （_INDEX_STATE_FOR，写盘一次），再进入调度 —— 否则 apply_stage_status
      的前驱集合永远不会推进 superseded 索引；
    - 调度：schedule_resume（纯函数）→ 逐阶段 barrier 执行；已跳过阶段
      （无 run 需要）不执行。阶段 1（source_prepare）的 item 域是缓存键
      （ctx["unique_keys"]，与 run_staged_pipeline 同契约 —— cache_build_worker
      按键寻址），不是 run id；
    - 单一写者：worker 只回报结果，task_status.json 在阶段边界由本进程
      统一写盘一次（apply_stage_status 批量推进，与 issue 03 同契约）；
    - 失败隔离：任一 run 阶段失败不影响同阶段其他 run，且被排除出后续
      阶段（失败清单汇总）；阶段 1（缓存构建）是任务级阶段，任一键失败
      → fail-closed 停；
    - blocked run：不调度任何阶段（不继续旧 run），整体报告 supersede
      建议；promoted/superseded 跳过；gated 等待确认不绕过；confirmed
      等待 gate_task 的 promote —— 本流程不自动确认、不自动 promote；
    - worker_of 可注入（无 Office 单测 seam），默认 task_prepare 的 worker。
    """
    root = ctx["root"]
    runs_dir = ctx["runs_dir"]

    decisions: dict = {}
    blocked: list[dict] = []
    for rid, entry in ctx["status_runs"].items():
        decision = classify_run_facts(
            gather_run_facts(runs_dir / rid),
            status_state=entry.get("state") if not rebuild else None)
        decisions[rid] = decision
        if decision["status"] == "blocked":
            blocked.append({
                "run": rid, "code": decision["blocked"]["code"],
                "message": decision["blocked"]["message"],
                "corrective_action": decision["blocked"]["corrective_action"],
            })

    # --rebuild：superseded 索引复位（显式重建的前置；阶段推进依赖合法索引）
    if rebuild:
        changed = False
        for rid, decision in decisions.items():
            entry = ctx["status_runs"].get(rid)
            if not isinstance(entry, dict) or entry.get("state") != "superseded":
                continue
            index_state = _INDEX_STATE_FOR[decision["status"]]
            if index_state is None:
                continue  # blocked：保持 superseded，报告 supersede 建议
            entry["state"] = index_state
            entry["superseded_by"] = None
            changed = True
        if changed:
            status["updated_at"] = utc_now_iso()
            (root / TASK_STATUS_NAME).write_text(
                json.dumps(status, ensure_ascii=False, indent=2),
                encoding="utf-8")

    schedule = schedule_resume(decisions)

    _record_timing(root, "task_resume")
    stage_reports = []
    failed_runs: set = set()
    total = len(schedule)
    for idx, stage in enumerate(schedule, 1):
        if stage == "source_prepare":
            # 任务级阶段：item 域是缓存键（cache_build_worker 按键寻址），
            # 全部键一次构建（命中零 officecli）；失败 → fail-closed 停
            items = list(ctx["unique_keys"])
        else:
            items = [rid for rid in schedule[stage] if rid not in failed_runs]
        if not items:
            continue
        worker = worker_of(stage) if worker_of is not None \
            else task_prepare._stage_worker(ctx, stage)
        progress(task_scheduler.stage_start_line(idx, total, stage, len(items)))
        res = task_scheduler.run_stage(stage, items, worker)
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
            task_prepare.finalize_cache_facts(ctx)  # 快照事实为空时补全
        failed_runs |= {f["run"] for f in res["failed"] if f.get("run")}

    checkpoints = {
        rid: {"status": d["status"], "needs": list(d["needs"]),
              "reason": d["reason"], "evidence": d["evidence"]}
        for rid, d in decisions.items()
    }
    return {
        "checkpoints": checkpoints,
        "stages": stage_reports,
        "failures": task_scheduler.aggregate_failures(stage_reports),
        "blocked": blocked,
        "skipped": {
            "promoted": sorted(rid for rid, d in decisions.items()
                               if d["status"] == "promoted"),
            "superseded": sorted(rid for rid, d in decisions.items()
                                 if d["status"] == "superseded"),
        },
        "gated_pending": sorted(rid for rid, d in decisions.items()
                                if d["status"] == "gated"),
        "confirmed": sorted(rid for rid, d in decisions.items()
                            if d["status"] == "confirmed"),
        "gate_presented": any(
            sr["stage"] == "gate" and len(sr["ok"]) > 0
            for sr in stage_reports),
    }


def resume_task(root, task: dict, manifest: dict, status: dict, *,
                rebuild: bool = False, progress=print) -> dict:
    """生产入口：任务级 prelude（staging/outline/漂移校验/缓存键 + 放宽的
    RUN_STATE_GUARD —— resume 允许任意状态）→ 断点判定 → 剩余阶段恢复。"""
    ctx = task_prepare.prepare_task_level(
        root, task, manifest, status,
        allowed_states=RUN_STATES)
    return resume_with_ctx(ctx, status, rebuild=rebuild, progress=progress)


# ── Supersede（失败二分：输入事实改变 → run 级 supersede） ────────────────

# 影响 run 业务身份的声明字段（template_family 仅记录，D6 不实现，排除）
_DECL_FIELDS = (("source", "file"), ("source", "sheets"),
                ("target", "template"), ("target", "sheet"),
                ("target", "output"))


def _decl_value(run: dict, section: str, key: str):
    return (run.get(section) or {}).get(key)


def validate_supersede(task: dict, manifest: dict, status: dict,
                       mappings: list[tuple[str, str]]) -> list[dict]:
    """supersede mapping 的静态校验（纯函数；返回缺陷清单，空 = 通过）。

    - 每个 pair：old ≠ new；old ∈ task_status；old 未 superseded；new 已在
      task.yaml 声明（新版本必须先入声明）；
    - 整体：无重复（old 侧 / new 侧）、无链/环（同一 id 不能既作 old 又作
      new）；已被 supersede 的 run 不能作 new 目标；
    - 结构契约：task.yaml 删除旧 run → 拒绝（superseded 状态在 status 中
      延续，旧声明必须保留）；
    - 失败二分守卫：声明被改动（source/target 业务字段）且未 mapping 的
      run → 拒绝（防“改了声明但不标记废弃”的静默错误，旧 run 只能以
      superseded 身份被替换）。"""
    defects: list[dict] = []
    task_ids = {r["id"] for r in task["runs"]}
    status_runs = status.get("runs") if isinstance(status, dict) else {}

    def _d(code, message, corrective_action, at):
        defects.append({"code": code, "message": message,
                        "corrective_action": corrective_action, "at": at})

    # 结构契约：旧 run 必须保留在 task.yaml（superseded 状态在 status 延续）
    for rid in status_runs:
        if rid not in task_ids:
            _d("RUN_REMOVED_FROM_TASK_YAML",
               f"run {rid} 从 task.yaml 中被删除，但仍是 status 的活跃条目",
               "把被 supersede 的 run 保留在 task.yaml（保留其声明），"
               "只新增新版本 run（如 <id>_v2）",
               at=f"task.yaml/runs/{rid}")

    old_ids = [o for o, _ in mappings]
    new_ids = [n for _, n in mappings]
    if len(old_ids) != len(set(old_ids)):
        _d("MAPPING_DUPLICATE_OLD", "mapping 的 old 侧有重复 id",
           "每个旧 run 只 mapping 一次", at="--map")
    if len(new_ids) != len(set(new_ids)):
        _d("MAPPING_DUPLICATE_NEW", "mapping 的 new 侧有重复 id",
           "每个新版本 run 只作一个旧 run 的继承者", at="--map")
    if set(old_ids) & set(new_ids):
        _d("MAPPING_CHAIN", "mapping 形成链/环（同一 id 既作 old 又作 new）",
           "一次 supersede 只做一层：新版本必须是全新 id", at="--map")

    # 每 run 声明 delta vs 冻结 manifest（残缺 manifest 视为无 delta 可判）
    old_decls = manifest.get("runs") if isinstance(manifest, dict) else None
    for run in task["runs"]:
        rid = run["id"]
        if rid in old_ids:
            continue  # 已 mapping：声明改动由新 run 承载
        if not isinstance(old_decls, dict) or rid not in old_decls:
            continue  # 新增 run：无旧声明可比
        old = old_decls[rid]
        changed = [f"{s}.{k}" for s, k in _DECL_FIELDS
                   if _decl_value(run, s, k) != _decl_value(old, s, k)]
        if changed:
            _d("UNMAPPED_RUN_CHANGED",
               f"run {rid} 的声明已改动（{', '.join(changed)}）但未被 mapping"
               " — 输入事实改变，禁止在旧 run 上继续修补",
               f"为 {rid} 声明新版本 run（如 {rid}_v2）并 --supersede "
               f"--map {rid}={rid}_v2",
               at=f"task.yaml/runs/{rid}")

    for old, new in mappings:
        if old == new:
            _d("MAPPING_SELF_LINK", f"mapping 的 old 与 new 相同: {old}",
               "新版本必须是独立 run id（如 {old}_v2）", at="--map")
        entry = status_runs.get(old)
        if entry is None:
            _d("RUN_NOT_FOUND", f"old run {old} 不在 task_status.json 中",
               "mapping 的旧 run 必须是 status 的活跃条目", at=f"--map {old}={new}")
        elif isinstance(entry, dict) and entry.get("state") == "superseded":
            _d("RUN_ALREADY_SUPERSEDED", f"run {old} 已是 superseded",
               "不要重复 supersede；核对 task_status.json 的 superseded_by",
               at=f"--map {old}={new}")
        if new not in task_ids:
            _d("RUN_NOT_FOUND", f"new run {new} 未在 task.yaml 中声明",
               "先在 task.yaml 声明新版本 run（如 {old}_v2），再执行 supersede",
               at=f"--map {old}={new}")
        new_entry = status_runs.get(new)
        if isinstance(new_entry, dict) and new_entry.get("state") == "superseded":
            _d("TARGET_ALREADY_SUPERSEDED", f"new run {new} 已是 superseded",
               "新版本必须是活跃 run，不能指向已废弃的 run", at=f"--map {old}={new}")
    return defects


def supersede_status(status: dict, task: dict, yaml_sha256: str,
                     mappings: list[tuple[str, str]],
                     updated_at: str | None = None) -> dict:
    """supersede 的状态演进（纯函数）：旧 run 标 superseded + superseded_by
    链接新版本；task.yaml 新增 run 初始化为 planned；其余 run 状态原样保留
    （绝不重置未涉 run —— 如 r32-cooling 已 gated，supersede 其他 run 后仍
    保持 gated）；task 绑定指纹刷新到新 task.yaml。"""

    new_status = copy.deepcopy(status)
    new_status["task"] = {"id": task["task"]["id"], "yaml": "task.yaml",
                          "yaml_sha256": yaml_sha256}
    for run in task["runs"]:
        rid = run["id"]
        if rid not in new_status["runs"]:
            new_status["runs"][rid] = {"state": "planned",
                                       "superseded_by": None}
    for old, new in mappings:
        entry = new_status["runs"][old]
        entry["state"] = "superseded"
        entry["superseded_by"] = new
    new_status["updated_at"] = updated_at or utc_now_iso()
    return new_status