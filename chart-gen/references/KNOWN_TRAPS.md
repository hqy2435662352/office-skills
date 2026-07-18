# Known Traps

Quick reference for known traps. Check this table first when encountering anomalies.

| # | Trap | Symptom | Cause | Fix | Example Command |
|---|------|---------|-------|-----|-----------------|
| 1 | **Preset overrides manual properties** | Passed `legend=bottom` but legend still appears on the right | preset ships with default legend/colors/gridlines etc.; passing both during add results in silent override | Two-step method: `add` with preset only → `set` to override needed properties. This affects legend, colors, gradient, seriesOutline, marker, and more — not just legend | ```bash
# Correct
officecli add file.xlsx /Sheet1 --type chart --prop preset=corporate ...
officecli set file.xlsx /Sheet1/chart[1] --prop legend=bottom

# Wrong (don't do this)
officecli add file.xlsx /Sheet1 --type chart --prop preset=corporate --prop legend=bottom ...
``` |
| 2 | **chart-series immutable** | Wrong series name or order; attempted `set` but no effect | officecli explicitly does not support chart-series remove; modifying series structure requires full chart rebuild | Carefully verify series config during confirmation. If series needs changing, delete chart and re-add: `officecli remove file.xlsx /Sheet1/chart[1]` then re-run `officecli add ...`. Use explicit binding (`seriesN.values`) for precise control over which columns become series. | ```bash
# Delete wrong chart
officecli remove file.xlsx /Sheet1/chart[1]

# Recreate with explicit binding (preferred — no ambiguity)
officecli add file.xlsx /Sheet1 --type chart \
  --prop chartType=column \
  --prop categories="Sheet1!$A$2:$A$13" \
  --prop series1.name="Sales" --prop series1.values="Sheet1!$B$2:$B$13" \
  --prop series2.name="Cost" --prop series2.values="Sheet1!$D$2:$D$13" ...
``` |
| 3 | **Multi-chart position overlap** | After second chart-gen, chart[2] covers or partially obscures chart[1] | Two chart-gen invocations did not coordinate anchors; new chart default position may overlap existing chart | `anchor=D2:J18` is better than cm coordinates (binds to cell grid). Step 1 pre-check records existing chart anchors; place new charts below data region in empty area | ```bash
# Step 1 pre-check: record existing chart positions
officecli query file.xlsx chart --json

# Place new chart below data in empty area (assuming data ends at row 15)
officecli add file.xlsx /Sheet1 --type chart --prop anchor=D17:J33 ...
``` |
| 4 | **Chinese sheet name in dataRange** | `dataRange=经营状况概览!A1:C13` parse failure or chart data empty | Chinese sheet names in shell environments may fail due to encoding parse errors. Python `subprocess.run()` passes raw bytes to the OS, bypassing shell encoding | Python `subprocess.run()` handles Chinese sheet names directly (verified). For PowerShell/CMD calls, wrap in double quotes: `--prop dataRange="经营状况概览!A1:C13"`. Prefer ASCII sheet names when possible | ```bash
# Python subprocess.run() — verified: no quoting needed
['officecli', 'add', file, '/Sheet1', '--prop', 'dataRange=经营数据!A1:C7', ...]

# PowerShell/CMD — double quotes required
officecli add file.xlsx /Sheet1 --type chart --prop dataRange="经营状况概览!A1:C13" ...

# Or use ASCII sheet names (safest)
officecli add file.xlsx /Sheet1 --type chart --prop dataRange="Sheet1!A1:C13" ...
``` |
| 5 | **dataRange includes empty rows** | Chart has large blank areas on right or bottom; series line breaks unexpectedly | LLM inferred an overly large range that includes empty rows below the total row, causing gaps in chart data | Step 1 head/tail sampling (inline `officecli get`) — detect empty rows → proposal marks warning; user can catch and shrink the range at Human Gate | ```bash
# Step 1 sample verification
officecli get file.xlsx /Sheet1/B2:B4 --depth 0 --json
officecli get file.xlsx /Sheet1/B11:B13 --depth 0 --json

# If empty rows found, shrink dataRange
# Before: dataRange="Sheet1!A1:C20"
# After:  dataRange="Sheet1!A1:C13"
``` |
| 6 | **Non-contiguous columns can't use dataRange** | Want to skip column B and only take A and C, but `dataRange=A1:C13` includes B too | `dataRange=A1:C13` requires a contiguous rectangular range; cannot express "skip column B" | Split into independent params: `categories=Sheet1!$A$2:$A$13` + `series1.values=Sheet1!$C$2:$C$13` + `series2.values=Sheet1!$E$2:$E$13` | ```bash
# Non-contiguous columns: split into independent params
officecli add file.xlsx /Sheet1 --type chart \
  --prop chartType=column \
  --prop categories="Sheet1!$A$2:$A$13" \
  --prop series1.values="Sheet1!$C$2:$C$13" \
  --prop series1.name="Sales Revenue" \
  --prop series2.values="Sheet1!$E$2:$E$13" \
  --prop series2.name="Profit"
