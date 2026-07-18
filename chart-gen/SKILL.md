---
name: chart-gen
description: |
  Creates charts for existing Excel (.xlsx) files. MUST USE this skill whenever the user mentions creating charts, graphs, visualizations, column charts, bar charts, line charts, pie charts, or any request related to adding charts to xlsx files. Trigger even when the user does not explicitly say "chart" or "graph" — if the task involves visualizing xlsx data, this skill applies.

  Not applicable for: creating xlsx from scratch (no data source), modifying existing chart styles (use officecli set directly), PPT charts (out of scope).
license: MIT
compatibility: |
  Required: officecli on PATH, Python 3.8+, Windows
  Optional: references/CHART_TYPES.md, references/CHART_PRESETS.md
---

# Chart Gen

Creates charts for existing xlsx data files. 3-step workflow: Analyze → Confirm → Generate. One chart per invocation.

**Why this structure matters**: Chart series structure is immutable after creation. If data range inference is wrong, the only fix is to delete and rebuild. The Human Gate lets the user validate data ranges before generation, avoiding costly rework.

---

## Hard Constraints

- **Data read-only**: Step 1 uses only `officecli get` to read; never `set`
- **Data untouched**: Step 3 only `add chart`; never modify any data-region cell
- **In-place edit**: Output is the input file itself; no copies
- **officecli only**: Chart creation uses `officecli add chart` exclusively; never openpyxl chart API
- **Python subprocess**: All officecli calls use Python `subprocess.run()`; never PowerShell pipes
- **Preset-then-override**: `add --prop preset=X` first, then `set` to override properties. Never pass preset and conflicting properties together on add
- **One chart per invocation**: Each call creates exactly one chart. Multiple charts require multiple independent invocations
- **chart-series immutable**: If a wrong series is created, delete the entire chart and rebuild. Never attempt to fix series structure with `set`
- **Rejection = re-analysis**: If the user rejects the recommendation at the Human Gate, return to Step 1 for fresh analysis. Never repeat the same recommendation
- **Close on start**: Run `officecli close <file>` before every operation (release lingering locks)
- **Close on end**: Run `officecli close <file>` after every operation (release to downstream)
- **Query before set**: Before any `officecli set` on a chart, run `officecli query <file> chart --json` to locate the target index by `title`/`anchor`. Never assume a new chart lands at a fixed index like `chart[2]`
- **Auxiliary table uses formulas**: Auxiliary table cells must use `=Sheet1!C12` formula references to source data; never hardcode values
- **File lock reported immediately**: When a file is detected as locked by WPS/Excel, immediately report: "File is locked by WPS/Excel. Please close the file and retry." Never silently retry
- **Prefer explicit binding**: Use `categories` + `seriesN.name` + `seriesN.values` for all charts where the source structure can be expressed this way (even non-contiguous columns). Use `dataRange` auto-inference only as a fallback when explicit binding cannot express the source structure (e.g., categories themselves need reconstruction, multi-sheet combination, or value transformation required)

---

## Exit Code Protocol

| Exit | Meaning | Action |
|------|---------|--------|
| 0 | Pass | Continue |
| 1 | Fatal (file missing, environment issue, file locked) | STOP, report to user |
| 3 | Retryable (prerequisites unmet, data validation failed) | Apply corrective_action, retry |

Error output format (four-segment; required):
```
[CHART_GEN_ERROR] ROOT_CAUSE
[CHART_GEN_ERROR] CORRECTIVE_ACTION
[CHART_GEN_ERROR] CONTEXT
```

---

## Step 1: Analyze

**Precondition**:
- `officecli close <file>` executed, exit 0
- Input file exists and is readable

**Analysis flow**:
1. Record existing charts: `officecli query <file> chart --json`
2. Read xlsx structure and data content (`officecli get`, `--depth 0`)
3. Infer recommended chart type, data range, series config, style preset, and position
4. Sample head and tail of the inferred dataRange (first 3 + last 3 rows, inline `officecli get`), verify non-empty
5. Output `flat_output/{name}_chart_proposal.yaml` (based on `assets/chart_proposal_template.yaml`)

**Data shape detection (must complete in Step 1)**:

- **Binding mode selection**: Determine whether the source structure can be expressed as explicit `categories` + `seriesN.values` bindings. Decision flow:
  ```
  Can categories be expressed as one column range?
    YES → Can each series values be expressed as one column range (even non-contiguous)?
        YES → binding_mode = "explicit"  (PREFERRED — always choose this first)
        NO  → binding_mode = "dataRange" with auxiliary_table
    NO  → binding_mode = "dataRange" with auxiliary_table (categories need reconstruction)
  ```
  In `binding_mode = "explicit"`, set `chart_options[].binding_mode = "explicit"` and populate `explicit_binding` with `categories_range` and one `series` entry per series (each containing `name` and `values_range`). The `data_range` field is left empty. This mode handles non-contiguous series columns (e.g., B, D, F with C, E skipped) without auxiliary tables.
