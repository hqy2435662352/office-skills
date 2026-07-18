---
name: officecli-xlsx
description: "Use this skill any time a .xlsx file is involved -- as input, output, or both. This includes: creating spreadsheets, financial models, dashboards, or trackers; reading, parsing, or extracting data from any .xlsx file; editing, modifying, or updating existing workbooks; working with formulas, charts, pivot tables, or templates; importing CSV/TSV data into Excel format. Trigger whenever the user mentions 'spreadsheet', 'workbook', 'Excel', 'financial model', 'tracker', 'dashboard', or references a .xlsx/.csv filename."
---

# OfficeCLI XLSX Skill

## Setup

If `officecli` is missing:

- **macOS / Linux**: `curl -fsSL https://d.officecli.ai/install.sh | bash`
- **Windows (PowerShell)**: `irm https://d.officecli.ai/install.ps1 | iex`

Verify with `officecli --version` (open a new terminal if PATH hasn't picked up). If install fails, download a binary from https://github.com/iOfficeAI/OfficeCLI/releases.

## ⚠️ Help-First Rule

**This skill teaches what good xlsx looks like, not every command flag. When a property name, enum value, or alias is uncertain, consult help BEFORE guessing.**

```bash
officecli help xlsx                         # List all xlsx elements
officecli help xlsx <element>               # Full element schema (e.g. pivottable, chart, cf)
officecli help xlsx <verb> <element>        # Verb-scoped (e.g. add chart, set cell)
officecli help xlsx <element> --json        # Machine-readable schema
```

Help reflects the installed CLI version. When this skill and help disagree, **help is authoritative**.

## Shell & Execution Discipline

- **Quote every path**: `"/Sheet1/row[1]"`, not `/Sheet1/row[1]`. Shells glob `[N]`.
- **Single-quote props with `$`**: `numFmt='$#,##0'`.
- **Cross-sheet `!` formulas** → use batch heredoc `<<'EOF'` (see references/pitfalls.md).
- **`\n` and `\t` in prop values** are interpreted by the CLI as real newline/tab. Pair `\n` in a cell with `--prop wrapText=true`.
- **Incremental execution.** Run one command at a time, read its exit code. `officecli` mutates the file on every call; a failing command mid-script cascades silently.

### Windows 平台

[REQUIREMENT] 必须在 Windows 上使用 Python `subprocess.run()` 调用 officecli，禁止使用 PowerShell 管道（中文输出损坏为 `"���"`，大输出触发 `ChildProcess.kill`）。详细调用模式（Python subprocess / PowerShell 备选 / 中文路径兜底）见 `@references/PLATFORM_WINDOWS.md`。

[REQUIREMENT] 文件路径包含中文时，必须先 `Copy-Item` 到纯英文路径再操作。

## Requirements for Outputs

Before reaching for a command, know what a good xlsx looks like.

### All Excel files

**Zero formula errors.** No `#REF!`, `#DIV/0!`, `#VALUE!`, `#NAME?`, `#N/A`. Guard denominators with `IFERROR` or `IF(x=0,...)`.

**Formulas, not hardcoded values.** If a number can be computed from other cells, it is a formula.

**Professional font.** One consistent font across the workbook (Arial / Calibri / Times New Roman).

**Explicit widths.** No auto-fit. Labels 20-25, numbers 12-15, dates 12, short codes 8-10.

**Preserve existing templates.** When editing a file that already has a look, match it.

### Visual delivery floor

Before declaring done, run `officecli view "$FILE" html` and Read the returned HTML path to confirm:

- **No `###` in any cell.** Column too narrow = unfinished work.
- **No truncated titles.** Widen or `wrapText=true`.
- **No placeholder tokens** (`$fy$24`, `{var}`, `<TODO>`, `xxxx`) in any cell or chart element.
- **Pie/doughnut slices have distinct fill colors.** Switch to bar/column or set `colors=` if same-colored.
- **No empty trailing pages / empty chart anchors.**

If any of the above fails, STOP and fix before declaring done.

For print layout, financial model color coding, and number formatting standards → see references/color-standards.md.

## Common Workflow

1. **Open/save lifecycle.** `officecli open` at start, `save` to flush. Batch ≤50 ops/block (80+ tested on pure value-sets). Save only before non-officecli programs read the file.
2. **Create or load.** `create` (new) or `view outline` (existing — get the lay of the land).
3. **Build incrementally.** One command at a time. After structural ops, `get` to confirm shape.
4. **Format.** Widths, numFmts, freeze panes, tab colors, header fills. Not optional polish.
5. **Save, then reckon with the cache.** `save` writes to disk. Formulas ship without cached values — downstream formulas may cache stale. After multi-formula builds, re-touch downstream cells via non-resident `set`, then `get` to confirm `cachedValue=`. `validate` flushes pending edits itself.
6. **QA — assume there are problems.** See QA section. You are done after one fix-and-verify cycle finds zero new issues.

## Quick Start

Minimal viable xlsx: 3 months of revenue + a total formula + column widths + a currency format.

```bash
officecli create "$FILE"
officecli open "$FILE"
officecli set "$FILE" /Sheet1/A1 --prop value=Month --prop bold=true
officecli set "$FILE" /Sheet1/B1 --prop value=Revenue --prop bold=true
officecli set "$FILE" /Sheet1/A2 --prop value=Jan
officecli set "$FILE" /Sheet1/A3 --prop value=Feb
officecli set "$FILE" /Sheet1/A4 --prop value=Mar
officecli set "$FILE" /Sheet1/B2 --prop value=42000 --prop numFmt='$#,##0'
officecli set "$FILE" /Sheet1/B3 --prop value=45000 --prop numFmt='$#,##0'
officecli set "$FILE" /Sheet1/B4 --prop value=48000 --prop numFmt='$#,##0'
officecli set "$FILE" /Sheet1/A5 --prop value=Total --prop bold=true
officecli set "$FILE" /Sheet1/B5 --prop formula="SUM(B2:B4)" --prop bold=true --prop numFmt='$#,##0'
officecli set "$FILE" "/Sheet1/col[A]" --prop width=12
officecli set "$FILE" "/Sheet1/col[B]" --prop width=15
officecli close "$FILE"
officecli validate "$FILE"
```

## Reading & Analysis

Start wide, then narrow.

**Visual preview.** `officecli view $FILE html` — Read the returned HTML path. Each sheet renders with charts inline. `officecli watch $FILE` for a live preview the human user opens.

**Orient.**
```bash
officecli view "$FILE" outline
```

**Extract.**
```bash
officecli view "$FILE" text --start 1 --end 50 --cols A,B,C
```

Also: `annotated` (values+types+warnings), `stats`, `issues`.

**Round-trip dump.** `officecli dump "$FILE" [path]` → replayable batch JSON. Learn from or clone existing workbooks.
```bash
officecli dump "$FILE" -o blueprint.json
officecli batch new.xlsx --input blueprint.json
```

**Inspect one element.** Always quote paths.
```bash
officecli get "$FILE" "/Sheet1/A1"            # one cell
officecli get "$FILE" "/Sheet1/A1:D10"        # range
officecli get "$FILE" "/Sheet1/chart[1]"      # chart
officecli get "$FILE" "/Sheet1/table[1]"      # ListObject
officecli get "$FILE" "/namedrange[1]"        # workbook-level named range
```

Add `--depth N` for children, `--json` for machine output.

**Query across the workbook.**
```bash
officecli query "$FILE" 'cell:has(formula)'       # every formula cell
officecli query "$FILE" 'cell:contains("#REF!")'  # broken references
officecli query "$FILE" 'cell[type=Number]'       # typed filter
officecli query "$FILE" 'Sheet1!B[value!=0]'      # sheet-scoped
```

Operators: `=`, `!=`, `~=` (contains), `>=`, `<=`, `[attr]` (exists). `query $FILE merge` or `mergedrange` returns all merged cells.

**Analytical elements for large data:** pivot tables (`add --type pivottable`), slicers (`--type slicer`), sparklines (`--type sparkline`; type enum: `line | column | stacked`). Consult `officecli help xlsx <element>` for prop names.

## Creating & Editing

Verbs: `add`, `set`, `remove`, `move`, `swap`, `batch`.

### Cells and formulas

```bash
officecli set "$FILE" /Sheet1/B5 --prop formula="SUM(B2:B4)" --prop numFmt='$#,##0'
officecli set "$FILE" /Sheet1/C5 --prop formula="B5/A5" --prop numFmt="0.0%"
```

Structural properties:
```bash
officecli set "$FILE" "/Sheet1/col[A]" --prop width=20
officecli set "$FILE" "/Sheet1/row[1]" --prop height=22
officecli set "$FILE" "/Sheet1" --prop freeze=A2 --prop tabColor=1F4E79
```

### Named ranges

Prefer named ranges over `$B$6` — self-documenting and survive cell moves. Use batch heredoc because `ref` contains `!` and `$`:

```bash
cat <<'EOF' | officecli batch "$FILE"
[
  {"command":"add","parent":"/","type":"namedrange","props":{"name":"GrowthRate","ref":"Sheet1!$B$6"}}
]
EOF
```

**Batch JSON does NOT accept shell aliases.** Always use full dotted names: `"font.color": "FF0000"`, `"font.size": 14`, never `"color": "FF0000"`. `parent` is `"/"` for workbook-level, `"/SheetName"` for sheet-scoped.

For charts, conditional formatting, data validation, and CSV import → see Reference Files below.

## QA (Required)

**Assume there are problems. Your job is to find them.**

### Minimum cycle before "done"

1. `officecli view "$FILE" issues` — empty sheets, broken formulas, missing refs.
2. `officecli view "$FILE" annotated` (sample ranges) — values + types + warnings.
3. Query every error type:
   ```bash
   officecli query "$FILE" 'cell:contains("#REF!")'
   officecli query "$FILE" 'cell:contains("#DIV/0!")'
   officecli query "$FILE" 'cell:contains("#VALUE!")'
   officecli query "$FILE" 'cell:contains("#NAME?")'
   officecli query "$FILE" 'cell:contains("#N/A")'
   ```
4. `officecli validate "$FILE"` — safe with resident open; flushes pending edits itself.
5. **Visual pass** — `officecli view "$FILE" html`, Read the HTML. Scan for `###`, truncation, placeholder tokens, sliced charts, white-slice pie charts, empty chart anchors. STOP and fix before declaring done.
6. **Print layout fix** — per-sheet orientation + fit mode:
   ```bash
   officecli set "$FILE" "/Summary" --prop orientation=landscape --prop fitToPage=true   # short sheet
   officecli set "$FILE" "/Data" --prop orientation=landscape --prop fitToPage=1x0       # tall table
   ```
7. If anything failed, fix, then **rerun the full cycle**.

`issues` + `view html` are the structural QA pair. For the full formula verification checklist, template QA, and honest limits → see references/qa.md.

## Reference Files

| Task | Read |
|------|------|
| Building charts | references/charts.md |
| Conditional formatting, data validation, named ranges | references/formatting.md |
| CSV / bulk import | references/csv-import.md |
| Full QA cycle + delivery gates | references/qa.md |
| Known issues, shell traps, renderer caveats | references/pitfalls.md |
| Financial model color coding + number formats | references/color-standards.md |
