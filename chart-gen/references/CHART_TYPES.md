# Chart Types

Quick reference for 18 chart types. Use this table when inferring chart type in Step 1.

## Phase 1 Core Types (detailed matching rules)

| Chart Type | officecli chartType | Typical Scenario | Data Shape Matching Rules | When to Avoid |
|-----------|---------------------|-----------------|--------------------------|---------------|
| **Column** | `column` | Comparison, ranking, categorical comparison | One category axis (text/date) + one or more value columns. 3-20 categories optimal. Series names in first row for multi-series. | >30 categories (columns too dense); time series (use line) |
| **Bar** | `bar` | Horizontal comparison, long category labels | Same as column, but category labels are long (>10 chars) or count >15. Horizontal layout avoids label overlap. | Need to show trends (use line); very few categories (<3, use pie) |
| **Line** | `line` | Trends, time series, continuous data | One continuous category axis (dates/months/quarters) + one or more value columns. ≥6 data points for meaningful trend. | Non-continuous categories (use column); only 2-3 points (no trend meaning) |
| **Pie** | `pie` | Proportions, composition, part-to-whole | One category column + one value column, where sum has meaning (=100% or total). 3-7 categories optimal. | >8 categories (slices too small); negative values; need precise comparison (use column) |

## Extended Types (quick reference)

| Chart Type | officecli chartType | Typical Scenario | Data Shape Matching Rules | When to Avoid |
|-----------|---------------------|-----------------|--------------------------|---------------|
| **Area** | `area` | Trend + accumulation feel | Same as line, but emphasizes magnitude. Stacked display for multi-series totals. | Large magnitude differences between series (small series gets flattened) |
| **Scatter** | `scatter` | Correlation, distribution | Two numeric columns (X, Y), optional third for bubble size. | No correlation requirement; categorical data (use column) |
| **Bubble** | `bubble` | 3D relationship (X, Y, size) | Three numeric columns: X, Y, bubble size. Size column must be positive. | Only 2 dimensions (use scatter); extreme size differences |
| **Radar** | `radar` | Multi-dimensional assessment, capability model | One dimension label column + one value column, or multi-series comparison. 4-8 dimensions optimal. | >10 dimensions (too crowded); incompatible units across dimensions |
| **Stock** | `stock` | Financial OHLC data | Five columns: Date, Open, High, Low, Close. Must be in this exact order. | Non-financial data; missing any price column |
| **Combo** | `combo` | Mixed display (column + line) | At least two value columns, one designated as column, one as line. Typically column=magnitude, line=ratio/trend. | Single series only; no complementary relationship between series |
| **Waterfall** | `waterfall` | Cumulative increase/decrease, financial breakdown | One category column (including "Total") + one value column, positive/negative alternation for gain/loss. First/last row typically totals. | No gain/loss logic in data; no total anchor |
| **Funnel** | `funnel` | Process conversion, stage-wise decrease | One stage name column + one value column, values strictly decreasing. | Non-decreasing values; >8 stages |
| **Treemap** | `treemap` | Hierarchical proportions, disk/budget allocation | Two columns: hierarchy path (e.g., "A/B/C") + value. Supports multi-level nesting. | No hierarchy; need precise comparison (use column) |
| **Sunburst** | `sunburst` | Multi-layer ring proportions | Same as treemap, but visual is concentric rings. 2-4 hierarchy depth optimal. | Too deep hierarchy (>5 rings unreadable); small dataset |
| **Box & Whisker** | `boxWhisker` | Statistical distribution, outlier detection | One numeric column or multiple groups. Auto-calculates quartiles, median, outlier points. | <10 data points (weak statistical meaning); only need mean (use column) |
| **Histogram** | `histogram` | Frequency distribution, data binning | One numeric column. Auto-bins and counts frequencies; no pre-aggregation needed. | Pre-aggregated frequency data (use column); categorical data |
| **Pareto** | `pareto` | 80/20 analysis, vital few | One category column + one value column, sorted descending by value, auto-overlays cumulative percentage line. | Already-sorted data; no cumulative percentage need |
| **Doughnut** | `doughnut` | Proportions (pie variant) | Same as pie. Center hole can display total text. | Same limits as pie; readability drops with multi-series |

