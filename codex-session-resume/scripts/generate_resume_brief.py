#!/usr/bin/env python3
"""
scripts/generate_resume_brief.py — resume_brief.md: the handoff start page (P0, V1.3).

A deterministic, mostly-small-model friendly landing page for the Agent taking
over the task. NOT a summary: it renders the machine layer (resume_state.json),
the optional agent layer (agent_state.json), and the asset layer
(artifact_manifest.json) into an imperative "what / do / don't" brief.

  python scripts/generate_resume_brief.py \
      --resume-state out/resume_state.json \
      --agent-state out/agent_state.json \
      --manifest out/artifact_manifest.json \
      --meta out/meta.json \
      --out out/resume_brief.md

Size control: hard caps on every section (user goal ~180 chars, completed/pending
<= 8 items, required-missing list <= 6 paths) so a cheap model can consume it.
No LLM, no clock, no randomness: same inputs -> byte-identical brief.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

GOAL_CHARS = 180
ITEM_CAP = 8
MISSING_CAP = 6

PHASE_ZH = {
    "approval_gate": "等待用户确认（审批门禁，尚未跨越）",
    "execution": "执行中/恢复中",
    "unknown": "状态证据不足",
}
STATUS_ZH = {
    "waiting_confirmation": "工作已就绪，等待确认类回复后才继续（不得自动执行发布类动作）",
    "interrupted": "上一轮被中断，需先定位断点再继续",
    "rolled_back": "最近 N 个用户轮已回滚失效，只能以 active:true 的用户要求为准",
    "recovered_after_interruption": "中途曾被打断，但最后一轮正常完成；关键产物建议复核",
    "completed_turn": "最后一轮正常结束",
    "no_lifecycle_evidence": "缺少生命周期证据，先看 clean.jsonl 尾部再决定",
}


def _trunc(text: str, cap: int) -> str:
    text = (text or "").strip()
    if len(text) <= cap:
        return text
    return text[:cap] + "…"


def _load(path: Path | None, required: bool) -> dict[str, Any]:
    if path is None or not path.is_file():
        if required:
            print(f"error: required file not found: {path}", file=sys.stderr)
            raise SystemExit(1)
        return {}
    with path.open("r", encoding="utf-8") as f:
        d = json.load(f)
    return d if isinstance(d, dict) else {}


def _do_not_lines(state: dict[str, Any], agent: dict[str, Any],
                  manifest: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    ex = state.get("execution", {})
    phase = ex.get("phase")
    status = ex.get("status")
    if phase == "approval_gate" or status == "waiting_confirmation":
        lines.append("不要自动执行发布/外发/promote/部署 —— 必须先等用户在本轮明确确认")
        if ex.get("tail_validation_marker"):
            lines.append("草稿已有验证证据（如 readback/validated），不要无依据地重新生成")
    if status == "interrupted":
        lines.append("不要直接继续 —— 先定位中断点（最后一条 tool evidence），验证后再继续")
    if status == "rolled_back" or ex.get("has_rollback"):
        lines.append("不要使用 active:false 的用户轮 —— 从 active:true 的轮次重建当前要求")
    stats = manifest.get("stats", {})
    if stats.get("required_missing", 0) > 0:
        lines.append("不要假设任务文件存在 —— 先 Rebind（branch/cwd/是否移动）；"
                     "required 缺失项需恢复或重建，重建后重新验证并重新走 gate")
    if not lines:
        lines.append("核对资产与 Gate 后再继续（无自动动作）")
    return lines


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Generate resume_brief.md from the handoff package.")
    ap.add_argument("--resume-state", required=True)
    ap.add_argument("--agent-state", default=None)
    ap.add_argument("--manifest", default=None)
    ap.add_argument("--meta", default=None)
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)

    state = _load(Path(args.resume_state), required=True)
    agent = _load(Path(args.agent_state), required=False)
    manifest = _load(Path(args.manifest), required=False)
    meta = _load(Path(args.meta), required=False)

    task = state.get("task", {})
    ex = state.get("execution", {})
    next_action = agent.get("next_action") or state.get("next_action", {})
    action_zh = {
        "continue": "直接继续下一步",
        "ask_user": f"询问用户（目标：{next_action.get('target', '确认')}）",
        "verify": "先验证（Rebind + 证据复核）",
        "recover": "恢复（仅按 active 轮重建）",
        "stop": "停止",
    }.get(next_action.get("action"), str(next_action.get("action")))

    body: list[str] = []
    body.append("# Resume Brief")
    body.append("")
    body.append(f"**任务 Task:** {task.get('identity') or '(未知)'}")
    goal = _trunc(task.get("user_goal"), GOAL_CHARS)
    body.append(f"**原始目标:** {goal or '(无)'}")
    body.append("")
    body.append("## 当前状态")
    phase = ex.get("phase")
    status = ex.get("status")
    body.append(f"- phase: `{phase}` / status: `{status}` — "
                f"{PHASE_ZH.get(phase, '')}{STATUS_ZH.get(status, '')}")
    extra = []
    if ex.get("abort_count"):
        extra.append(f"会话中途中断 {ex['abort_count']} 次")
    if ex.get("has_rollback"):
        extra.append("存在 rollback")
    if ex.get("turn_count") is None and meta.get("turn_count") is not None:
        extra.append(f"{meta['turn_count']} 轮")
    if extra:
        body.append(f"- 事实: {'；'.join(extra)}")
    body.append(f"- **下一步:** {action_zh}")
    body.append("")

    completed = agent.get("completed") or []
    pending = agent.get("pending") or []
    body.append("## 已完成（Agent 解读；未解读时见证据入口）")
    if completed:
        for item in completed[:ITEM_CAP]:
            if isinstance(item, dict):
                refs = item.get("evidence_refs")
                refs_txt = f" [evidence: {refs}]" if refs else ""
                body.append(f"- {item.get('item') or item.get('action')} "
                            f"({item.get('status', '')}){refs_txt}")
        if len(completed) > ITEM_CAP:
            body.append(f"- …另有 {len(completed) - ITEM_CAP} 项（见 agent_state.json）")
    else:
        body.append("- （待 Agent 解读；证据入口：resume_state.json → evidence_refs → "
                    "clean.jsonl → source_line）")
    body.append("")
    body.append("## 待办 / 阻塞")
    if pending:
        for item in pending[:ITEM_CAP]:
            if isinstance(item, dict):
                body.append(f"- {item.get('action') or item.get('item')} "
                            f"[{item.get('type', '')}]")
    elif agent:
        body.append("- 见 agent_state.json（pending 数组为空）")
    body.append(f"- 下一步动作: `{next_action.get('action')}`")
    body.append("")

    body.append("## 不要（Do not）")
    for line in _do_not_lines(state, agent, manifest):
        body.append(f"- {line}")
    body.append("")

    stats = manifest.get("stats", {})
    body.append("## 资产（artifact_manifest）")
    body.append(f"- 共 {stats.get('total', 0)} 项：required={stats['importance']['required']} "
                f"optional={stats['importance']['optional']} "
                f"historical={stats['importance']['historical']} "
                f"ephemeral={stats['importance']['ephemeral']}")
    body.append(f"- 磁盘存在 {stats.get('exists_on_disk', 0)} / 缺失 "
                f"{stats.get('missing_on_disk', 0)}；"
                f"**required 缺失 {stats.get('required_missing', 0)}** / "
                f"required 存在 {stats.get('required_exists', 0)}")
    if stats.get("required_missing", 0):
        body.append("- 缺失的 required 资产（先 rebind 再决定恢复）：")
        shown = 0
        for a in manifest.get("artifacts", []):
            if a.get("importance") == "required" and not a.get("verification", {}).get("exists"):
                body.append(f"  - `{a['path']}`")
                shown += 1
                if shown >= MISSING_CAP:
                    body.append(f"  - …另有 {stats['required_missing'] - shown} 项")
                    break
    body.append("")
    body.append("## 证据入口")
    body.append("- `resume_state.json`（机器层） / `agent_state.json`（解读层）")
    body.append("- `clean.jsonl`：对任何结论存疑时按 evidence_refs 的 seq/source_line 核验")
    body.append("- 极端取证：按 source_line 定点读 raw rollout，不全文加载")

    brief = "\n".join(body) + "\n"
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8", newline="\n") as f:
        f.write(brief)

    print(f"brief chars={len(brief)} (~{len(brief) // 2} tokens for Chinese)")
    print(f"wrote: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())