#!/usr/bin/env python3
"""
scripts/fill_spec_first_validator.py — FillSpec First Rule 行为契约的可执行校验器 (ticket 04).

把「MOD 决议后初稿是强制下一步动作」落成可验收的行为契约, 不依赖编译器强化或
物理锁 (ADR 0017 第 5 条: 行为契约写 SKILL, 无物理锁、「text, not a runtime gate」).
本脚本是这个行为契约的**可执行化**: 给定一条「动作日志」(决议→初稿之间的动作序列),
判断它是否违反 FillSpec First 契约。

契约 (与 SKILL.md「硬约束」节同源):
  - MOD 决议后首个业务动作 = 产出 FillSpec 初稿 (可以不完整)。
  - 未产出初稿禁止深度能力探索; 编译缺陷是下一轮探索的唯一入场券
    (错误驱动, 非探索驱动)。
  - 模式索引路由优先于全文阅读: 已知形态只读对应 pattern。
  - 决议→初稿之间只允许「解除撰写阻塞的最小动作集」: 读 pattern → 读 digest →
    定向读参考小节 / `--capability <key>` → 写 spec → compile (判据优先于计数)。

动作模型 (阶段 = MOD 决议落盘之后的窗口, 直到首次 fill_spec.yaml 写入):
  每个动作是一个字符串, 由「动作类别」归一化:
    - read_pattern   : 读 assets/fillspec_patterns.yaml (模式索引路由) — 合法
    - read_digest    : 读 {name}_digest.md / premod_evidence / outline / MOD 规则 — 合法
    - read_reference : 具名参考文件/小节的定向读取, 或 `--capability <key>` 单键
                       契约查询 (为解除具体阻塞的最小读取) — 合法
    - write_spec     : 写/编辑 fill_spec.yaml — 合法 (首个业务动作的落点)
    - compile        : 运行 compile_fill.py — 合法 (错误驱动的反馈源)
    - explore        : 深度探索 (--probe / --capabilities 全量 dump / 源码阅读 /
                       全文通读 / 机制求证 / 读 case 复盘当证据) — 违规
    - other          : 未识别动作 — 按未知处理 (fail-open 于校验, 但记录)

  判据: 合法与否看「该动作是否解除具体阻塞」, 不看动作数量 (case-009 记录的
  初稿前定向读 FILLSPEC 小节 + combination_patterns 是正确路径)。

校验规则 (纯函数):
  1. 决议后若先出现 explore 动作、且此时尚未 write_spec → 违规
     (探索出现在初稿写入之前)。
  2. 首个 write_spec 之前出现的动作集合必须 ⊆ PRE_DRAFT_ACTION_CLASSES
     (read_pattern / read_digest / read_reference / write_spec / compile)
     — 集合之外的 → 违规。
  3. 合法循环: write_spec 之后, compile (缺陷) → 定向 explore → 修复 → 重 compile
     是合法且预期的 (错误驱动) — explore 出现在首个 write_spec 之后不再判违规。

本脚本同时提供 CLI (读一个 JSON action-log 文件) 与可 import 的纯函数, 供
contract test (tests/test_fillspec_first_contract.py) 断言合法序列通过、非法
序列 (探索在前) 失败。

CLI:
  python scripts/fill_spec_first_validator.py --actions action_log.json

  action_log.json 形如:
    {"actions": ["read_pattern", "read_digest", "write_spec", "compile"]}

  exit 0 = 合法 (契约通过); exit 3 = 违规 (打印结构化 violation);
  exit 1 = 输入非法 (文件缺失/JSON 畸形/未知动作)。

Exit codes: 0=pass, 1=fatal, 3=violation (retryable 语义在此借用: 违规需修动作日志)。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

LEGAL_PRE_DRAFT_ACTIONS = frozenset(
    {"read_pattern", "read_digest", "read_reference", "write_spec", "compile"}
)
# 决议→初稿窗口的合法动作集: 判据是"解除撰写阻塞的最小读取", 不是动作数量。
PRE_DRAFT_ACTION_CLASSES = ("read_pattern", "read_digest", "read_reference",
                            "write_spec", "compile")

# 深度能力探索动作 (FillSpec First Rule 禁止在初稿前出现)。
EXPLORE_ACTIONS = frozenset({"explore"})

# 未知动作: 校验 fail-open (不判违规) 但 CLI 侧标记为 unknown。
KNOWN_ACTIONS = LEGAL_PRE_DRAFT_ACTIONS | EXPLORE_ACTIONS | frozenset({"other"})


def normalize_action(raw: str) -> str:
    """把自由文本动作归一化为契约动作类别 (宽容匹配, Agent 动作日志可读)。

    匹配顺序按确定性区分度排列: 越具体的动作越先匹配。"""
    s = (raw or "").strip().lower()
    if s in KNOWN_ACTIONS:
        return s

    # 1. 编译 (最具体): 含 compile 词 → compile
    if "compile" in s:
        return "compile"

    # 2. 写 spec (含写/编辑语义): 先于读判断, 避免「write spec」误判为读
    if "spec" in s and any(k in s for k in ("write", "edit", "author", "write", "写", "落盘", "产出")):
        return "write_spec"

    # 3. 模式索引 (读 pattern): 明确指向 fillspec_patterns.yaml / pattern
    if "pattern" in s and any(k in s for k in ("pattern", "index", "read", "读", "fillspec_pattern")):
        return "read_pattern"
    if "fillspec_pattern" in s:
        return "read_pattern"

    # 4. 全文 / 源码 / 机制阅读 (深度能力探索): 含全文/源码/机制/完整 读 → explore
    if any(k in s for k in ("全文", "完整", "full", "源码", "source code",
                            "source-code", "机制求证", "mechanism", "implement")):
        return "explore"

    # 5. 探测与全量能力 dump = 深度探索; 单键定向查询 = 最小读取.
    if "probe" in s:
        return "explore"
    if "--capabilities" in s:
        return "explore"
    if any(k in s for k in ("--capability", "capability query", "能力查询")):
        return "read_reference"

    # 6. 读 digest / 证据 (读 digest): digest / premod / outline / mod 规则
    if any(k in s for k in ("digest", "premod", "outline", "mod")):
        return "read_digest"

    # 7. 具名参考文件 / 小节的定向读取 → read_reference (最小读取, 合法).
    #    全文与源码已在规则 4 拦截, 此处只兜定向读取.
    if any(k in s for k in ("fillspec", "known_traps", "failure_classes",
                            "officecli help", "参考小节", "reference")):
        return "read_reference"

    return "other"


def validate_actions(actions: list[str]) -> dict:
    """FillSpec First 行为契约的纯函数校验器。

    返回 {'ok': bool, 'violations': [str], 'first_draft_at': int|None}:
      first_draft_at = 首个 write_spec 动作的索引 (0-based), 无则 None。
    """
    violations: list[str] = []
    first_draft_at: int | None = None
    norm = [normalize_action(a) for a in actions]

    for i, act in enumerate(norm):
        if act == "write_spec" and first_draft_at is None:
            first_draft_at = i

    for i, act in enumerate(norm):
        # 初稿写入之前: 只允许合法动作集 (判据: 是否解除具体阻塞).
        if first_draft_at is None or i < first_draft_at:
            if act == "explore":
                violations.append(
                    f"动作 {i} ({actions[i]!r}) 是深度能力探索, 但出现在 "
                    "初稿写入之前 — FillSpec First Rule 违规 (未产出初稿禁探索)。"
                )
            elif act not in LEGAL_PRE_DRAFT_ACTIONS:
                violations.append(
                    f"动作 {i} ({actions[i]!r}) 不属于决议→初稿窗口的合法动作集 "
                    f"(读 pattern / 读 digest / 定向读参考小节 / 写 spec / "
                    f"compile)。"
                )

    return {"ok": not violations, "violations": violations,
            "first_draft_at": first_draft_at, "normalized": norm}


def _load_actions(path: Path) -> list[str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    actions = data.get("actions")
    if not isinstance(actions, list) or not all(isinstance(a, str) for a in actions):
        raise ValueError("action log 必须含 'actions' 字符串数组")
    return actions


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="FillSpec First Rule 行为契约校验器")
    ap.add_argument("--actions", required=True,
                    help="action log JSON 文件 ({'actions': [...]})")
    args = ap.parse_args(argv)

    try:
        actions = _load_actions(Path(args.actions))
    except (OSError, ValueError, json.JSONDecodeError) as e:
        print(json.dumps({"status": "ERROR", "code": "INVALID_ACTION_LOG",
                          "message": str(e)}, ensure_ascii=False))
        return 1

    unknown = [a for a in actions if normalize_action(a) == "other"]
    result = validate_actions(actions)

    if result["ok"]:
        print(json.dumps({
            "status": "PASS", "code": "FILLSPEC_FIRST_OK",
            "first_draft_at": result["first_draft_at"],
            "normalized": result["normalized"],
            "unknown_actions": unknown,
        }, ensure_ascii=False))
        return 0

    print(json.dumps({
        "status": "ERROR", "code": "FILLSPEC_FIRST_VIOLATION",
        "violations": result["violations"],
        "first_draft_at": result["first_draft_at"],
        "unknown_actions": unknown,
    }, ensure_ascii=False))
    return 3


if __name__ == "__main__":
    sys.exit(main())