``` |
| 7 | **set targets wrong chart by index** | Meant to modify the new chart but set an old one; pie chart gets outsideEnd error | `officecli set` uses `chart[N]` index, but new chart index depends on existing chart count; it won't always be `chart[2]` | Before `set`, run `officecli query <file> chart --json`, find the real index by `title` or `anchor`, then set on that index | ```bash
# Query first to confirm index
officecli query file.xlsx chart --json
# Suppose target is chart[4]
officecli set file.xlsx /Sheet1/chart[4] --prop legend=bottom --json
``` |
| 8 | **Ghost series from wide dataRange on pivot tables** | Chart contains extra series named `汇总`, `总计`, `Total`, or `Sum` not requested in the proposal | `dataRange` auto-inference on pivot tables picks up aggregate/total columns as additional series; officecli treats every column in the range as a series | Use explicit binding instead: pass `seriesN.values` for only the columns you want. This completely avoids auto-inference of unwanted columns. If already created with ghost series, delete and rebuild with explicit binding | ```bash
# WRONG (creates ghost series from pivot total columns)
officecli add file.xlsx /Sheet1 --type chart \
  --prop chartType=column --prop dataRange="Sheet1!A1:F13" ...

# CORRECT (explicit binding — only selected columns)
officecli add file.xlsx /Sheet1 --type chart \
  --prop chartType=column \
  --prop categories="Sheet1!$A$2:$A$6" \
  --prop series1.name="25FY" --prop series1.values="Sheet1!$B$2:$B$6" \
  --prop series2.name="26FY" --prop series2.values="Sheet1!$D$2:$D$6" \
  --prop series3.name="27FY" --prop series3.values="Sheet1!$F$2:$F$6"
``` |
| 9 | **Data label format drifts with data source changes** | After changing source from static integers to formula references, chart labels flip from decimals to integers or vice versa | `dataLabels` number format does not auto-sync with data source; label format may drift when source data type/format changes | After modifying source data, explicitly inspect and re-set `dataLabels` number format; use `officecli set ... --prop dataLabels=...` to correct if needed | ```bash
# After source change, check label format and re-set if needed
officecli set file.xlsx /Sheet1/chart[1] --prop dataLabels=outsideEnd --json
``` |
| 10 | **Auxiliary table should use formula references to source** | Auxiliary table contains hardcoded numbers; user cannot trace back to source; chart doesn't sync after source data update | Static values were entered for speed, sacrificing auditability and auto-sync | Auxiliary table cells should use formula references to source data, e.g., `=Sheet1!B2`, not hardcoded numbers | ```bash
# In Excel / WPS, set auxiliary table cells as formulas
# AB8: =Sheet1!B2
# AB9: =Sheet1!B3
# Have the chart reference auxiliary table AB8:AB13
``` |
| 11 | **Chart anchor overlaps data region** | Chart covers or displays on top of source data; data not visible when viewing in Excel | Default anchor behavior may place chart to the left of or directly below data; "right + 2 columns" rule not followed | Always place anchor to the **right** of the data region, with at least a 2-column gap. E.g., data at A12:M16 → anchor starts at O12. Step 1 proposal validates anchor does not overlap data | ```bash
# Correct: anchor right of data (data A12:M16, anchor O12:V28)
officecli add file.xlsx /Sheet1 --type chart --prop anchor=O12:V28 ...

# Wrong: anchor overlaps or is adjacent to data (anchor J2:Q18 covers J-M columns)
officecli add file.xlsx /Sheet1 --type chart --prop anchor=J2:Q18 ...  ← FORBIDDEN
``` |
| 12 | **Magnitude difference makes small series invisible** | Series A values ~100, series B values ~10,000; in chart, series A column height approaches 0, nearly invisible | Series values in the same chart differ by ≥2 orders of magnitude (≥100x); smaller series is visually drowned out | **Split charts** rather than log axis. Group data by magnitude and create 2+ independent charts. Mark `split_reason: "magnitude_diff"` in the proposal | ```bash
# Split plan:
# Chart 1 (small magnitude): Series A (range 10-100), Y-axis 0-120
# Chart 2 (large magnitude): Series B (range 10,000-50,000), Y-axis 0-60,000
officecli add file.xlsx /Sheet1 --type chart \
  --prop chartType=column \
  --prop dataRange="Sheet1!A1:B13" \
  --prop title="Series A (10k CNY)" ...
