# 0022 table-fill constraint audit — business contracts kept, model-behavior instructions re-grounded

## Status

Accepted on 2026-09-11. Applies a read-only constraint review of `table-fill/SKILL.md` (20 items) to `SKILL.md`, `references/CAPABILITY_EVIDENCE.md`, `scripts/fill_spec_first_validator.py` and the contract tests that pin their wording. Every surviving constraint was re-grounded in the repo's own recorded evidence before any wording changed.

## Context

A review of `SKILL.md` (496 lines / 44,321 chars) classified 10 items as over-constrained model-behavior instructions and 10 as legitimate business/execution contracts. Independent verification of its citations found 18/18 line references accurate, so the review was treated as a real reading of the file rather than an impression — but citation accuracy is not evidence that a constraint ever prevented an observed failure. Two of its arguments were found factually inaccurate (see Corrections).

Because these documents are pinned by contract tests (`test_optimization.py`, `test_fillspec_first_contract.py`, `test_runtime_governance.py`, `test_single_run_isolation.py` use keyword-presence guards), the order was fixed: **triage first, then wording + validator + contract test in one change**. Triage channels: `docs/test-cases/case-002…010` (real-run retrospectives), `references/KNOWN_TRAPS.md`, `references/FAILURE_CLASSES.md`.

Triage verdicts for the six relax candidates:

| Constraint | Verdict | Recorded evidence |
|---|---|---|
| C-a pre-draft four-action whitelist | PARTIAL | Pre-draft probing/case/source reading is recorded waste (`case-007:79`, `KNOWN_TRAPS:92`), but the **closed enumeration is contradicted**: `case-009:61/:80` judge the pre-draft read of FILLSPEC + combination_patterns as the correct, undeletable path |
| C-b fixed exploration budgets | PARTIAL | Repeated manual re-verification is recorded waste (`case-003:87`, `case-007:100`) and probe-like attempts exceeded 1/run (`case-005:77`); no run-bound accounting failure exists and the ≤2-get cap is **contradicted** by `case-004:73` (15 gets NECESSARY — readback does not cover formula results) |
| C-c no source reading | **EVIDENCE_FOUND** | `KNOWN_TRAPS:94` (25 interactions to confirm one behavior), `:106` (repeated tests+source reads for facts FILLSPEC already declared), `:92` (source read produced a **wrong** decision). One carve-out: `case-004:68` |
| C-d one-shot questioning | **EVIDENCE_FOUND** | `case-009:64/:83` record the two-round failure and its cause (incomplete first-round enumeration); `case-010:26` is the compliant exemplar |
| C-e machine-evidence termination | **EVIDENCE_FOUND (split)** | Waste from post-evidence re-checking is real (`case-007:100/:102`, `case-010:77`); the absolute form is **refuted** by recorded escapes: `KNOWN_TRAPS:18` (delivered file showed 净价 all 0$ while readback/issue/validate were green), `:56`, `case-007:208`, `case-008:77` |
| C-f reference reading caps | PARTIAL | Only the criterion is evidenced (`case-008:66`, `case-004:116`); the 0/1 caps are contradicted — no zero-reference run is recorded and FILLSPEC+patterns together are ruled necessary (`case-009:80`, `case-003:81`, `case-010:81`) |

## Decision

