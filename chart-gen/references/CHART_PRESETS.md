# Chart Presets

7 built-in officecli style presets. Apply via `--prop preset=X` when creating a chart.

## Core Rule: Two-Step Method

**Always preset first, override second.**

```bash
# Step 1: Create chart skeleton with preset
officecli add <file> /SheetName \
  --type chart \
  --prop chartType=column \
  --prop dataRange="Sheet1!A1:C13" \
  --prop title="Monthly Sales Trend" \
  --prop preset=corporate \
  --prop anchor=D2:J18

# Step 2: Override preset-affected properties after creation
officecli set <file> /SheetName/chart[N] --prop legend=bottom
officecli set <file> /SheetName/chart[N] --prop dataLabels=outsideEnd
```

**Why two steps?** `preset=corporate` ships with legend=right, business color scheme, and other defaults. If you pass `legend=bottom` together with preset during `add`, the preset may silently override it. Two-step separation ensures manual properties are written after the preset takes effect.

**Warning**: Passing `preset=X` together with properties like `legend=bottom` during `add` can result in the latter being silently overridden by the preset. Never do this.

---

## Preset Overview

### 1. minimal

| Property | Description |
|----------|-------------|
| **Visual effect** | Ultra-minimal: no gridlines, no background fill, no legend border, thin series outlines, no 3D effects |
| **Best for** | Embedded in report body, needs to blend with surrounding text, print-friendly |
| **Properties overridden** | `gridlines` (none), `legend` (no border), `chartBackground` (transparent/white), `seriesOutline` (thin or none), `dataLabels` (hidden by default), `3D` (off) |

### 2. dark

| Property | Description |
|----------|-------------|
| **Visual effect** | Dark background + high-contrast series colors. Suitable for dark-themed presentations or night-mode reports |
| **Best for** | Dark PPT themes, Dashboard dark mode, projection environments |
| **Properties overridden** | `chartBackground` (dark gray/black), `plotAreaBackground` (dark gray), `fontColor` (white/light gray), `colors` (high-saturation contrast), `gridlines` (light gray, thin), `legend` (dark background) |

### 3. corporate

| Property | Description |
|----------|-------------|
| **Visual effect** | Business-professional: blue-gray primary palette, clear gridlines, standard legend, professional fonts |
| **Best for** | Annual reports, business presentations, financial analysis, formal proposals |
| **Properties overridden** | `colors` (blue/gray/orange business palette), `gridlines` (light gray solid), `legend` (right, with border), `font` (sans-serif, e.g., Calibri), `chartBackground` (white), `title` (dark, bold) |

### 4. magazine

| Property | Description |
|----------|-------------|
| **Visual effect** | Magazine-style: bold color palette, no gridlines, large title font, artistic legend placement |
| **Best for** | Marketing collateral, brand showcases, social media/editorial graphics, visual impact pieces |
| **Properties overridden** | `colors` (high-saturation/complementary), `gridlines` (none), `legend` (bottom or top, no border), `font` (large size, may use serif), `title` (large, prominent), `chartBackground` (white or transparent) |

### 5. dashboard

| Property | Description |
|----------|-------------|
| **Visual effect** | Dashboard-optimized: compact layout, data labels displayed outside, simplified legend, readability-focused |
| **Best for** | KPI panels, real-time monitoring, data walls, quick-scan scenarios |
| **Properties overridden** | `dataLabels` (show values, outside), `legend` (bottom or hidden), `gridlines` (faint or none), `colors` (high distinction), `chartBackground` (transparent, blends into panel), `title` (small or hidden) |

### 6. colorful

| Property | Description |
|----------|-------------|
| **Visual effect** | Multicolored: rainbow palette, lively and bright, good for distinguishing many series |
| **Best for** | Educational materials, children's content, complex charts with 5+ series to distinguish |
| **Properties overridden** | `colors` (rainbow/multi-color wheel), `chartBackground` (white), `legend` (colored markers), `fontColor` (dark gray), `gridlines` (faint) |

### 7. monochrome

| Property | Description |
|----------|-------------|
| **Visual effect** | Single-hue: different lightness/saturation of the same hue to distinguish series. Minimal, professional, colorblind-friendly |
| **Best for** | Academic papers, black-and-white printing, colorblind accessibility, serious data presentations |
| **Properties overridden** | `colors` (single-hue gradient, e.g., blue 100%-20%), `chartBackground` (white), `legend` (grayscale markers), `gridlines` (gray), `fontColor` (black/dark gray) |

---

## Preset vs. Manual Property Interaction

### Safe practice

```bash
# Correct: preset at add stage, override at set stage
officecli add file.xlsx /Sheet1 --type chart --prop preset=corporate ...
officecli set file.xlsx /Sheet1/chart[1] --prop legend=bottom
officecli set file.xlsx /Sheet1/chart[1] --prop dataLabels=outsideEnd
```

### Dangerous practice (don't do this)

```bash
# Wrong: preset and legend passed together — legend may be overridden
officecli add file.xlsx /Sheet1 --type chart --prop preset=corporate --prop legend=bottom ...
```

### Commonly Overridden Properties

Regardless of which preset you choose, the following properties may be overridden. If you need custom values, you must apply them via `set` after `add`:

- `legend` (position, border, background)
- `colors` (series color scheme)
- `gridlines` (presence, color, line style)
- `chartBackground` / `plotAreaBackground` (background color)
- `font` / `fontColor` / `fontSize` (global font)
- `seriesOutline` (series border)
- `dataLabels` (show/hide, position)
- `marker` (line chart point marker style)

> **Rule**: At the `add` stage, pass only `preset=X` — never pass any property the preset might override. All style adjustments happen during the `set` stage.
>
> **Note**: When source data changes from static values to formula references, the `dataLabels` number format may drift. After changing source data, verify data label formatting and re-apply `officecli set ... --prop dataLabels=...` if necessary.

---

## Data Label Format & Overlap Prevention

### Number Shortening (use only when labels are too long or overlapping)

**Do not shorten numbers for all charts by default**. Only apply custom number formats when data labels are already overlapping, or when long digit strings hurt readability:

```bash
# Display 12345 as 1.2万
officecli set file.xlsx /Sheet1/chart[1] --prop numberFormat='#,##0.0,"万"'

# Display 150000000 as 1.5亿
officecli set file.xlsx /Sheet1/chart[1] --prop numberFormat='#,##0.0,,"亿"'

# Percentage with 1 decimal place
officecli set file.xlsx /Sheet1/chart[1] --prop numberFormat='0.0%'
```

**Format notes**:
- `#,##0.0,` — thousands separator, 1 decimal, trailing "," divides by 1000
- `#,##0.0,"万"` — divides by 10000 and appends "万" (10k)
- `#,##0.0,,"亿"` — double comma divides by 1000000 and appends "亿" (100M)

### Gap Width (gapWidth) Override

Increasing inter-column gap in column/bar charts can relieve label crowding:

```bash
# Increase gap width (default 150, max 500)
officecli set file.xlsx /Sheet1/chart[1] --prop gapWidth=200
```

**When to use**: Columns are too dense, causing data labels to overlap; widening the anchor wasn't enough. **Note**: `gapWidth` only applies to column/bar charts; ignored by pie/line charts.

### Overlap Prevention Trilogy (apply in priority order)

1. **Shorten number format** → reduce label text width
2. **Widen anchor by 2+ columns** → increase chart drawing area
3. **Increase gapWidth** → increase inter-column spacing

Try them in sequence; stop once any fix resolves the issue.
