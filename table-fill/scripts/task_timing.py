#!/usr/bin/env python3
"""
scripts/task_timing.py — task 级 timing 聚合（issue 06，spec S7 Timing）。

埃及复盘教训：25 个计时目录仅 13 个最终有效，废弃 run 的耗时（重复 flatten、
模板返工）混在总耗时里，无法回答「本次交付成本」与「系统优化收益」两个不同
问题。本模块把聚合做成可 import 的纯函数（读 task_status.json + 各 run
run_timing.json，无 Office，可单测）——先按生命周期状态过滤 run 集，再按
kind+phase 分组（复用 aggregate_run_timings 的分组语义）：

  active      活 run（状态 ∈ prepared..promoted 且非 superseded），合计 =
              本次交付成本；
  superseded  废弃 run 的耗时单独统计（重复 flatten / 模板返工 / 废弃轮次
              的量化证据），合计 = 本可避免的浪费（优化收益证明）；
  excluded    其余状态（planned / 未知）—— 未开始无成本，不污染双栏。

只读契约：聚合不写任何文件 —— 每 run run_timing.json 保留不动（现有机制
append，证据不删，spec S7 Timing）。报告随 Execution Gate 呈现
（gate_summary.json 的 task_timing 块 + gate_task.py --set 输出，machine +
agent 双栏既有要求不变）与 timing_task.py 独立 CLI 输出。
"""

from __future__ import annotations

import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # 套件导入纪律

import task_resume  # noqa: E402
from task_schema import utc_now_iso  # noqa: E402

TASK_TIMING_SCHEMA_VERSION = 1

# 活 run = 状态 ∈ prepared..promoted 且非 superseded（ticket 06；planned
# 未开始无成本，superseded 是保留证据的终止分支，单独成栏；与 task_gate 的
# PRESENTABLE/TERMINAL 同一「状态词汇切片」模式）
ACTIVE_STATES = ("prepared", "compiled", "drafted", "gated", "promoted")


# ── 单 run 计时（纯读取，不写任何文件） ─────────────────────────────────

def load_entries(run_dir) -> list:
    """run_timing.json → 条目列表；缺失/损坏/非 list → []。

    计时缺失不阻塞聚合（timing 不是执行约束）；与 task_resume._try_json
    同一读取语义（复用，不重复实现）。"""
    entries = task_resume._try_json(Path(run_dir) / "run_timing.json")
    return entries if isinstance(entries, list) else []


def _duration(entry) -> int | float | None:
    """条目的有效 duration_ms（非 mapping / 非数值 / bool → None）。

    bool 是 int 子类（JSON true/false 会解析成 bool），与
    aggregate_run_timings 同一严格度显式排除。"""
    if not isinstance(entry, dict):
        return None
    ms = entry.get("duration_ms")
    if not isinstance(ms, (int, float)) or isinstance(ms, bool):
        return None
    return ms


def totals(entries: list) -> dict:
    """entries → 机器 + agent 双栏毫秒合计（SKILL.md Observability 契约：
    kind: machine 与 kind: agent；非 agent 一律计入 machine，与
    aggregate_run_timings 语义一致）。"""
    machine_ms = agent_ms = 0
    for e in entries:
        ms = _duration(e)
        if ms is None:
            continue
        if e.get("kind") == "agent":
            agent_ms += ms
        else:
            machine_ms += ms
    return {"machine_ms": int(machine_ms), "agent_ms": int(agent_ms)}


