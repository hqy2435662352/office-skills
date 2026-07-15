---
name: table-fill
description: >
  Use this skill whenever the user asks to fill, populate, map, or transfer data
  between spreadsheet tables — in any direction (xlsx→pptx, xlsx→xlsx, pptx→pptx,
  pptx→xlsx). Activate immediately on phrases like "fill", "populate", "map", "展平",
  "读源数据→填模板", "把数据填到模板里", or "从源表出报告", even if the user does not
  explicitly name the source and target formats. Also activate when the user describes
  a multi-file data transfer workflow involving Office tables.


  Do NOT activate when the user wants to create a new file from scratch, edit a single
  cell, or build a general-purpose spreadsheet or slide deck — those are not table-fill
  tasks.

  COMPLIANCE: 4-layer workflow. Layer execution is SEQUENTIAL. The single Human Gate
  after Layer 3 is MANDATORY — do not proceed to Layer 4 until the user confirms.
license: MIT
compatibility: >
  Required: officecli (on PATH), Python 3.8+
  All read/write goes through officecli. No openpyxl, no pandas, no python-pptx in the pipeline.
  Must co-load: officecli-xlsx (for path syntax, open/save lifecycle, batch patterns, QA gates)
  Recommended: officecli-win (for Windows subprocess encoding workaround)
metadata:
  drift-risk: high
  layer-count: 4
  human-gate: after-layer-3
  exit-gate: required
---

# Table Fill

Four-layer workflow for filling tables between xlsx and pptx in any direction.
Layers 1 and 4 use deterministic scripts (flatten, state gate, verify). Layers 2
and 3 are LLM reasoning with hardened reference rules. A single Human Gate separates
analysis (L1-3) from execution (L4).

**Why this structure matters**: The gate exists because mapping errors caught after
filling are expensive to fix — modifying cell data in PPTX without destroying formatting
requires precise `officecli set` paths. One wrong cell reference wastes a full fill cycle.

**Why officecli over openpyxl**: All operations in this skill use officecli exclusively.
Three reasons: (1) officecli's `add --type row` auto-updates all dependent structures
(formulas, conditional formatting sqref, data validation, named ranges) when inserting
rows — openpyxl's `insert_rows()` does not; (2) python-pptx's `save()` silently
overwrites all officecli-written changes by rebuilding the ZIP from scratch;
(3) openpyxl's `max_row`/`max_column` metadata is unreliable — benchmarked on
`分公司片区经营状况一览表_v2.xlsx`, openpyxl reported 1×1 for a 28-row sheet.
If data cleaning or aggregation is needed, use pandas separately before entering
the table-fill pipeline — never inside it.

---

## ⚠️ 依赖加载（必须先执行）

本 skill 是编排层工作流，不重复基础 skill 的规则。在执行任何 Layer 之前，**必须加载以下依赖**：

```python
# 基础依赖 — 始终需要
skill(name="officecli-xlsx")    # 路径语法、open/save 生命周期、batch 模式、QA 门禁

# 按目标类型加载
# - 当目标文件是 PPTX 时，追加加载：
#   skill(name="officecli-pptx")  # 幻灯片结构、形状/图表/动画规则、交付门禁
# - 当目标文件是 XLSX 时，无需额外加载
```

**为什么这是强制的**：`table-fill` 的 Layer 3 生成 `batch.json` 时需要遵循 officecli 的路径语法、格式安全规则（如 `font.color` 而非 `color`）、以及跨 sheet 引用规范——这些规则在 `officecli-xlsx/SKILL.md` 中定义，本 skill 不重复。

**验证方式**：
- xlsx 目标：确认 `officecli-xlsx/SKILL.md` 已加载
- pptx 目标：确认 `officecli-xlsx/SKILL.md` 和 `officecli-pptx/SKILL.md` 均已加载

---

## Tech Stack

All operations use officecli. The 2026-07-13 rewrite eliminated the last openpyxl
dependency from `flatten_table.py`. The same day, `rebuild_target.py` and `fill_cells.py`
were retired — their translation-layer role is now handled by the LLM in Layer 3
generating `batch.json` directly in officecli's native format.

---

## Hard Constraints

