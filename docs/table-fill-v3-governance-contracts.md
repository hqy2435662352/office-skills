# Table Fill V3 Governance Contracts

## Document status

- Status: planning baseline
- Date: 2026-08-06
- Decision record: `docs/adr/0006-table-fill-v3-trusted-host-governance.md`
- Canonical terms: `CONTEXT.md` and `UBIQUITOUS_LANGUAGE.md`
- Purpose: preserve the completed architecture grilling as executable design constraints before implementation begins

This document defines what Table Fill V3 must guarantee. It does not authorize implementation and does not prescribe mechanisms beyond those required by the accepted contracts.

## Problem statement

Table Fill already has a strong business workflow: deterministic table inspection, semantic classification, mapping, Human Gates, batch execution, and delivery verification. Its remaining gap is not another mapping feature. The gap is that Skill instructions and scripts cannot independently guarantee all of the following:

- a Gate answer came from a real user rather than Agent-authored text;
- a write-capable host tool cannot bypass the intended execution script;
- an approval still refers to the exact facts and proposal now being executed;
- a resumed host session cannot race or coexist with an old session binding;
- a changed MOD Catalog cannot silently alter an active run;
- the system reports honestly when the host cannot enforce these properties.

V3 therefore adds the minimum trusted mechanism that closes those demonstrated gaps.

## System boundary

### In scope

- Table Fill run registration and lifecycle state.
- Exact protected-path registration for one run.
- Host capability negotiation and Governance Mode selection.
- Host Session binding and explicit resume.
- Host tool classification at the protected-write boundary.
- MOD Gate and Execution Gate request, basis identity, confirmation, invalidation, and consumption.
- Immutable run-local capture of the selected MOD revision.
- OpenCode stable V1 as the first Host Adapter and contract-test target.
- Reuse of existing MOD Catalog entries, MOD documents, and deterministic business scripts.

### Out of scope

- General workspace sandboxing or host-wide policy enforcement.
- A broker service, daemon, RPC protocol, signed action, capability token, or secret distribution system.
- A generic Action DSL or command proxy.
- Multi-user concurrency, distributed consensus, or remote authorization.
- Migration of V2 run state into V3 authority.
- Codex implementation before the OpenCode reference boundary is proven.
- New business MOD content or changes to Table Fill mapping semantics unrelated to governance.

## Trust model

### Trusted

- The Workflow Runtime's persisted state transitions and authorization decisions.
- The Host Adapter code loaded by the supported host.
- The host's native human interaction result delivered to the Host Adapter through the documented completion hook.
- The operating system's ordinary file-path and process isolation at the level already assumed by local OpenCode execution.

### Untrusted

- Agent-generated prose, summaries, and claims of approval.
- Tool arguments before Host Adapter classification.
- Ordinary chat messages as evidence of a Gate decision.
- V2 run-state files.
- Live MOD Catalog content after a Run-Local MOD Snapshot has been captured.
- Tool calls whose mutation behavior or destination paths cannot be positively established.

### Explicitly not defended

- A malicious local administrator modifying Runtime state or Adapter code outside the host process.
- Kernel, filesystem, or OpenCode core compromise.
- Writes performed by unrelated external processes that never pass through the supported host interception surface.

These exclusions keep the V3 claim precise: fail-closed governance applies to supported host-mediated writes for the registered paths of a Governed Run.

## Component responsibilities

| Component | Owns | Must not own |
| --- | --- | --- |
| **Workflow Skill** | Procedure, phase instructions, business vocabulary, when to request each Gate, how to present decisions | Trusted confirmation, protected-write enforcement, authoritative run state |
| **Workflow Runtime** | Run identity, phase state, Governance Mode, exact protected paths, Host Session binding, Gate lifecycle, basis digests, invalidation, MOD snapshot, authorization decisions | OpenCode tool schemas, host UI, business reasoning, generic command execution |
| **Host Adapter** | Capability probe, session binding, tool-call classification, protected-write interception, native question/result bridge | Business policy, independent Gate state, live MOD semantics, alternate authorization rules |
| **Existing deterministic scripts** | Extraction, metadata generation, mapping validation, OfficeCLI batch execution, MOD catalog operations where retained | Final authority to bypass Runtime state in `HOST_GOVERNED` mode |

No component may silently assume another component's authority.

## Governance modes

