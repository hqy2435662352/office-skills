# Task Orchestration Layer for table-fill

## Status

Accepted on 2026-08-24. This ADR records the Q2 (Task Layer boundary) decision of
the task-orchestration grilling session; it authorizes the Task Orchestration
Layer implemented by the `table-fill` Task Orchestration work (spec under
`.scratch/table-fill-task-orchestration/spec.md`) and detailed in
`table-fill/references/TASK_ORCHESTRATION.md`. This decision is
hard-to-reverse and is documented now, not retrofitted.

## Context

The 2026-08-22 Egypt parameter-sheet batch retrospective exposed three
structural defects of table-fill in the "one task, many runs" scenario:

1. **Repeated preparation (D1)**: one task contained 13 product-line runs
   sharing the same source workbook and multiple parameter sheets, but each run
   had its own workdir and re-flattened independently — 27 flatten passes
   totaling 38.1 minutes, about 82% of the measurable machine time. The same
   R32/R410A parameter pages were scanned over and over.
2. **No batch orchestration (D2)**: table-fill defines "one run = one source to
   target mapping". Shared preparation, batch spec generation, aggregated Gate,
   and progress reporting all relied on agent-written throwaway scripts
   (`generate_batch_fillspecs.py` / `prepare_batch_runs.py`), which were
   unreusable and unverifiable.
3. **Ungoverned run lifecycle (D11)**: 25 preparation directories produced only
   13 final valid runs; four generations (`runs` / `runs_heating` /
   `runs_heating_v2` / `runs_header_v2`) coexisted, abandoned runs mixed with
   live runs, and timing attribution required manual post-hoc aggregation.

The design tree (Q2 Task Layer boundary → Q3 three-file model → Q4 task-local
cache → Q5 Prepare Ownership → Q6 materialized consumption → Q7 staged
scheduling → Q8 lifecycle/resume/supersede → Q9 three-layer verification → Q10
documentation) converged in the grilling session into this spec; this ADR fixes
the Q2 architectural boundary.

## Decision

Introduce a **Task Orchestration Layer** as a first-class orchestration
container in table-fill, without changing any Run Layer contract:

- **Task Artifact Model**: a canonical `task.yaml` (Task Definition: task id,
  project metadata, run list with input/output references and output naming) +
  derived `task_manifest.json` (Prepare Snapshot: staged files with SHA-256,
  outlines, flatten cache references, fingerprints) + derived `task_status.json`
  (runtime state index per run), mirroring the existing
  canonical/derived-separated philosophy.
- **Task-local Flatten Cache**: shared source preparation hoisted to task
  level under `<task_root>/cache/`, keyed by hash of (staged source hash, sheet
  name, flatten schema version, officecli version); eager pre-flatten once per
  unique (file, sheet); cache products are materialized (byte-for-byte copied)
  into each run's workspace before compile so run artifacts stay
  self-contained. Cache Identity (reusability) is distinct from Run Artifact
  Identity (what this run actually consumed).
- **Barrier-based staged scheduling**: stage-internal parallelism, stage
  barriers, no cross-stage pipeline, no DAG, no worker pool; concurrency
  defaults are implementation constants (2/2/4/2/1/2), never task.yaml fields,
  never CLI-tunable. `task_status.json` has a single writer: workers report
  results and the orchestrator updates state once at stage boundaries.
- **Run lifecycle governance**: planned → prepared → compiled → drafted →
  gated → promoted, with superseded as an evidence-preserving terminal branch
  (run-level, `superseded_by` linking the replacement version). Status is an
  index, not truth: resume determines checkpoints by artifact existence + hash
  verification, covering the execute crash window (draft exists but receipt
  missing → re-run execute). Failure dichotomy: input facts unchanged → stage
  retry or REPAIR; input facts changed → supersede the run, never keep patching
  the old one. `resume_task.py` is the sole recovery/supersede entry and never
  auto-skips the Gate or auto-promotes.
- **Aggregate Gate**: `gate_task.py --set` presents one `gate_summary.json`
  for one human interaction; `--confirm` expands confirmation per run with
  unchanged hash-trio binding and fail-closed semantics, then promotes with
  unchanged HASH_DRIFT rejection.