- **All operations use officecli.** No openpyxl, no pandas, no python-pptx in the pipeline. If you must create a table on a missing slide, use python-pptx ONCE before any officecli operation, then close it permanently — `save()` destroys all officecli-written changes.
- **All file paths must be ASCII-safe.** On Windows, officecli `batch`/`set` operations fail with `Access denied` on paths containing Chinese characters. Copy all input files to an ASCII-only directory (e.g. `C:\Temp\tablefill\`) before Layer 1. `get` may appear to work on Chinese paths — `set` and `batch` will not.
- **Template data is discarded during fill.** The target template's existing cell values are placeholders, not fallback defaults. Every target cell that has a mapped source value must be overwritten. The only exceptions are: (a) cells explicitly listed as "保留" (preserve) in the mapping table, (b) header/label cells that are not part of any data mapping, and (c) structural cells (merged ranges, empty spacer rows) identified in Layer 2. If a source value is missing for a mapped cell, leave it empty or report the gap in Layer 3 — never silently keep the template's old value.
- **OLE detection**: Before reporting "no tables" on any slide, probe `/slide[N]/ole[M]`. OLE embedded Excel IS a valid fill target.
- **All officecli calls**: Python `subprocess.run()`. Never raw PowerShell — GBK encoding corrupts Chinese text. See `references/OFFICECLI_REFERENCE.md`.
- **Verification**: Always read back from the OUTPUT file path, never a temp or working file path.
- **Not a valid reason to skip layers**: "The target is xlsx not pptx", "the data is simple", or "I already understand the structure." Layers 1-3 logic (flatten, classify, map) applies regardless of file format.

---

## Layer 3 Pinned Constraints

These constraints govern batch.json generation in Layer 3. Per the agentskills.io
drift-prevention framework, they are marked as `[REQUIREMENT]` / `[PROHIBITION]`
so the agent harness can re-inject them at critical turn depths.

### Batch JSON ordering

[REQUIREMENT] Operations in `batch.json` must appear in this exact order:
1. `set` with `text=""` to clear existing data rows (preserve their formatting)
2. `add --type row --from /Sheet/row[K]` to clone formatted rows when expanding
3. `remove` (row deletion) — only when source has fewer rows than template, from bottom to top
4. `set` with `merge` prop (cell merging)
5. `set` with `text` or `formula` or formatting props (value fill)
6. `set` with structural props (`col[].width`, `row[].height`, `freeze`, `tabColor`)

### Row lifecycle: clear first, clone to expand, delete last

[REQUIREMENT] Never delete a template data row just to replace it with a new row.
The template's existing data rows carry formatting (borders, fills, fonts, column widths,
conditional formatting) that must survive the fill. The correct approach:

1. **Clear**: For all data rows that will receive new values, set each cell's `text` to
   empty (`""`). This preserves row formatting while removing old template values.
   **Exception**: Skip cells that will receive formulas in the fill step — `set text=""`
   converts the cell type to literal, which blocks subsequent `set formula`. Formula-bound
   cells should be overwritten directly by the formula set command without prior clearing.

2. **Expand**: If the source has MORE rows than the template, clone formatted rows using
   `{"command": "add", "parent": "/Sheet", "type": "row", "from": "/Sheet/row[K]", "after": "/Sheet/row[K]"}`
   where K is the index of an existing formatted data row. **`from` and `after` are
   separate parameters**: `from` picks the format source, `after`/`before`/`index` sets
   the insertion position. Without a position parameter, the row is appended to the sheet
   end — not inserted at the `from` row. officecli clones the source row's cells, styles,
   and single-row merge cells; relative formula references are delta-shifted automatically.

3. **Delete**: Only delete rows when the source has FEWER rows than the template,
   and only from the bottom. Use `remove` commands ordered bottom-to-top so indices
   don't shift.

The ordering matters because add/remove change row indices; subsequent set operations
must use the post-structural-change coordinate system.

### Path safety

[REQUIREMENT] Every path in batch.json must correspond to a target cell or structure
documented in the mapping table. No path may be invented during batch.json generation.

[REQUIREMENT] Cross-sheet references must use the full sheet-name prefix (e.g.
`P&L!B13`, not `B13`). The sheet-name-less form works inconsistently.

[PROHIBITION] Do not use openpyxl or python-pptx path syntax in batch.json. All paths
must use officecli's XPath-style format: `/SheetName/row[N]`, `/SheetName/A1`,
`/slide[N]/table[@id=M]/tr[X]/tc[Y]`.

### Format safety

[REQUIREMENT] In batch JSON props, always use full dotted names: `"font.color"` for
text color, `"fill"` for background. The bare `"color"` key is ambiguous in cell context
and will be rejected.

[REQUIREMENT] Hex colors drop the `#` prefix: `FF0000`, not `#FF0000`.

[PROHIBITION] Never embed `=` at the start of a formula value — the CLI strips it.
Write `"formula": "SUM(B2:B4)"`, not `"formula": "=SUM(B2:B4)"`.

### python-pptx guard

[PROHIBITION] python-pptx must not be imported in any process that will later call
officecli on the same file. python-pptx's `save()` rebuilds the ZIP from scratch and
silently destroys all officecli-written changes. If you must create a table on a missing
slide, do it once before any officecli operation, then close python-pptx permanently
before entering Layer 4.

---

## Exit Code Protocol

All scripts follow this convention. Read stderr JSON on failure:

| Exit | Meaning | Action |
|------|---------|--------|
| 0 | Pass | Proceed |
| 1 | Fatal (file missing, env error) | STOP, report to user |
| 3 | Retryable (prerequisite not met) | Apply corrective_action from stderr, retry |

---

## Workflow

| Layer | Type | Gate | Output |
|-------|------|------|--------|
| 0 - Preflight | script | No | ASCII-safe workdir, clean environment |
| 1 - Flatten | script | No | `{name}_展平.csv` |
| 2 - Classify | LLM | No | `{name}_元数据.yaml` |
| 3 - Map | LLM | **Gate** ⛔ | `{name}_映射表.md` + `{name}_batch.json` |
| 4 - Execute | script | No | `{output}_filled.{ext}` → EXIT GATE |

---

## Layer 0: Preflight (MANDATORY)

`ash
python scripts/preflight.py --workdir <workspace_root>
`

Runs before Layer 1. Checks:
- Working directory is ASCII-safe (non-ASCII paths cause officecli Access denied on Windows)
- officecli is on PATH and functional
- Stale officecli resident processes are cleaned up
- Python UTF-8 encoding is functional

exit 0 = environment clean, proceed to Layer 1.
exit 1 = fatal (officecli missing), stop and report.
Non-fatal warnings (non-ASCII path) are reported to stderr — AI should copy files
to an ASCII-only temp directory before Layer 1.

### Data Processing Scenarios

Before Layer 2, identify which scenario applies. This determines Layer 3's traceability output:

| Scenario | Detection | Source→Target | Traceability Need |
|----------|-----------|---------------|-------------------|
| **直接迁移** | Source and target have identical row/column structure. 1:1 cell mapping. | Direct copy | Source coordinate per cell |
| **数据聚合** | Source has detail rows, target has summary cells. GROUP BY + SUM/COUNT needed. | N:1 aggregation | Source row list per aggregate cell (for manual spot-check) |
| **数据清洗** | Source values need normalization, filtering, or type conversion before mapping. | Transformed copy | Cleaning rule + original value + transformed value |

---

## Layer 1: Flatten

Discover all tables and flatten them into tidy CSV. Uses officecli exclusively for
dimension discovery and data reading.

```bash
# xlsx (source or target)
python scripts/flatten_table.py --input <file.xlsx> --target "SheetName" --output 展平元数据输出/{name}_展平.csv

# pptx (source or target)
python scripts/flatten_table.py --input <file.pptx> --target "5:2,6:3,21:3" --output 展平元数据输出/{name}_展平.csv
```

`--target`: sheet name for xlsx; `slide:table_id,slide:table_id,...` for pptx.
The script auto-detects OLE objects and reports them with extraction instructions.

**OLE extraction** (run if detected): `python scripts/extract_ole.py --input <file.pptx> --slide <N> --output-dir <workspace_root>`. OLE xlsx are final outputs — save alongside the PPTX, not in intermediate data dir.

Details: `references/LAYER1_FLATTEN_ALGORITHM.md`, `references/LAYER1_OLE_HANDLING.md`

---

## Layer 2: Classify

**PRECONDITION**: `layer_gate.py --target 2 --workdir 展平元数据输出/` exits 0.

Classify every column (DIMENSION / MEASURE_AGGREGABLE / MEASURE_DERIVED / METADATA),
detect multi-block sources, trace derived measure formulas. Write `{name}_元数据.yaml`.

Read `references/LAYER2_CLASSIFICATION.md` for the classification rules. Do not pre-load
this file — read it only when Layer 2 begins.

---

## Layer 3: Map + batch.json — SINGLE HUMAN GATE

**PRECONDITION**: `layer_gate.py --target 3 --workdir 展平元数据输出/` exits 0.

Map source indicators to target cells, decide structural changes, and produce TWO outputs:
- `{name}_映射表.md` — human-readable mapping with traceability table
- `{name}_batch.json` — machine-executable officecli batch commands

Read `references/LAYER3_MAPPING.md` for mapping strategy — do not pre-load.

### What goes into 映射表.md

1. **Structure decisions**: Which rows to delete/insert, which cells to merge, why
2. **Value mappings**: Source → target cell pairs with values and format notes
3. **Format operations**: Column widths, number formats, font changes applied to ranges
4. **Traceability table**: Per the detected scenario (直接迁移/数据聚合/数据清洗), every
   mapped cell must be traceable back to its source. Use templates in
   `references/HUMAN_GATE_TEMPLATES.md`.

### What goes into batch.json

The batch.json contains ALL structural and value operations in a single file,
strictly ordered per the Layer 3 Pinned Constraints section above.

Format: an array of command objects. Each object has `"command"` (add/set/remove),
`"parent"` or `"path"`, `"type"` (for add), and `"props"` (key→value map).

```json
[
  {"command": "remove", "path": "/Sheet/row[28]"},
  {"command": "remove", "path": "/Sheet/row[27]"},
  {"command": "add", "parent": "/Sheet", "type": "row", "props": {"cols": 10}},
  {"command": "set", "path": "/Sheet/B2", "props": {"merge": "B2:B7"}},
  {"command": "set", "path": "/Sheet/C5", "props": {"text": "空调", "font.color": "000000"}},
  {"command": "set", "path": "/Sheet/D5", "props": {"text": "37.65", "numFmt": "#,##0.00"}},
  {"command": "set", "path": "/Sheet/col[D]", "props": {"width": 18}}
]
```

For PPTX targets, include `"font": "微软雅黑"` and `"size": "9pt"` in text-cell props.
For cross-sheet formula references, use `"formula": "P&L!B13"` (no `=` prefix).

Reference for all supported commands and props: `references/OFFICECLI_REFERENCE.md`.

### ⛔ STOP — Human Gate

Present ALL Layer 1-3 results together:
- **Layer 1**: tables found, OLE detected, files extracted
- **Layer 2**: block boundaries, column types, derived formulas
- **Layer 3**: 映射表.md (scrollable) + batch.json summary (count of operations by type)
- **⚠️ Decisions needed**: Structural changes rationale, OLE handling plan, any format
  decisions that need user input

Then run: `layer_gate.py --set-gate 3 --workdir 展平元数据输出/`

**End your response here. Do NOT continue to Layer 4 until the user replies.**

---

## Layer 4: Execute + Verify

**PRECONDITION**: `layer_gate.py --target 4 --workdir 展平元数据输出/` exits 0.
Also requires `.gate3_pending` to be cleared (user confirmed Gate).

### Step 0: Validate batch.json (MANDATORY)

`ash
python scripts/validate_batch.py --batch {name}_batch.json
`

This runs ZERO officecli calls. Checks for: rom without position, formula/clear
conflicts, ordering violations. exit 0 = proceed; exit 3 = fix issues and re-run.

[REQUIREMENT] If batch.json has more than 50 operations, split it into chunks of ≤50 ops.
Each chunk is a separate batch.json file executed in sequence. Between chunks, run at
least one officecli get on a key cell to verify the coordinate system is intact.
This catches row-insertion ordering errors early, before 900 ops have been wasted.

### Step 1: Clean resident + Copy template

On Windows, officecli get calls leave resident processes that hold file locks,
blocking subsequent atch operations. Clean them first:

`ash
taskkill /F /IM officecli.exe 2>nul
`

Then copy the template to the output path (preserves all formatting, charts, and
non-data content):

`ash
copy /Y <template> <output_file>
`

Use copy /Y (not Python shutil.copy2) to avoid Python-level file handle retention.

```bash
cp <template> <output_file>
```

This preserves all formatting, charts, and non-data content from the template.

### Step 2: Execute batch

```bash
officecli batch <output_file> --input 展平元数据输出/{name}_batch.json
```

All structural and value operations execute in a single officecli batch pass.
officecli's `add --type row` auto-updates all dependent structures (formulas, CF,
named ranges) — no manual reference repair needed.