| Mode | Entry condition | Guarantee | Required behavior |
| --- | --- | --- | --- |
| `HOST_GOVERNED` | Adapter proves required interception, human-response, binding, and Runtime connectivity capabilities | Fail-closed enforcement for registered protected paths | Register and bind the run; intercept writes; reject stale, unknown, or unauthorized actions |
| `SKILL_ONLY` | Skill can operate but trusted host capabilities are absent or intentionally not required | Guidance only; no enforcement guarantee | State the limitation before execution; never use governed wording or claim protected-write enforcement |
| `UNSUPPORTED` | The requested run cannot execute safely or functionally on the host | No run | Explain the missing capability and stop |

If `require_host_governance=true`, any outcome other than `HOST_GOVERNED` is a start failure. There is no silent downgrade.

## Governed Run model

A Governed Run has one authoritative Runtime record with, at minimum, these conceptual fields:

| Field | Meaning |
| --- | --- |
| `run_id` | Stable V3 run identity |
| `state_version` | Explicit V3 schema/version discriminator |
| `governance_mode` | `HOST_GOVERNED`, `SKILL_ONLY`, or `UNSUPPORTED` |
| `require_host_governance` | Caller requirement used during capability negotiation |
| `host_kind` | Adapter family, initially OpenCode |
| `host_session_id` | The only Host Session currently authorized for the run |
| `binding_revision` | Monotonic revision changed on every successful resume |
| `phase` | Current Table Fill workflow phase |
| `protected_paths` | Set of normalized exact absolute paths with artifact roles |
| `mod_snapshot` | Immutable selected MOD identity, revision, and content identity for this run |
| `gates` | Runtime-owned MOD Gate and Execution Gate records |
| `state_revision` | Monotonic local mutation revision for compare-and-apply semantics |
| `audit_events` | Append-only transition facts sufficient to explain authorization decisions |

The exact persistence encoding is an implementation decision, but the Runtime must expose typed operations rather than permit callers to mutate this record directly.

## Run lifecycle

The minimum lifecycle is:

```text
register -> bind -> active -> completed
                    |  |
                    |  +-> failed
                    +----> resumed (binding replacement) -> active
```

Required rules:

1. Registration selects a Governance Mode from observed capabilities and the caller requirement.
2. A `HOST_GOVERNED` run is unusable for confirmation or protected writes until bound to one Host Session.
3. Runtime operations from a Host Adapter include the expected `host_session_id` and `binding_revision`.
4. Resume is an atomic binding replacement that increments `binding_revision`.
5. After resume, the old Host Session fails every confirm and authorize request. Gate records bound to the former `binding_revision` remain as audit history but cannot be consumed; any still-required Gate must be reopened and confirmed under the new binding.
6. Completed or failed runs do not authorize new protected writes.
7. Runtime state is authoritative; chat reconstruction and filesystem inference cannot reopen a run.

## Protected paths

### Registration

- Each path is normalized to an exact absolute path before storage.
- Every path has an artifact role and the workflow phase that owns it.
- Duplicate normalized paths collapse to one registration or fail if their declared roles conflict.
- Relative paths, globs, parent-directory policy, and paths derived only from chat are not accepted as protected-path identities.
- Registration changes are Runtime transitions and participate in invalidation where they alter a Gate's basis.

### Classification

The OpenCode Adapter classifies every relevant tool call as exactly one of:

- `READ_ONLY`: positive evidence proves the call cannot mutate filesystem state.
- `WRITE(paths)`: positive evidence identifies every possible mutated path for the call.
- `UNKNOWN`: the Adapter cannot prove read-only behavior or enumerate all possible mutated paths.

Absence from a write-name blacklist is never evidence for `READ_ONLY`.

### Authorization

| Classification | Protected-path intersection | Result in a bound `HOST_GOVERNED` run |
| --- | --- | --- |
| `READ_ONLY` | Not applicable | Allow |
| `WRITE(paths)` | None | Allow as outside this run's governance boundary |
| `WRITE(paths)` | One or more paths | Ask Runtime; allow only if current state authorizes every intersecting path |
| `UNKNOWN` | Cannot prove disjointness | Reject |

A `WRITE(paths)` classification is valid only when the tool schema and arguments make its complete mutation set knowable. Otherwise the result is `UNKNOWN`.

## Human Gate protocol

### Gate request

The Agent may ask the Runtime, through the Adapter, to open a Gate. The Runtime creates a pending Gate containing:

