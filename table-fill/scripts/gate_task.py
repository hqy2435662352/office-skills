#!/usr/bin/env python3
"""
scripts/gate_task.py — 聚合 Gate：一次人机交互 + 逐 run 确认展开（issue 05）。

埃及案例实际做了「13 个 execution_gate --set → 一次性呈现 → 用户一次确认 →
13 个 --confirm」，但那是 agent 手工编排；本脚本把它制度化为两段，复用现有
脚本（execution_gate.py / promote_output.py 零改动，fail-closed 语义不变）：

  --set      聚合呈现：收集全部 Draft 就绪 run（产物证据判定 ∈ drafted/gated）
             的验证摘要生成 <task_root>/gate_summary.json —— 每 run 带
             id / 输出名 / 行数 / 关键校验结果（readback·来源覆盖·issue
             delta·validate）/ spec·plan·draft SHA-256 / MOD 裁决 / timing
             双栏；未完成 run 不入呈现，缺口（gaps）与终态（excluded）
             逐条列全（呈现形态对齐 SKILL.md Execution Gate 内容要求）。
             Draft 就绪但尚无呈现的 run 先逐个 execution_gate --set（每个
             run 的 pending 绑定自己的哈希三元组）。呈现后停（fail-closed）：
             不自动确认、不自动 promote。
  --confirm  确认展开：按 gate_summary 呈现集合逐 run execution_gate
             --confirm（串行；任一 run 确认失败 → 整体停止并报告该 run，
             不静默跳过、不继续确认、不进入 promote）→ 全部确认后逐 run
             promote_output.py --final <task_root>/outputs/<target.output>
             （并发默认 2；HASH_DRIFT 三方核对拒绝逻辑不变）→ status
             gated → promoted（promote 阶段边界单一写者）。

呈现守卫（聚合不得削弱逐 run 授权粒度，fail-closed）：
  - 呈现过的 run 在确认时已不可确认（产物回退/消失）→ GATE_SUMMARY_STALE
    阻塞，必须重新 --set；
  - 确认时可确认但不在呈现集合（新出现）→ GATE_NOT_PRESENTED 阻塞；
  - 已 confirmed 的 run 幂等跳过确认（.gate3_confirmed 绑定自己的哈希三
    元组），仍进 promote —— 重试不重复授权。

Exit codes（与套件一致）: 0=pass, 1=fatal (env/file), 3=retryable
(validate defects / gate / promote failures)。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # 套件导入纪律：先 insert 再打 E402

import _officecli  # noqa: E402
import task_gate  # noqa: E402
import task_resume  # noqa: E402
import task_scheduler  # noqa: E402
import task_schema  # noqa: E402
from prepare_task import (  # noqa: E402 —— 复用既有前置契约（入口零重复）
    _ascii_path_check, _fail_json, _load_derived, _progress,
)

ensure_utf8_stdio = _officecli.ensure_utf8_stdio
fail = _officecli.fail


def _task_block(task: dict) -> dict:
    return {"id": task["task"]["id"], "runs": [r["id"] for r in task["runs"]]}


def run_gate_set(root: Path) -> None:
    """聚合呈现（--set）：draft 就绪但未呈现的 run 先 execution_gate --set
    （每个 run 的 pending 绑定自己的哈希三元组），再生成 gate_summary.json。
    呈现后停（fail-closed）：一次人机交互，不自动确认、不自动 promote。"""
    task, _yaml_sha256, _manifest, status = _load_derived(
        root, require_existing=True)
    runs_dir = root / "runs"

    # 1. Draft 就绪（evidence drafted）但尚无呈现的 run：逐个 --set。
    #    已呈现（evidence gated，pending 有效）的 run 原样保留，不重写呈现。
    to_present = []
    for run in task["runs"]:
        rid = run["id"]
        decision = task_resume.classify_run_facts(
            task_resume.gather_run_facts(runs_dir / rid),
            status_state=status["runs"][rid]["state"])
        if decision["status"] == "drafted":
            to_present.append(rid)
    for i, rid in enumerate(to_present, 1):
        _progress(f"Gate 呈现 {i}/{len(to_present)}: {rid}")
        try:
            task_gate._run_child(
                "execution_gate.py", ["--set", "--workdir", str(runs_dir / rid)],
                "GATE_SET_FAILED",
                "检查该 run 的 gate 前置产物（spec/plan/draft）后重试")
        except task_scheduler.StageError as e:
            # 呈现失败 = 可重试缺陷（与 --confirm 的 StageError 归一同一契约：
            # 结构化 code/message/corrective_action + exit 3，不裸 traceback）
            _fail_json(
                "GATE_SET_FAILED",
                f"run {rid} 的 Gate 呈现失败: {e.message}",
                e.corrective_action,
                defects=[{"stage": "gate_set", "run": rid, "code": e.code,
                          "message": e.message,
                          "corrective_action": e.corrective_action}])

    # 2. 索引与证据对齐（status 是生命周期索引，不是真值源）：呈现集合的
    #    run 统一推进到 gated（证据 = 有效 pending 呈现，含刚 --set 与早已
    #    呈现的 run）。与 resume --rebuild 的 _INDEX_STATE_FOR 同一原则：
    #    索引必须先与判定对齐，promote 边界的 apply_stage_status（前驱
    #    gated）才能推进。单一写者：阶段边界写盘一次。
    summary = task_gate.collect_gate_summary(task, status, root)
    presented = list(summary["runs"])
    changed = False
    for rid in presented:
        entry = status["runs"].get(rid)
        if isinstance(entry, dict) and entry.get("state") != "gated":
            entry["state"] = "gated"
            changed = True
    if changed:
        status["updated_at"] = task_schema.utc_now_iso()
        (root / task_schema.TASK_STATUS_NAME).write_text(
            json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")

    # 3. 聚合呈现摘要（未完成 run → gaps；终态 run → excluded）
    summary["gate"] = {
        "state": "presented",
        "note": "一次人机交互：向用户呈现 Gate 内容并停止（不发 promote）；"
                "收到明确确认后运行 gate_task.py --confirm",
    }
    (root / task_gate.GATE_SUMMARY_NAME).write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    if not summary["runs"]:
        _fail_json("GATE_NOTHING_TO_PRESENT",
                   "没有 Draft 就绪的 run 可呈现（未完成 / 已确认待 "
                   "promote / 已终态）",
                   "处理缺口后重试 --set；已确认待 promote 的 run 直接"
                   "运行 --confirm",
                   defects=summary["gaps"] + summary["excluded"])

    print(json.dumps({
        "status": "PASS", "code": "GATE_SUMMARY_WRITTEN",
        "task": _task_block(task),
        "gate_summary": str(root / task_gate.GATE_SUMMARY_NAME),
        "runs": list(summary["runs"].keys()),
        "confirmed": summary["confirmed"],
        "gaps": summary["gaps"],
        "excluded": summary["excluded"],
        "gate": summary["gate"],
        "timing": {  # task 级双栏合计（issue 06；完整 kind+phase 分组见
                     # gate_summary.json 的 task_timing 块）
            "active": summary["task_timing"]["active"]["totals_ms"],
            "superseded": summary["task_timing"]["superseded"]["totals_ms"],
        },
    }, ensure_ascii=False, indent=2))


def run_gate_confirm(root: Path) -> None:
    """确认展开（--confirm）：逐 run execution_gate --confirm（串行
    fail-fast）→ 全部确认后逐 run promote_output.py --final（并发 2）。"""
    task, _yaml_sha256, _manifest, status = _load_derived(
        root, require_existing=True)
    summary_path = root / task_gate.GATE_SUMMARY_NAME
    if not summary_path.is_file():
        _fail_json("GATE_SUMMARY_MISSING",
                   "gate_summary.json 不存在 — 无可确认的呈现内容",
                   "先运行 gate_task.py --set 聚合呈现（一次人机交互），"
                   "用户明确确认后再 --confirm", defects=[])
    try:
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
    except (ValueError, OSError, UnicodeDecodeError):
        _fail_json("GATE_SUMMARY_INVALID",
                   "gate_summary.json 损坏或不可读",
                   "重新运行 gate_task.py --set 重新呈现", defects=[])

    # 输出名冲突守卫：同一最终路径两个 run → promote 会互相覆盖（原子替换
    # 语义下后写覆盖先写，必须显式拒绝）
    collisions = []
    seen_paths = {}
    for run in task["runs"]:
        p = str(task_gate.final_output_path(root, run))
        seen_paths.setdefault(p, []).append(run["id"])
    for p, ids in seen_paths.items():
        if len(ids) > 1:
            collisions.append({
                "code": "OUTPUT_COLLISION", "at": ",".join(ids),
                "message": f"{len(ids)} 个 run 解析到同一最终路径: {p}",
                "corrective_action": "修正 task.yaml 的 target.output"
                                     "（输出名唯一）后重新 --set/--confirm",
            })
    if collisions:
        _fail_json("OUTPUT_COLLISION",
                   f"{len(collisions)} 条输出路径冲突",
                   "修正 task.yaml 后重新 --set/--confirm",
                   defects=collisions)

    # 当前产物证据判定（status 只提供 superseded 标记，其余由证据裁决）
    decisions = {}
    for run in task["runs"]:
        rid = run["id"]
        decisions[rid] = task_resume.classify_run_facts(
            task_resume.gather_run_facts(root / "runs" / rid),
            status_state=status["runs"][rid]["state"])

    plan = task_gate.confirm_plan(summary, decisions)
    report = task_gate.run_confirm_expansion(root, task, status, plan,
                                             progress=_progress)

    # 呈现集合与产物证据不一致（stale / 未呈现）：fail-closed，不展开
    if report["blocked"]:
        _fail_json("GATE_PRESENTATION_MISMATCH",
                   f"{len(report['blocked'])} 条呈现集合与当前产物证据不一致",
                   "重新 gate_task.py --set 聚合呈现后再次确认（改动即重新授权）",
                   defects=report["blocked"])

    # gate_summary 状态演进（任何失败也落账；per-run 呈现快照不动）
    summary = task_gate.refresh_gate_summary(summary, report)
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    if report["confirm_failures"]:
        _fail_json("GATE_CONFIRM_FAILED",
                   f"{len(report['confirm_failures'])} 条 run 确认失败 — "
                   "整体停止，未执行任何 promote",
                   "按失败 run 的 corrective_action 处置（哈希漂移 → 重新 "
                   "--set 呈现并再次获得确认）；修复后重跑 --confirm，已确认"
                   " run 幂等跳过",
                   defects=report["confirm_failures"])
    if report["promote_failures"]:
        _fail_json("PROMOTE_FAILED",
                   f"{len(report['promote_failures'])} 条 run promote 失败"
                   "（HASH_DRIFT 拒绝逻辑不变）",
                   "失败二分：输入事实未变 → 修复后重跑 --confirm（已确认/"
                   "已交付 run 幂等跳过）；输入事实改变 → supersede"
                   "（resume_task.py --supersede）",
                   defects=report["promote_failures"])

    gate = summary["gate"]
    if not report["confirmed"] and not report["already_confirmed"] \
            and not report["promoted"]:
        print(json.dumps({
            "status": "PASS", "code": "GATE_NOOP",
            "task": _task_block(task),
            "gate": gate,
            "note": "无待确认/待交付的 run（全部已终态）— 幂等重跑无操作",
        }, ensure_ascii=False, indent=2))
        return

    print(json.dumps({
        "status": "PASS", "code": "GATE_CONFIRMED_AND_PROMOTED",
        "task": _task_block(task),
        "confirmed": report["confirmed"],
        "already_confirmed": report["already_confirmed"],
        "promoted": report["promoted"],
        "skipped_terminal": report["skipped_terminal"],
        "gate": gate,
        "note": "逐 run .gate3_confirmed 与自己的哈希三元组绑定；每个 run 的"
                " final_receipt.json 记录最终交付 hash（== 已确认 draft hash）",
    }, ensure_ascii=False, indent=2))


def main() -> None:
    ensure_utf8_stdio()
    parser = argparse.ArgumentParser(
        description="聚合 Gate：一次人机交互（gate_summary 呈现）+ 逐 run 确认"
                    "展开（confirm + promote，复用现有脚本，fail-closed 不变）")
    parser.add_argument("--task-root", type=Path, required=True,
                        help="任务根目录（ASCII），含 agent 撰写的 task.yaml")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--set", action="store_true",
                      help="聚合呈现：收集全部 Draft 就绪 run 的验证摘要生成 "
                           "gate_summary.json（缺 pending 的 run 先 --set；"
                           "呈现后停，等待用户确认；含 task 级 timing 双栏 — "
                           "active/superseded，issue 06）")
    mode.add_argument("--confirm", action="store_true",
                      help="确认展开：按呈现集合逐 run execution_gate --confirm"
                           "（任一失败即整体停止）→ 全部确认后逐 run promote"
                           "（并发 2；HASH_DRIFT 拒绝逻辑不变）")
    args = parser.parse_args()

    root = args.task_root
    _ascii_path_check(root)
    if not root.is_dir():
        fail("TASK_ROOT_MISSING", f"task root 不存在: {root}",
             "创建任务根目录并放入 task.yaml", exit_code=1)
    if args.set:
        run_gate_set(root)
    elif args.confirm:
        run_gate_confirm(root)
    else:
        fail("NO_MODE", "choose --set or --confirm",
             "Pass one of the modes")
    sys.exit(0)


if __name__ == "__main__":
    main()