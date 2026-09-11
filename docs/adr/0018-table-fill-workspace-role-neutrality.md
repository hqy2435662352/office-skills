# 0018 table-fill workspace role neutrality

## Status

Accepted on 2026-09-09 following the table-fill workspace-first grilling session (Q2–Q5). Supersedes the single-target semantics of `workspace_init.py` (schema v3 which recorded `manifest.target`, `kind` tags, and binary source/target fingerprints) and retires the TOPOLOGY_BARRIER (flatten count = 0 before topology) from §1.1 of SKILL.md. Extends ADR 0017's "Task shrinks to a data organization object".

## Context

Workspace Init was designed as the single Job-level entry, but its manifest still encoded run-level semantics: a mandatory `--target`, a singular `manifest.target` pointer, per-entry `kind` tags, and aggregated `source_structure`/`target_structure` fingerprints computed at init time. That forced init to answer "who is source, who is target" before topology existed, and the TOPOLOGY_BARRIER ("no flatten before topology resolution") directly contradicted the --init entry flattening in one atomic step — a conflict Agents resolved by improvising their own workflow. Additionally, flattening facts like placeholder/clone-source style segments were found to be target-view renderings of data already present in every entry's `meta["style_granularity"]`, not target-extracted facts — so role knowledge added no factual content, only presentation.

## Decision

1. **Manifest role neutrality**: `workspace_manifest.json` encodes physical facts only — staged input identities, outlines, and the explicitly selected business-sheet union flattened into entries, each carrying its own `structure_sha256` entry-level fingerprint. No `target` pointer, no `kind` tags, no binary source/target fingerprints. `--target` is removed from `workspace_init.py`; it also stops emitting `prepare_manifest.json`.
2. **Selective Flatten Invariant**: role neutrality does not authorize workbook-wide flattening. Init flattens only the explicit business-sheet union the Job names (via `--sheets`); selecting that scope needs no topology decision. Sheet Scope (which sheets enter the fact space) is decided in init; Run Role (which entries are source/target in a run) is decided by topology and applied by materialization.
3. **Run Materialization** (`materialize_run.py`, new thin stateless CLI): the sole lowering from a resolved run definition to a run-local `prepare_manifest.json`. It resolves/validates entry references (`RUN_ENTRY_NOT_IN_WORKSPACE` fail-closed — never incremental flatten), projects run-level fingerprints from entry-level facts, and renders the Target Routing View for the target entry (`structure_digest --pre-mod --target` over existing meta/csv/candidates — pure re-render, zero probing/flattening/extraction). It performs no discovery, no caching, no topology or business reasoning, and never mutates the workspace.
4. **Run definition inputs**: single-run uses ephemeral CLI `--sources/--target` arguments (no `task.yaml`, no `run_definition.json`); multi-run uses the validated `task.yaml`. Both normalize to one minimal internal RunDefinition and one materialization implementation — two input adapters, one execution semantics. `materialize_run.py --task task.yaml` materializes all runs in a plain loop, no scheduler/status/retry; it never writes back to task.yaml.
5. **Single-run unification**: single-run and multi-run share the same init → materialize path; there is no "init writes the single-run view" shortcut (the old `_write_compile_view` dual-track is removed).

## Consequences

- `workspace_init.py` shrinks: no target staging, no compile view emission, entry-level fingerprints only. `prepare_manifest.json` is redefined as Run-local derived compiler view, generated only by materialization; downstream consumers (compile_fill, mod_nominate, spec_review, execute_batch) keep their existing schema surface.
- Agents get one unambiguous program counter: S0 Workspace Init → S1 Topology + Materialization → S2… — the topology/init conflict that previously forced workflow improvisation is gone.
- A run referencing a sheet outside the initialized scope fails closed and instructs a full re-init with the complete sheet union — no incremental prepare revival.
- SKILL.md §1.1 topology text and its TOPOLOGY_BARRIER / Adopt Discovery / Task Bootstrap / Shared Prepare machinery are retired (see ADR 0020).

## Alternatives rejected

- Keeping a file-level weak role marker (option 2): the same workbook can be target in run A and source in run B; file-level role tags reintroduce the granularity error manifest `target` already had.
- Multi-value target retained in workspace manifest (option 3): preserves the buggy last-entry-wins target assignment and keeps role semantics inside canonical facts.
- Workbook-wide flattening ("flatten all, select later"): reintroduces context pollution, MOD nomination noise, and history-sheet contamination that role neutrality was meant to remove.