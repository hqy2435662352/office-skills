# 0012-table-fill-first-draft-convergence

# FillSpec 首版收敛：行为规则与 Compiler 复杂度优先于运行时强制

## Status

Accepted on 2026-08-28 following a grilling session over the eg_fresh_local
retrospective and a reviewer revision pass. This ADR authorizes
implementation of Tickets 01–04 in `.scratch/tablefill-fillspec-convergence/`
within the defined scope; Deferred items remain rejected-for-now, not
committed work.

## Context

The eg_fresh_local run (毛利表 → 报价汇总 `11_FRESH本土` 双块追加) delivered a
correct result but spent ≈66 minutes of agent-effective time, ≈63 of them
before the first `fill_spec.yaml` was written; FillSpec → Compile → Execute →
Gate took ≈3.5 minutes, and the final compiler needed only 3 cheap rounds
(one crash-class input, one clone-residue miss, then success). After the
canonical pattern `multiproduct_block_append` matched exactly (11:33), the
agent still drifted ≈43 minutes re-deriving mapping/routing/inheritance —
including re-reading case reviews the pattern note itself pointed to, two
manifest pollutions from flattening inheritance sheets, and one redundant
ASK (D/F/X inherit-vs-null) that FLD-008 had already decided. A first
remediation draft proposed a full guard-layer (commit state, exploration
budgets, ASK preemption runtime, manifest namespaces, telemetry). It was
deliberately contracted: the existing main flow is designed correctly — the
failure was that existing convergence rules sat in the wrong section (the
canonical-pattern stop-rule lived in §4 compile-repair context, never
applicable before the first compile), plus two genuine contract gaps and one
crash-class compiler input.

## Decision

Fix with four low-cost, near-zero happy-path increments — behavior text where
the drift happens, one authoritative contract, one compiler defect code — and
add no new states, gates, budgets, or runtime enforcement:

1. SKILL §3「首版收敛原则」: first FillSpec is the next primary artifact
   after MOD Resolution; only blocking unknowns (target/shape/authority-less
   semantics) may delay the first compile; Compiler-detectable issues are
   never blocking; decided questions are not reopened; a small conflict-
   resolution box separates business-semantics authority (user instruction >
   selected MOD > pattern defaults) from facts (evidence for structure and
   premises, never an authority that overrides business decisions), with the
   Compiler authoritative for structural legality.
2. Canonical patterns become self-contained instantiation skeletons: only
   normative pointers (FILLSPEC Q#/sections, KNOWN_TRAPS entries) survive;
   case numbers, test-case paths, issue numbers, dates, contract-test names,
   and provenance sentences are removed. A pattern's authority is its
   contract status, not its résumé.
3. Usage-role contract: manifest membership is decided by fill-source use,
   never by lookup need — do not flatten a sheet solely because it is needed
   for lookup; a sheet consumed as a fill source belongs to the fill
   manifest, and one sheet may serve both uses without its manifest identity
   changing. One authoritative definition in FILLSPEC.md; one in-place
   reminder at the flatten call site; the tool's own --help carries the
   happy path.
4. `LOOKUP_KEY_COLUMN_INVALID`: static compiler defect (invalid_format /
   out_of_range, both entry points, no logical-name coercion) — the machine
   judges what the machine can judge.

Escalation options (commit state, exploration budgets, manifest freeze /
lookup namespace, prepare_run guard, ASK preemption runtime) are recorded as
rejected-for-now in the spec's Deferred section — Deferred does not imply
planned work. Re-trigger only if the same failure
class recurs across multiple independent real tasks after these fixes and no
smaller contract clarification exists.

## Considered Options

- Full guard layer (FILLSPEC_COMMIT_READY state, per-tool exploration
  budgets, KNOWN→STOP runtime state, ASK preemption runtime) — rejected:
  adds a new governance/interpretation layer for the agent precisely to stop
  the agent over-interpreting; the main flow already contains the right
  rules, misplaced or under-specified.
- Physical manifest namespace split (lookup_sources/inheritance_inputs) —
  rejected for now: fix interface ambiguity first; escalate to runtime only
  on recurrence.
- Accept-and-coerce logical key names (map `sku` to a header match) —
  rejected: reintroduces guessing (duplicate headers, aliases) where a
  deterministic defect is cheaper.
- Keeping contract-test names in pattern notes as "mechanical backing" —
  rejected: they answer "why believe it", not "how to do it", and are a
  reading-lure; the admission charter in the file header remains
  maintainer-facing.

## Consequences

- KNOWN_TRAPS' charter is sharpened by use: mechanism traps go in; plain
  invalid schema parameters (ticket 04) deliberately do not — a conscious
  narrowing of the "不落 KNOWN_TRAPS 不算完成" institutionalization clause.
- Pattern notes carry no provenance; pattern admission/verification evidence
  lives on the development side (tests, admission charter), not in the
  agent's runtime reading surface.
- The convergence rule is text, not a runtime gate: the residual risk
  (agent ignores §3 text) is accepted, same trust model as every v2.5
  behavioral rule; recurrence across real tasks is the recorded escalation
  trigger.
- Benchmark/eval metrics (TTFFS, canonical-match→FillSpec, etc.) are fully
  out of scope including Deferred; the test/evaluation system is a separate
  future effort.
