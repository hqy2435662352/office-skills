#!/usr/bin/env python3
"""
scripts/prepare_task.py — Task 级编排入口（issue 01 切片：Task Artifact Model 静态层）。

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

阶段调度 / task-local 展平缓存 / run 创建在 issue 02、03 落地，
本脚本是它们的挂载点；既有单 run 脚本（prepare_run.py 等）零改动。

Exit codes（与套件一致）: 0=pass, 1=fatal (env/file), 3=retryable (validate defects)。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from _officecli import ensure_utf8_stdio, fail  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))

import task_schema  # noqa: E402


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


def run_validate(root: Path) -> None:
    task = _load_and_validate(root)
    _pass("TASK_VALIDATED", task)


def run_init(root: Path) -> None:
    task = _load_and_validate(root)
    yaml_sha256 = task_schema.file_sha256(root / task_schema.TASK_YAML_NAME)

    # task_manifest.json：冻结快照，一旦落盘不再改写
    manifest, m_defect = task_schema.load_manifest(root)
    if m_defect is not None:
        fail(m_defect["code"], m_defect["message"], m_defect["corrective_action"],
             defects=[m_defect])
    manifest_state = "frozen"
    if manifest is None:
        manifest = task_schema.derive_task_manifest(task, yaml_sha256)
        (root / task_schema.TASK_MANIFEST_NAME).write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        manifest_state = "written"
    else:
        problems = task_schema.check_frozen(task, yaml_sha256, manifest)
        if problems:
            fail("MANIFEST_CHECK_FAILED",
                 f"{len(problems)} 条 manifest 一致性缺陷",
                 "按缺陷清单处置（task.yaml 已变 → supersede 路径；手改 → 恢复）",
                 defects=problems)

    # task_status.json：运行时状态，初始全部 planned；已存在则校验一致性
    status, s_defect = task_schema.load_status(root)
    if s_defect is not None:
        fail(s_defect["code"], s_defect["message"], s_defect["corrective_action"],
             defects=[s_defect])
    status_state = "ok"
    if status is None:
        status = task_schema.derive_task_status(task, yaml_sha256)
        (root / task_schema.TASK_STATUS_NAME).write_text(
            json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
        status_state = "written"
    else:
        problems = task_schema.check_status(task, status, yaml_sha256)
        if problems:
            fail("STATUS_CHECK_FAILED",
                 f"{len(problems)} 条 task_status.json 一致性缺陷",
                 "按缺陷清单处置（run id 一一对应；state ∈ 状态集合）",
                 defects=problems)

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


def main() -> None:
    ensure_utf8_stdio()
    parser = argparse.ArgumentParser(
        description="Task 级编排：task.yaml 静态校验 + derived 文件初始化")
    parser.add_argument("--task-root", type=Path, required=True,
                        help="任务根目录（ASCII），含 agent 撰写的 task.yaml")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--validate", action="store_true",
                      help="仅静态校验 task.yaml，不写任何文件")
    mode.add_argument("--init", action="store_true",
                      help="校验 + 首写 task_manifest.json / task_status.json")
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
    else:
        fail("NO_MODE", "choose --validate or --init",
             "Pass one of the two modes")
    sys.exit(0)


if __name__ == "__main__":
    main()
