# 0019 table-fill compile-before-review

## Status

Accepted on 2026-09-09 following the table-fill grilling session (Q1). Reverses a v2.0 frozen decision (ADR 0017 Q4: Spec Review after FillSpec authoring and before compile). The new order is canonical: FillSpec First Draft → Compile/Repair Loop → COMPILE CLEAN → Spec Review → Execute.

## Context

The frozen "Review before Compile" order mixed two different feedback loops into one human touchpoint. Compiler feedback answers "is this IR mechanically legal and executable"; human Review answers "is this IR what I want". Because FillSpec First explicitly allows incomplete first drafts and treats compile defects as an expected convergence mechanism, confirming a FillSpec before mechanical convergence meant exposing an unstable intermediate state as the approval object — every mechanical repair round then invalidated the previous confirmation (`SPEC_HASH_DRIFT` fail-closed on any FillSpec byte change), dragging the user into repeated confirmations of the same business meaning. Document check confirmed the SKILL contained two authorized orderings (Part II Review-before-Compile vs §1.4b Compile-Driven Discovery without Review), which Agents resolved by picking a path themselves.

## Decision

1. **Order**: `FillSpec First Draft → Formal Compile → {FAIL → defect.code/corrective_action → repair → recompile | PASS → COMPILE CLEAN} → Spec Review → review_confirm(hash H) → Execute`. The SKILL's canonical SOP is updated accordingly; no Review-before-Compile wording remains.
2. **Responsibility boundary**: Compile before Review ≠ business decision before Review. In the Compile/Repair loop, only mechanical classes may be auto-repaired and recompiled: YAML/schema, locator structure, fingerprint, duplicate write owner, formula/merge/nulls structural conflicts, and defects with a deterministic corrective_action. If a defect actually exposes business ambiguity (two mappings both plausible; missing value 0 vs empty; which target role a field belongs to; which output semantics the user wants), the agent must NOT "repair until it compiles" — it enters the business-ambiguity path (separate ASK or `gaps` surfaced at Review).
3. **Review binds the exact IR bytes**: `review_confirm.json` records the fill_spec sha256 of the compile-clean spec; it is compared (D — see below) against `execution_plan.fill_spec_sha256` before Execute. Any FillSpec change after confirmation invalidates the confirmation; every FillSpec-affecting repair requires a new Compile Clean + Spec Review cycle. Non-semantic execution/environment failures that do not modify FillSpec (timeout, file lock, IO error, render service unavailable) may retry without re-review.
4. **`--skip-review`**: TASK MODE forbids it (CLI retained only as a compatibility flag, not a canonical path); the Execute gate does not distinguish "skipped" from "omitted".
5. **Review timing in multi-run**: Spec Review covers all runs in one summary (`spec_review.py --task task.yaml`), one confirm binds all run spec hashes, after all runs are compile-clean.

## Consequences

- **Execute Input Gate** (`execute_batch.py`): fail-closed before template copy — `review_confirm.json` must exist (`SPEC_REVIEW_MISSING`) and `review_confirm.fill_spec_sha256` must equal `execution_plan.fill_spec_sha256` (`SPEC_REVIEW_STALE`), alongside input hash drift (`INPUT_HASH_DRIFT`). Both hashes already exist in the respective artifacts (compile_fill writes `plan["fill_spec_sha256"]`; spec_review --confirm writes per-run sha256), so no schema change is needed.
- The user confirms exactly the IR that will be executed; mechanical repair loops never disturb the human touchpoint.
- SKILL §1.4b's second state machine (PLANNING → AUTHORING_READY → FIRST_DRAFT → FIRST_COMPILE) and its Review-less Compile-Driven Discovery path are retired — the authoring allowed/forbidden list is folded into S4, the compile loop into S5.
- CONTEXT.md "Spec Review" term moves from "after FillSpec authoring and before compile" to "after Compile Clean and immediately before Execute".

## Alternatives rejected

- Keeping Review-before-Compile with a semantic (business-meaning) hash instead of the full YAML hash: requires building and maintaining a semantic-hash extractor, and still forces users into the ambiguity of "what counts as business meaning"; the full-bytes binding is simpler and strictly stronger.
- "Let the user review once and trust later mechanical fixes": contradicts the fail-closed hash drift contract and the Execute gate's guarantee that the executed IR is the reviewed IR.