- `gate_id` and Gate kind;
- `run_id`;
- canonical Gate Basis;
- deterministic `basis_digest`;
- current Host Session binding revision;
- the artifact and state revisions on which the basis depends;
- status `PENDING`.

The Runtime returns presentation data. The Skill or Adapter may format that data for the human, but formatting does not redefine the Gate Basis.

### Human Decision collection

For OpenCode stable V1:

1. The Agent invokes the native `question` tool with the pending Gate's presentation.
2. OpenCode waits for a real user response.
3. The Adapter observes the completed response in `tool.execute.after` through `output.metadata.answers`.
4. The Adapter submits the Human Decision together with the run, Gate, Host Session, binding revision, and basis digest expected by the Runtime.
5. Only a successful Runtime transition changes the Gate to confirmed or rejected.

Agent prose, a fabricated tool result, a pre-filled answer, or a normal assistant message cannot perform step 4.

### Confirmation

The Runtime accepts a Gate Confirmation only if all of these still match:

- run identity and active state;
- Gate identity and `PENDING` status;
- current Host Session identity and binding revision;
- Gate kind;
- `basis_digest`;
- every dependent artifact/state revision;
- expected Runtime state revision.

Any mismatch rejects the transition. Rejection never downgrades to a warning.

### Consumption

A confirmed Gate authorizes only the effects represented in its Gate Basis. It is not a reusable session-wide approval. A protected write must identify the governed artifact role and current revision that the Runtime checks against the applicable Gate.

## Gate and invalidation matrix

| Change or operation | MOD Gate effect | Execution Gate effect | Protected-write rule |
| --- | --- | --- | --- |
| Layer 1 flattened metadata changes | Invalidate | Invalidate | Layer 1 artifact write may proceed only under the phase rule; downstream writes remain blocked |
| Layer 2 classification or MOD candidates change | Invalidate | Invalidate | Layer 2 artifact write may proceed only under the phase rule; mapping remains blocked until MOD Gate is current or deterministically skipped |
| MOD selection confirmed | Confirm with Run-Local MOD Snapshot | Remains unconfirmed or invalidated | Enables Layer 3 mapping work for the confirmed business basis |
| MOD Gate deterministically skipped | Record `SKIPPED` with reason | Remains unconfirmed or invalidated | Enables only the mapping behavior allowed by the skip contract |
| Layer 3 mapping or execution proposal changes | Preserve current MOD Gate only if its business basis is unchanged; otherwise invalidate it | Invalidate | Delivery remains blocked |
| Execution Gate confirmed | No change | Confirm for exact mapping/batch basis | Enables represented Layer 4 protected writes |
| Layer 4 delivery path/content proposal changes before write | No change | Invalidate if the approved effect set changes | Reject until a new Execution Gate is confirmed |
| Run resume | Retain the old record as stale audit history; require a new confirmation if the Gate is still needed | Retain the old record as stale audit history; require a new confirmation if the Gate is still needed | Old Host Session is rejected immediately |
| Shared MOD Catalog revision changes | No effect on an existing snapshot | No effect by itself | Active run continues from its immutable snapshot |

Gate invalidation is a Runtime state transition. Deleting a file, changing chat text, or asking the Agent to remember that a Gate is stale is not sufficient.

## MOD snapshot contract

When a run selects a MOD, the Runtime captures a Run-Local MOD Snapshot containing enough identity and content evidence to reproduce the selected business basis. The active run reads business rules from that snapshot, never by re-resolving the live Catalog.

Required properties:

- immutable for the lifetime of the run;
- tied to the MOD Gate basis;
- traceable to the Catalog identity and revision from which it was captured;
- independent of later Catalog updates, aliases, or file replacement;
- preserved in the run audit record even if the Catalog entry is later removed.

MOD capture or Catalog publication after delivery is a separate governed workflow action. It cannot retroactively alter the current run.

## OpenCode V1 Adapter boundary

The first Adapter must remain thin. It may know:

- OpenCode session identity;
- the host plugin hook lifecycle;
- native `question` tool request and completion shapes;
- the schemas of explicitly supported read/write tools;
- how to reject a tool call in a pre-execution hook;
- how to call typed Workflow Runtime operations.

It must not know:

- when a MOD should match business data;
- how mapping rows are inferred;
- whether a business transformation is acceptable;
- how a Gate basis is semantically evaluated;
- alternate Runtime state-transition rules.

The initial plugin registration is a development and deployment concern. It must not be performed until the implementation plan is approved and its tests pass.

