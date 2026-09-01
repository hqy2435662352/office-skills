#!/usr/bin/env python3
"""
scripts/extract_resume_state.py — deterministic Execution State Snapshot (V1.2).

Consumes ONLY the clean representation (clean.jsonl + meta.json) — never the raw
rollout — and emits resume_state.json, the "state layer" of the Agent
Continuation Package:

  handoff/
  ├── meta.json               identity
  ├── clean.jsonl             facts (evidence layer)
  ├── stats.json              cleaning diagnostics
  ├── resume_state.json       state layer     <- this script
  └── artifact_manifest.json  asset layer     <- extract_artifacts.py

  python scripts/extract_resume_state.py --clean out/clean.jsonl \
      --meta out/meta.json --out out/resume_state.json
  python scripts/extract_resume_state.py --clean out/clean.jsonl \
      --meta out/meta.json --out out/resume_state.json --merge agent_state.json

Boundary (kept from V1): this script derives state only from STRUCTURE,
deterministic rules and traceable keyword signals. Completed / in-progress /
pending / blocked items are semantic facts — the script leaves them empty with
"awaiting_agent": true. The reading Agent fills them (optionally via --merge,
which validates every evidence reference against clean.jsonl so nothing can be
invented without a pointer to evidence).

Every inferred field carries a "basis" (event seqs) and a "confidence" so the
Agent can audit the inference. Never a silent guess: unknown stays unknown.

Exit codes: 0 = ok; 1 = usage/input error; 2 = --merge validation failed.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

PHASE_UNKNOWN = "unknown"
PHASE_EXECUTION = "execution"
PHASE_APPROVAL_GATE = "approval_gate"

LIFECYCLE_TURN_END = {"task_complete", "turn_completed"}
LIFECYCLE_ABORT = {"turn_aborted"}

GATE_KEYWORDS = (
    "确认发布", "确认输出", "门禁", "等待确认", "请确认", "请回复", "确认后",
    "publish", "promote", "gate", "approval", "confirmation", "awaiting",
)
VALIDATION_KEYWORDS = ("回读", "验证", "validated", "readback", "issues", "校验", "测试")

USER_GOAL_CAP = 2000  # chars; head+tail truncation with marker


def _trunc(text: str, cap: int, ctx: str) -> str:
    if len(text) <= cap:
        return text
    head = int(cap * 0.6)
    tail = cap - head
    return (text[:head] + f"\n...<TRUNCATED in resume_state ({ctx})>\n" + text[-tail:])


def fail(msg: str, code: int = 1) -> None:
    """Print to stderr and exit with an explicit numeric code."""
    print(f"error: {msg}", file=sys.stderr)
    raise SystemExit(code)


def load_clean(path: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as f:
            for i, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    fail(f"{path} line {i} is not valid JSON; clean.jsonl "
                         "must be produced by preprocess_session.py first")
                if not isinstance(ev, dict) or "seq" not in ev:
                    fail(f"{path} line {i} is not a normalized event; "
                         "clean.jsonl must be produced by preprocess_session.py")
                events.append(ev)
    except FileNotFoundError:
        fail(f"clean file not found: {path}")
    return events


def load_meta(path: Path | None) -> dict[str, Any]:
    if path is None or not path.is_file():
        return {}
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    return data if isinstance(data, dict) else {}


def first_user_goal(events: list[dict[str, Any]], cap: int) -> str | None:
    for ev in events:
        if ev["kind"] == "user" and ev.get("active") is not False:
            return _trunc(ev.get("text", "").strip(), cap, "user_goal")
    return None


def collect_refs(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """High-signal, deterministic evidence references for the reading pass."""
    refs: list[dict[str, Any]] = []
    def want(kind: str, cond=None):
        for ev in reversed(events):
            if ev["kind"] == kind and (cond is None or cond(ev)):
                return ev
        return None

    def add(ev, kind):
        if ev is not None:
            refs.append({"kind": kind, "seq": ev["seq"], "source_line": ev["source_line"]})

    add(want("user", lambda e: e.get("active") is not False), "latest_user")
    add(want("assistant", lambda e: e.get("phase") == "final_answer"), "final_answer")
    add(want("lifecycle"), "last_lifecycle")
    add(want("tool_output"), "last_tool_output")
    add(want("file_change"), "last_file_change")
    add(want("rollback"), "last_rollback")
    add(want("lifecycle", lambda e: e.get("event") in LIFECYCLE_ABORT), "last_abort")
    return refs[:10]


def infer_execution(events: list[dict[str, Any]]) -> dict[str, Any]:
    # last user-turn boundary: lifecycle facts after it describe the ACTIVE turn
    last_user_seq = -1
    for ev in events:
        if ev["kind"] == "user" and ev.get("active") is not False:
            last_user_seq = ev["seq"]
    all_lifecycles = [ev for ev in events if ev["kind"] == "lifecycle"]
    tail_lifecycles = [ev for ev in all_lifecycles if ev["seq"] > last_user_seq]
    last_lifecycle = tail_lifecycles[-1] if tail_lifecycles else (
        all_lifecycles[-1] if all_lifecycles else None)
    abort_count = sum(1 for ev in all_lifecycles if ev.get("event") in LIFECYCLE_ABORT)
    last_rollback = None
    for ev in reversed(events):
        if ev["kind"] == "rollback":
            last_rollback = ev
            break

    # tail text window (final answer + last 5 tool outputs) for keyword scan
    tail_parts: list[str] = []
    for ev in reversed(events):
        if ev["kind"] == "assistant" and ev.get("phase") == "final_answer":
            tail_parts.append(str(ev.get("text", "")))
        elif ev["kind"] == "tool_output":
            tail_parts.append(str(ev.get("output", "")))
        if len(tail_parts) >= 6:
            break
    window = "\n".join(tail_parts)
    has_gate = any(k in window for k in GATE_KEYWORDS)
    has_validation = any(k in window for k in VALIDATION_KEYWORDS)

    exec_state: dict[str, Any] = {
        "phase": PHASE_UNKNOWN,
        "status": None,
        "confidence": 0.2,
        "basis": [],
        "abort_count": abort_count,
        "has_rollback": last_rollback is not None,
        "tail_validation_marker": has_validation,
    }

    rb_seq = last_rollback["seq"] if last_rollback else -1
    lc_seq = last_lifecycle["seq"] if last_lifecycle else -1

    if rb_seq > lc_seq:
        exec_state.update(
            phase=PHASE_EXECUTION, status="rolled_back",
            confidence=0.8,
            basis=[lb["seq"] for lb in events if lb["kind"] == "rollback"][:5],
            note="newest user turns were rolled back; only active:true turns count",
        )
        return exec_state
    if last_lifecycle is None:
        exec_state.update(status="no_lifecycle_evidence", basis=[])
        return exec_state
    if last_lifecycle.get("event") in LIFECYCLE_ABORT:
        exec_state.update(
            phase=PHASE_EXECUTION, status="interrupted",
            confidence=0.8,
            basis=[e["seq"] for e in events
                   if e["kind"] == "lifecycle" and e.get("event") in LIFECYCLE_ABORT],
            note="last turn aborted mid-way; rebind workspace and locate the interrupted step",
        )
        return exec_state

    # task_complete / turn_completed tail
    basis = [last_lifecycle["seq"]]
    last_final = None
    for ev in reversed(events):
        if ev["kind"] == "assistant" and ev.get("phase") == "final_answer":
            last_final = ev
            break
    if last_final:
        basis.append(last_final["seq"])
    basis = list(dict.fromkeys(basis))  # dedupe, keep order
    if has_gate:
        exec_state.update(
            phase=PHASE_APPROVAL_GATE, status="waiting_confirmation",
            confidence=0.85, basis=basis,
            note="session ended at an approval/confirmation boundary; do not auto-cross it",
        )
    elif abort_count > 0:
        exec_state.update(
            phase=PHASE_EXECUTION, status="recovered_after_interruption",
            confidence=0.6, basis=basis,
            note="earlier turns were interrupted but the tail completed normally",
        )
    else:
        exec_state.update(
            phase=PHASE_EXECUTION, status="completed_turn",
            confidence=0.55, basis=basis,
            note="last turn completed normally; validate rebuild needs via workspace check",
        )
    return exec_state


def next_action_for(exec_state: dict[str, Any]) -> dict[str, Any]:
    phase = exec_state["phase"]
    status = exec_state.get("status")
    if phase == PHASE_APPROVAL_GATE:
        return {"action": "ask_user", "target": "confirmation"}
    if status == "interrupted":
        return {"action": "verify", "note": "rebind workspace and locate the interrupted step"}
    if status == "rolled_back":
        return {"action": "recover", "note": "rebuild intent from active:true turns only"}
    if status == "no_lifecycle_evidence":
        return {"action": "verify", "note": "missing lifecycle evidence; inspect clean tail"}
    return {"action": "continue"}


def validate_agent_state(agent: dict[str, Any], events: list[dict[str, Any]],
                         clean_path: Path) -> None:
    """Fail-visible: every evidence ref in the agent-supplied state must exist."""
    seqs = {ev["seq"] for ev in events}
    bad: list[str] = []
    for field in ("completed", "in_progress", "pending", "blocked_by"):
        for item in agent.get(field, []) or []:
            if not isinstance(item, dict):
                continue
            for ref in item.get("evidence_refs", []) or []:
                if isinstance(ref, dict):
                    ref = ref.get("seq")
                if not isinstance(ref, int) or ref not in seqs:
                    bad.append(f"{field}:bad-ref:{ref!r}")
    if bad:
        fail("agent state references events missing from clean.jsonl: "
             + ", ".join(sorted(set(bad))), code=2)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Extract resume_state.json from a preprocessed session.")
    ap.add_argument("--clean", required=True, help="clean.jsonl from preprocess_session.py")
    ap.add_argument("--meta", default=None, help="meta.json (same preprocess output)")
    ap.add_argument("--out", required=True, help="output resume_state.json")
    ap.add_argument("--merge", default=None,
                    help="agent-supplied state file (completed/in_progress/pending/"
                         "blocked_by/next_action) merged after evidence validation")
    args = ap.parse_args(argv)

    clean = Path(args.clean)
    events = load_clean(clean)
    meta = load_meta(Path(args.meta) if args.meta else clean.parent / "meta.json")

    session = {
        "session_id": meta.get("session_id"),
        "cwd": meta.get("cwd"),
        "model": meta.get("model"),
        "model_provider": meta.get("model_provider"),
        "originator": meta.get("originator"),
        "started_at": meta.get("started_at"),
        "last_timestamp": meta.get("last_timestamp"),
        "turn_count": meta.get("turn_count"),
        "source_rollout": meta.get("source_rollout"),
    }
    cwd = meta.get("cwd")
    identity = Path(cwd).name if isinstance(cwd, str) and cwd else (cwd or None)

    exec_state = infer_execution(events)
    next_action = next_action_for(exec_state)

    state: dict[str, Any] = {
        "version": "1.0",
        "session": session,
        "task": {
            "identity": identity,
            "user_goal": first_user_goal(events, USER_GOAL_CAP),
        },
        "execution": exec_state,
        "completed": [],
        "in_progress": [],
        "pending": [],
        "blocked_by": [],
        "next_action": next_action,
        "evidence_refs": collect_refs(events),
        "awaiting_agent": True,
    }

    if args.merge:
        merge_path = Path(args.merge)
        if not merge_path.is_file():
            raise SystemExit(f"error: merge file not found: {merge_path}")
        agent = json.loads(merge_path.read_text(encoding="utf-8"))
        if not isinstance(agent, dict):
            raise SystemExit("error: agent state file must be a JSON object")
        validate_agent_state(agent, events, clean)
        for field in ("completed", "in_progress", "pending", "blocked_by"):
            if field in agent:
                state[field] = agent[field]
        if "next_action" in agent:
            state["next_action"] = agent["next_action"]
        state["awaiting_agent"] = False
        state["agent_merged"] = True

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(state, ensure_ascii=False, indent=2) + "\n")

    ex = state["execution"]
    print(f"phase={ex['phase']} status={ex.get('status')} "
          f"confidence={ex['confidence']} next={state['next_action'].get('action')} "
          + (f"merge=ok" if args.merge else "awaiting_agent=True"))
    print(f"wrote: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())