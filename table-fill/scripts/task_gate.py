#!/usr/bin/env python3
"""
scripts/task_gate.py — 聚合 Gate：gate_summary 生成 + 逐 run 确认展开
（issue 05，spec S6 阶段 5 / Implementation Decision 31）。

埃及案例的实际做法是「13 个 execution_gate --set → 一次性呈现 → 用户一次确认
→ 13 个 --confirm」，但那是 agent 手工编排、未制度化；且聚合不得削弱逐 run 授权
粒度。本模块把该流程制度化为两段，全部复用现有脚本（execution_gate.py /
promote_output.py 零改动，fail-closed 语义不变）：

1. 聚合呈现（gate_task.py --set 承载）：
   - 呈现集合 = 产物证据已就绪的 run（classify_run_facts 判定 ∈
     drafted/gated —— 有有效 draft+receipt，或已有有效 pending 呈现）；
   - 每 run 携带：id、输出名、行数（结构 readback 最终行数）、关键校验结果
     （readback/来源覆盖/issue delta/validate）、spec/plan/draft SHA-256
     哈希三元组、MOD 裁决摘要、timing 双栏（机器 + agent）—— 呈现形态对齐
     SKILL.md 现有 Execution Gate 内容要求，全程一次人机交互。

2. 逐 run 确认展开（gate_task.py --confirm 承载）：
   - 确认阶段：plan.confirm 逐 run 调用 execution_gate.py --confirm（串行，
     复用现有脚本的 fail-closed 语义）—— 任一 run 确认失败即整体停止并
     报告该 run，不静默跳过、不继续确认、不进入 promote；
   - promote 阶段：全部确认后逐 run 调用 promote_output.py --final（现有
     脚本，HASH_DRIFT 三方核对拒绝逻辑不变）；并发默认 2（spec S6 阶段 6 /
     task_scheduler.STAGE_CONCURRENCY["promote"]）；
   - 每个 run 的 .gate3_confirmed 绑定自己的哈希三元组（execution_gate
     --confirm 按 pending 呈现的三元组逐一落账）；
   - 单一写者：task_status.json 在 promote 阶段边界统一写盘一次
     （apply_stage_status：gated → promoted）。

摘要分区（gate_summary.json）：
  runs       呈现集合：产物证据 ∈ drafted/gated（等待本次人工确认；gated
             也纳入 —— prepare_task --run/resume 的 gate 阶段已逐 run
             --set 呈现，--set 只聚合不重写，幂等）
  confirmed  已确认待 promote：授权已落账（.gate3_confirmed 绑定呈现三元
             组）但尚未交付的未决项（promote 失败/中断后等待重试）
  gaps       未完成 run（其余非终态）：不入呈现，列全带 state + reason
  excluded   真正终态（promoted/superseded）：授权已消费或显式废弃

gate_summary 生成与 confirm 展开逻辑全部实现为可 import 的纯函数（无
Office、可单测 —— spec Testing Decision #8：gate 交互本身 set/confirm 逻辑
已有 test_optimization.py 覆盖，本模块只承担聚合与展开编排）；交互部分由
gate_task.py CLI 入口承接。
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # 套件导入纪律

from _officecli import record_timing as _record_timing  # noqa: E402

import task_resume  # noqa: E402
import task_scheduler  # noqa: E402
from task_prepare import _run_child  # noqa: E402 —— 现有脚本 subprocess 入口复用
from task_resume import _try_json  # noqa: E402 —— 与 resume 共享同一 JSON 读取语义
from task_schema import TASK_STATUS_NAME, utc_now_iso  # noqa: E402

try:  # tolerance mirror in task_schema（selected_mod 解析用，缺失不致命）
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

GATE_SUMMARY_NAME = "gate_summary.json"
GATE_SUMMARY_SCHEMA_VERSION = 1

# 最终交付目录（task 级输出的唯一归属；命名来自 task.yaml 的 target.output）
FINAL_OUTPUT_DIR = "outputs"

# 呈现集合 = 产物证据已就绪（classify_run_facts 判定；spec S7 状态机里
# drafted 是 execute 完成后、gated 是 pending 呈现有效 —— 两者都等待人
# 工确认，一起进本次呈现）。gated 也纳入：prepare_task --run / resume 的
# gate 阶段已逐 run --set 呈现，--set 只聚合，不再重写呈现（幂等）。
PRESENTABLE_STATES = ("drafted", "gated")

# 真正终态（交付完成 / 显式废弃）：不入呈现、不入缺口，excluded 单独记录。
# confirmed 不在其中 —— 它授权已落账但尚未交付（promote 失败/中断后等待
# 重试），是「已确认待 promote」的未决项，在 summary["confirmed"] 呈现。
TERMINAL_STATES = ("promoted", "superseded")


def final_output_path(root: Path, run_decl: dict) -> Path:
    """最终交付路径：<task_root>/outputs/<target.output>（纯函数）。

    输出命名来自 task.yaml（target.output），交付目录是 task 级输出的唯一
    归属；promote_output.py 的 `--final` 即此路径（原子替换语义不变）。"""
    return Path(root) / FINAL_OUTPUT_DIR / run_decl["target"]["output"]


# ── 单 run 呈现摘要（纯函数：读产物文件 + hash，无 Office） ───────────────

def timing_totals(run_dir) -> dict:
    """run_timing.json → timing 双栏（机器 + agent，毫秒合计）。

    SKILL.md Observability 契约：机器相位由脚本自动追加（kind: machine），
    思考/等待由 agent 自报（kind: agent）；Gate 引用该文件时按两栏呈现。
    缺失/损坏/非数值条目 → 零值（计时缺失不阻塞 Gate，timing 不是执行约束）。
    """
    entries = _try_json(Path(run_dir) / "run_timing.json")
    machine_ms = agent_ms = 0
    if isinstance(entries, list):
        for e in entries:
            if not isinstance(e, dict):
                continue
            ms = e.get("duration_ms")
            if not isinstance(ms, (int, float)):
                continue
            if e.get("kind") == "agent":
                agent_ms += ms
            else:
                machine_ms += ms
    return {"machine_ms": int(machine_ms), "agent_ms": int(agent_ms)}


def mod_summary(run_dir) -> dict | None:
    """mod_resolution.json（MOD 裁决，SKILL.md Output Files）的紧凑摘要。

    只带裁决所需的表面（status/candidates 命中等/why），不带候选的完整
    信号清单 —— 裁决记录可指向原文件；缺失/损坏 → None（Gate 呈现如实
    说明该 run 无 MOD 裁决记录）。
    """
    data = _try_json(Path(run_dir) / "mod_resolution.json")
    if not isinstance(data, dict):
        return None
    cands = data.get("candidates")
    return {
        "file": "mod_resolution.json",
        "status": data.get("status"),
        "candidates": [
            {"name": c.get("name"), "display_name": c.get("display_name"),
             "hits": c.get("hits"), "pending": c.get("pending"),
             "missed": c.get("missed")}
            for c in cands if isinstance(c, dict)
        ] if isinstance(cands, list) else [],
        "why": data.get("why"),
    }


def spec_mod(run_dir) -> str | None:
    """fill_spec.yaml 的 selected_mod（关键 mapping 与业务决策证据）。

    MOD 裁决后 agent 把用户的明确选择写进 fill_spec（MOD NONE / 具名 MOD）；
    解析失败或缺失 → None（呈现实话说明，不猜测）。
    """
    path = Path(run_dir) / "fill_spec.yaml"
    if not path.is_file():
        return None
    if yaml is None:
        return None
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return None
    if isinstance(data, dict) and isinstance(data.get("selected_mod"), str):
        return data["selected_mod"]
    return None


def receipt_validation(receipt) -> dict:
    """draft_receipt.json → 关键校验结果摘要（纯变换）。

    字段对齐 SKILL.md §6 Execution Gate 内容要求：Draft 验证结果
    （readback / source_coverage / issue_delta / structural / render_qa /
    validate）。receipt 缺失/非 mapping → 空 dict（呈现降级但不阻塞）。
    """
    if not isinstance(receipt, dict):
        return {}
    out: dict = {}
    rb = receipt.get("readback")
    if isinstance(rb, dict):
        out["readback"] = {"total": rb.get("total"), "passed": rb.get("passed")}
    sc = receipt.get("source_coverage")
    if isinstance(sc, dict):
        out["source_coverage"] = {
            "result": sc.get("result"),
            "entries": sc.get("entries") if isinstance(sc.get("entries"), list)
            else [],
        }
    idl = receipt.get("issue_delta")
    if isinstance(idl, dict):
        out["issue_delta"] = {
            "supported": idl.get("supported"),
            "new_issues": idl.get("new_issues"),
        }
    st = receipt.get("structural")
    if isinstance(st, dict):
        out["structural"] = {
            "pass": st.get("pass"),
            "final_row_count": st.get("actual_final_row_count"),
        }
    rq = receipt.get("render_qa")
    if isinstance(rq, dict):
        out["render_qa"] = {"status": rq.get("status")}
    out["validate"] = receipt.get("validate")
    return out


def summarize_run(rid: str, run_decl: dict, run_dir: Path, facts: dict) -> dict:
    """单 run 的 Gate 呈现摘要（纯函数；facts = gather_run_facts 产物）。

    字段：id / 输出名 / 行数（结构 readback 的最终行数）/ 校验结果 /
    spec-plan-draft 哈希三元组 / MOD 裁决 / selected_mod / timing 双栏。
    """
    validation = receipt_validation(facts["receipt"])
    structural = validation.get("structural") or {}
    return {
        "id": rid,
        "output": run_decl["target"]["output"],
        "rows": structural.get("final_row_count"),
        "hashes": facts["trio_current"] or {},
        "validation": validation,
        "mod": mod_summary(run_dir),
        "spec": {"selected_mod": spec_mod(run_dir)},
        "timing": timing_totals(run_dir),
    }


# ── 聚合呈现摘要（纯逻辑：产物证据 + hash，无 Office） ────────────────────

def collect_gate_summary(task: dict, status: dict, root: Path) -> dict:
    """聚合 Gate 呈现摘要 → gate_summary.json 的内容（纯函数 seam）。

    - 呈现集合 = evidence ∈ PRESENTABLE_STATES 的 run（按 task.yaml 声明
      顺序），逐 run 摘要见 summarize_run；
    - 已确认待 promote（evidence confirmed —— 授权已落账但尚未交付，
      promote 失败/中断后等待重试）不入呈现，summary["confirmed"] 单独
      呈现为未决项（不是缺口：授权记录 .gate3_confirmed 已绑定其呈现内容）；
    - 未完成 run（非 drafted/gated/confirmed 且非终态）不入呈现，gaps
      列全（带 state + reason，人读诊断）；
    - 真正终态 run（promoted / superseded）不入呈现也不入缺口，excluded
      单独记录（授权已消费或显式废弃，不是「未完成」）。
    """
    root = Path(root)
    runs_dir = root / "runs"
    summary = {
        "schema_version": GATE_SUMMARY_SCHEMA_VERSION,
        "task": {"id": task["task"]["id"]},
        "generated_at": utc_now_iso(),
        "runs": {},
        "confirmed": [],
        "gaps": [],
        "excluded": [],
    }
    for run in task["runs"]:
        rid = run["id"]
        run_dir = runs_dir / rid
        facts = task_resume.gather_run_facts(run_dir)
        decision = task_resume.classify_run_facts(
            facts, status_state=status["runs"][rid]["state"])
        if decision["status"] in PRESENTABLE_STATES:
            summary["runs"][rid] = summarize_run(rid, run, run_dir, facts)
        elif decision["status"] == "confirmed":
            summary["confirmed"].append({
                "run": rid, "state": "confirmed", "reason": decision["reason"],
            })
        elif decision["status"] in TERMINAL_STATES:
            summary["excluded"].append({
                "run": rid, "state": decision["status"],
                "reason": decision["reason"],
            })
        else:
            summary["gaps"].append({
                "run": rid, "state": decision["status"],
                "reason": decision["reason"],
            })
    return summary


# ── 确认展开计划（纯函数：呈现集合 vs 当前证据判定） ──────────────────────

def confirm_plan(summary: dict, decisions: dict) -> dict:
    """确认展开计划（纯函数）：呈现集合与当前产物证据的对照 + 授权顺序。

    守卫（fail-closed：聚合不得削弱逐 run 授权粒度，呈现后才可确认）：
    - stale：呈现过但当前已不可确认（判定回退到 planned/prepared/compiled/
      execute_retry/drafted/blocked —— 呈现内容消失或回退）→ 阻塞，必须
      重新 --set 呈现；
    - not_presented：当前**待确认**（gated）但不在呈现集合（新出现或未被
      呈现）→ 阻塞，未呈现的内容不能被授权，必须重新 --set；
    - confirmed（已确认待 promote）不做呈现集合守卫：.gate3_confirmed
      记录本身即呈现 + 授权的证据链（presented_at + 绑定三元组，且三元组
      与当前产物相等才判 confirmed）—— 授权已落账，重试幂等跳过确认仍进
      promote；
    - skipped_terminal：判定 ∈ promoted/superseded（授权已消费或显式废弃）
      → 跳过且不阻塞。
    展开顺序（deterministic）：
    - confirm = 判定 gated（逐个 execution_gate --confirm）；
    - already_confirmed = 判定 confirmed（.gate3_confirmed 有效 ——
      重试幂等，跳过确认仍进 promote）；
    - promote = confirm + already_confirmed（按 decisions 的声明顺序 ——
      调用方按 task.yaml 顺序喂入）。
    """
    presented = set(summary.get("runs", {}) if isinstance(summary, dict) else {})
    plan = {
        "confirm": [], "already_confirmed": [], "promote": [],
        "skipped_terminal": [], "stale": [], "not_presented": [],
    }
    for rid, d in decisions.items():
        st = d.get("status")
        if st == "gated":
            if rid not in presented:
                plan["not_presented"].append({
                    "run": rid, "state": st,
                    "reason": "当前可确认但不在 gate_summary 呈现集合 — "
                              "未呈现的内容不能被授权，重新 --set 呈现",
                })
            else:
                plan["confirm"].append(rid)
                plan["promote"].append(rid)
        elif st == "confirmed":
            # 授权已落账（.gate3_confirmed 绑定呈现三元组）— 不设呈现守卫
            plan["already_confirmed"].append(rid)
            plan["promote"].append(rid)
        elif st in TERMINAL_STATES:
            plan["skipped_terminal"].append({
                "run": rid, "state": st, "reason": d.get("reason"),
            })
        elif rid in presented:
            plan["stale"].append({
                "run": rid, "state": st, "reason": d.get("reason"),
            })
    return plan


# ── 确认展开编排（串行确认 fail-fast + promote 并发 2 + 单一写者） ────────

def default_confirm_worker(ctx: dict, rid: str) -> dict:
    """确认 worker：execution_gate.py --confirm（复用 fail-closed 语义）。

    失败 → StageError（由调度器/展开循环归并）；成功返回 ok 结果。已确认
    run 不进本 worker（plan.already_confirmed 幂等跳过）。"""
    run_dir = ctx["runs_dir"] / rid
    _run_child("execution_gate.py", ["--confirm", "--workdir", str(run_dir)],
               "GATE_CONFIRM_FAILED",
               "按 execution_gate 的 corrective_action 处置：哈希漂移 → "
               "重新 --set 呈现并再次获得确认")
    return {"run": rid, "status": "ok",
            "artifacts": {"gate": ".gate3_confirmed"}}


def default_promote_worker(ctx: dict, rid: str) -> dict:
    """promote worker：promote_output.py --final（HASH_DRIFT 拒绝逻辑不变）。"""
    run_dir = ctx["runs_dir"] / rid
    final = ctx["final_paths"][rid]
    _run_child("promote_output.py",
               ["--workdir", str(run_dir), "--final", str(final)],
               "PROMOTE_FAILED",
               "读取失败明细修复后重试该 run 的确认展开（已确认/已交付 run "
               "幂等跳过）；输入事实改变 → supersede")
    return {"run": rid, "status": "ok",
            "artifacts": {"final_receipt": "final_receipt.json",
                          "final": str(final)}}


def run_confirm_expansion(root, task, status, plan, *, confirmer=None,
                          promote_worker_of=None, progress=print) -> dict:
    """确认展开编排（可 import、可单测；worker 可注入取代 subprocess）。

    - 前置守卫：plan.stale / plan.not_presented → 不展开，report["blocked"]
      带缺陷清单（fail-closed，调用方负责 fail）；
    - 确认阶段：plan.confirm 逐 run 调用 confirmer（默认 execution_gate.py
      --confirm）—— 任一 run 确认失败即整体停止并报告该 run（不静默跳过、
      不继续确认、不进入 promote）；已确认记录各自绑定自己的哈希三元组
      （execution_gate --confirm 逐一落账，聚合不削弱逐 run 授权粒度）；
    - promote 阶段：plan.promote 全部已确认 run，TaskLayer 阶段并发常量 2
      （task_scheduler.run_stage("promote")）；失败 run 不推进状态、同阶段
      其他 run 不受影响（失败清单汇总，重试幂等）；
    - 单一写者：task_status.json 只在 promote 阶段边界写盘一次
      （apply_stage_status：gated → promoted；确认阶段零写盘）。
    """
    root = Path(root)
    ctx = {"root": root, "runs_dir": root / "runs",
           "final_paths": {run["id"]: final_output_path(root, run)
                           for run in task["runs"]}}
    _record_timing(root, "task_gate")

    def _defect(stage, rid, code, message, corrective_action):
        return {"stage": stage, "run": rid, "code": code, "message": message,
                "corrective_action": corrective_action}

    blocked = [
        _defect("gate_confirm", e["run"], "GATE_SUMMARY_STALE",
                f"{e['run']} 在呈现后已不可确认（{e['state']}）: {e['reason']}",
                "重新 gate_task.py --set 聚合呈现后再次确认（改动即重新授权）")
        for e in plan["stale"]
    ]
    blocked += [
        _defect("gate_confirm", e["run"], "GATE_NOT_PRESENTED",
                f"{e['run']} 可确认但未在呈现集合: {e['reason']}",
                "重新 gate_task.py --set 聚合呈现（未呈现的内容不能被授权）")
        for e in plan["not_presented"]
    ]
    if blocked:
        return {"plan": plan, "blocked": blocked,
                "confirmed": [], "already_confirmed": [],
                "confirm_failures": [], "promoted": [],
                "promote_failures": [],
                "skipped_terminal": [e["run"] for e in plan["skipped_terminal"]],
                "stages": []}

    # ── 确认阶段（串行、fail-fast；worker 只回报，不写 status） ──
    do_confirm = confirmer if confirmer is not None else (
        lambda rid: default_confirm_worker(ctx, rid))
    confirmed: list = []
    confirm_failures: list = []
    total = len(plan["confirm"])
    for idx, rid in enumerate(plan["confirm"], 1):
        progress(f"Gate 确认展开 {idx}/{total}: {rid}")
        try:
            res = do_confirm(rid)
        except task_scheduler.StageError as e:
            confirm_failures.append(_defect(
                "gate_confirm", rid, e.code, e.message, e.corrective_action))
            break  # 任一 run 确认失败 → 整体停止（不静默跳过、不继续确认）
        if not isinstance(res, dict) or res.get("status") != "ok":
            confirm_failures.append(_defect(
                "gate_confirm", rid, "GATE_CONFIRM_FAILED",
                "确认 worker 返回无效结果（需要 status=ok）",
                "修复后重试该 run 的确认展开"))
            break
        confirmed.append(rid)
    if confirm_failures:
        return {"plan": plan, "blocked": [],
                "confirmed": confirmed, "already_confirmed": [],
                "confirm_failures": confirm_failures, "promoted": [],
                "promote_failures": [],
                "skipped_terminal": [e["run"] for e in plan["skipped_terminal"]],
                "stages": []}

    # ── promote 阶段（并发默认 2 = STAGE_CONCURRENCY["promote"]；纯文件） ──
    promote_items = list(plan["promote"])
    stage_report = None
    if promote_items:
        worker = (promote_worker_of(ctx) if promote_worker_of is not None
                  else (lambda rid: default_promote_worker(ctx, rid)))
        progress(task_scheduler.stage_start_line(1, 1, "promote",
                                                 len(promote_items)))
        stage_report = task_scheduler.run_stage("promote", promote_items, worker)
        status = task_scheduler.apply_stage_status(
            status, "promote", stage_report["results"])
        (root / TASK_STATUS_NAME).write_text(
            json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
        progress(task_scheduler.stage_end_line(1, 1, "promote", stage_report))
    promoted = ([r["run"] for r in stage_report["results"]
                 if r["status"] == "ok"] if stage_report else [])
    promote_failures = (task_scheduler.aggregate_failures([stage_report])
                        if stage_report else [])
    return {"plan": plan, "blocked": [],
            "confirmed": confirmed,
            "already_confirmed": list(plan["already_confirmed"]),
            "confirm_failures": confirm_failures,
            "promoted": promoted,
            "promote_failures": promote_failures,
            "skipped_terminal": [e["run"] for e in plan["skipped_terminal"]],
            "stages": [stage_report] if stage_report is not None else []}


# ── gate_summary 状态刷新（--confirm 后落账；纯函数） ─────────────────────

def refresh_gate_summary(summary: dict, report: dict) -> dict:
    """--confirm 后的 gate_summary 状态演进（纯函数）。

    gate.state ∈ presented（原样）/ confirm_failed / promote_failed /
    promoted / noop；per-run 呈现快照（runs）保持不动 —— 它是「被呈现的
    内容」的封存记录，演进只写在 gate 块。blocked（呈现集合与证据不一致）
    不落账：调用方在 refresh 前 fail（GATE_PRESENTATION_MISMATCH），
    refresh 只演进展开后的状态。
    """
    new = copy.deepcopy(summary)
    if report["confirm_failures"]:
        state = "confirm_failed"
    elif report["promote_failures"]:
        state = "promote_failed"
    elif not report["confirmed"] and not report["already_confirmed"] \
            and not report["promoted"]:
        state = "noop"
    else:
        state = "promoted"
    new["gate"] = {
        "state": state,
        "confirmed": report["confirmed"] + report["already_confirmed"],
        "promoted": report["promoted"],
        "failures": report["confirm_failures"] + report["promote_failures"],
    }
    return new