officecli add file.xlsx /Sheet1 --type chart \
  --prop chartType=column \
  --prop dataRange="Sheet1!A1:C13" \
  --prop title="Series B (10k CNY)" ...
# Note: select dataRange columns per chart, only include same-magnitude series
``` |
| 13 | **Data label numbers not shortened causing overlap** | Labels show `12345678`; long numbers cause adjacent labels to overlap and become unreadable | Default data labels show full numbers; large values consume excessive horizontal space | Three fixes in order: ① shorten number format (`numberFormat='#,##0.0,"万"'`) → ② widen anchor by 2+ columns → ③ increase gapWidth (200+) | ```bash
# Fix 1: shorten format (12345 → 1.2万)
officecli set file.xlsx /Sheet1/chart[1] --prop numberFormat='#,##0.0,"万"'

# Fix 2: widen anchor (J18 → L18)
officecli set file.xlsx /Sheet1/chart[1] --prop anchor=D2:L18

# Fix 3: increase gap (default 150 → 200)
officecli set file.xlsx /Sheet1/chart[1] --prop gapWidth=200
``` |
| 14 | **File locked by WPS/Excel when officecli runs** | `officecli add` returns permission error, file lock error, or times out then fails | User has the target xlsx open in WPS/Excel; OS-level exclusive lock prevents officecli from writing | **Report to user immediately**: "File is locked by WPS/Excel. Please close the file and retry." Never silently retry. Do not attempt to kill external processes | ```bash
# Error example (officecli output):
# Error: Access denied / File is locked by another process
# 
# → Immediately report to user, STOP
# "File is locked by WPS/Excel. Please close the file and retry."
#
# Do NOT execute:
# taskkill /f /im et.exe        ← FORBIDDEN
# Loop 5 times retrying add      ← FORBIDDEN
``` |

---

## Quick Diagnostic Index

| Symptom | Related Trap # |
|---------|---------------|
| Legend position wrong | 1 |
| Series name/order wrong and unfixable | 2 |
| New chart covers old chart | 3 |
| Chinese sheet name chart has no data | 4 |
| Chart has large blank areas | 5 |
| Non-contiguous columns incorrectly included | 6 |
| set hit wrong chart / pie chart outsideEnd error | 7 |
| Ghost series (汇总/总计/Total) in chart | 8 |
| Data label format changed after source edit | 9 |
| Auxiliary table numbers can't be traced | 10 |
| Chart covers source data region | 11 |
| Small series invisible in chart (column height near 0) | 12 |
| Data label digits too long, causing overlap | 13 |
| officecli permission/lock error | 14 |

## Preventive Checklist

Confirm before every chart creation:

1. **Separate preset and manual properties**: `add` stage passes only preset; style adjustments at `set` stage
2. **Verify series config carefully at Human Gate**: Once created, series structure is immutable
3. **Anchor does not overlap existing charts**: Step 1 pre-check queries existing chart positions. New anchor placed 2+ columns to the right of data
4. **Quote Chinese sheet names**: `--prop dataRange="ChineseName!A1:C13"`
5. **Sample head and tail of dataRange**: Empty row warnings → shrink range
6. **Identify non-contiguous columns early**: Prefer explicit `seriesN.values` binding; auxiliary table is a last resort
7. **Query before set**: Confirm real chart index by title/anchor; never assume a fixed index
8. **Check data label format after source changes**: Re-set dataLabels number format if needed
9. **Auxiliary table uses formula references to source**: Never hardcode numbers; ensure auditability and sync
10. **Detect magnitude differences**: Series value diff ≥2 orders of magnitude → split charts; don't force log axis
11. **Only shorten data labels when overlapping**: Only apply `#,##0.0,"万"` etc. when labels are already overlapping; do not proactively set by default
12. **File lock detection**: officecli lock error → immediately report to user to close file; never silently retry
13. **Ghost series check**: When source is a pivot table, avoid `dataRange` auto-inference — use explicit `seriesN.values` to prevent unwanted aggregate columns
