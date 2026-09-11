# Table Fill V3 Acceptance Standard

## 1. Purpose and authority

This document is the sole cross-plan acceptance standard for the split Table Fill V3 implementation. The five execution plans own implementation and local verification; this standard independently decides whether their combined result satisfies the approved monolithic plan.

Approved source baseline:

- Plan: `.omo/plans/table-fill-v3-opencode.md`
- SHA-256: `0407020912de0c105e80759e29b99abdd08bcccc2f1af34de0eec3e2b47f0f78`
- Architecture: `docs/adr/0006-table-fill-v3-trusted-host-governance.md`
- Contracts: `docs/table-fill-v3-governance-contracts.md`

Acceptance is read-only except for `.omo/evidence/table-fill-v3-acceptance/**`. It may not repair Runtime, Adapter, Skill, documentation, configuration, or business files. A defect reopens its owning plan and invalidates every downstream handoff.

## 2. Required inputs

The acceptance run may start only when all inputs exist and validate:

| Plan | Required handoff | Required companion artifacts | Required status |
| --- | --- | --- | --- |
| 01 | `.omo/evidence/table-fill-v3-01/handoff.json` | `capability-baseline.json`, `source-snapshot-manifest.json`, `candidate-clone-manifest.json`, `isolation_recipe`, raw host probes, dirty/config snapshots | `PASS` |
| 02 | `.omo/evidence/table-fill-v3-02/handoff.json` | `runtime-api-contract.json`, Runtime test/type/lint evidence | `PASS` |
| 03 | `.omo/evidence/table-fill-v3-03/handoff.json` | `adapter-contract.json`, classifier manifests, Adapter test evidence | `PASS` |
| 04 | `.omo/evidence/table-fill-v3-04/handoff.json` | `candidate-skill-development.json`, external regression evidence, rollback manifest | `PASS` |
| 05 | `.omo/evidence/table-fill-v3-05/handoff.json` | `fixture-load-manifest.json`, `e2e-results.json`, real-CLI evidence | `PASS` |

Each handoff must bind the source baseline hash, predecessor handoff hash, original Todo ownership, live artifact hashes, evidence hashes, allowed write manifest, global-config before/after hash, status, stop reason, and expected successor. Missing or unverifiable fields fail entry.

## 3. Handoff validation algorithm

Perform these steps in order and record each result in `.omo/evidence/table-fill-v3-acceptance/entry-validation.json`:

1. Parse all five handoffs and companion JSON files without coercion or ignored fields.
2. Require plan IDs `01` through `05`, status `PASS`, and the exact approved source SHA-256.
3. Recompute every immutable handoff, evidence, companion contract, classifier/command manifest, and test-result hash from disk. For product artifacts, verify each ordered `sha256_before -> sha256_after` transition; require each successor's before hash to match its predecessor/baseline and compare only the last authorized writer's after hash with the final live file.
4. Require a continuous chain `01 -> 02 -> 03 -> 04 -> 05 -> acceptance`; reject a skipped, reordered, duplicated, or replaced handoff.
5. Require original Todo ownership to be exactly `1-5`, `6-11`, `12-17`, `18-22`, and `23`, with no omission or duplicate.
6. Compare live OpenCode version, capability receipt, final Runtime API, final Adapter contract, final candidate Skill, final package documentation, and global configuration to the latest owning handoff; validate older states through the ordered transition chain rather than against final live bytes.
7. Verify the active Skill tree digest equals Plan 01 `source_tree_digest`; verify candidate baseline fields equal Plan 01 `candidate-clone-manifest.json` and candidate current fields equal Plan 04 `candidate-output-manifest.json`; reject any active-tree write or unowned candidate transition.
8. Verify the Plan 01 `isolation_recipe` is present and reproducible.
9. Require every local final check named by each plan to be present, current, and unconditional; self-reported success without raw evidence is invalid.
10. Reject unresolved findings, `UNSUPPORTED`, stale evidence, an unrecognized dirty-path change, or any hash drift before running F1-F4.

## 4. Original-plan coverage

| Owner | Exclusive implementation scope | Cross-plan output |
| --- | --- | --- |
| Plan 01 | Original Todos 1-5: baseline plus OpenCode capability, plugin, question, and tool/command probes | Frozen capability and command evidence |
| Plan 02 | Original Todos 6-11: package, store, paths/phases, Gates, snapshots, authorization | Frozen host-independent Runtime API |
| Plan 03 | Original Todos 12-17: loader, safe tools, classifiers, question bridge, guard | Frozen project-local Adapter contract |
| Plan 04 | Original Todos 18-22: lifecycle and Layers 1-4 procedural development plus external regressions | V3 candidate Skill and rollback manifest |
| Plan 05 | Original Todo 23: isolated real OpenCode E2E and fixture-local enable/disable documentation | Final implementation handoff |

