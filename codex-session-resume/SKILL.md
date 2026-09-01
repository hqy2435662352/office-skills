---
name: codex-session-resume
description: >
  Resume a previous Codex session's task state from its local rollout-*.jsonl
  archive and CONTINUE the work — restore the current goal, verified progress and
  the exact resume point via deterministic preprocessing + agent reading, then
  rebind the current workspace and keep executing. Use this skill whenever the
  user asks to continue an earlier Codex conversation or task, e.g. "继续刚才那个
  任务", "继续昨天的 table-fill 开发", "继续这个 session", "继续 session
  01a051c7-...", "读取这个 rollout jsonl 继续做", "接着上一个 Codex 对话做",
  "resume the codex session", "pick up where we left off", or hands you a
  rollout-*.jsonl / codex session id and expects ongoing work. Also trigger on
  vague continuation phrases ("接着做", "继续上一次", "上次做到哪了，继续") when
  a Codex session archive is the likely source. Do NOT use for: summarizing a
  past conversation, migrating a thread to a new provider, or restarting a task
  from scratch — this skill continues tasks, it does not replay conversations
  or treat the old rollout as current truth.
license: MIT
compatibility: >
  Python 3.10+ (stdlib only; no third-party dependencies), PowerShell or POSIX
  shell. Primary scenario is Windows Codex Desktop. The old rollout is read-only.
---

# Codex Session Resume

## Purpose

- The old rollout is **historical evidence** of what happened. The current
  workspace is **current truth**. On conflict: **workspace wins**.
- This skill continues tasks. It does not summarize conversations, and it does
  not migrate threads between providers, and it does not restart from scratch.
- Answer only: where did I stop / can I continue / what assets do I need / what
  is the next action.

## Workflow

1. **Locate the session** — explicit rollout path > exact session id > user's
   time hint > cwd/project match > user-message semantics. Build a session
   index once: `scripts/index_sessions.py --root <~/.codex/sessions>
   --root <~/.codex/archived_sessions> --out session-index.json`. Pick by hard
   filters first (time, cwd), then read `user_messages` and choose semantically.
   One clear winner → proceed. Truly ambiguous (≥2 plausible) → ask with 2-3
   named candidates.
2. **Build the handoff package** (rollout stays read-only; one output dir):
   ```bash
   python scripts/preprocess_session.py --input <rollout.jsonl> --output <out>
   python scripts/extract_resume_state.py --clean <out>/clean.jsonl \
       --meta <out>/meta.json --out <out>/resume_state.json
   python scripts/extract_artifacts.py --clean <out>/clean.jsonl \
       --out <out>/artifact_manifest.json
   ```
3. **Restore state** — reading order below.
4. **Rebind the workspace** — see Rebind.
5. **Continue** — see Continue.

## Reading order

1. `resume_state.json` — state snapshot (phase, next action, evidence refs).
2. `artifact_manifest.json` — required assets + `exists` per path.
3. `meta.json` + `stats.json` — identity and cleaning health; unknown /
   malformed counts must be checked, never ignored.
4. **All `user` messages with `active: true`** — initial goal → scope changes →
   corrections → current goal. Later explicit user decisions override earlier
   ones. `active: false` user turns were rolled back: exclude from the goal.
5. Tail evidence — last lifecycle (`task_complete` = finished normally,
   `turn_aborted` = interrupted), last `final_answer`, last tool evidence.
   `clean.jsonl` is the evidence layer: consult it whenever a state claim is
   uncertain. Read raw rollout ONLY for targeted backfill via `source_line`,
   never the whole file.

## State semantics

- `resume_state.json` phase/status are rule-based inferences with `confidence`
  and `basis` — recheck before trusting them. `completed/in_progress/pending`
  are filled by YOU (the script leaves them empty, `awaiting_agent: true`):
  fill them, then merge back with `--merge agent_state.json` (invalid evidence
  refs are rejected).
- Evidence hierarchy: A) workspace/tool/test/git/validation output > B) user
  decisions > C) assistant claims > D) assistant plans. **D never proves
  completion; C cannot override A.**
- Classify items as: verified completed / completed-but-unverified / in
  progress / pending (planned only). Resume mistakes come from treating
  "planned" as "done".
- Approval gate: a session stopped at a confirmation boundary must stay there.

## Rebind

- Check cwd, branch, `git status`, `git diff --stat`, then the artifacts the
  resume point depends on (manifest `exists` flags are pre-computed).
- Missing files are NOT failure: wrong branch / moved files / wrong session
  are likelier. Rebind before rebuilding anything.
- Workspace newer than the session (committed, changed, another agent) →
  never overwrite; recompute the resume point.
- Re-run tests only when the session stopped before tests, the workspace
  changed, the next step depends on old validation, or acceptance needs a
  fresh run. Do not re-run for ritual.

## Continue

- If the next action is clear and no special gate is involved: **do it**.
  Do not present a summary and ask "continue?".
- Do NOT auto-cross approval gates: publish / promote / deploy / release /
  external send / destructive or irreversible actions require fresh user
  confirmation in THIS conversation. Old-session approval is not a license.
- Last turn interrupted (`turn_aborted` tail): locate the interrupted step,
  verify, then resume.
- After any rebuild (files regenerated, hashes changed): fresh verification
  and a fresh gate.

## Safety boundaries

- Rollout and everything under `~/.codex`: read-only. Never touch SQLite,
  session/provider metadata, or codex threads.
- Never silently drop unknown schema — unknown records stay visible
  (`unknown` events + counts).
- Do not use vector search or LLM summaries as a source of truth; use the
  evidence stream.
- Handoff *documents* are not required; the package above is the deliverable.

## When NOT to use

Summarizing a conversation, migrating a thread/provider, restarting from
scratch, or inspecting rollout files without resuming a task.

## Details

Live outside this file: `references/schema.md` (event/state/manifest schemas),
`references/parser.md` (structural rules), `references/architecture.md`
(rationale, history, discovery), `references/safety.md` (expanded boundaries).