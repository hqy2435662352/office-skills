# 0011-table-fill-business-reasoning-barrier

# Business Reasoning Barrier in Table Fill v2.5

## Status

Accepted on 2026-08-27 following a grilling session with the `grilling` and
`domain-modeling` skills. This ADR authorizes specification and planning, not
implementation.

## Context

Table Fill v2.5 prepares a full structural digest per sheet during
`prepare_run.py --flatten`. That digest already carries formula-chain
templates, column profiles (samples, min/max, uniqueness, numeric ratio),
numFmt, and merge anchors — FillSpec authoring material, not nomination
material. In the 2026-08-27 fresh_baojia run the agent consumed digest + flat
CSV before MOD Resolution, built its own mapping hypotheses (prototype cost ←
parts price), skipped ASK, and was overturned later by FLD-006. Two-phase MOD
rule loading already exists in v2.5 (nomination carries no full rule set; full
rules load only after selection) — the open gap is data-evidence access before
resolution, not rule-load timing.

## Decision

- **Pre-MOD Evidence**: `prepare_run.py --flatten` writes
  `{name}_premod_evidence.md` per sheet (a `structure_digest.py --pre-mod`
  view): dimensions, row gaps, header roles, block positions without titles,
  merge ranges, and target placeholder/clone-source style facts. Full digest
  generation is deferred to post-resolution.
- **Business Reasoning Barrier**: after Prepare, until `mod_resolution.json`
  status ∈ {resolved, none}, the agent may read only premod_evidence, outlines,
  and (during adjudication) mod_resolution.json; uncertain-routing bounded
  reads (view html + ≤2 targeted gets) answer task shape only. Reading flat
  CSV / meta.json / candidates.yaml, generating a digest, and any
  mapping/formula/inheritance/selector/ASK/FillSpec derivation are forbidden.
- **Unlock order**: resolved with a selected MOD → full rules injected via
  `load_rules_for_selected_mod()` first, then `structure_digest.py` generates
  the full digest. resolved NONE / status none → digest generation directly.
- **MOD Adjudication Record**: `mod_nominate.py` gains `--mod <NAME|NONE>`;
  re-running after user adjudication writes the final decision (resolved +
  selected; overridden_exclusions; adjudicated_from). Barrier unlock becomes a
  literal file check, not a conversational interpretation.
- **Compiler checks**: C1 `MOD_RESOLUTION_MISSING`, C2 `MOD_SELECTION_MISMATCH`
  (spec.selected_mod must equal the recorded decision; NONE only when the
  record says NONE or status=none), C3 `MOD_REVISION_MISMATCH`,
  C4 `MOD_UNRESOLVED`. The compiler verifies MOD reference and decision
  consistency, never the adjudication process itself.
- **Design principle**: when adjudication lacks evidence, extend Pre-MOD
  Evidence — never extend pre-MOD read permission.

## Considered Options

- Split artifact + cognitive stop-rule only (full digest remains on disk) —
  rejected: unverifiable soft constraint; the failure class under repair is
  precisely agent self-authorization.
- Deferring flat CSV generation too — rejected: CSV is compile input, not a
  cognitive artifact; moving it couples prepare/compile for governance only.
- Fuzzy "adjudication-necessary evidence" read exception during
  ambiguous/conflict — rejected: reopens the self-authorization window;
  adjudication facts already live in mod_resolution.json + premod_evidence.
- Runtime read-gate (V3 host governance) — out of scope for v2.5; recorded as
  the residual weakness of this design.

## Consequences

- ADR-0010's routing input ("任务指令 × 源 digest × 目标 digest") is corrected:
  task-shape routing now consumes Pre-MOD Evidence instead of the full digest.
- Residual: flat CSV / meta.json / candidates.yaml remain on disk pre-MOD and
  are gated by skill text only — v2.5 has no read-gate. Accepted.
- The NONE authorization record proves the decision path was recorded, not
  that a human made it — the same trust model as every v2.5 gate (gates catch
  mistakes, not malice).
- mod_resolution.json changes role from nomination recommendation to final
  decision record; the pre-adjudication card state survives via
  `adjudicated_from`.
- PPTX targets keep the current flatten-time minimal digest; Barrier coverage
  for the PPTX path is deferred.
- Institutionalization ships in one piece: the four defect codes + contract
  Q&A text + contract tests (compiler check, contract entry, regression test).
