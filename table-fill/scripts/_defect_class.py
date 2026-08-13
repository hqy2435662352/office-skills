#!/usr/bin/env python3
"""
scripts/_defect_class.py — 执行失败缺陷分类 (共享)

把 execute_batch.py 的失败记录映射到已知缺陷类别 + 标准修复动作,
让 agent 拿到失败记录直接修复, 无需逐格反向侦查。
完整映射表见 references/FAILURE_CLASSES.md。
"""

from __future__ import annotations

import re


def classify_issue(path: str, subtype: str, message: str) -> str:
    """按 issue 的 path/subtype/message 分类. 返回缺陷类别."""
    msg = (message or "").lower()
    if "overflow" in msg or "需要" in msg and "pt" in msg:
        return "text_overflow"
    if "empty" in msg and ("cell" in msg or "value" in msg):
        return "empty_cell"
    if any(e in msg for e in ("#ref", "#value", "#div", "#name", "#n/a", "not evaluated")):
        return "formula_error"
    if "merge" in msg:
        return "merge_residue"
    return "unknown"


def classify_chunk(stderr: str) -> str:
    """按 officecli 执行错误文本分类."""
    err = (stderr or "").lower()
    if "order" in err and ("violat" in err or "batch" in err):
        return "order_violation"
    if "merge" in err and any(k in err for k in ("overlap", "already", "anchor", "conflict", "stale")):
        return "merge_residue"
    if any(k in err for k in ("unknown property", "unknown key", "ambiguous", "rejected",
                              "invalid_selector", "unsupported props")):
        return "prop_rejection"
    if "access denied" in err or "permission" in err:
        return "path_permission"
    if "formula" in err:
        return "formula_error"
    return "unknown"


def classify_chunk_error(text: str) -> str:
    """按 officecli 批量输出 (stdout+stderr) 中的错误行分类.

    2026-08-12 复盘: batch 的 `[N] ERROR: ...` 行走 stdout, stderr 常为空;
    `Anchor row N not found` 属于行号空洞 (模板 row 元素缺失), 需与
    普通 unknown 区分开, 否则 agent 只能盲猜修复."""
    err = (text or "").lower()
    if "anchor row" in err and "not found" in err:
        return "row_anchor_missing"
    return "unknown"


def defect_classes(new_issues: set | list | None, chunk_failures: list | None,
                   structural_failures: list | None = None) -> list[str]:
    """汇总全部缺陷类别 (去重, 保持确定性顺序).

    structural_failures: 结构 readback 失败 (FINAL_ROW_COUNT_MISMATCH /
    GROUP_BOUNDARY_MISMATCH) — v2.5 机器码直接透传为标准修复依据."""
    classes = []
    for issue in (new_issues or []):
        # new_issues 元素可能为 (path, subtype, message) 元组或已格式化字符串
        if isinstance(issue, tuple) and len(issue) == 3:
            cls = classify_issue(issue[0], issue[1], issue[2])
        else:
            cls = classify_issue(str(issue), "", "")
        if cls != "unknown" and cls not in classes:
            classes.append(cls)
    for cf in (chunk_failures or []):
        # cf: (chunk_start, rc, stdout_tail, stderr_tail) 或 dict
        if isinstance(cf, dict):
            stderr = cf.get("stderr_tail", "")
            stdout = cf.get("stdout_tail", "")
            rc = cf.get("rc", 0)
        else:
            stderr = cf[3] if len(cf) > 3 else ""
            stdout = ""
            rc = cf[1] if len(cf) > 1 else 0
        cls = classify_chunk(stderr)
        if cls == "unknown":
            cls = classify_chunk_error(stdout + " " + stderr)
        if cls != "unknown" and cls not in classes:
            classes.append(cls)
    for sf in (structural_failures or []):
        cls = classify_structural(sf)
        if cls != "unknown" and cls not in classes:
            classes.append(cls)
    return classes


def classify_structural(failure) -> str:
    """结构 readback 失败码 (v2.5): dict 带 code 键, 或 (code, ...) 元组."""
    code = ""
    if isinstance(failure, dict):
        code = failure.get("code") or ""
    elif isinstance(failure, (tuple, list)) and failure:
        code = str(failure[0])
    if code in ("FINAL_ROW_COUNT_MISMATCH", "GROUP_BOUNDARY_MISMATCH",
                "RENDER_QA_FAILED"):
        return code
    return "unknown"


def primary_defect(classes: list[str]) -> str:
    """主缺陷 = 第一个非 unknown 类别; 无则 unknown."""
    for c in classes:
        if c != "unknown":
            return c
    return "unknown"


def standard_fix(cls: str) -> str:
    fixes = {
        "text_overflow": "值超出列宽: ①直接写入的长精度值编译期已拦截(NUMERIC_OVERFLOW_RISK, 加 transform: round4) ②派生公式浮点残值 → ROUND(...,2), 且只加在减法/乘法/除法/SUM 聚合上, 纯加法(如 R=P+Q)不加 — 加法加 ROUND 会把结算价 168.7151 截成 168.72、放大毛利 ③公式返回空串 → 改 0-口径公式链 ④set col width 加宽是末选。详见 references/FAILURE_CLASSES.md",
        "empty_cell": "关键格为空: 检查克隆残留未置空或填充遗漏; 对必须为空的列显式 value:null",
        "formula_error": "公式错误: 检查引用范围/跨 sheet 前缀(Sheet!B13); 派生公式加 ROUND 防浮点残值 (ROUND 精准原则: 只加在减/乘/除/SUM 上)",
        "merge_residue": "合并残留: 在 merge:true 前对相关单元格 set merge:false; 新锚点补 font/alignment 样式",
        "order_violation": "操作排序违规: 用 compile_fill.py 重新生成 plan(脚本内置 clear→add→remove→merge→fill 全局排序); 禁止手写 batch",
        "prop_rejection": "props 键名错误: 查 `officecli help xlsx set cell`; batch 内用完整点号名(font.color)",
        "path_permission": "路径权限: 确认输出为 ASCII 路径且文件非只读; stage 阶段文件已强制可写",
        "FINAL_ROW_COUNT_MISMATCH": "最终行数不符: 核对源匹配行数与 capacity/start_row(trim 或 overflow 方向); selectors 是否漏配/误配行; 修 fill_spec.yaml 重编译重执行",
        "GROUP_BOUNDARY_MISMATCH": "组边界不符: 核对 group_by 物化值同值段与预期; 是否 merges/group_merges 同列混用; 残留合并需 lowering 逐行 unmerge(含单格残留); 修 fill_spec.yaml 重编译重执行",
        "RENDER_QA_FAILED": "渲染产物生成失败: png 失败 → 降级 --render html(纯文本模型只做结构渲染检查, 不得声称视觉验证); html 也失败 → 核对 region(plan.render_qa.region)与文件路径",
        "row_anchor_missing": "行号空洞: 目标 sheet row 元素 r 值不连续, officecli `add after/from /row[N]` 锚点不存在 → 用 scripts/repair_row_gaps.py --workdir <dir> 物化缺失行 → 重跑 prepare_run.py --flatten(指纹变化) → 更新 spec 指纹 → 重编译 → 重执行",
        "unknown": "无法自动分类: 读取失败记录原始信息, 参考 references/FAILURE_CLASSES.md 人工判定",
    }
    return fixes.get(cls, fixes["unknown"])
