# Charts

Chart types live under `officecli help xlsx chart` — the enum is long (20+). Pick the right one for the message: column for category comparison, line for time series, pie only when slices are self-evidently proportional, scatter for correlation. Avoid exotic types unless they answer a specific question.

**Three ways to feed chart data. Pick one per chart — mixing them at add-time is a common trap.**

| Form | Shape | When to use |
|---|---|---|
| (a) inline `data` | `--prop data="Sales:100,200,300" --prop categories="Jan,Feb,Mar"` | Tiny demo charts, numbers you will not edit. Source of truth lives in the chart XML, not a cell. |
| (b) 2D `dataRange` | `--prop dataRange="Sheet1!A1:B4"` (first col = categories, first row = header / series name) | Normal case. Must be **2-D** — single column fails with "Chart requires data". |
| (c) dotted per-series | `--prop series1.name=Sales --prop series1.values="Sheet1!B2:B4" --prop series1.categories="Sheet1!A2:A4"` | Multi-series charts where each series points at non-contiguous ranges, or you want explicit series naming. `series1.values` alone (no `categories`) emits a chart with `1,2,3` as the x-axis. |

**The single-column trap.** `dataRange="Sheet1!B2:B13"` looks like "value column" but the engine rejects it with `Chart requires data`. Either widen the range to include the category column (`A2:B13`), or switch to form (c) with explicit `series1.categories`.

**Move / resize a chart after create:** `set chart[N] --prop anchor="F5:N25"` (also `--prop x= --prop y= --prop width= --prop height=`). **Series are still immutable** — to add/change a series, `officecli remove` the chart and `officecli add` with the full series list. Note `remove chart[1]` shifts `chart[2] → chart[1]` and re-add **appends at the end** — to preserve chart order, remove all and rebuild in order.

**Anchor sizing.** No auto-fit. A column chart with 5-6 categories + 2 series needs roughly `A5:L22` (12 cols × 18 rows) to show all labels uncut. Narrower and X-axis labels clip; wider and the chart can split across pages on print/export. If in doubt, start narrow, preview via `view html` (Read the returned HTML path), widen in increments.

**Chart `dataRange` — always prefix with the sheet.** Even when the chart lives on the same sheet, write `dataRange="Summary!A17:C22"`, not `A17:C22`. The sheet-less form works inconsistently; the prefixed form is 100% reliable.

officecli adds extended chart types the classic Excel object model lacks: `boxWhisker`, `waterfall`, `funnel`, `histogram`, `treemap`, `sunburst`, `pareto`. Use them when the data calls for them.

**NEVER put unreplaced template tokens in chart title / series name / legend / axis title.** `$fy$24`, `{var}`, `<TODO>`, `$VAR`, `{{placeholder}}` render **literally** in the legend — validate passes, but a CFO sees `$fy$24` where "FY2024" should be. Always bind to final text or a cell reference (`title="FY2024 Revenue"` or `series1.name="Sheet1!A1"`).

## Chart Axis-by-Role

Editing a chart axis in place is cheaper than rebuilding the chart. Address axes by **role** (`value` = Y, `category` = X), not by index — the XML order isn't stable.

```bash
officecli get "$FILE" "/Sheet1/chart[1]/axis[@role=value]"
officecli set "$FILE" "/Sheet1/chart[1]/axis[@role=value]" --prop min=0 --prop max=100000
officecli set "$FILE" "/Sheet1/chart[1]/axis[@role=category]" --prop title="Month"
```

Safe props: `title`, `min`, `max`, `majorGridlines`, `visible`, `labelRotation`.