Local plan checks do not replace the four independent checks below.

## 5. F1 - Plan compliance audit

Verify all 23 original Todo acceptance criteria, every required evidence path, both stop-gate outcomes, and all eight ADR contracts against the live implementation:

1. Capability truth: `HOST_GOVERNED` is available only from proven OpenCode 1.17.8 capabilities; missing capability yields `UNSUPPORTED`, not silent downgrade.
2. Narrow enforcement: only registered Table Fill protected paths and effects are governed.
3. Exact scope and invalidation: changed mapping, batch, output, extra path, or resume invalidates prior authorization.
4. Deterministic Gate identity: canonical full basis, revision, digest, request, and single consumption agree.
5. Human authority boundary: only the native OpenCode question reply path can confirm Gate or resume; Agent text/tools cannot.
6. Session binding: one current Host Session owns the run; resume atomically replaces it and stales prior decisions.
7. Conservative classification: only positively proven reads or fully derived writes are known; malformed/unlisted/dynamic behavior is `UNKNOWN` and denied when protected paths intersect.
8. Clean state cutover: V2 markers and legacy state never authorize a V3 governed write.

Reject missing evidence, undocumented defaults, compatibility shims, file-presence authority, or claims based only on logs. Output `final-f1-plan-compliance.md` ending in exactly `APPROVE` or `REJECT` with actionable findings.

## 6. F2 - Code quality and regression audit

Run from `table-fill-governance/`:

```bash
bun test
bunx tsc --noEmit
bunx biome check .
```

Run from `20260724_table-fill-tests/`:

```bash
TABLE_FILL_SKILL_ROOT="C:\Users\Administrator\Desktop\Shirley冷年汇报\table-fill-v3" PYTHONDONTWRITEBYTECODE=1 python -W error::ResourceWarning -m unittest discover -v
```

Also inspect:

- strict types and typed errors; no `as any`, suppression directives, empty catches, or swallowed Runtime/store failures;
- no OpenCode imports under `src/runtime/` and no business policy duplicated under `src/opencode/`;
- every pure source module is at most 250 pure LOC;
- no broad shell execution/parser, dynamic OLE support, arbitrary Python, generic action policy, speculative compatibility, or global plugin registration;
- Catalog, capture, loader, branch, OfficeCLI, legacy `SKILL_ONLY`, Runtime, Adapter, classifier, question, guard, and external governance suites remain green.

F2 runs the Python V3 tests only against the repository-local candidate. It does not require the V3 suite to pass against the untouched active V2 Skill.

Output `final-f2-code-quality.md` ending in exactly `APPROVE` or `REJECT`.

## 7. F3 - Independent real-host QA

Do not reuse Plan 05 server, run state, question IDs, destination, or evidence directory. Create a fresh isolated fixture and independently start a real OpenCode server/SDK driver using the exact Plan 01 `isolation_recipe`.

Required observations:

- the fixture loads exactly one `table-fill-v3` from `.opencode/skills/table-fill-v3/` and zero global `table-fill`;
- project-local plugin loads without global configuration changes;
- start and exact path registration bind the expected project and Host Session;
- a pre-Gate protected write is denied before invocation;
- conditional MOD Gate or explicit skip follows its exact basis;
- a mapping change invalidates the previous basis;
- a native Execution Gate request/reply records real OpenCode request/reply IDs and confirms once;
- one exact approved output changes exactly once;
- extra-path, duplicate-target, mixed-write, unknown Bash, Runtime-unavailable, forged metadata, old-session, stale-Gate, dynamic OLE, and prohibited child writes are denied before invocation;
- resume binds a second session and makes all first-session authority unusable;
- complete and fail paths scrub pending decision bindings and do not leak authority to a second clean run.

Hash source, target, denied sentinels, global config, active MOD assets, active Skill, candidate Skill, predecessor source, and unrelated workspace paths before and after. Output `final-f3-manual-qa.json` with raw events, commands, revisions, IDs, hashes, observed invocation counts, and final `APPROVE` or `REJECT`.

## 8. F4 - Scope fidelity audit

Compare the cumulative write manifests with the allowed sets in Plans 01-05 and the original monolithic plan. Require:

- global `C:\Users\Administrator\.config\opencode\opencode.jsonc` unchanged;
- older workspace `table-fill/`, preserved MOD business assets, real source/target files, and unrelated dirty paths unchanged;
- no Codex work, broker/daemon/token/signature/RPC layer, Action DSL, V2 migration, generic policy platform, or global plugin registration;
- no Git staging, commit, amend, reset, clean, checkout, push, or force operation;
- candidate Skill wording states capability truth, fail-closed behavior, and OfficeCLI-only business-file I/O; package README states unsupported cases, fixture-local loading, state inspection, and disable/cleanup behavior;
- the active Skill canonical tree manifest/digest recomputed at acceptance time equals Plan 01 `source_tree_digest` exactly; candidate baseline fields equal Plan 01 and candidate current fields equal Plan 04 `candidate-output-manifest.json`;
- no global skill installation, promotion, or replacement occurred; no active tree write occurred;
- every produced artifact belongs to exactly one plan or this acceptance run.