1. **Description (item 1)**: seven synonyms of a single trigger branch collapse to one branch statement plus exclusions; the four format directions move to the body. Grounded in the repo's own standard, `.agents/skills/writing-great-skills/SKILL.md:27` ("One trigger per branch … synonyms that rename a single branch are duplication").
2. **Navigation (item 2)**: the five-part list is corrected (I/II were swapped and "运行时心智模型" was never a heading). **Full Part III re-layering is deferred**: `references/TASK_ORCHESTRATION.md` still describes retired artifacts (`task_manifest.json`, `task_status.json`, deleted `prepare_task.py`) in the present tense, so collapsing SKILL.md's Task Public Contract into a pointer would spread stale contract instead of removing duplication. Refresh that file first.
3. **Pre-draft rule (item 3)**: draft-first and error-driven stay; **the closed four-class whitelist becomes criterion + enumeration**. A minimal read that unblocks authoring (pattern index / digest / a named reference section) is legal pre-draft, because that is the recorded correct path. Still banned pre-draft: probe, broad capability dump, full-document read, case-retrospective reading, source reading.
4. **Exploration budgets (item 4)**: the fixed counts (1 probe / 1 rescue / ≤2 `officecli get` / ≤1 structural probe) are **replaced by criteria plus defaults**. Recorded harm is *repetition* and probe inflation, not a count overflow; a rule the record shows being broken correctly erodes the rest of the contract.
5. **Source reading (item 5)**: **prohibition kept** — the best-evidenced constraint of the six. Two wording fixes only: (a) `runtime code/docs` in the Source Scope Guard is a search *upper bound*, not a reading permit, and is reworded so it cannot be read as one; (b) the single recorded carve-out (`case-004:68`) — when a required mechanism fact exists only in tests/source, one targeted read is allowed, recorded as a Capability Gap Discovery, and never used as a business answer.
6. **One-round questioning (item 6)**: **kept**, with the checklist strengthened. The recorded failure is enumeration completeness (the 原型机成本源列 question was missed in round 1), so the fix is a better first round, not permission for a second. The escape channel is stated explicitly: a newly surfaced ambiguity goes to `gaps` and is presented at Spec Review — never guessed.
7. **Machine-evidence termination (item 7)**: "human re-verification is redundant exploration" is **withdrawn**. Machine evidence is necessary but not sufficient (`KNOWN_TRAPS:18` green evidence over a visibly broken deliverable). New rule: do not repeat assertions machine evidence already covers; do verify targets it cannot cover (delivered-view formula results, layout/visual — the render verdict is the agent's, `execute_batch.py:409`) and any contradiction between machine evidence and observation.
8. **Reference caps (item 8)**: the "0 references / single reference" caps are removed; "no preload, no full-document read beyond the question, read the minimal face the trigger names" is kept.
9. **MOD resolution summary (item 9)**: the control-plane one-liner becomes "nominate → auto-adopt per adjudication rules or ask → record resolved/none → load selected rules", matching the auto-resolve paths already implemented in `mod_nominate.py`, so a satisfied auto-adopt no longer reads as a mandatory user round.
10. **Exit-code protocol (item 10)**: `exit 1` splits into transient vs fatal environment errors — transient (lock contention, timeout, resident) → RECOVER and retry; fatal (missing file, unavailable dependency, permission) → STOP + report. Removes the collision with the failure-handling table's RECOVER row.

## Consequences

- Surviving constraints now carry recorded evidence; changed ones keep their mechanism and lose only their unevidenced absolute form.
- **Three-piece sync is mandatory** for this class of change (wording + validator/script + contract test). This decision touches `SKILL.md`, `CAPABILITY_EVIDENCE.md`, `fill_spec_first_validator.py` and 4 test files (`test_fillspec_first_contract.py`, `test_runtime_governance.py`, `test_optimization.py`, `test_single_run_isolation.py`) in one change set.
- **Item 2 executed partially (follow-up commit)**: Part I's `## S0`–`## S8` per-stage blocks (~66 lines) restated Part III §1–§9 — and §1–§9 is the expansion the contract tests actually pin (`^### N\.` scope regexes in `test_optimization.py`, `test_axis_neutral_grid_routing.py`, `test_mod_canonical_resolver.py`, `test_single_run_isolation.py`). The blocks were collapsed to a 12-line stage table (stage → owning § → non-omittable contract); stage **order** remains in Part I as the single authority. SKILL.md 496 → 440 lines (44,321 → 42,678 chars).
- Remaining item-2 work is out of scope: refresh `references/TASK_ORCHESTRATION.md`, then collapse the duplicated Task Public Contract (23 lines); move Part IV's Runtime Governance (29 lines) and §3's routing detail (59 lines) into references — each requires repointing the test scopes that currently pin them inside SKILL.md.
- **New finding beyond the review's 20 items**: `TASK_ORCHESTRATION.md` documents deleted scripts/artifacts in the present tense — a Contract Drift instance, the repo's highest-priority defect class.

## Corrections to the review

- **Item 4** — "no business basis" is too strong: the run-scoped ceiling bounds *architecture forks per run*, not API limits. Triage then showed the counts themselves are unevidenced, so the ceiling is demoted to a default while the criterion becomes binding.
- **Item 7** — its rebuttal "column width is not machine-proven" is inaccurate: column width *is* asserted at compile time for `precision: keep` columns (`flatten_table.py:670` → `meta.column_width`; `compile_fill.py:1289-1347` rejects on insufficiency). The real defect is the verdict-ownership overclaim, not the column-width check.
- **Item 6** — the proposed relaxation is rejected; the recorded failure is enumeration completeness, not the ban.
- **Item 5** — the proposed general read-only source lane is rejected; the record shows pre-draft source reading produced a wrong decision.

## Alternatives rejected

- **Deleting the pre-draft rule outright** (item 3 maximal form): re-opens recorded waste (`case-007:79` pre-draft case reading; `KNOWN_TRAPS:92` wrong decision from a source read).
- **Keeping the fixed counts because they are harmless**: contradicted by a recorded *necessary* 15-get run (`case-004:73`); a count the record shows being correctly exceeded teaches the agent to ignore the contract.
- **Doing the full Part III → references re-layering now**: would re-baseline ~10 test files' pins in one step and risk exactly the contract drift this ADR exists to prevent.
- **Rewriting history for the already-pushed tests**: the removed `test_migration_anchor_comment` assertion stays removed; this ADR re-pins wording going forward rather than retroactively.
