# 0010-table-fill-task-shape-routing

# Table-fill task-shape routing: shape×route decoupling and Grid fast path

Context: table-fill is the unified intent entry for spreadsheet-to-template fill tasks. After officeval_087 it gained a grid_record/form_content split (V1). We decide that **task_shape (workload essence) and route (execution choice) are orthogonal**: shape ∈ {grid_record, form_content, mixed, uncertain}, route ∈ {fillspec, officecli_native, combined}; obvious Grid takes a zero-cost fast path, bounded grid-shaped edits route Direct to officecli_native, and mixed workloads run as `combined` — one run, one Final Gate placed after all writes — never a third engine.

Why: FillSpec applicability ≠ execution justification (three fixed cells are grid-shaped but do not justify the Grid pipeline), and rare-path routing must not tax the common Grid path (the dominant customer workload). "Obvious" routing decisions must stay obvious — the fast path is a stop-rule ("不得继续分析"), not a new classification step.

**Considered Options**
- 1:1 shape→route binding with new shape values (`direct`, `mixed` each with a fixed route) — rejected: `direct` is an execution decision, not a workload essence; binding the two dims re-conflates capability semantics with execution strategy.
- `hybrid` as the combined route name — rejected: collides with FILLSPEC's "hybrid overflow" (inplace position model, an unrelated concept).
- Symmetric classification of every task (grid/form/mixed compared fairly each run) — rejected: adds LLM/tooling tax to the dominant Grid workload, which customers already report as slow.

**Consequences**
- The Execution Gate is the final-draft gate: in `combined` runs, officecli finishing precedes the single Gate, preserving promote's post-Gate hash binding; ownership (one side effect, one executor) is an agent constraint, not a DSL or artifact.
- `task_shape.json` evidence is short snake_case codes (e.g. `["obvious_grid"]`), not narrative — routing must not become a reasoning tax.
- Sentinel tests guard the vocabulary and matrix shape (combined-not-hybrid, mixed shape, direct legal combination, APPLICABLE/NOT_APPLICABLE split), so the decoupling cannot silently drift back.
