# 0017 table-fill v3 convergence: Excel compiler, not workflow runtime

## Status

Accepted on 2026-09-02 following the table-fill v3 grilling session (Q1–Q7) over the Egypt ELITE-R410a retrospective (`dsh-clean-8209f807`). Authorizes planning only; implementation proceeds via `.scratch/table-fill-v3-convergence/` tickets. Supersedes parts of ADR 0006 and ADR 0009; extends ADR 0012.

## Context

The Egypt 3-run task delivered correctly (0 execution defects, readback 313/211/211) yet spent 68.6 minutes wall time: ~24 min of pre-draft exploration, ~20 min of post-execute validation tail, machine phase only 170.6 s. Every significant waste item traced to runtime machinery, not to the compiler: semantic_policy authoring + preflight trial loops, ~40 manual `officecli get` re-verifications after machine readback, per-run duplication of MOD resolution/policy/flatten products, and gate ceremony. Grilling Q1–Q7 converged on one boundary correction: **table-fill is a versioned Excel transformation compiler — FillSpec is the IR, compile_fill the compiler, officecli the runtime — not a workflow orchestration engine.** The correct response to a wrong understanding is a cheaper next attempt, not a lifecycle manager for the failed one.

## Decision

1. **Five principles govern v3**: FillSpec First; Compiler Driven (errors resolve through compile feedback, never through state management); Task Is Optimization (Task = shared-context container only); Run Is Disposable (one execution attempt: spec/plan/output/receipt; failure means rerun); Minimum Runtime State.
2. **Eight-phase pipeline**: Workspace Prepare → MOD Selection (Job-level, immediately after Prepare evidence exists, loaded once, only when nomination hits) → FillSpec Authoring (FillSpec First Rule: draft is the mandatory next action after MOD resolution) → Spec Review (the single human point, after FillSpec, reviewing the IR summary, default-on with explicit user opt-out) → Compile → Execute → Verify (machine QA: validate/readback/render/issue-delta/structural) → Deliver (hash-verified copy; no human confirmation).
3. **Delete**: the semantic validation layer (`semantic_policy.json`, `semantic_preflight.py`, `semantic_gate.py`); all Gate machinery (`execution_gate.py`, `gate_task.py`, `task_gate.py`); resume/supersede/checkpoint machinery (`task_resume.py`, `resume_task.py`); the stage scheduler (`task_scheduler.py`); Assembly (`task_assembly.py`, `assemble_task.py`); task-level timing aggregation (`task_timing.py`, `timing_task.py`); probe fixtures from the runtime scripts dir.
4. **Replace with**: Spec Review (human IR checkpoint) + Verify (machine QA) + Deliver (hash-verified copy). Multi-round business changes become file versions (`fill_spec_v2.yaml`, `output_v2.xlsx`) — no Iteration abstraction. Input immutability = Input Manifest hash recorded at Prepare + re-verified before Execute (drift → re-prepare); no artifact registry, no append-only store, no filesystem locks.
5. **FillSpec First Rule replaces phase locks**: exploration is error-driven — a compile defect or an inexpressible transform is the only ticket to targeted lookup; SKILL-level behavioral rule, no physical state machine, no compiler enforcement (consistent with ADR 0012's "text, not a runtime gate").
6. **Task shrinks to a data organization object**: `task.yaml` (run list + references, zero business mapping, zero state) + `context/` (mod_resolution, input_manifest) + `shared/` (staged/flatten/digest, prepared once) + `runs/<id>/` (fill_spec/plan/output/receipt). Single-run jobs are the default entry and never enter Task structure.

## Consequences

- SKILL.md collapses toward a ~300-line five-section form; Validation language becomes `compile → execute → verify`; 14 runtime scripts are deleted; run directories carry ~7 artifact classes instead of ~14.
- Accepted risk: no mechanical backstop for business-rule violations the LLM never notices (CJK leak, wrongly mapped internal field). The control point is Spec Review on the FillSpec IR; if a violation class recurs, the fix belongs in FillSpec/Compiler structural constraints (e.g. `required_empty`), never in a revived validator layer.
- Accepted risk: no resume — a killed execute re-runs from the surviving execution_plan; no aggregated run lifecycle — receipts are per-run file evidence.
- ADR 0009's shared-prepare insight is preserved (task-level flatten cache, 27→7–9 passes); its lifecycle/aggregate-Gate machinery is withdrawn. ADR 0006's host-interaction surface still hosts Spec Review's human decision; its Execution-Gate delivery authorization is withdrawn.

## Alternatives rejected

Physical phase-state locks and compiler-side enforcement of authoring order (Q7); risk-tiered review classifiers (Q5); an artifact registry with append-only store (Q1–Q3); NTFS hardlink materialization (Q1); retaining Assembly for the 1-workbook/N-sheets output shape (Q6); a second business-semantics layer in any form.
