# 0021 table-fill row-gap canonical re-init

## Status

Accepted on 2026-09-09 following the table-fill grilling session (Q7). Redefines row-gap repair as input-version repair, replacing the previous in-place repair + resync-flatten + fingerprint-patch flow in `repair_row_gaps.py` and SKILL §1.1.

## Context

Row-number gaps (`row` element r-values discontinuous in sheet XML) break officecli `add ... after: /row[N]` anchor chains permanently (Egypt 2026-08-12 case). The old repair flow modified the staged target in place, then auto-re-ran `prepare_run.py --flatten` on the target sheet to resync `prepare_manifest.json` fingerprints, then required the agent to patch the FillSpec fingerprint and recompile. Under the frozen architecture this flow conflicts three ways: (a) modifying the staged file violates the Workspace Manifest's frozen input hashes (drift → re-init is the only honest path); (b) `prepare_run.py`'s CLI is retired (ADR 0020) so the resync call dies; (c) the repair is target-specific while Workspace Init must stay role-neutral (ADR 0018), and any spec fingerprint change after a review invalidates the review under Compile-before-Review (ADR 0019).

## Decision

1. **Repair is input-version repair, not workspace mutation**: `repair_row_gaps.py` becomes a pure input-repair utility — copy the affected workbook, materialize missing row elements on the copy (`set numberformat=0.00` style-only writes, `close` to flush), verify gaps repaired, output the repaired workbook as a **new repaired input snapshot**. It no longer calls prepare/prepare_run resync, patches manifests or FillSpec fingerprints, recompiles, or derives any next state.
2. **Canonical re-entry**: the repaired snapshot is the new input. The current workspace (with its old input hashes) becomes obsolete; `workspace_init --init` runs again with the repaired snapshot in `--files`. No partial manifest refresh, no incremental flatten, no fingerprint-patching shortcut, no `--accept-drift` style flag, no workspace_init repair mode.
3. **Replay, not restart**: if the user task is unchanged, Topology decision, run definitions, MOD decision, and business mapping remain valid and are replayed without re-reasoning; run views are re-materialized (`materialize_run.py`), FillSpec fingerprints are rebound to the new workspace facts, then Compile → Spec Review → Execute (the spec bytes changed, so a new Review is mandatory — consistent with ADR 0019).
4. **Multi-run consequence**: all runs referencing the repaired entry naturally get the new fingerprints through re-materialization; no affected-run dependency graph, no selective invalidation engine — full re-materialize is cheap.

## Consequences

- Failure-handling table entry becomes: `ROW_GAP_DETECTED → repair copy → repaired input snapshot → invalidate current workspace → re-init with repaired snapshot → re-materialize run views → refresh FillSpec fingerprints → Compile Clean → Spec Review → Execute`.
- The immutable-workspace contract survives its first adversarial case: the canonical manifest is never in a deliberately corrupted state between repair and re-init (repair works on a copy).
- `repair_row_gaps.py` shrinks to its irreplaceable Office-surgery core; its old automated resync machinery (and tests around it) is removed.

## Alternatives rejected

- In-place staged repair then local re-flatten (option B): opens a "partially trusted manifest" hole — input hashes recorded in the manifest no longer match the file, and the canonical fact space silently diverges.
- Fail-closed manual repair (option C): row gaps are a recurring REPAIR class (Egypt case), not a rare corner; forcing officecli surgery on the user is strictly worse than the canonical re-init path.
- Re-running init against the original source after repairing the staged file (naive variant of A): stage_files would copy the un-repaired original back, silently undoing the repair — the repaired snapshot must be the new `--files` input, not the original.