## Quick Decision Flow

```
Does the data have trends / a time axis?
  → Yes → Line / Area
  → No → Showing proportions?
      → Yes → Categories ≤7? Pie / Doughnut
               Categories >7 or hierarchical? Treemap / Sunburst
      → No → Comparing magnitudes?
          → Yes → Long labels? Bar : Column
          → No → Multi-dimensional assessment? Radar
              → Financial gain/loss? Waterfall
              → Process conversion? Funnel
              → Correlation? Scatter / Bubble
              → Statistical distribution? BoxWhisker / Histogram
              → 80/20 analysis? Pareto
              → Financial? Stock
              → Mixed display? Combo
```

## Data Shape Checklist

Confirm before creating a chart:

1. **Category axis exists and is non-empty**: At least 3 valid category labels
2. **No empty rows in value columns**: Sample head and tail of dataRange (Step 1 inline `officecli get`)
3. **Series names in first row or column**: officecli auto-infers series name position
4. **No hidden total rows mixed in**: Total rows should be excluded from dataRange, or treated as a separate series
5. **Consistent units**: All values within a series use the same unit (e.g., all in "10k CNY")

## Pivot Table & Auxiliary Table

**When to use an auxiliary table**: Source data comes from a WPS/Excel pivot table and the category columns needed for the chart are non-contiguous in the source (e.g., need column A for month, C for sales, E for profit, but B and D are unrelated columns).

**Preferred approach (explicit binding)**: Even with non-contiguous source columns, if each series can be expressed as a single column range, use explicit `seriesN.values` binding instead of an auxiliary table. This avoids the extra sheet clutter and keeps the chart bound directly to source data.

```bash
# Explicit binding for non-contiguous columns (B, D are data; C is gap)
officecli add file.xlsx /Sheet1 --type chart \
  --prop chartType=column \
  --prop categories="Sheet1!$A$2:$A$13" \
  --prop series1.name="Sales" --prop series1.values="Sheet1!$B$2:$B$13" \
  --prop series2.name="Profit" --prop series2.values="Sheet1!$D$2:$D$13"
```

**Fallback (auxiliary table)**: Use only when categories themselves need reconstruction, data spans multiple sheets, or value transformation is required before charting.

**Process** (Step 1 detects + Step 3 creates):

1. Step 1 detects non-contiguous categories → proposal marks `auxiliary_table.needed = true`
2. Step 3 creates an auxiliary table to the right of the chart anchor, each cell writes a formula `=Sheet1!SourceCell` (e.g., `=Sheet1!C12`, `=Sheet1!E12`)
3. Once the auxiliary table forms a contiguous region, the chart's `dataRange` references the auxiliary table instead of the source data
4. **Critical constraint**: Auxiliary table cells must use formulas; never hardcode numbers (ensures chart auto-syncs when source data updates)

**Example**:

```
Source pivot table (Sheet1):       Auxiliary table (Sheet1, AB8:AC13):
  A(Month) B(empty) C(Sales)        AB8 =Sheet1!A2(Jan)  AC8 =Sheet1!C2(120)
  A3 ...                             AB9 =Sheet1!A3(Feb)  AC9 =Sheet1!C3(135)
                                     ...
```

Chart dataRange becomes `Sheet1!AB8:AC13` (contiguous rectangular region, no column gaps).

## Data Magnitude Differences

**Problem**: Different series in the same chart differ by 2 or more orders of magnitude (e.g., series A range 10-100, series B range 10,000-50,000). Result: the smaller series becomes nearly invisible in the chart (column height approaches 0, line hugs the axis).

**Recommended handling**: **Split charts** rather than using a log axis. Split one chart into 2, each using an appropriate Y-axis range.

**Detection threshold**: `max(series_A) / max(series_B) >= 100` or `min(series_A) / min(series_B) <= 0.01` → mark `split_reason: "magnitude_diff"`, output multiple chart_options.

**Log axis exception**: Only consider a log axis when business semantics genuinely require comparing magnitude-different data side by side (e.g., comparing national GDP with a province's GDP). Default recommendation: do not use log axis.
