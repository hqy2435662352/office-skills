---
name: officecli-pptx
description: "Use this skill any time a .pptx file is involved -- as input, output, or both. This includes: creating slide decks, pitch decks, or presentations; reading, parsing, or extracting text from any .pptx file; editing, modifying, or updating existing presentations; combining or splitting slide files; working with templates, layouts, speaker notes, or comments. Trigger whenever the user mentions 'deck', 'slides', 'presentation', 'pitch', or references a .pptx filename."
---

# OfficeCLI PPTX Skill

## Setup

If `officecli` is missing:

- **macOS / Linux**: `curl -fsSL https://d.officecli.ai/install.sh | bash`
- **Windows (PowerShell)**: `irm https://d.officecli.ai/install.ps1 | iex`

Verify with `officecli --version` (open a new terminal if PATH hasn't picked up). If install fails, download a binary from https://github.com/iOfficeAI/OfficeCLI/releases.

## ⚠️ Help-First Rule

**This skill teaches what good slides look like, not every command flag. When a property name, enum value, or alias is uncertain, consult help BEFORE guessing.**

```bash
officecli help pptx                         # List all pptx elements
officecli help pptx <element>               # Full element schema (e.g. shape, chart, animation, connector, zoom, group)
officecli help pptx <verb> <element>        # Verb-scoped (e.g. add shape, set slide)
officecli help pptx <element> --json        # Machine-readable schema
```

Help reflects the installed CLI version. When skill and help disagree, **help is authoritative**. Triggers to run help immediately: `UNSUPPORTED props:` warning, unknown animation preset, `connector.shape=` enum drifts, prop-vs-alias (`lineWidth` vs `line.width`, `color` vs `font.color`).

## Shell & Execution Discipline

- **Quote paths.** Always `"/slide[1]"` — unquoted `[N]` gets globbed by zsh/bash.
- **`$` in values.** Single-quote: `--prop text='$15M'`. Double-quoted `"$15M"` expands to `M`. In batch heredocs, escape `\$` for currency values.
- **`\n` / `\t`.** Interpreted by the CLI as paragraph break / tab. Double `\\n` for a literal backslash-n.
- **Incremental execution.** One command → check exit code → continue. After any structural op (new slide, chart, animation, connector) run `get` before stacking more.
- **If in doubt**, `view text` and compare character-for-character.

### Windows 平台

[REQUIREMENT] 必须在 Windows 上使用 Python `subprocess.run()` 调用 officecli，禁止使用 PowerShell 管道。详细调用模式见 `@references/PLATFORM_WINDOWS.md`。

[REQUIREMENT] 文件路径包含中文时，必须先 `Copy-Item` 到纯英文路径再操作。

**PPTX 特有 — depth 控制（防 `ChildProcess.kill`）**：PPTX table cell 包含完整 `txBodyRaw` XML，默认 `--depth`（全量）会将整段 XML dump 出来，一个 cell 上百行，极易触发进程超时。读 PPTX table 数据时始终从 `--depth 0` 开始：

| depth | 包含字段 | 每 cell 约 | 用途 |
|-------|---------|-----------|------|
| 0 | fill, size, bold, color, border summary, rowspan, text | ~15 行 | 快速格式检查、语义建模 |
| 1 | + 更多 border 细节 | ~30 行 | 格式审计 |
| 2 | + 完整 txBodyRaw XML | ~100 行 | 需要看原始 XML 时 |

只有在 depth 0 缺少所需信息时才升到 depth 1，极少需要 depth 2。

## Requirements for Outputs

These are the deliverable standards every deck MUST meet. Violating any one = not done, regardless of content quality.

### All decks

**One idea per slide.** If a slide needs a second title to explain what it covers, split it.

**Explicit type hierarchy — do NOT rely on theme defaults.** Set sizes explicitly on every text shape.

| Element | Minimum | Typical | Min shape height |
|---|---|---|---|
| Slide title | **≥ 36pt** bold | 36–44pt | ≥ 2cm |
| Section / subtitle | ≥ 20pt | 20–24pt | ≥ 1.2cm |
| Body text | **≥ 18pt** | 18–22pt | ≥ 1cm |
| Caption / axis label | ≥ 10pt muted | 10–12pt | ≥ 0.6cm |

Rule of thumb: **min shape height ≈ font_pt × 0.05cm**. Title must be **≥ 2× body size**. Four legit exceptions to body ≥ 18pt: chart axis labels, legends, footer / page number, and ≤ 5-word KPI sublabels. Left-align body; center only titles and hero numbers. If "the cards won't fit", drop cards instead of shrinking font.

**Two fonts max, one palette.** One heading font + one body font — a third *display* face only for big numerals or cover title. One dominant brand color (60–70%) + one supporting + one accent. If user gave brand colors/fonts or an existing template, match those first; otherwise see `references/design.md` for calibrated seeds.

**Every slide carries a non-text visual — one that informs.** Shape, chart, icon, gradient band that carries meaning. Exceptions: literal quote slides, code blocks, a single summary-table slide.

**Less is more — every element earns its place.** Don't pad with decorative stats, icons, or filler sections. If a slide feels empty, fix it with layout and whitespace — cut scope rather than bulk it up.

**Speaker notes on every content slide.** `--type notes --prop text="..."`.

**Copy reads human, not AI.** Titles orient on content, not punchline. No "It's not X. It's Y.", no manufactured tension, no faux-insight, no one-word drama. Cut hype adjectives — let the number carry it.

**Preserve existing templates.** When a file already has a theme and masters, match them. Existing conventions override these guidelines.

### Visual delivery floor

Before declaring done, EVERY slide MUST satisfy:

- **No placeholder tokens.** `{{name}}`, `$fy$24`, `<TODO>`, `lorem`, `xxxx`, empty `()`/`[]` in chart titles never appear.
- **No overflow off-edge, no clipped text.** `view issues` flags both. To fix: grow the box or shorten the value.
- **Cover carries orienting elements.** Title + subtitle + presenter/client + date + brand band or key-takeaway strap.
- **Contrast.** `view issues` auto-flags opaque dark text on dark fill (`low_contrast`). On any fill with brightness < 30%, confirm every body run, card body, chart series, and icon is `FFFFFF` or brightness > 80%. Spot-check via `view html`.

If any fails, STOP and fix before declaring done.

Design principles (grid, palettes, font pairings, chart-choice table, animation, layout patterns, image treatment, motifs, AI-tells) → `references/design.md`.

## Common Workflow

1. **Open/save lifecycle.** `open` at start, `save` at end (or `close` to release resident). Flush before non-officecli programs read the file.
2. **Orient.** New: `create`. Existing: `view outline` first. Never edit blind.
3. **Title sequence first (plan, don't build yet).** Write all slide titles; fix the arc before building. Pick ONE title grammar.
4. **Build in display order.** Cover → agenda → dividers → content → closing. Linear append keeps the build script readable.
5. **Incremental per slide.** Create slide + background, then title, then supporting shapes/charts/connectors. `layout=blank` for custom designs. `get /slide[N] --depth 1` after each structural op.
6. **Format to spec.** Per the Requirements section; formatting is deliverable, not polish.
7. **Save + verify.** `save` flushes to disk. Always open in target presentation viewer before shipping.
8. **QA — assume there are problems.** Fix-and-verify until a cycle finds zero new issues.

## Quick Start

Minimal viable deck: cover + one content slide + notes. `$FILE` stands in for your filename.

```bash
FILE="deck.pptx"
officecli create "$FILE"
officecli open "$FILE"

# Cover — dark fill, centered title
officecli add "$FILE" / --type slide --prop layout=blank --prop background=1E2761
officecli add "$FILE" /slide[1] --type shape --prop text="FY26 Strategic Review" \
  --prop x=2cm --prop y=7cm --prop width=29.87cm --prop height=3cm \
  --prop font=Georgia --prop size=44 --prop bold=true --prop color=FFFFFF --prop align=center

# Content — white fill, title + body + notes
officecli add "$FILE" / --type slide --prop layout=blank --prop background=FFFFFF
officecli add "$FILE" /slide[2] --type shape --prop text="Revenue grew 18% YoY" \
  --prop x=1.5cm --prop y=1.2cm --prop width=30cm --prop height=2cm \
  --prop font=Georgia --prop size=36 --prop bold=true --prop color=1E2761
officecli add "$FILE" /slide[2] --type shape --prop text="Enterprise renewals + new EMEA region drove the beat; NRR held at 118%." \
  --prop x=1.5cm --prop y=4cm --prop width=30cm --prop height=3cm \
  --prop font=Calibri --prop size=20 --prop color=333333
officecli add "$FILE" /slide[2] --type notes --prop text="Lead with the 18% beat, preview EMEA."

officecli save "$FILE"
officecli validate "$FILE"
```

Shape of every build: open → slide+background → title → body → notes → save → validate.

## Reading & Analysis

Start wide, then narrow. `outline` first, `view text` / `get` / `query` once you know where to look.

```bash
officecli view "$FILE" outline                 # slide count + titles
officecli view "$FILE" annotated               # per-slide font/size/table/chart breakdown
officecli view "$FILE" text --start 1 --end 5  # text dump (includes table cells)
officecli view "$FILE" issues                  # empty slides, overflow hints
officecli view "$FILE" stats                   # counts + totals (incl. pictures missing alt)
```

**Inspect one element.** XPath-style paths, 1-based. Always quote. Prefer `@name=` / `@id=` over positional `[N]`. `[last()]` works. Add `--json` for machine output.

```bash
officecli get "$FILE" "/slide[1]" --depth 1
officecli get "$FILE" "/slide[1]/shape[@name=Title]"
```

**Query across the deck.** CSS-like selectors: `=`, `~=`, `>=`, `:contains()`, `:no-alt`.

```bash
officecli query "$FILE" 'shape:contains("Revenue")'
officecli query "$FILE" 'picture:no-alt'
```

**Visual preview:** `view "$FILE" html` (per-slide structural ground truth), `view "$FILE" svg --start 3 --end 3` (single slide; charts + gradients do NOT render in SVG).

**Expected non-defect:** `layout=blank` has no title placeholder — `view outline` reporting `(untitled)` is expected.

## Creating & Editing

Verbs: `add` / `set` / `remove` / `move` / `swap` / `batch` / `raw-set`. Ninety percent of a deck is slides, shapes, text, a few charts, pictures, connectors.

### Slides

```bash
officecli add "$FILE" / --type slide --prop layout=blank --prop background=1E2761                          # solid
officecli add "$FILE" / --type slide --prop layout=blank --prop "background=1E2761-CADCFC-180"             # gradient
officecli add "$FILE" / --type slide --prop layout=blank --prop background=image:/path/to/hero.jpg         # image
```

### Shapes

```bash
officecli add "$FILE" /slide[2] --type shape --prop name=Title --prop text="Key Insight" \
  --prop x=2cm --prop y=2cm --prop width=20cm --prop height=3cm \
  --prop font=Georgia --prop size=36 --prop bold=true --prop color=1E2761 --prop fill=none
```

Positioning is explicit — you own the grid math. `--prop preset=` picks geometry (`rect`, `roundRect`, `ellipse`, `triangle`, `star5`, ...). **Name shapes at creation** (`--prop name=HeroTitle`) — names survive z-order / remove-then-add. Re-`get --depth 1` after any structural change before using positional indexes.

### Text inside shapes

`--prop text=` handles one-line text; `\n` = paragraph break, `\t` = tab. For mixed styling within a line, append a styled run:

```bash
officecli add "$FILE" "/slide[2]/shape[@name=Card1]/paragraph[1]" --type run \
  --prop text=" (inline detail)" --prop size=14 --prop italic=true --prop color=8899BB
```

### Charts

Pick chart type per the decision table in `references/design.md`. Full prop list: `help pptx add chart`.

```bash
officecli add "$FILE" /slide[3] --type chart --prop chartType=column \
  --prop series1.name=Revenue --prop series1.values="42,45,48" --prop series1.color=1E2761 \
  --prop categories="Q1,Q2,Q3" \
  --prop x=2cm --prop y=4cm --prop width=20cm --prop height=10cm
```

Gotchas: chart titles with `()`/`[]`/`TBD` ship as literal text; some viewers normalize colors to theme defaults. Full chart reference → `references/charts.md`.

### Pictures

```bash
officecli add "$FILE" /slide[4] --type picture --prop src=hero.jpg \
  --prop x=1cm --prop y=1cm --prop width=32cm --prop height=18cm \
  --prop alt="Product hero, gradient lit from right"
```

Confirm with `officecli query "$FILE" 'picture:no-alt'` — must be empty before delivery.

### Connectors

```bash
officecli add "$FILE" /slide[5] --type connector \
  --prop "from=/slide[5]/shape[@name=BoxA]" --prop "to=/slide[5]/shape[@name=BoxB]" \
  --prop shape=elbow --prop color=333333 --prop tailEnd=triangle
```

Every flow connector needs an arrowhead. Full enum: `help pptx add connector`.

### Other elements

Animations, hyperlinks, tooltips, slide-jump, tables, placeholders, groups, zoom, comments → `references/interactive.md`. Deck-level layout recipes (cover, data slide, flowchart, KPI cards, decision tree, multi-slide skeletons) → `references/layouts.md`.

## QA (Required)

**Assume there are problems.** First render is almost never correct.

- **Gate 1 — schema.** `officecli validate "<file>"`. Any error → REJECT.
- **Gate 2 — overflow / format / structure.** `officecli view "<file>" issues`. Any issue → REJECT, fix, re-run.
- **Gate 2b — leftover placeholders.** `officecli view "<file>" text`, scan for `xxxx`, `lorem`, `<TODO>`, `placeholder`, empty `()`/`[]`. Any hit → REJECT.

Full Gate 3 visual audit (screenshot / HTML-text fallback, per-slide checklist, fix-verify loop, mandatory flush) → `references/qa.md`.

## Reference Files

| Task | Read |
|------|------|
| Design principles, grid, palettes, font pairings | `references/design.md` |
| Chart types, series, data feeds | `references/charts.md` |
| Layout recipes (cover, data slide, flowchart, KPI, decision tree) | `references/layouts.md` |
| Animations, hyperlinks, zoom | `references/interactive.md` |
| Full QA delivery gates + visual audit | `references/qa.md` |
| Common pitfalls, shell traps | `references/pitfalls.md` |
