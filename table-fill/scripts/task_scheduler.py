#!/usr/bin/env python3
"""
scripts/task_scheduler.py — Stage Orchestrator（issue 03，spec S6）。

barrier 式阶段批处理 + 并发控制 + 单一写者：

  - 阶段表与并发默认值是 implementation constant：不进入 task.yaml、不暴露
    CLI 调参（环境稳定性参数不得污染任务定义，spec S6 / Implementation
    Decision 20/21）；
  - 阶段内并行（Python 线程池）、阶段间 barrier（等待全部完成）、无跨阶段
    流水线、无 DAG / 无 worker pool（spec S6）；
  - 单一写者：worker 只向主进程回报结果（状态码 + 产物路径），本模块不写
    任何文件；task_status.json 由调用方在阶段边界统一批量写盘一次（阶段内
    并发时状态文件零并发写；spec S6 / Decision 23）；
  - 失败传播：任一 item 失败不影响同阶段其他 item；阶段结束汇总失败清单，
    失败 run 按 issue 04 的失败二分处置（重试 / REPAIR / supersede）。

调度实现（Decision 30）：Python 线程池 + subprocess 调用现有脚本
（compile_fill.py / execute_batch.py 等本就是独立进程入口）；现有脚本零改动
—— 本模块不含任何脚本/文件系统知识（纯编排），worker 是薄适配层。

本模块是契约测试 seam（spec Testing Decision #3）：run_stage /
apply_stage_status / 阶段常量 / 进度行均为可 import 的纯逻辑，无 Office
可单测（fake worker 注入即可覆盖 barrier、并发上限、失败隔离与边界状态
推进）。
"""

from __future__ import annotations

import copy
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # 套件导入纪律

from task_schema import utc_now_iso  # noqa: E402


# ── 阶段表与并发默认值（implementation constant，spec S6 表格） ────────

# 顺序即调度顺序：阶段间 barrier，无跨阶段流水线。
STAGES = ("source_prepare", "run_prepare", "compile", "execute", "gate",
          "promote")

# 并发默认值（决策 20/21）：execute=2 是 Office 稳定性边界（Windows resident
# 进程持文件锁，3 并发曾实证 validate_state=fail），不是理论吞吐参数；
# compile 纯文本取 4。不进入 task.yaml、不暴露 CLI 调参。
STAGE_CONCURRENCY = {
    "source_prepare": 2,   # 阶段 1: source prepare/flatten/cache build（Office 密集）
    "run_prepare": 2,      # 阶段 2: run prepare（物化 + target prepare + manifest 组装，Office 密集）
    "compile": 4,          # 阶段 3: compile（纯文本）
    "execute": 2,          # 阶段 4: execute（validate/readback/render QA，Office 密集）
    "gate": 1,             # 阶段 5: gate_task 聚合呈现（串行，一次人机交互）
    "promote": 2,          # 阶段 6: promote（纯文件）
}

STAGE_LABELS = {
    "source_prepare": "source prepare/flatten/cache build",
    "run_prepare": "run prepare（物化 + manifest 组装）",
    "compile": "compile（纯文本）",
    "execute": "execute（validate/readback/render QA）",
    "gate": "gate 呈现（execution_gate --set，串行；聚合在 issue 05）",
    "promote": "promote（纯文件；worker 挂载在 issue 05）",
}

# 阶段边界的后继状态（spec S7 主路径 planned → prepared → compiled →
# drafted → gated → promoted；superseded 是保留证据的终止分支，本层只推进
# 活 run）。source_prepare 是任务级阶段（缓存构建），不推进任何 run 状态。
STAGE_SUCCESSOR = {
    "source_prepare": None,
    "run_prepare": "prepared",
    "compile": "compiled",
    "execute": "drafted",
    "gate": "gated",
    "promote": "promoted",
}

# 允许被本阶段推进的前驱状态（幂等：已到达后继或更远状态的 run 不触碰；
# 防御性约束，item 选择已按状态过滤）。
_STAGE_PRECURSOR = {
    "source_prepare": frozenset(),
    "run_prepare": frozenset({"planned", "prepared"}),
    "compile": frozenset({"prepared"}),
    "execute": frozenset({"compiled"}),
    "gate": frozenset({"drafted"}),
    "promote": frozenset({"gated"}),
}


