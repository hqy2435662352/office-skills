#!/usr/bin/env python3
"""
scripts/prepare_task.py — Task 级编排入口（issue 01 + 02 + 03 切片）。

当前实现：
  --validate  静态校验 task.yaml（run id 唯一 / sheets、target 引用存在 /
              输出名合法 / 业务规则键禁止入内），失败 exit 3 + defect 清单；
              task.yaml 缺失 exit 1。
  --init      校验 + 首写 task_manifest.json（声明快照：run 清单 + 输入/输出
              引用 + task.yaml 指纹，封存后不静默重派生）与 task_status.json
              （全部 run planned）——derived 文件只能由脚本写入，不手改。
              task.yaml 变化 → MANIFEST_STALE（exit 3，fail-closed，失败二分
              预留 supersede 路径）；manifest/status 被手改 → 一致性缺陷。
              快照的输入事实（staged/outline/cache/fingerprint）由 prepare
              阶段（issue 02）补全，本脚本只写声明骨架。
  --prepare   阶段 1/2（issue 02 + 03）：staging + outline（任务级一次）+
              eager 预展平入 task-local cache（每缓存键恰好一个 worker，
              命中零 officecli）+ 逐 run 物化（缓存产物 → runs/<id>/，单 run
              命名）+ run 级 prepare_manifest.json 组装（compile-facing 与
              单 run 同构，仅 flattened 条目多 cache_key/sha256）+ status
              边界推进（planned → prepared）。复用 prepare_run /
              flatten_cache / task_prepare 的底层函数，现有单 run 脚本零改动。
  --run       阶段 1–5 完整编排（issue 03）：source_prepare → run_prepare →
              compile → execute → gate。barrier 式阶段批处理（阶段内并行、
              阶段间 barrier、无流水线）、并发默认值 = implementation
              constant（不进 task.yaml、不暴露 CLI 调参）、单一写者
              （task_status.json 在阶段边界统一写盘一次，阶段内零并发写）、
              任一 run 失败不阻断同阶段其他 run（失败清单汇总）。gate 呈现
              （--set）后停（fail-closed）：不自动确认、不自动 promote；
              确认展开与 promote 由 gate_task（issue 05）/ resume_task
              （issue 04）承担。
  --resume    预留：断点恢复在 issue 04（resume_task.py）。
  调度实现：Python 线程池 + subprocess 调用现有脚本（compile_fill.py /
  execute_batch.py / execution_gate.py 本就是独立进程入口），现有脚本零改动。

Exit codes（与套件一致）: 0=pass, 1=fatal (env/file), 3=retryable (validate defects / stage failures)。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # 套件导入纪律：先 insert 再打 E402

import _officecli  # noqa: E402
import task_schema  # noqa: E402
import task_prepare  # noqa: E402

ensure_utf8_stdio = _officecli.ensure_utf8_stdio
fail = _officecli.fail


def _ascii_path_check(root: Path) -> None:
    try:
        str(root).encode("ascii")
    except UnicodeEncodeError:
        fail("NON_ASCII_PATH",
             f"task root 含非 ASCII 字符: {root} — officecli batch/set 在中文路径上失败",
             "使用 ASCII task root，如 C:/Temp/tablefill/<task>/", exit_code=1)


def _pass(code: str, task: dict) -> None:
    print(json.dumps({
        "status": "PASS",
        "code": code,
        "task": {"id": task["task"]["id"],
                 "runs": [r["id"] for r in task["runs"]]},
    }, ensure_ascii=False, indent=2))


def _load_and_validate(root: Path) -> dict:
    """加载 + 静态校验 task.yaml；失败走 fail() 契约。"""
    data, defect = task_schema.load_task_yaml(root)
    if defect is not None:
        fail(defect["code"], defect["message"], defect["corrective_action"],
             exit_code=1 if defect.get("fatal") else 3)
    defects = task_schema.validate_task_yaml(data, root)
    if defects:
        fail("TASK_YAML_INVALID",
             f"{len(defects)} 条 task.yaml 缺陷",
             "按缺陷清单修 task.yaml（run id 唯一、引用存在、输出名合法）",
             defects=defects)
    return data


def _load_derived(root: Path, *, require_existing: bool):
    """加载 task.yaml + 派生文件并做一致性检查（--init / --prepare 共用前置）。

    损坏的 manifest/status → 缺陷 fail；task.yaml 自封存后变化、run id 不一致、
    非法状态 → fail-closed 拒绝（不静默重派生，失败二分预留 supersede）。
    require_existing=True 时，缺失派生文件 → TASK_MANIFEST_MISSING /
    TASK_STATUS_MISSING（--prepare 需要 --init 产物）。返回
    (task, yaml_sha256, manifest|None, status|None)。
    """
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

    if manifest is None:
        if require_existing:
            fail("TASK_MANIFEST_MISSING",
                 "task_manifest.json 不存在 — --prepare 需要 --init 的派生文件",
                 "先运行: python scripts/prepare_task.py --task-root <dir> --init")
    else:
        problems = task_schema.check_frozen(task, yaml_sha256, manifest)
        if problems:
            fail("MANIFEST_CHECK_FAILED",
                 f"{len(problems)} 条 manifest 一致性缺陷",
                 "按缺陷清单处置（task.yaml 已变 → supersede 路径；手改 → 恢复）",
                 defects=problems)

    if status is None:
        if require_existing:
            fail("TASK_STATUS_MISSING",
                 "task_status.json 不存在 — --prepare 需要 --init 的派生文件",
                 "先运行: python scripts/prepare_task.py --task-root <dir> --init")
    else:
        problems = task_schema.check_status(task, status, yaml_sha256)
        if problems:
            fail("STATUS_CHECK_FAILED",
                 f"{len(problems)} 条 task_status.json 一致性缺陷",
                 "按缺陷清单处置（status 只能由任务脚本写入）",
                 defects=problems)

    return task, yaml_sha256, manifest, status


def run_validate(root: Path) -> None:
    task = _load_and_validate(root)
    _pass("TASK_VALIDATED", task)


def run_init(root: Path) -> None:
    task, yaml_sha256, manifest, status = _load_derived(
        root, require_existing=False)

    # task_manifest.json：冻结快照，一旦落盘不再改写
    manifest_state = "frozen"
    if manifest is None:
        manifest = task_schema.derive_task_manifest(task, yaml_sha256)
        (root / task_schema.TASK_MANIFEST_NAME).write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        manifest_state = "written"

    # task_status.json：运行时状态，初始全部 planned；已存在则校验一致性
    status_state = "ok"
    if status is None:
        status = task_schema.derive_task_status(task, yaml_sha256)
        (root / task_schema.TASK_STATUS_NAME).write_text(
            json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
        status_state = "written"

    print(json.dumps({
        "status": "PASS",
        "code": "TASK_INITIALIZED",
        "task": {"id": task["task"]["id"],
                 "runs": [r["id"] for r in task["runs"]]},
        "artifacts": {
            task_schema.TASK_MANIFEST_NAME: manifest_state,
            task_schema.TASK_STATUS_NAME: status_state,
        },
    }, ensure_ascii=False, indent=2))


def _progress(line: str) -> None:
    """进度摘要走 stderr（stdout 只承载结构化 JSON，解析器不受污染）。"""
    print(line, file=sys.stderr)


def _fail_json(code: str, message: str, corrective_action: str,
               defects: list) -> None:
    """阶段级失败（--prepare/--run 用）：结构化 ERROR JSON 走 stdout + exit 3。

    与 fail() 同负载形态（status/code/message/corrective_action/defects），
    但走 stdout —— 这两个模式的 stderr 已承载人读进度行，不再承担
    fail() 的「整块 JSON」契约；守卫级失败（进度行出现之前）仍走 fail()
    （stderr 纯 JSON 契约保持）。exit 3 = retryable 与套件一致。"""
    print(json.dumps({
        "status": "ERROR", "code": code,
        "message": message, "corrective_action": corrective_action,
        "defects": defects,
    }, ensure_ascii=False, indent=2))
    sys.exit(3)


def run_prepare(root: Path) -> None:
    """执行 task_prepare.run_prepare（staging/缓存/物化/run manifest，阶段
    1+2 barrier 编排）；派生文件存在性与冻结一致性由 _load_derived 前置保证。
    run 级失败不阻断同阶段其他 run：失败清单在阶段边界汇总，统一 exit 3。"""
    task, _yaml_sha256, manifest, status = _load_derived(
        root, require_existing=True)

    report = task_prepare.run_prepare(root, task, manifest, status,
                                      progress=_progress)
    if report["failures"]:
        # 失败路径：结构化 ERROR JSON 走 stdout（stderr 只承载人读进度行，
        # 不被 fail() 的双语法打破）；exit 3 与 fail() 语义一致。
        _fail_json("RUN_PREPARE_FAILED",
                   f"{len(report['failures'])} 条 run 在 prepare 阶段失败",
                   "失败二分（issue 04）：输入事实未变 → 阶段重试/REPAIR；"
                   "输入事实改变 → supersede 该 run",
                   defects=report["failures"])
    print(json.dumps({
        "status": "PASS",
        "code": "TASK_PREPARED",
        "task": {"id": task["task"]["id"],
                 "runs": [r["id"] for r in task["runs"]]},
        "cache": report["cache"],
        "runs": report["runs"],
        "superseded": report["superseded"],
    }, ensure_ascii=False, indent=2))


def run_task(root: Path) -> None:
    """--run 完整编排（阶段 1–5 barrier 调度；并发 = implementation
    constant）。阶段失败清单统一汇总；gate 呈现后停（fail-closed）。"""
    task, _yaml_sha256, manifest, status = _load_derived(
        root, require_existing=True)

    report = task_prepare.run_staged_pipeline(root, task, manifest, status,
                                              stages=task_prepare.RUN_STAGES,
                                              progress=_progress)
    if report["failures"]:
        _fail_json("STAGE_FAILURES",
                   f"{len(report['failures'])} 条 run 在阶段执行中失败",
                   "失败二分（issue 04）：输入事实未变 → 重试该 run 的阶段；"
                   "输入事实改变 → supersede 该 run",
                   defects=report["failures"])
    stage_summaries = [{
        "stage": sr["stage"], "items": sr["items"],
        "ok": len(sr["ok"]), "failed": len(sr["failed"]),
    } for sr in report["stages"]]
    output = {
        "status": "PASS",
        "code": "TASK_RUN_GATE_PRESENTED",
        "task": {"id": task["task"]["id"],
                 "runs": [r["id"] for r in task["runs"]]},
        "stages": stage_summaries,
        "cache": report["cache"],
        "prepared": report["prepared"],
        "superseded": report["superseded"],
        "gate": {
            # 词汇与状态机一致：task_status 的 gated 即「已呈现、待确认」
            "state": "presented"
            if any(sr["stage"] == "gate" and len(sr["ok"]) > 0
                   for sr in report["stages"]) else "not-presented",
            "note": "确认展开与 promote 由 gate_task（issue 05）/ resume_task"
                    "（issue 04）承担；--run 不自动确认、不自动 promote",
        },
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))


def main() -> None:
    ensure_utf8_stdio()
    parser = argparse.ArgumentParser(
        description="Task 级编排：task.yaml 静态校验 + derived 文件初始化 + "
                    "prepare（阶段 1–2）+ run（阶段 1–5 完整编排）")
    parser.add_argument("--task-root", type=Path, required=True,
                        help="任务根目录（ASCII），含 agent 撰写的 task.yaml")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--validate", action="store_true",
                      help="仅静态校验 task.yaml，不写任何文件")
    mode.add_argument("--init", action="store_true",
                      help="校验 + 首写 task_manifest.json / task_status.json")
    mode.add_argument("--prepare", action="store_true",
                      help="staging + outline + eager 展平缓存 + 逐 run 物化"
                           "与 manifest 组装（需先 --init）")
    mode.add_argument("--run", action="store_true",
                      help="阶段 1–5 完整编排（prepare → compile → execute →"
                           " gate，barrier 调度 + 单一写者；需先 --init）")
    args = parser.parse_args()

    root = args.task_root
    _ascii_path_check(root)
    if not root.is_dir():
        fail("TASK_ROOT_MISSING", f"task root 不存在: {root}",
             "创建任务根目录并放入 task.yaml", exit_code=1)
    if args.validate:
        run_validate(root)
    elif args.init:
        run_init(root)
    elif args.prepare:
        run_prepare(root)
    elif args.run:
        run_task(root)
    else:
        fail("NO_MODE", "choose --validate, --init, --prepare or --run",
             "Pass one of the modes")
    sys.exit(0)


if __name__ == "__main__":
    main()
