#!/usr/bin/env python3
"""
scripts/resume_task.py — Task 层唯一恢复 / supersede 入口（issue 04，spec S7）。

  --resume               断点恢复：逐 run 按产物证据（存在性 + SHA-256，无
                         Office 纯函数）判定实际断点，继续剩余阶段（compile
                         barrier → execute barrier → gate）。跳过
                         promoted / superseded；gated 等待人工确认（不绕过）；
                         confirmed 等待 gate_task 的 promote（不自动 promote，
                         fail-closed 不变）；输入事实改变 → 阻塞并建议
                         supersede。
  --supersede --map     失败二分：输入事实改变（task.yaml 修改、源 hash
                         --map 旧run=新run   漂移、映射裁决变化等）→ 旧 run 标
                         superseded + superseded_by 链接新版本，产物完整保留；
                         新 run（如 <id>_v2）是独立版本。本入口是唯一被授权
                         在 task.yaml 变化后重派生 task_manifest.json 快照
                         的地方（issue 01 的 MANIFEST_STALE fail-closed 由此
                         显式解除，不静默重派生）。
  --rebuild              （与 --resume 配合）已 superseded 的 run 按产物证据
                         重新进入主路径（显式重建；默认跳过）。

Exit codes（与套件一致）: 0=pass, 1=fatal (env/file), 3=retryable
(validate defects / stage failures / supersede required)。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # 套件导入纪律：先 insert 再打 E402

import _officecli  # noqa: E402
import task_resume  # noqa: E402
import task_schema  # noqa: E402
from prepare_task import (  # noqa: E402 —— 复用既有前置契约（入口零重复）
    _ascii_path_check, _fail_json, _load_and_validate, _load_derived,
    _progress,
)

ensure_utf8_stdio = _officecli.ensure_utf8_stdio
fail = _officecli.fail


def _task_block(task: dict) -> dict:
    return {"id": task["task"]["id"], "runs": [r["id"] for r in task["runs"]]}


def run_resume(root: Path, rebuild: bool) -> None:
    """断点恢复：前置契约（--init 产物 + 冻结一致性，与 --prepare/--run
    相同）→ 逐 run 断点判定 → 剩余阶段 barrier 恢复。"""
    task, _yaml_sha256, manifest, status = _load_derived(
        root, require_existing=True)

    report = task_resume.resume_task(root, task, manifest, status,
                                     rebuild=rebuild, progress=_progress)

    # 输入事实改变（run 级绑定漂移）：阻塞 + supersede 建议，不继续旧 run
    if report["blocked"]:
        _fail_json(
            "SUPERSEDE_REQUIRED",
            f"{len(report['blocked'])} 条 run 的输入事实在 execute 后改变",
            "失败二分：输入事实改变 → 用 --supersede --map 旧run=新run 标记"
            "废弃并链接新版本（旧 run 产物保留，新 run 重新编译执行）",
            defects=report["blocked"])
    if report["failures"]:
        _fail_json("STAGE_FAILURES",
                   f"{len(report['failures'])} 条 run 在恢复阶段执行中失败",
                   "失败二分：输入事实未变 → 重试该 run 的阶段（再次 --resume "
                   "从产物断点继续）；输入事实改变 → --supersede",
                   defects=report["failures"])

    stages = [{
        "stage": sr["stage"], "items": sr["items"],
        "ok": len(sr["ok"]), "failed": len(sr["failed"]),
    } for sr in report["stages"]]
    gate_pending = report["gated_pending"] or []
    gate_state = "pending" if gate_pending else (
        "presented" if report["gate_presented"] else "none")
    print(json.dumps({
        "status": "PASS",
        "code": "TASK_RESUME_GATE_PENDING" if (
            gate_pending or report["gate_presented"]) else "TASK_RESUMED",
        "task": _task_block(task),
        "checkpoints": report["checkpoints"],
        "stages": stages,
        "failures": report["failures"],
        "skipped": report["skipped"],
        "confirmed": report["confirmed"],
        "gate": {
            "state": gate_state,
            "pending_runs": gate_pending,
            "note": "gated run 等待人工确认（resume 不绕过 Gate）；confirmed "
                    "run 的 promote 由 gate_task 展开（resume 不自动 promote）",
        },
    }, ensure_ascii=False, indent=2))


def run_supersede(root: Path, mappings: list[tuple[str, str]]) -> None:
    """supersede：mapping 校验 → 重派生 manifest 快照（唯一授权解冻路径）→
    状态演进（旧 run superseded + superseded_by，新增 run planned）。"""
    task = _load_and_validate(root)
    yaml_sha256 = task_schema.file_sha256(root / task_schema.TASK_YAML_NAME)

    manifest, m_defect = task_schema.load_manifest(root)
    if m_defect is not None:
        fail(m_defect["code"], m_defect["message"], m_defect["corrective_action"],
             defects=[m_defect])
    status, s_defect = task_schema.load_status(root)
    if s_defect is not None:
        fail(s_defect["code"], s_defect["message"], s_defect["corrective_action"],
             defects=[s_defect])
    if manifest is None or status is None:
        fail("TASK_DERIVED_MISSING",
             "task_manifest.json / task_status.json 缺失 — supersede 需要"
             "--init 的派生文件（先 --init 后 supersede）",
             "先运行: python scripts/prepare_task.py --task-root <dir> --init")

    defects = task_resume.validate_supersede(task, manifest, status, mappings)
    if defects:
        fail("SUPERSEDE_INVALID",
             f"{len(defects)} 条 supersede 校验缺陷",
             "按缺陷清单修正（旧 run 保留在 task.yaml、新 run 先声明、"
             "声明被改动的 run 必须 mapping）",
             defects=defects)

    # 唯一授权解冻：task.yaml 变化 → 在此显式重派生快照（不静默重派生）
    new_manifest = task_schema.derive_task_manifest(task, yaml_sha256)
    (root / task_schema.TASK_MANIFEST_NAME).write_text(
        json.dumps(new_manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    new_status = task_resume.supersede_status(status, task, yaml_sha256,
                                              mappings)
    (root / task_schema.TASK_STATUS_NAME).write_text(
        json.dumps(new_status, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps({
        "status": "PASS",
        "code": "TASK_SUPERSEDED",
        "task": _task_block(task),
        "superseded": [{
            "old": old, "new": new,
            "state": new_status["runs"][old]["state"],
            "superseded_by": new_status["runs"][old]["superseded_by"],
        } for old, new in mappings],
        "note": "旧 run 产物完整保留；新的输入事实快照已重新封存（下次 "
                "--prepare/--resume 从缓存命中续建，零重复展平）",
    }, ensure_ascii=False, indent=2))


def parse_mappings(raw: list[str]) -> list[tuple[str, str]]:
    """--map 参数（重复出现）：'old=new'。"""
    mappings = []
    for item in raw:
        if "=" not in item:
            fail("MAPPING_FORMAT", f"--map 需要 old=new 形式: {item!r}",
                 "如: --map r32-heating=r32-heating_v2")
        old, _, new = item.partition("=")
        old, new = old.strip(), new.strip()
        if not old or not new:
            fail("MAPPING_FORMAT", f"--map 两侧不能为空: {item!r}",
                 "如: --map r32-heating=r32-heating_v2")
        mappings.append((old, new))
    return mappings


def main() -> None:
    ensure_utf8_stdio()
    parser = argparse.ArgumentParser(
        description="Task 层唯一恢复/supersede 入口：断点判定（产物证据 + "
                    "hash）+ 剩余阶段恢复 + run 级 supersede")
    parser.add_argument("--task-root", type=Path, required=True,
                        help="任务根目录（ASCII），含 agent 撰写的 task.yaml")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--resume", action="store_true",
                      help="断点恢复：继续剩余阶段（compile → execute → gate；"
                           "跳过 promoted/superseded；不绕过 Gate、不自动 promote）")
    mode.add_argument("--supersede", action="store_true",
                      help="失败二分：输入事实改变 → 标记旧 run superseded 并"
                           "重派生输入快照（唯一授权解冻路径）")
    parser.add_argument("--map", action="append", metavar="OLD=NEW",
                        help="（--supersede 用，可重复）旧 run → 新版本 run 映射")
    parser.add_argument("--rebuild", action="store_true",
                        help="（与 --resume 配合）已 superseded 的 run 按产物"
                             "证据重新进入主路径（显式重建）")
    args = parser.parse_args()

    root = args.task_root
    _ascii_path_check(root)
    if not root.is_dir():
        fail("TASK_ROOT_MISSING", f"task root 不存在: {root}",
             "创建任务根目录并放入 task.yaml", exit_code=1)
    if args.supersede:
        if not args.map:
            fail("MAPPING_REQUIRED", "--supersede 需要至少一个 --map old=new",
                 "如: --supersede --map r32-heating=r32-heating_v2")
        run_supersede(root, parse_mappings(args.map))
    else:
        run_resume(root, rebuild=args.rebuild)
    sys.exit(0)


if __name__ == "__main__":
    main()