Output `final-f4-scope-fidelity.md` ending in exactly `APPROVE` or `REJECT`.

## 9. Failure ownership and invalidation

| Finding | Reopen | Invalidate |
| --- | --- | --- |
| Host capability, raw hook, question shape, tool fixture, command manifest, baseline snapshot | Plan 01 | Plans 02-05 and acceptance |
| Runtime state, paths, phases, Gate, snapshot, store, authorization, public API | Plan 02 | Plans 03-05 and acceptance |
| Plugin, Agent-safe tool, classifier, question bridge, guard, child policy | Plan 03 | Plans 04-05 and acceptance |
| Candidate Skill procedure, four layers, recovery, external regressions | Plan 04 | Plan 05 and acceptance |
| E2E harness/evidence or fixture-local documentation only | Plan 05 | Acceptance |
| Acceptance procedure/evidence only | Acceptance run | Current acceptance receipt |

After repair, rerun the reopened plan and every invalidated successor from their entry checks. Never patch a predecessor during acceptance.

## 10. Final receipt

Write `.omo/evidence/table-fill-v3-acceptance/final-acceptance.json` only after entry validation and F1-F4 complete. It must contain:

```json
{
  "schema_version": 1,
  "source_plan_sha256": "0407020912de0c105e80759e29b99abdd08bcccc2f1af34de0eec3e2b47f0f78",
  "handoffs": [{ "plan_id": "01", "path": "...", "sha256": "..." }],
  "checks": {
    "F1": { "status": "APPROVE", "path": "...", "sha256": "..." },
    "F2": { "status": "APPROVE", "path": "...", "sha256": "..." },
    "F3": { "status": "APPROVE", "path": "...", "sha256": "..." },
    "F4": { "status": "APPROVE", "path": "...", "sha256": "..." }
  },
  "live_artifacts": [{ "path": "...", "sha256": "..." }],
  "global_config_sha256_before": "...",
  "global_config_sha256_after": "...",
  "active_skill_root": "C:\\Users\\Administrator\\.config\\opencode\\skills\\table-fill",
  "candidate_skill_root": "C:\\Users\\Administrator\\Desktop\\Shirley冷年汇报\\table-fill-v3",
  "source_tree_digest": "...",
  "candidate_baseline_manifest_sha256": "...",
  "candidate_baseline_tree_digest": "...",
  "candidate_current_manifest_sha256": "...",
  "candidate_current_tree_digest": "...",
  "clone_lineage": { "baseline": ".omo/evidence/table-fill-v3-01/candidate-clone-manifest.json", "current": ".omo/evidence/table-fill-v3-04/candidate-output-manifest.json" },
  "isolation_recipe": { "env": { "OPENCODE_TEST_HOME": "absolute temporary home", "HOME": "absolute temporary home", "XDG_CONFIG_HOME": "absolute temporary config root", "XDG_DATA_HOME": "absolute temporary data root", "XDG_STATE_HOME": "absolute temporary state root", "XDG_CACHE_HOME": "absolute temporary cache root", "OPENCODE_CONFIG_CONTENT": "inline isolated JSON", "OPENCODE_DISABLE_PROJECT_CONFIG": "1", "OPENCODE_PURE": "1", "OPENCODE_DISABLE_AUTOUPDATE": "1", "OPENCODE_DISABLE_AUTOCOMPACT": "1", "OPENCODE_DISABLE_MODELS_FETCH": "1", "OPENCODE_AUTH_CONTENT": "{}" } },
  "release_status": "NOT_INSTALLED",
  "status": "PASS",
  "completed_at": "RFC3339 timestamp"
}
```

`status` is `PASS` only when all five handoffs are `PASS`, all four independent checks are exactly `APPROVE`, every bound hash validates, no unresolved finding exists, and `release_status` is `"NOT_INSTALLED"`. Otherwise write `FAIL` with `failed_stage`, `owner_plan`, and `findings`, and do not claim Table Fill V3 accepted. A mechanical `PASS` or user approval must not install, promote, or replace the active Skill.

## 11. Completion rule

Mechanical acceptance ends when the final receipt is `PASS` with `release_status: "NOT_INSTALLED"`. Surface the receipt, F1-F4 reports, known unsupported cases, clone lineage, isolation proof, and rollback boundary to the user. The implementation may be described as accepted only after the user explicitly approves that evidence; the Agent may not infer approval from silence or from its own report. Acceptance never installs, promotes, or replaces the active Skill.
