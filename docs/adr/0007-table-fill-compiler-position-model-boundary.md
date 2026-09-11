# Table-fill Compiler position-model boundary: inplace reuse, group merges, absolute writes

## Status

Accepted on 2026-08-12 following a grilling session with the `grilling` + `domain-modeling` skills. This ADR records the locked design (Q4–Q13) for the Compiler's position-model boundary. It authorizes documentation and planning, not implementation — implementation is scheduled for a follow-up session per the handoff document.

## Context

Table Fill V2's Compiler (`compile_fill.py`) has exactly one row-layout model: clone-append. A `data` role always emits `add --from <template_row>` rows after `base_last_row`; fills/merges/nulls/formulas operate only on those newly cloned rows; `remove_rows` is an absolute-row mechanism whose semantics are "delete after append".

The customer quotation scenario (MXP quote template, sheet `ATLAS Quotation`: 18 pre-formatted placeholder rows 7–24, no formulas, customer-facing so no cost data, group merges on columns A/F, Total row 25, features section 26–40) required a manual script (`run_mxp_quote.py`) because the Compiler could not express:

- **B1** — fill existing placeholder rows in place and delete surplus rows;
- **B2** — rebuild data-driven, non-uniform group merges (the template's A/F merges group by product family; boundaries come from the data, not from `1:{n}`);
- **B3** — write cells outside any block (A4 customer name, F4 issue date, A31 port clause);
- **B4** — data-driven per-group labels (column A type texts);
- plus per-column style props (price column `$#,##0.00`, absent from the template).

The manual run also exposed execution hazards: single-cell merge residue after row deletion (`A19:A19`) broke re-merging; officecli `validate` is blind to merge residue.

## Decision

### Modeling principle

`CONTEXT.md` defines what a concept **is**; this ADR carries processing rules, compile invariants, error conditions, and lowering algorithms. The seven terms — Row Layout Mode, Placeholder Region, Placeholder Residue, Trim, Group, Group Merge, Absolute Cell Write — are recorded in `CONTEXT.md` with definitions only.

### 1. Row Layout Mode

A data role's placement semantics. `mode: inplace` (locked name) consumes pre-existing template rows; the default (`append`) is unchanged and backward compatible.

```yaml
clone_roles:
  - role: data
    mode: inplace
    start_row: 7          # template coordinate
    capacity: 18          # explicit declaration (required)
    template_row: 7       # clone format source for overflow rows (required)
```

- **Capacity is explicit** (Q4): the LLM reads it from the digest; the Compiler validates `start_row + capacity - 1 <= digest rows` and that the region exists.
- **`start_row` is a template coordinate, always** (Q7): the spec never computes shifted row numbers. Coordinate stability is guaranteed by two structural constraints, not runtime row-tracking: (a) append blocks' structural row mutations are legal only in the append zone (new gate, see invariants), and (b) no preceding operation may touch the region. The Placeholder Region is coordinate-stable until the terminal inplace block begins executing.
- **Hybrid overflow** (Q5): when matched source rows `N > capacity`, the region is filled and `N - capacity` rows are cloned from `template_row` after the region (the Total row shifts down naturally). When `N < capacity`, **Trim** removes the surplus tail rows (Q6: always the tail, compiler-derived, not expressed via `remove_rows`). Trim and overflow are mutually exclusive directions of the same count comparison.

### 2. Block ordering (Q7)

A target may contain at most one inplace block, and it must be the **last** block. Append blocks may precede it. Rationale: Excel row insertion/deletion shifts subsequent rows; a following append block's insertion point would be ambiguous after a trimmed region. Mixed-before-inplace displacement cannot occur mechanically because append adds land below `base_last_row` (below the region); the remaining risk (absolute removes in earlier blocks) is closed by compile checks.

### 3. Group Merge (Q8)

```yaml
group_merges:
  - col: A
    group_by: A          # target column's logical materialized value
    style: label
  - col: F
    group_by: A
    label: ""            # required when the column has no mapping ("" = clear anchor)
```

Locked rules:
1. `group_by` references a **target column's per-row logical materialized value** (post lookup/transform), forming Groups as **consecutive equal-value runs** — not a global distinct-value grouping. The `group_by` column must have a column mapping (`GROUP_BY_COLUMN_UNMAPPED` otherwise).
2. Mapped group columns: the anchor cell gets the group's materialized value; non-anchor mapped writes are suppressed **and the cells are explicitly cleared** — "skip" never means "leave untouched", keeping the Placeholder Residue invariant closed.
3. Unmapped group columns: `label` is required (including `""` = clear); missing label is `GROUP_MERGE_ANCHOR_UNCOVERED`.
4. `merges` (`1:{n}` block-wide) and `group_merges` are mutually exclusive per column (`MERGE_MODE_CONFLICT`).
5. Rebuild algorithm (lowering, deterministic — the LLM declares, the Compiler expands): ① materialize rows/lookups/transforms → ② compute Group boundaries → ③ unmerge every existing merge overlapping the column's data region (including single-cell merge residue such as `A19:A19`) → ④ clear non-anchor cells and write anchors (mapped value or label) → ⑤ merge ranges of length > 1; **singleton Groups never create a merge** (and residue singleton merges are removed, never recreated).
6. `group_merges` is a **general block capability** (append and inplace alike): it fixes V2's structural limitation that `merges: 1:{n}` can express only one group per block per column (which under-served multi-product-group blocks in the quotation-summary MODs).

### 4. Absolute Cell Write (Q9)

```yaml
sets:
  - path: A4
    value: "To Messrs: MXP"
  - path: B25
    value: null          # explicit clear (EMPTY readback)
```

Locked rules:
1. Target-level list; `path` accepts a bare cell coordinate (xlsx) or a full DOM path (pptx).
2. `sets.path` is a **template coordinate** (Q9): the spec never computes post-shift row numbers.
3. `value: null` is an explicit clear (EMPTY readback), not a no-op; `key_outputs` may reference set cells.
4. Write ownership enters the global registry — any collision (block writes, fills, nulls, group anchors, other sets) is `DUPLICATE_TARGET_WRITE`.
5. `sets` may only target existing template coordinates within digest bounds; it is not a layout-creation mechanism.
6. Execution is a **phase invariant** (Q9): append blocks → sets → terminal inplace block's structural operations. Excel's natural row shift then relocates set cells (e.g. A31 moves up with the trimmed region) without the Compiler tracking coordinates. `sets` must not target the Placeholder Region or other block-owned mutable regions (region-overlap check).
7. V1 values are literal or null only — no formulas (formula-on-absolute-cell is future work).

### 5. Controlled style props (Q9)

`columns[].props` (applied to every materialized cell of the mapping) and `sets[].props` (applied to the single cell) support a **whitelist**: V1 locks `numberformat`. Value semantics and presentation semantics are orthogonal — `value: null` with `props.numberformat` is legal. The whitelist prevents growth into a full style engine.

### 6. Compile invariants (Q10) — 12 codes

| Code | Check |
|---|---|
| `INPLACE_MULTIPLE_BLOCKS` | more than one inplace block per target |
| `INPLACE_NOT_LAST_BLOCK` | a block follows the inplace block |
| `INPLACE_REGION_OVERLAP` | a preceding operation (add/remove) or `sets` touches the region; ADR note: coordinate stability = **append-zone legality + region-overlap check jointly** |
| `STRUCTURAL_OP_OUT_OF_ZONE` | any structural row op outside the terminal inplace block targets rows `<= base_last_row` |
| `INPLACE_REGION_OUT_OF_BOUNDS` | `start_row + capacity - 1` exceeds digest rows — a **model-fact contradiction** (the spec claims template rows that do not exist), not a source-count mismatch |
| `INPLACE_NO_CLONE_SOURCE` | inplace data role without `template_row` |
| `PLACEHOLDER_RESIDUE_UNHANDLED` / `_PARTIAL_NULLS` | per **retained placeholder row** baseline (each row individually, not a single template row); coverage sources: per-row fills, nulls, per-row formulas, group anchors/labels — **not** `sets` (Q9 forbids sets inside the region) |
| `GROUP_MERGE_ANCHOR_UNCOVERED` | group column without mapping and without `label` |
| `GROUP_BY_COLUMN_UNMAPPED` | `group_by` column has no column mapping |
| `MERGE_MODE_CONFLICT` | `merges` + `group_merges` on the same column |
| `DUPLICATE_TARGET_WRITE` | judged by **logical owner**: group-aware materialization is one owner, not two (the column mapping remains the value owner; group_merge only changes its materialization strategy) |
| `SET_OUT_OF_BOUNDS` | `sets.path` beyond digest dimensions |

**Double residue baseline**: a mixed inplace block carries two baselines — retained placeholder rows are checked against each row's own original values (Placeholder Residue); overflow cloned rows are checked against `template_row` (existing Clone Residue checks). Both run in one compile.

**Readback postconditions** (not compile-time): the plan precomputes `expected_final_row_count` from **all** structural deltas (base rows + Σ append deltas − trim count + overflow clone count) and readback compares it against the actual extent (`FINAL_ROW_COUNT_MISMATCH`). Group boundaries must be asserted in readback: officecli `validate` is blind to merge residue (evidence: xlsx `A19:A19`; pptx dangling `vMerge`).

### 7. Failure & Recovery Policy (Q11)

- **Four outcomes**: `ADAPT` (business variation is normal capability: overflow→clone, shortfall→trim, residue→overwrite/clear, multi-group→group_merges) / `REPAIR` (spec problems: aggregated diagnostics → one repair) / `ASK` (multiple safe interpretations) / `STOP` (no provably safe plan). **Compiler Error ≠ User Failure**.
- **Three layers**: L1 adaptive behavior (no error); L2 self-repair loop with **aggregated diagnostics** (independent static checks report all at once; only blocking failures fail fast); L3 human escalation — unique safe solution → ADAPT/REPAIR; multiple safe solutions → ASK; no provable safe solution → STOP.
- **Repair budget is normative, not aspirational**: at most **one** aggregated REPAIR round; a second compile failure must be reclassified as ASK or STOP. KPI: 0 repair loops on the happy path, ≤ 1 otherwise.
- **Problem placement**: known high-frequency patterns → MOD (positive recipes); LLM-likely dangerous shortcuts → TRAP.md; mechanically provable → Compiler invariants; deterministic execution steps → Compiler lowering; discoverable + clearly repairable → one aggregated repair; multiple business interpretations → ASK; unprovable safety → STOP.
- **Error-code design criterion**: every new code must answer "why can't this be eliminated in an earlier layer?" — e.g. capacity-exceeded is ADAPT, never an error; `INPLACE_REGION_OUT_OF_BOUNDS` only expresses model-fact contradiction.
- **Error UX is two-layered**: machine contract (stable codes + structured fields for Agent/Compiler) and user message (title / explanation / location / remediation). The Compiler defines standard message templates per code; the LLM only naturalizes concrete context (sheet/cell/range/original value). No improvised explanations.

### 8. PPTX capability parity (Q12, "C'")

**Domain capabilities are platform-neutral; platform support is determined by verified lowering capabilities, not by excluding the platform from the schema.** `mode: inplace` (the pptx model is already pre-built-row fills), `sets` (pptx DOM cell paths), and `group_merges` are all in scope for pptx. Capability spike verdict (officecli 1.0.143, fixture table):

- row add / row remove: work mechanically; **removing a merge-anchor row leaves dangling `vMerge` continuations that validate does not flag** — the lowering must clean them;
- unmerge exists but is multi-step, not a single verb: anchor `rowspan=1` + per-continuation `vmerge=false`;
- remerge works: `merge.down=N` sets **total span N+1** (mirrors `merge.right=N`) — must be documented in lowering (a naive `merge.down=2` produced a 3-row span that swallowed the next group's label cell);
- readback of merge state via `rowspan`/`vmerge` get-props works and is mandatory (validate is merge-blind);
- `view html` renders merge semantics and is available for both formats.

### 9. Capability-aware Render QA (Q13)

Deterministic QA always runs first (validate + issue-delta + readback + structural assertions). Render QA then branches on model capability: **multimodal models render PNG and inspect visually (primary path); text-only models fall back to `view html` structural render checks and must not claim visual verification**. Only affected regions are rendered (sheet / slide range). Visual QA is a **detection layer with closed defect classes** (clipping/truncation, overlap, placeholder leakage, merge artifact, gross alignment break, unexpected blank, obvious style loss) — never an aesthetic-optimization loop. It is single-shot and terminal, and consumes the Q11 repair budget (no second automatic repair round on visual defects).

### 10. Help-first (Q13)

When officecli property names, argument semantics, element capabilities, or merge/remove behavior are uncertain, run `officecli help <format> <element>` before generating related ops; never guess unconfirmed command semantics. Known behavior discovered by spikes goes into lowering/MOD/TRAP documentation instead of being re-discovered at runtime (spike findings in section 8 are first candidates).

## Consequences

- `fill_spec.yaml` schema grows: data-role `mode/start_row/capacity/template_row`, `group_merges`, `sets`, `columns[].props`/`sets[].props` (numberformat whitelist); plan `schema_version` 2 → 2.5 (v3 is reserved for the plugin-化 generation). Missing `mode` keeps append behavior (backward compatible).
- FILLSPEC.md / SKILL.md / TRAP.md / MOD docs are updated **atomically with the implementation** to avoid doc-code drift; this ADR is the authoritative design record until then.
- The MXP quote template reproduction package is the acceptance target for the end-to-end test (13 products, trim 5 rows, group merges rebuilt, absolute sets A4/F4/A31, E-column numberformat).
- PPTX lowering for group_merges/sets is verified against the spike fixture before rollout.

## Implementation note (2026-08-12)

Implemented per the handoff: compile_fill.py v2.5 (inplace layout / group_merges lowering / sets phase invariant / 12 error codes / double residue baseline / `expected_final_row_count`), execute_batch.py structural readback + Render QA, 26 unit tests + the MXP end-to-end acceptance test (all 70 pass, draft structurally equivalent to the v4 snapshot). Two fixes surfaced during the run: selectors are validated against the **source** column count (not the target's), and `execute_batch` explicitly closes the draft after chunked execution — the coordinate probe starts a resident, later chunks apply in memory, and a taskkill before the deferred flush dropped the tail chunk's writes (E15–E19 missing).

**MOD boundary ruling (user, 2026-08-12)**: a "placeholder template" MOD was proposed and **rejected**. The boundary: *table-fill defines how tables are handled; MOD defines what the business scenario means*. inplace/trim/overflow/group_merges/sets/numberformat are generic capabilities of the Skill body (FILLSPEC/Compiler/readback) and must NOT be re-declared per scenario; only true quotation business knowledge (e.g. valid price > 0, product-family definition and ordering, family→customer label translation, client/date/port clause field semantics, business fallbacks) belongs in a future `MOD_quotation` / `MOD_product_quotation`, to be created only when enough business rules accumulate. This ADR's Q11 problem placement stands: high-frequency known *business* rules → MOD; generic table mechanics → SKILL/FILLSPEC/Compiler; LLM-dangerous shortcuts → TRAP.