- **Auxiliary table (fallback only)**: Use only when explicit binding cannot express the structure — categories need reconstruction (pivot-table row labels spread across columns), multi-sheet data combination, formula auditability required, or value transformation needed before charting. When triggered, set `auxiliary_table.needed = true`. Auxiliary table cells use formulas like `=Sheet1!C12` referencing source data.
- **Magnitude differences**: If series values in the same dimension differ by ≥2 orders of magnitude (e.g., 100 vs 10,000), recommend splitting into multiple charts rather than using a log axis. Mark `split_reason` in the proposal.
- **Anchor position**: Default to placing the chart to the **right** of the data region, with at least a 2-column gap. E.g., if data is at A12:M16, anchor starts at O12 or further right. Never place the anchor over or on top of source data.
- **Multi-chart scenarios**: Step 1 may output a `chart_options` array (multiple alternatives), each independently evaluated for data range and chart type.

**Postcondition assertions**:
- `flat_output/{name}_chart_proposal.yaml` created and YAML is valid
- All series in the traceability section have non-empty samples_first_3 / samples_last_3
- If auxiliary table is triggered, `auxiliary_table.needed = true` and `source_cells` are populated
- Anchor does not overlap existing charts or data regions

**Status anchor** (must output after completion):
```
[CHART-GEN: S1=DONE, Proposal=flat_output/{name}_chart_proposal.yaml, ChartOptions=N, Gate=pending, Next=S2]
```

---

## Step 2: Confirm ⛔ HUMAN GATE

**Precondition**: `python scripts/layer_gate.py --target 2 --workdir flat_output/` exits 0.

Read the proposal.yaml and present the recommended configuration + traceability table to the user.

**Multi-chart selection flow** (when `chart_options` has multiple entries):
- Present each option's recommended type, data range, and rationale one by one
- User selects which to generate first; set `selected_index`
- Remaining charts are generated in subsequent independent invocations

### Traceability Table: Chart Recommendation Verification

| # | Series Name | Data Range | Sample Values (first 3) | Sample Values (last 3) | Recommended Chart Type | Rationale |
|---|------------|------------|------------------------|-----------------------|----------------------|-----------|
| 1 | (from proposal) | (from proposal) | (Step 1 sampling) | (Step 1 sampling) | (from proposal) | (from proposal) |
| — | Category Axis | (from proposal) | (Step 1 sampling) | (Step 1 sampling) | — | Used as X-axis category labels |

> How to verify: Open the source xlsx and compare each series' "Sample Values" against the actual source data. If they don't match, the inferred data range is wrong — manually specify the correct range.

The user may confirm, modify, or reject:
- **Confirm**: Proceed to Step 3, mark `confirmed: true`
- **Modify**: Update corresponding fields in proposal.yaml (e.g., dataRange, chart_type), re-sample and validate
- **Reject**: Return to Step 1 for fresh analysis (never repeat the same recommendation)
- **Multi-chart confirmation**: Set `selected_index` to the option to generate now

After confirmation, run: `python scripts/layer_gate.py --confirm-gate 1 --workdir flat_output/`

**Postcondition assertions**:
- `confirmed: true` written to proposal.yaml
- If multi-option, `selected_index` is set
- `layer_gate --target 3` passes normally

**Status anchor** (must output after completion):
```
[CHART-GEN: S2=DONE, Gate=CONFIRMED, SelectedIndex=N, Next=S3]
```

**End your response here. Do NOT continue to Step 3 until the user replies.**

---

## Step 3: Generate + Verify

**Precondition**: `python scripts/layer_gate.py --target 3 --workdir flat_output/` exits 0, and the proposal contains `confirmed: true`.

### Auxiliary Table Preparation (if proposal.auxiliary_table.needed == true)

Before chart creation, build the auxiliary table at the `target_range` specified in the proposal:
1. Use `officecli set` to write `=SourceSheet!SourceCell` formulas cell by cell
2. Change the chart's dataRange to reference the auxiliary table's contiguous region

### Explicit Binding (primary — use whenever possible)

When proposal uses `binding_mode: explicit`, `dataRange` is **not used**. Instead, pass `categories`, `seriesN.name`, and `seriesN.values` as independent parameters.

