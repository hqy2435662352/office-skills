# QA (Full Cycle)

**Assume there are problems. Your job is to find them.**

Your first workbook is almost never correct. Treat QA as a bug hunt, not a confirmation step. If you found zero issues on first inspection, you were not looking hard enough. The formulas look fine **until** you check two of them against source cells.

## Minimum cycle before "done"

1. `officecli view "$FILE" issues` — empty sheets, broken formulas, missing refs.
2. `officecli view "$FILE" annotated` (sample ranges) — values + types + warnings.
3. For every Excel error type, query it:
   ```bash
   officecli query "$FILE" 'cell:contains("#REF!")'
   officecli query "$FILE" 'cell:contains("#DIV/0!")'
   officecli query "$FILE" 'cell:contains("#VALUE!")'
   officecli query "$FILE" 'cell:contains("#NAME?")'
   officecli query "$FILE" 'cell:contains("#N/A")'
   ```
4. `officecli validate "$FILE"` — safe with a resident open; `validate` flushes pending edits to disk itself.
5. **Visual pass — walk every sheet via the HTML preview.** Run `officecli view "$FILE" html` and Read the returned HTML path. Each sheet renders with charts inline. Scan for `###`, truncated titles, placeholder tokens (`$fy$24`, `{var}`, `<TODO>`), sliced charts, white-slice pie charts, empty chart anchors — **STOP and fix before declaring done**. "validate pass" is not delivery; "the preview looks like a real workbook" is delivery. For human preview, run `officecli watch "$FILE"` (user opens the live preview at their own discretion) or have them open the `.xlsx` directly in Excel / WPS / Numbers.
6. **Print layout fix (wide tables / multi-chart sheets).** When a sheet holds a chart or a wide table and the user will print it, set per-sheet page layout — but match the fit mode to the sheet's height:
   ```bash
   # Short summary / chart sheet → fit to one page.
   officecli set "$FILE" "/Summary" --prop orientation=landscape --prop fitToPage=true
   # Tall data table → fit width only (fitToPage=true would crush all rows onto one unreadable page).
   officecli set "$FILE" "/Data" --prop orientation=landscape --prop fitToPage=1x0
   ```
   Outcome: charts/wide tables print without mid-chart splits; tall tables stay readable across natural page breaks. Apply to every sheet that holds a chart or a > 8-column table.
7. If anything failed, fix, then **rerun the full cycle**. One fix commonly creates another problem.

`officecli view issues` + `view html` are the structural QA pair: `issues` catches broken formulas and empty sheets; `view html` (Read the returned HTML path) catches `###`, truncation, and token leakage. Chart fill colors / theme tints can vary across viewers — spot-check in the user's target viewer when color fidelity matters.

## Formula verification checklist

- [ ] Pick 2-3 formulas at random. Run `officecli get` on each. Confirm the formula string is what you intended **and** `cachedValue=` is what you expect — arithmetic in your head.
- [ ] **Cached value sanity on every summary cell.** Any cell that aggregates (COUNTA / COUNTIF / SUMPRODUCT / INDEX&MATCH) must have a plausible `cachedValue`. If a progress tracker shows `199 / 199 / 100%` on a blank template, the cache is lying — re-touch the formula via `set` (forces recompute) or manually set a correct cached value. Do NOT ship "validate passes but the numbers are fiction".
- [ ] **Spot-check one cell per numeric column.** `%` columns showing integer `0.0%` throughout means the denominator is wrong or the numerator is cached stale — investigate one cell, fix the pattern.
- [ ] Ranges include every row: off-by-one on `SUM(B2:B12)` when data goes to `B13` is the most common bug.
- [ ] Cross-sheet formulas (`Sheet1!A1`) contain no `\!`. If `officecli get` shows `Sheet1\!A1`, the `!` was shell-corrupted — delete and re-enter via batch/heredoc.
- [ ] Named ranges (`officecli get "$FILE" "/namedrange[1]"`) point at what their names claim.
- [ ] Every `/` denominator is guarded — `IFERROR(x/y, 0)` or `IF(y=0, 0, x/y)`.
- [ ] Chart data vs source cells: for every chart with inline data, spot-check data points against `officecli get` of the source cells.
- [ ] Chart title / series name / legend contain **no** unreplaced tokens (`$...$`, `{var}`, `<TODO>`). Grep the chart via `officecli get /Sheet1/chart[N]`.

## Template QA

When editing a template, check for leftover placeholders — they look like content and slip past `validate`:

```bash
officecli query "$FILE" 'cell:contains("{{")'
officecli query "$FILE" 'cell:contains("xxxx")'
officecli query "$FILE" 'cell:contains("TBD")'
```

## Fresh eyes

When you finish a workbook, open it fresh. Read `view text` / HTML preview top-to-bottom as if you are a new reviewer — look for formulas, numbers that look off, formatting inconsistency, missing data.

## Honest limit

`validate` catches schema errors, not design errors. A workbook can pass `validate` with every number wrong. The checklist above — especially spot-checking formulas against source cells — is how you catch what validation can't.