## Error semantics

The Adapter and Runtime return structured outcomes that distinguish at least:

- capability unavailable;
- run not found or not active;
- Host Session or binding revision mismatch;
- path not registered;
- tool classification unknown;
- applicable Gate missing, pending, rejected, invalidated, or stale;
- basis digest mismatch;
- Runtime state revision conflict;
- operation authorized.

In `HOST_GOVERNED` mode, an error or unavailable Runtime at a protected-write boundary rejects the write. The Adapter must not convert a Runtime failure into `SKILL_ONLY` after the run has started.

## Audit and traceability

Audit data explains state transitions; it is not a second authority. Each event records the run, event kind, prior and resulting revisions, Host Session binding, relevant Gate/path identities, and outcome. Human Decision content is recorded only to the extent required for traceability and must not be treated as an Agent-readable secret channel.

No event log entry can authorize a write if current Runtime state does not.

## Verification contract

Implementation is not complete until tests prove both allowed and rejected paths.

### Runtime contract tests

- capability negotiation, including no silent downgrade with `require_host_governance=true`;
- run registration, binding, completion, failure, and resume replacement;
- exact path normalization and intersection behavior;
- Gate request, deterministic basis identity, confirmation, rejection, and one-state transition;
- invalidation matrix for Layer 1 through delivery;
- stale Host Session, stale binding revision, stale basis digest, and state revision rejection;
- immutable MOD snapshot under live Catalog mutation;
- V2 state rejection and V3 clean initialization.

### Adapter classifier tests

- each supported read-only tool is positively proven read-only;
- each supported mutator enumerates all possible write paths from its real schema;
- malformed, dynamic, shell-like, or unsupported mutators classify as `UNKNOWN`;
- unrelated known writes outside protected paths are allowed;
- protected, mixed protected/unprotected, and unknown writes fail closed as required.

### OpenCode integration tests

- a real native `question` result reaches `tool.execute.after` before the Agent receives the result;
- the Adapter alone submits that Human Decision to the Runtime;
- Agent-authored text and forged tool-shaped data cannot confirm a Gate;
- the pre-execution hook blocks a protected write before mutation;
- an Execution Gate confirmation allows only the represented write set;
- resume makes the old OpenCode session fail immediately;
- Runtime unavailability blocks, rather than downgrades, a protected write.

### Existing Table Fill regression tests

- Layer 1 extraction and Layer 4 execution remain deterministic;
- MOD Catalog discovery and retained business scripts still work behind the new boundary;
- MOD Gate skip rules and the mandatory Execution Gate preserve approved business behavior;
- existing V2 tests that assert V2 state filenames or markers are replaced by V3 contract tests, not weakened to accept both models.

Every integration test must assert an observable artifact or blocked mutation. Passing logs or self-reported Adapter decisions are not sufficient evidence.

## Preserve, replace, and defer

| Disposition | Assets |
| --- | --- |
| Preserve | MOD Catalog business entries, MOD Markdown documents, reusable extraction/mapping/execution scripts, OfficeCLI-only mutation rules, business acceptance checks |
| Replace | V2 run-state authority, prompt-only Gate confirmation, V2 Gate markers, any direct-write path that bypasses Runtime authorization in `HOST_GOVERNED` mode |
| Add | Host-independent Workflow Runtime, typed Runtime operations, OpenCode V1 Adapter, conservative tool classifier, protected-path registry, Gate basis/digest and invalidation, session binding/resume |
| Defer | Codex Adapter, multi-host compatibility suite beyond the Runtime contract, remote service topology, generic policy language, multi-user concurrency |

## Planning invariants

The implementation plan must satisfy all of the following:

1. No product-code or OpenCode configuration write occurs before the plan approval gate.
2. Runtime domain logic has no dependency on OpenCode tool or hook types.
3. OpenCode-specific behavior is isolated behind one Host Adapter boundary.
4. Tests are written against public Runtime operations and real Adapter hook inputs, not internal state mutation.
5. The first end-to-end proof blocks an actual attempted protected write before claiming fail-closed governance.
6. V2 state compatibility is not added. Preserved business assets are imported through explicit V3 paths.
7. New security or orchestration mechanisms require a demonstrated contract failure and a new decision record.

## Open decisions

None at the architecture-contract level. Reversible implementation defaults, file layout, and exact test commands belong in the approval-ready work-plan draft and remain subject to the plan approval gate.
