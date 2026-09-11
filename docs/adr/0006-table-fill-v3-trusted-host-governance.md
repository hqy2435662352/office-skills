# Adopt trusted-host governance for Table Fill V3

## Status

Accepted on 2026-08-06. This ADR records the decisions completed during the Table Fill V3 architecture grilling. It authorizes planning, not implementation.

## Context

Table Fill V2 expresses its workflow and Human Gates primarily through Skill instructions and deterministic scripts. Those mechanisms can guide a cooperative Agent, but they cannot prove that a real user supplied a Gate decision, prevent a different write-capable tool from bypassing the expected script, or keep stale approvals validly bound across changed artifacts and resumed host sessions.

V3 needs a narrow guarantee: when the host exposes the required interception and human-interaction capabilities, writes to the exact artifacts registered by a Table Fill run must fail closed unless the current run state authorizes them. The design must preserve Table Fill's business workflow and MOD assets without turning the system into a generic policy platform.

## Decision

Table Fill V3 adopts a `Skill + Workflow Runtime + Host Adapter` shape.

- The **Skill** remains the human-readable business procedure and Agent guidance.
- The **Workflow Runtime** is the trusted, host-independent authority for run state, Gate state, exact protected paths, MOD snapshots, invalidation, and authorization decisions.
- A **Host Adapter** is a thin host-specific integration. It binds a Host Session to one Governed Run, classifies tool calls, blocks unauthorized protected writes, collects native human responses, and submits those responses to the Runtime.

The architecture is governed by eight contracts.

### Contract 1: capability truth

Every run reports one Governance Mode: `HOST_GOVERNED`, `SKILL_ONLY`, or `UNSUPPORTED`. Only `HOST_GOVERNED` claims fail-closed enforcement. A caller may set `require_host_governance`; if the host cannot satisfy it, the run does not start.

### Contract 2: narrow enforcement

The Host Adapter implements a thin Protected Write Guard. V3 does not introduce a broker service, daemon, capability token, signature scheme, RPC layer, generic Action DSL, or workflow-wide command proxy. Mechanism follows demonstrated failure.

### Contract 3: exact scope and invalidation

Governance applies only to normalized exact absolute paths registered by the current Governed Run. It is not a workspace-wide sandbox and does not infer protection from files merely mentioned in chat.

Changes to Layer 1 or Layer 2 governed artifacts invalidate dependent downstream Gate confirmations. A Layer 3 semantic change requires the MOD Gate basis to be current. Delivery writes require a current Execution Gate confirmation.

### Contract 4: deterministic Gate identity

Each pending Gate has a canonical Gate Basis and deterministic `basis_digest`. A confirmation is accepted only for the matching run, Gate identity, Host Session binding, and digest. MOD selection captures an immutable Run-Local MOD Snapshot; later Catalog revisions do not change an active run.

### Contract 5: human authority boundary

The Agent may request a Human Gate but may not submit or manufacture its confirmation. The Host Adapter collects a response through the host's native human interaction surface and submits it to the Runtime only after the host reports a real Human Decision.

For the first reference implementation, OpenCode's native `question` tool is the interaction surface. The Adapter observes the completed response in `tool.execute.after` and then calls the Runtime confirmation operation. Chat text alone is never confirmation evidence.

### Contract 6: session binding

A Governed Run is bound to exactly one current Host Session. Resume is an explicit Runtime transition that replaces the binding; the former Host Session becomes invalid immediately and cannot confirm Gates or authorize protected writes.

### Contract 7: conservative tool classification

Every intercepted tool call is classified as `READ_ONLY`, `WRITE(paths)`, or `UNKNOWN`. `READ_ONLY` must be positively proven. A known write is checked against current protected paths and run authorization. An opaque or unclassifiable mutator is rejected when it may reach protected state in a governed session.

### Contract 8: clean state cutover

V3 run state starts in a new format. V2 run state is not migrated or accepted as V3 authority. Existing MOD Catalog entries, MOD documents, and reusable business scripts are preserved as business assets and may be adapted behind the V3 Runtime contract.

## Reference host sequence

OpenCode stable V1 is the first Host Adapter and contract-test target. Codex is deferred until the host-independent Runtime and OpenCode adapter prove the boundary. This sequencing decision does not make OpenCode behavior part of the Runtime domain model.

## Non-goals

- Governing arbitrary workspace writes unrelated to a registered Table Fill run.
- Treating prompt compliance or Skill text as a security boundary.
- Building a generic plugin framework, policy language, or multi-tenant authorization service.
- Migrating V2 run state or preserving unreleased V3 draft formats.
- Allowing Catalog updates to mutate the semantics of an active Run-Local MOD Snapshot.
- Letting an Agent, tool result, or ordinary chat acknowledgment stand in for a host-collected Human Decision.

## Alternatives rejected

### Skill-only enforcement

Rejected as the guaranteed mode because a cooperative prompt cannot intercept all host writes or prove the provenance of a Gate decision. It remains available only as the explicitly non-guaranteed `SKILL_ONLY` mode.

### Broker service with signed actions or capability tokens

Rejected because the demonstrated failures require host interception, state ownership, and stale-confirmation rejection, not a new distributed trust protocol. The additional lifecycle and secret-management burden would exceed the current threat model.

### Workspace-wide path policy

Rejected because Table Fill owns only its registered run artifacts. Broader policy would create false blocks, unclear ownership, and a generic authorization surface unrelated to the workflow.

### Live MOD Catalog references

Rejected because a Catalog revision during a run could silently change approved business semantics. A Run-Local MOD Snapshot makes the approved basis stable and auditable.

### Backward-compatible V2 state migration

Rejected because V2 state was not produced under the trusted-host contract. Treating it as equivalent authority would weaken the V3 boundary and add speculative compatibility machinery.

## Consequences

- `HOST_GOVERNED` has a precise, testable meaning rather than a prompt-level promise.
- The Runtime and Host Adapter become separate failure and test surfaces, while the Skill stays readable and business-focused.
- OpenCode-specific tool schemas and hook behavior remain outside the host-independent Runtime.
- Resumes, artifact changes, and Catalog revisions require explicit state transitions instead of implicit trust.
- Some hosts will honestly report `SKILL_ONLY` or `UNSUPPORTED`; capability truth is preferred over a false guarantee.
- Existing V2 run artifacts cannot resume as V3 Governed Runs, but MOD business knowledge remains reusable.
- New mechanisms must be justified by an observed contract failure rather than added preemptively.
