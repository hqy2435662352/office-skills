#!/usr/bin/env python3
"""
scripts/task_prepare.py — Task runtime retirement (ADR 0020): pure-function host.

The Task-level execution state machine and lifecycle orchestration are
retired: no RUN_STAGES, no stage workers, no run_staged_pipeline, no
task_status lifecycle, no compile/execute/deliver orchestration. Task is a
multi-run topology/container abstraction (task.yaml + runs/<id>/), not an
execution engine; run views are projected by materialize_run.py after
Topology (S1) — see SKILL Part III.

This module remains ONLY as a host for the run-manifest assembly pure
function that materialization reuses (Q4 frozen decision: reuse
assemble_run_manifest(); do not migrate helpers in this round — module name
is accepted technical debt). No lifecycle behavior may grow back here.
"""

from __future__ import annotations

RUN_MANIFEST_NAME = "prepare_manifest.json"


def assemble_run_manifest(workdir, task_label, files, outlines, flattened,
                          target_entry, fingerprints, row_gaps=None,
                          style_granularity=None) -> dict:
    """run 级 prepare_manifest.json 组装（纯函数）。

    compile-facing 顶层形态与单 run manifest 同构（schema_version 2 /
    workdir / task / files / outlines / flattened / target / fingerprints /
    row_gaps / style_granularity）；flattened 条目由 materialize_run 从
    workspace entry facts 投影（源条目复用 workspace evidence；target 条目
    的 evidence 指向 run-local target routing view）。

    本函数只做 dict 组装, 不触碰文件系统 — 调用方负责写盘。
    """
    return {
        "schema_version": 2,
        "workdir": workdir,
        "task": task_label,
        "files": files,
        "outlines": outlines,
        "flattened": flattened,
        "target": target_entry,
        "fingerprints": fingerprints,
        "row_gaps": row_gaps or {},
        "style_granularity": style_granularity or {},
    }