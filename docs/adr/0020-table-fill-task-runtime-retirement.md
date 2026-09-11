# 0020 table-fill task runtime retirement

## Status

Accepted on 2026-09-09 following the table-fill grilling session (Q6, Q8). Retires the Task-level execution state machine and lifecycle orchestration identified in ADR 0017 §4; extends ADR 0018's materialization model.

## Context

Inspection found the Task layer retained two incompatible identities: the container semantics (ADR 0017: task.yaml + runs/, optimization only) and a vestigial runtime engine (`task_prepare.py`'s RUN_STAGES = (source_prepare, run_prepare, compile, execute, deliver), stage workers, `task_status.json` single-writer lifecycle, and compile/execute/deliver stage orchestration; `prepare_task.py`'s Task Bootstrap / Shared Prepare / Adopt Discovery entry). The runtime engine also contradicted the review contract: RUN_STAGES has no review stage, so the Task path could legally skip the human touchpoint while the SKILL claimed review was mandatory. `assemble_run_manifest()` (the run-view assembly function materialization needs) lives inside `task_prepare.py`, tying the pure function to the state machine.

## Decision

1. **Retire the Task runtime state machine**: `task_prepare.py` loses RUN_STAGES, `run_stage`, stage workers, `task_status.json` lifecycle, and its compile/execute/deliver orchestration. It remains temporarily in place as a pure-function host for `assemble_run_manifest()` and other helpers materialization imports — module names are accepted technical debt; no helper migration or module reorganization in this round.
2. **Retire `prepare_task.py` entirely**: Task Bootstrap / Shared Prepare / shared-cache orchestration / Adopt Discovery are not part of the new architecture. `task.yaml` static validation stays with `task_schema.py` (already in use).
3. **`prepare_run.py` loses its runtime CLI role** (no more outline-only / flatten two-stage entry, no longer a documented compatibility path); its pure functions reused by other scripts (`facts_sha256`, `structure_facts`, `ascii_slug`, `_entry_for`, `flatten_pptx_table`, `collect_style_granularity`, …) stay in place for now. Same no-migration rule.
4. **`task_status.json` / `task_manifest.json`**: no external read-only consumer exists (verified: prepare_task/task_schema/tests only) — delete both artifacts from the canonical flow; nothing may drive stage progression from them. If any transient display consumer appears, it is a derived display artifact at most, never an execution truth source.
5. **Text mechanisms retired together**: TOPOLOGY_BARRIER, AUTHORING_READY (PLANNING → AUTHORING_READY → FIRST_DRAFT → FIRST_COMPILE second state machine), Adopt Discovery, ADOPT four conditions, Task Bootstrap, Shared Prepare, "五个公开命令" miscount, duplicate Deliver §6/§7. The SKILL top-level control plane (S0–S8) is the only execution-order authority; later sections may explain gates/exceptions/syntax but must never redefine stage order.

## Pin reversal (Q8)

Retire text-presence tests that pin deleted runtime mechanisms. Replace them with one canonical control-plane contract test containing:

- negative tombstones (`assertNotIn`) for explicitly retired mechanisms: TOPOLOGY_BARRIER, AUTHORING_READY, Adopt Discovery, Task Bootstrap, Shared Prepare, 五个公开命令, Review-before-Compile wording — plus forbidden artifact/mechanism names (`run_definition.json`, task_status-driven progression, incremental flatten, workspace refresh, target role in workspace_manifest). The negative list stays small — only artifacts already adjudicated as forbidden.
- positive assertions for stable runtime stage names and critical ordering semantics (Workspace before Topology · Compile before Review · Review before Execute; stage names Workspace Init / Topology / Task Shape / MOD Resolution / FillSpec / Compile / Spec Review / Execute / Deliver).
- **no pinning of large prose blocks or incidental stage numbering** (S0/S1 are navigation aids, not contract).

Four duplicated AUTHORING_READY/SOP text-pin files (`test_fillspec_pattern_index.py`, `test_task_public_contract.py`, `test_task_topology.py`, `test_topology_barrier.py` — same content, mostly mismatched filenames) collapse into one canonical control-plane contract test; `test_single_run_isolation.py` keeps its behavioral assertions (no Task runtime pollution) and drops its mechanism-presence pins. The 15+ e2e tests that currently drive preparation via `prepare_run.py --outline/--flatten --target` migrate their fixture driver to `workspace_init --init` + `materialize_run`, **keeping their domain assertions unchanged** — driver swap only, no assertion reformatting, no fixture renaming, no test-reorg, to keep failures attributable to the architecture change.

## Consequences

- `materialize_run.py` (ADR 0018) imports `assemble_run_manifest()` from the stripped `task_prepare.py`; single-run and multi-run both use it. No run definition file is introduced; no scheduler/status/retry mechanism is re-added.
- Agent-facing commands become exactly the seven public commands: workspace_init / materialize_run / mod_nominate / spec_review / compile_fill / execute_batch / promote_output.
- Task is now purely a multi-run topology/container abstraction; its execute serial discipline (single Office resident window) remains as documented run behavior, not engine state.