class StageError(Exception):
    """worker 的可预期失败（携带与套件 fail() 同构的 code/message/
    corrective_action）。调度器捕获后转为 failed 结果，不阻断同阶段其他
    item；未预期的异常（含 worker 内 fail() 的 SystemExit）同样被捕获归一。
    """

    def __init__(self, code: str, message: str, corrective_action: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.corrective_action = corrective_action


def _failed(item, code: str, message: str, corrective_action: str) -> dict:
    return {
        "run": item if isinstance(item, str) else None,
        "status": "failed",
        "code": code,
        "message": message,
        "corrective_action": corrective_action,
        "artifacts": {},
    }


def _run_item(worker, item) -> dict:
    """单个 worker 的隔离执行：任何失败都归一成 failed 结果返回，不抛出
    （barrier 语义要求同阶段其他 item 不受影响）。"""
    try:
        result = worker(item)
    except StageError as e:
        return _failed(item, e.code, e.message, e.corrective_action)
    except SystemExit as e:
        return _failed(
            item, "WORKER_EXIT",
            f"worker 内部 fail() 退出 (exit {e.code}) — 细节见上方 stderr",
            "修复环境/输入后重试该 item（或按失败二分处置）")
    except Exception as e:  # noqa: BLE001 — worker 边界就是捕获点
        return _failed(
            item, "WORKER_RAISED", f"{type(e).__name__}: {e}",
            "修复后重试该 item（或按失败二分处置）")
    if not isinstance(result, dict):
        return _failed(
            item, "WORKER_INVALID_RESULT",
            f"worker 返回类型 {type(result).__name__}，需要 dict "
            "(必带 \"status\": \"ok\"|\"failed\" 与 \"artifacts\")",
            "修正 worker 结果契约")
    if "status" not in result or result["status"] not in ("ok", "failed"):
        return _failed(
            item, "WORKER_INVALID_RESULT",
            f"worker 结果缺合法 status（{result.get('status')!r}），"
            "必须显式给出 \"status\": \"ok\"|\"failed\"",
            "修正 worker 结果契约")
    result.setdefault("run", None)
    result.setdefault("code", None)
    result.setdefault("message", None)
    result.setdefault("corrective_action", None)
    result.setdefault("artifacts", {})
    return result


def run_stage(stage: str, items: list, worker) -> dict:
    """barrier 式单个阶段执行（纯编排，不写任何文件）。

    - 阶段内并行：ThreadPoolExecutor，并发上限取 STAGE_CONCURRENCY 实现
      常量（implementation constant：不进 task.yaml、不暴露 CLI 调参，
      也不接受运行时覆盖 —— spec S6）；
    - 阶段间 barrier：with 块退出即等待全部 worker 完成；无跨阶段流水线；
    - 失败隔离：任何 item 失败（异常或 failed 结果）不影响同阶段其他 item；
      结果按 items 顺序收集（确定性）。

    worker 契约：worker(item) -> dict，必须显式给出 "status":
    「ok」或「failed」，并携带 "run" / "artifacts"（缺失自动补默认值）；
    业务失败抛 StageError(code, message, corrective_action)。返回 stage
    报告：{"stage", "items", "results", "ok", "failed"}（results 全长、
    顺序稳定）。
    """
    if stage not in STAGE_CONCURRENCY:
        raise ValueError(f"unknown stage: {stage!r} (known: {STAGES})")
    limit = STAGE_CONCURRENCY[stage]

    with ThreadPoolExecutor(max_workers=limit) as pool:
        futures = [pool.submit(_run_item, worker, item) for item in items]
        results = [f.result() for f in futures]  # 顺序 = items 顺序

    ok = [r for r in results if r["status"] == "ok"]
    failed = [r for r in results if r["status"] != "ok"]
    return {"stage": stage, "items": len(items),
            "results": results, "ok": ok, "failed": failed}


def aggregate_failures(stage_reports: list[dict]) -> list[dict]:
    """跨阶段失败清单汇总（阶段结束的失败汇总，供 fail()/缺陷呈现）。

    每条: {stage, run, code, message, corrective_action}；run 为 None 表示
    任务级 item（如缓存键）。"""
    failures = []
    for sr in stage_reports:
        for r in sr.get("failed", []):
            failures.append({
                "stage": sr.get("stage"),
                "run": r.get("run"),
                "code": r["code"],
                "message": r["message"],
                "corrective_action": r["corrective_action"],
            })
    return failures


def apply_stage_status(status: dict, stage: str, results: list[dict], *,
                       updated_at: str | None = None) -> dict:
    """阶段边界的批量状态推进（单一写者语义：本函数只返回新 status 字典，
    写盘由调用方在阶段边界执行一次；阶段内并发时状态文件零并发写）。

    - 只推进「结果 ok 且当前状态 ∈ 本阶段前驱集」的 run；
    - 失败的 run 不推进（保持原状态，待失败二分处置）；
    - superseded 与未出现在结果中的 run 不触碰；
    - updated_at 刷新（阶段边界 = 检查点时刻）。"""
    new_status = copy.deepcopy(status)
    successor = STAGE_SUCCESSOR.get(stage)
    precursor = _STAGE_PRECURSOR.get(stage, frozenset())
    if successor is not None:
        for r in results:
            entry = new_status.get("runs", {}).get(r.get("run"))
            if entry is None or entry.get("state") not in precursor:
                continue  # 未知 run / 不在前驱状态（幂等）→ 不触碰
            if r.get("status") != "ok":
                continue  # 失败的 run 不推进
            entry["state"] = successor
    new_status["updated_at"] = updated_at or utc_now_iso()
    return new_status


def stage_start_line(stage_idx: int, total: int, stage: str, n_items: int) -> str:
    """阶段开始进度行：`阶段 x/y 开始: <stage> — n 项, 并发 c`."""
    c = STAGE_CONCURRENCY[stage]
    return (f"阶段 {stage_idx}/{total} 开始: {stage} — {n_items} 项, "
            f"并发 {c} ({STAGE_LABELS[stage]})")


def stage_end_line(stage_idx: int, total: int, stage: str, result: dict) -> str:
    """阶段边界摘要行：`阶段 x/y 完成: <stage> — ok=n failed=n`
    （对齐复盘『超过 60 秒主动说明进度』的要求）。"""
    ok = len(result.get("ok", []))
    failed = len(result.get("failed", []))
    return f"阶段 {stage_idx}/{total} 完成: {stage} — ok={ok} failed={failed}"