- **Timing dual columns**: task aggregate reports split active cost (this
  delivery) from superseded cost (avoidable waste), quantifying the
  optimization value; superseded evidence is never deleted.
- **Documentation separation**: SKILL.md gets only an entry section;
  `references/TASK_ORCHESTRATION.md` is the single detailed contract source;
  KNOWN_TRAPS collects task-layer failure modes; CONTEXT.md collects terms;
  this ADR records the decision; FAILURE_CLASSES.md is not modified.

Two layers connect through manifest and artifact references, never through a
shared spec. `fill_spec.yaml` remains the only business-facts source; the
Compiler/Executor/Gate/Promote are unaware of tasks, and the existing single-run
scripts are unchanged (task scripts call them as subprocesses).

## Non-goals

- **Not a replacement of the Run Layer**: the Run Layer keeps all business
  fact, compile, execute, and verification responsibilities; the Task Layer
  owns lifecycle, shared preparation, and aggregate interaction only.
- **Not a modification of Compiler/Executor/Gate/Promote**: no interface
  semantics of the existing single-run scripts change.
- **Not a workflow engine**: no DAG, no pipeline, no worker pool, no task
  database (SQLite/state DB), no external scheduler.
- **Not a second business-semantics layer**: `task.yaml` carries orchestration
  only; mapping/lookup/transform/formula/validation rules never enter it.
- **Not a cache system**: no global cache, no LRU, no TTL, no eviction, no
  shared cache flag; the cache only accelerates Prepare and never changes
  hashing/fingerprint/compile-binding semantics.
- **Not a clock**: no wall-clock SLA; verification is structural (flatten
  count == unique demand count), not timing-based.

## Consequences

Positive:

- Repeated preparation cost is decoupled from run count: unique (file, sheet)
  pairs flatten once per task (Egypt's 27 passes compress to the 7–9 unique
  demands), with cache hits causing zero officecli calls on re-runs.
- One aggregate Gate interaction collapses the confirmation loop to a single
  human decision (per-run `gate_summary` presentation) while per-run
  authorization granularity (hash-trio binding per run) is fully preserved.
- Run lifecycle is governed: superseded runs keep all artifacts as evidence
  with `superseded_by` links; interruption recovery is deterministic via
  artifact-verified checkpoints.
- Optimization value becomes measurable: the superseded-cost column quantifies
  avoided waste; run manifests remain the single compile-facing input
  interface (compile cannot tell task artifacts from single-run artifacts).

Negative:

- A new task-layer artifact surface (task.yaml/manifest/status, cache,
  gate_summary, outputs) with its own consistency invariants: frozen manifest,
  single writer for status, artifact self-containment, no reference paths into
  the cache.
- Agent authoring overhead: a `task.yaml` must be written and kept in sync
  with input-fact changes through the supersede path; the Agent owns the
  canonical file.
- Office concurrency remains an environment-bound empirical limit (execute and
  flatten at 2, from a demonstrated 3-concurrency `validate_state=fail` on
  Windows resident processes); this layer governs around it, it does not
  remove it.

## Alternatives Rejected

### Agent-scripted batch orchestration (status quo)

Rejected: ad-hoc `generate_batch_fillspecs.py`/`prepare_batch_runs.py` style
scripts are unreusable, unverifiable, and carried no lifecycle or Gate
discipline — this is the defect being fixed, not a design candidate.

### `task_spec` + run_override three-layer business override

Rejected: layering business mappings on top of per-run specs creates a second
semantics surface that diverges from fill_spec.yaml; business facts belong in
exactly one place, the run's fill_spec.

### Task database / DAG engine / worker pool

Rejected: a state database and a scheduling engine contradict the
file-as-state philosophy of table-fill and add a runtime dependency; barrier
stages plus a single-writer status file provide the auditability needed
without new infrastructure.

### Global shared flatten cache

Rejected for this iteration: cache lifespan must bind to the task root (task
archive = cache archive); a global cache brings eviction, staleness, and
identity questions with no current user. The key design (no task identity in
the key) keeps a future global upgrade migration-free.