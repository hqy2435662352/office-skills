# Bound capability resolution in Table Fill V2.5

## Status

Accepted on 2026-08-13 following a grilling session with the `grilling` and
`domain-modeling` skills. This ADR authorizes specification and planning, not
implementation.

## Context

Table Fill V2.5 already directs Agents away from implementation-source reading
and toward contracts, capability queries, Compiler probes, and validated drafts.
In practice, this constrained the method of investigation without providing a
strong stopping rule. Agents could still write repeated tests and spikes that
eventually re-proved behavior already covered by the Skill, adding substantial
Exploration Tax to otherwise short runs.

A strict ban on all in-task investigation would reduce that cost but would also
lower completion rates while table-fill is still growing. Some real tasks expose
a narrow, task-blocking mechanism question that has no applicable standard
evidence channel and no business-equivalent supported path.

## Decision

Table Fill V2.5 adopts evidence-fitted, bounded capability resolution in
Table-Fill Task Mode.

- Capability knowledge has exactly three states: Known Supported, Known
  Rejected, and Capability Unknown. Evidence resolves a question only when its
  channel has direct Evidence Fit, and the resulting claim cannot exceed the
  evidence's scope.
- Known Supported behavior is used without extra capability-seeking actions.
  This never bypasses formal compile, execution, Validated Draft, readback,
  structural verification, or required Render QA.
- Canonical Patterns are preferred construction paths, not capability
  whitelists. Known Supported capabilities are presumed composable unless the
  authoritative contract declares a conflict or constraint; formal compile
  validates the concrete composition.
- Each Table-Fill Run may use at most one Extra Capability Probe, only for a
  task-relevant Compiler-acceptance architecture fork that existing capability
  authority cannot answer. A dispositive result ends that Capability Question.
- Each Table-Fill Run may use at most one Bounded Rescue: one predeclared,
  task-blocking Capability Unknown, one Black-Box Rescue Experiment, and one
  verdict. Rescue is eligible only when the question is unresolved, blocks the
  task, has no Standard Evidence Path, and has no Known Equivalent Adaptation.
  It does not inspect implementation, run the Skill test suite, modify the
  Skill, or increase the existing REPAIR budget.
- Probe and Rescue budgets are independent because their question domains are
  different, but one Capability Question may consume only one of them.
- A successful Rescue produces Run-Local Capability Evidence, disclosed briefly
  at the Execution Gate. It does not establish cross-run support. A Capability
  Gap Discovery may be triaged after the run and becomes formal knowledge only
  through later, user-directed Capability Institutionalization.
- If Rescue cannot produce Sufficient Evidence, ASK is permitted only to choose
  between multiple safe Known Supported paths whose differences are business
  trade-offs. User confirmation cannot turn a Capability Unknown into technical
  permission; without a provably safe path, the run stops.
- Only the user may change the primary goal from Table-Fill Task Mode to Skill
  Development. Capability Unknown, Rescue, Contract Drift, and perceived reuse
  value do not authorize an Agent to switch modes.

For table-fill, normal execution against the retained Validated Draft plus its
deterministic verifiers is the preferred controlled attempt required by ADR
0005. A separate preflight experiment is not added merely because an object or
composition appears complex.

## Consequences

- V2.5 remains prompt- and document-governed; it makes the desired cognitive
  path explicit but does not claim runtime enforcement. V3 trusted-host policy
  remains governed by ADR 0006.
- Existing uncertainty-handling prose must be replaced atomically rather than
  layered with precedence rules. Confirmed mechanical facts remain; open-ended
  TASK MODE source reading, test-suite inspection, and spikes do not.
- A small, on-demand capability-evidence reference becomes the detailed source
  for the three states, Standard Evidence Paths, Probe, and Rescue. The main
  Skill retains only the short execution algorithm and budgets.
- The initial implementation also adds only high-value Canonical Patterns that
  came from real Validated Drafts and remove meaningful future design work. It
  does not enumerate supported feature combinations.
- Success is evaluated without new runtime instrumentation: task outcome, Agent
  wall time, Extra Capability Probe count, Redundant Exploration count, and
  whether Bounded Rescue occurred and its approximate duration. The target is
  lower wall time and near-zero Redundant Exploration without a material drop in
  completion rate.

## Alternatives Rejected

### Stop whenever standard evidence is absent

Rejected because a small task-critical black-box check can sometimes complete a
run safely, and table-fill still benefits from real-task capability discovery.

### Allow open-ended minimal exploration

Rejected because “minimal” leaves the Agent discretion to change questions,
experiments, and proof goals, recreating the repeated exploration this decision
is intended to remove.

### Implement V3 runtime enforcement now

Rejected for V2.5 because the required interception, state, and observability
work is materially larger and already belongs to the V3 architecture in ADR
0006.