If batch fails on specific cells, re-run the individual `set` commands that
failed via `officecli set <output_file> <path> --prop <key>=<value>`.

### Step 3: Verify

```bash
python scripts/verify_output.py --output <final_output> --workdir 展平元数据输出/ --table-map "5:2,6:3,21:3"
```

If exit non-zero: fix issues and re-run from Step 2 or 3. Do NOT report completion
until verify passes.

### Missing table (pptx only)

If the target slide has no table (reported in Layer 1), create it with python-pptx once
before Step 1, then close python-pptx permanently. Do not import python-pptx after
any officecli operation.

---

## Output Files

```
展平元数据输出/              ← Intermediate (Layer 1-3)
├── {name}_展平.csv
├── {name}_元数据.yaml
├── {name}_映射表.md
└── {name}_batch.json

{workspace}/                 ← Final (Layer 4)
├── {output}_filled.pptx
├── oleObject2_filled.xlsx   (OLE, if extracted)
└── oleObject3_filled.xlsx   (OLE, if extracted)
```

---

## Status Anchor

After completing each layer, output:

```
[TABLE-FILL: L{N}=DONE, Gate={status}, Next={L{N+1} or EXIT}]
```

This creates an attention anchor in the output history that prevents layer
progress from becoming a forgotten omission constraint.

---

## Troubleshooting

`references/KNOWN_TRAPS.md` — 11 documented failure patterns with fixes.
`references/OFFICECLI_REFERENCE.md` — officecli path syntax, batch JSON format,
encoding rules, and Windows subprocess patterns.