def phase_rows(entries: list) -> list:
    """kind+phase 分组（复用 aggregate_run_timings 的分组语义）。

    同 (kind, phase) 的条目聚合 count / total_seconds / average_seconds /
    minimum_seconds / maximum_seconds / percent_of_kind（百分比按 kind 内
    占比），行按 (-total_seconds, kind, phase) 稳定排序。缺失 kind/phase
    归入 unknown；非数值条目忽略。"""
    groups: dict = {}
    for e in entries:
        ms = _duration(e)
        if ms is None:
            continue
        key = (str(e.get("kind", "unknown")), str(e.get("phase", "unknown")))
        groups.setdefault(key, []).append(float(ms) / 1000.0)
    totals_by_kind: dict = {}
    for (kind, _phase), values in groups.items():
        totals_by_kind[kind] = totals_by_kind.get(kind, 0.0) + sum(values)
    rows = []
    for (kind, phase), values in groups.items():
        total = sum(values)
        denominator = totals_by_kind.get(kind, 0.0)
        rows.append({
            "kind": kind, "phase": phase, "count": len(values),
            "total_seconds": round(total, 3),
            "average_seconds": round(statistics.fmean(values), 3),
            "minimum_seconds": round(min(values), 3),
            "maximum_seconds": round(max(values), 3),
            "percent_of_kind": round(total / denominator * 100, 1)
            if denominator else 0.0,
        })
    rows.sort(key=lambda r: (-r["total_seconds"], r["kind"], r["phase"]))
    return rows


def _totals_ms(t: dict) -> dict:
    """机器 + agent 双栏 → 双栏 + 合计（本次交付 / 优化收益的总口径）。"""
    return {"machine_ms": t["machine_ms"], "agent_ms": t["agent_ms"],
            "total_ms": t["machine_ms"] + t["agent_ms"]}


def summarize_entries(entries: list) -> dict:
    """条目列表 → 汇总形态：{"entry_count", "totals_ms", "phases"}。"""
    return {
        "entry_count": len(entries),
        "totals_ms": _totals_ms(totals(entries)),
        "phases": phase_rows(entries),
    }


def summarize_run(run_dir) -> dict:
    """单 run 计时汇总（读 run_timing.json + 汇总，只读）。"""
    return summarize_entries(load_entries(run_dir))


# ── task 级聚合（双栏；先按状态过滤 run 集，再 kind+phase 分组） ─────────

def _column(root: Path, rids: list) -> dict:
    """一个状态栏的聚合：跨 run 的 kind+phase 分组 + 合计 + 逐 run 汇总。

    每个 run_timing.json 只解析一次：同一次读取同时供逐 run 汇总与栏内
    跨 run 分组使用。"""
    per_run = []
    entries_acc: list = []
    for rid in rids:
        entries = load_entries(root / "runs" / rid)
        entries_acc.extend(entries)
        per_run.append({"run": rid, **summarize_entries(entries)})
    return {
        "runs": list(rids),
        "count": len(rids),
        "entry_count": len(entries_acc),
        "totals_ms": _totals_ms(totals(entries_acc)),
        "phases": phase_rows(entries_acc),
        "per_run": per_run,
    }


def aggregate_task_timing(task: dict, status: dict, root) -> dict:
    """task 级 timing 聚合报告（纯函数；只读，无 Office）。

    - active：状态 ∈ ACTIVE_STATES（prepared..promoted 且非 superseded）
      的 run，按 task.yaml 声明序；totals_ms 合计 = 本次交付成本；
    - superseded：废弃 run 单独统计；totals_ms 合计 = 本可避免的浪费
      （优化收益量化指标）—— 文件证据保留不动，不删不改写；
    - excluded：其余状态（planned / 未知）逐条列出（未开始无成本，不进
      任一栏）。
    每栏含跨 run 的 kind+phase 分组（aggregate_run_timings 语义）、合计与
    逐 run 汇总（per_run）。"""
    root = Path(root)
    active: list = []
    superseded: list = []
    excluded: list = []
    runs_state = status.get("runs", {}) if isinstance(status, dict) else {}
    for run in task["runs"]:
        rid = run["id"]
        entry = runs_state.get(rid)
        state = entry.get("state") if isinstance(entry, dict) else None
        if state in ACTIVE_STATES:
            active.append(rid)
        elif state == "superseded":
            superseded.append(rid)
        else:
            excluded.append({"run": rid, "state": state})
    return {
        "schema_version": TASK_TIMING_SCHEMA_VERSION,
        "task": {"id": task["task"]["id"]},
        "generated_at": utc_now_iso(),
        "active": _column(root, active),
        "superseded": _column(root, superseded),
        "excluded": excluded,
    }