#!/usr/bin/env python3
"""
scripts/timing_task.py — task 级 timing 聚合报告 CLI（issue 06，spec S7
Timing）。

只读输出「本次交付成本 vs 优化收益」双栏聚合报告（active / superseded，
kind+phase 分组，复用 aggregate_run_timings 语义），不依赖 Gate 流程即可
在任意时刻查看：

  python scripts/timing_task.py --task-root <dir>

报告内容与 gate_summary.json 的 task_timing 块同源（同一纯函数
scripts/task_timing.py）。只读：不写任何文件 —— 每 run run_timing.json
保留不动（现有机制 append，证据不删）。

Exit codes（与套件一致）: 0=pass, 1=fatal (env/file), 3=retryable
(validate defects)。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # 套件导入纪律：先 insert 再打 E402

import _officecli  # noqa: E402
import task_timing  # noqa: E402
from prepare_task import (  # noqa: E402 —— 复用既有前置契约（入口零重复）
    _ascii_path_check, _load_derived,
)

ensure_utf8_stdio = _officecli.ensure_utf8_stdio
fail = _officecli.fail


def main() -> None:
    ensure_utf8_stdio()
    parser = argparse.ArgumentParser(
        description="task 级 timing 聚合报告（active / superseded 双栏，"
                    "只读；不写任何文件）")
    parser.add_argument("--task-root", type=Path, required=True,
                        help="任务根目录（ASCII），含 agent 撰写的 task.yaml")
    args = parser.parse_args()

    root = args.task_root
    _ascii_path_check(root)
    if not root.is_dir():
        fail("TASK_ROOT_MISSING", f"task root 不存在: {root}",
             "创建任务根目录并放入 task.yaml", exit_code=1)

    task, _yaml_sha256, _manifest, status = _load_derived(
        root, require_existing=True)
    report = task_timing.aggregate_task_timing(task, status, root)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    sys.exit(0)


if __name__ == "__main__":
    main()