```python
import subprocess, json

# Build props from proposal.explicit_binding
props = [
    'officecli', 'add', filepath, '/SheetName', '--type', 'chart',
    '--prop', f'chartType={chart_type}',
    '--prop', f'categories={explicit_binding["categories_range"]}',
    '--prop', f'title={title}',
    '--prop', f'preset={preset}',
    '--prop', f'anchor={anchor}',
]
for i, s in enumerate(explicit_binding["series"], 1):
    props.extend([
        '--prop', f'series{i}.name={s["name"]}',
        '--prop', f'series{i}.values={s["values_range"]}',
    ])

subprocess.run(props + ['--json'], capture_output=True)

# Query to find real index (never assume a fixed index)
charts = json.loads(subprocess.run(
    ['officecli', 'query', filepath, 'chart', '--json'],
    capture_output=True).stdout.decode('utf-8'))
# Locate real_idx by title/anchor from the response

# Set to override preset-inherited properties
subprocess.run([
    'officecli', 'set', filepath, f'/SheetName/chart[{real_idx}]',
    '--prop', 'legend=bottom',
    '--json'
], capture_output=True)
```

> **Why explicit binding is preferred**: It creates exactly the intended series with no ghost artifacts. With `dataRange` auto-inference on pivot tables, officecli can pick up extra columns like `汇总`/`Total` as unwanted series. Explicit series binding avoids this entirely.

### Fallback / Auto-Binding Mode (dataRange)

When proposal uses `binding_mode: dataRange` (or the field is missing for backward compatibility), use the dataRange method below.

### Data Label Overlap Prevention (only execute when overlap occurs)

**Do not proactively set data label formats by default**. After chart creation, only apply the following fixes in order when data labels overlap or obscure each other:

1. **Shorten number format**: `officecli set ... --prop numberFormat='#,##0.0,"万"'` (displays 12345 as 1.2万)
2. **Widen the anchor**: Extend the anchor's right boundary by 2+ columns (e.g., J18 → L18)
3. **Increase gap width**: `officecli set ... --prop gapWidth=200` (default is 150)

### File Lock Detection

If officecli returns a lock/permission error, immediately report and STOP:
> "File is locked by WPS/Excel. Please close the file and retry."

Do not silently retry. Do not attempt to kill external processes.

### EXIT GATE

```bash
python scripts/verify_output.py --output <file> --workdir flat_output/
```

Verification checks:
- Chart existence: `officecli query chart` confirms the chart was created
- Chart object readable: `officecli get /sheet/chart[N]` is readable
- Data binding: `officecli get /sheet/chart[N]/series[1]` confirms valuesRef is non-empty and points to the correct column
- Explicit binding (if `binding_mode: explicit`): verify `seriesCount` matches `len(explicit_binding.series)`, each `series[N].valuesRef` matches the proposal's corresponding `explicit_binding.series[N-1].values_range`, and `categoriesRef` matches `explicit_binding.categories_range`
- Ghost series detection: check for series named `汇总`/`总计`/`Total`/`Sum` regardless of binding mode; any unexpected aggregate series means the binding was wrong

If exit non-zero: fix the issue and re-run. Do NOT report completion.

**Postcondition assertions**:
- `verify_output.py` exit 0
- Chart visible in query results
- series[1].valuesRef is non-empty and points to the auxiliary table or source data

**Status anchor** (must output after completion):
```
[CHART-GEN: S3=DONE, Chart=/SheetName/chart[N], Gate=EXIT_PASSED, Next=COMPLETE]
```

---

## Output Files

```
flat_output/                       ← Intermediate
└── {name}_chart_proposal.yaml     # Chart recommendation draft (includes traceability section)

{workspace}/                       ← Final (in-place edit)
└── <input>.xlsx                   # Same file, with new chart added
```

---

## References

- `references/CHART_TYPES.md` — Quick reference for 18 chart types (when to use + data shape matching rules)
- `references/CHART_PRESETS.md` — 7 preset style effects + list of overridden properties
- `references/KNOWN_TRAPS.md` — Known traps (preset interaction / series immutability / layout collision / encoding)
- `assets/chart_proposal_template.yaml` — Schema template for proposal.yaml

---

## ⚠️ Constraint Pinning (Anti-Drift)

In long-context execution, the following constraints are most easily forgotten. **Re-confirm each one before every Step 3 execution**:

1. **Data read-only** — Step 1 never modifies any cell; only `officecli get`
2. **Preset-then-override** — add only passes preset; style tweaks use set
3. **Query before set** — never assume chart index; query first, then set with real index
4. **Auxiliary table uses formulas** — never hardcode numbers; use `=Sheet1!C12`
5. **Rejection = re-analysis** — never repeat the same recommendation
6. **Prefer explicit binding** — use `categories` + `seriesN.values` by default; fall back to `dataRange` only when explicit binding cannot express the source structure
