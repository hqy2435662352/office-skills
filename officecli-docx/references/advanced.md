# Advanced & Specialty Topics

Reports, memos, letters, proposals, and HR templates don't need this. Keep reading only if your document is academic (equations, footnotes, bibliography), reviewed (comments, tracked changes), or marked (watermark).

## Equations and footnotes

`--type equation` takes LaTeX — `\frac`, `\sum`, Greek, `\mathit`, `\mathcal` all render. By default it creates a standalone `/body/oMathPara[N]` display block; pass `--prop mode=inline` with a paragraph parent path (`add "/body/p[N]" --type equation --prop formula=… --prop mode=inline`) to drop an inline `<m:oMath>` into running text. Footnotes auto-number by paragraph index. Bibliography hanging indent: `firstLineIndent=-720 indent=720` per entry.

```bash
officecli add "$FILE" /body --type equation --prop formula="\\frac{a}{b} + \\sum_{i=1}^{n} x_i"
officecli add "$FILE" "/body/p[3]" --type footnote --prop text="See Appendix A for methodology."
```

## Comments and tracked changes

Bulk accept/reject: `set "$FILE" /revision --prop revision.action=accept` (or `--prop revision.action=reject`); narrow with a selector like `/revision[@author=Alice]` or `/revision[@type=ins]`. Locate individual changes with `query ins` and `query del` (`trackedchange` is not a selector). Create tracked changes on a run with `--prop revision.type=ins|del --prop revision.author=…` (`help docx run` for the full `revision.*` set — `format`/`moveFrom`/`moveTo` too). Add a comment: `add "/body/p[4]" --type comment --prop author=… --prop text=…`; reply-thread it with `--prop parentId=N` and mark it resolved with `set "/comments/comment[N]" --prop done=true` (resolve rather than delete to keep the audit trail — `query 'comment[done=false]'` then lists what's still open). Prop schema: `help docx comment` / `help docx run`.

## Watermark

`add / --type watermark --prop text="DRAFT" --prop color=BFBFBF --prop opacity=0.8` in one command (default opacity 0.5); `set /watermark --prop opacity=…` adjusts it later.

## Images

Pictures go inside a run. Alt text is mandatory for accessibility — pass `alt` directly at create time:

```bash
officecli add "$FILE" "/body/p[5]" --type picture --prop src=logo.png --prop width=1.5in --prop alt="Acme logo"
```

Confirm `officecli query "$FILE" 'image:no-alt'` is empty before delivery.

## Charts

For data, add a **native chart** — editable, themeable, accessible, re-renders in Word — never a flat PNG screenshot of a chart. `data="Label:v1,v2,…"` per series; one `data=` per series (or `series1=`/`series2=`).

```bash
officecli add "$FILE" /body --type chart --prop chartType=bar --prop title="Revenue by Region" --prop categories="EMEA,APAC,Americas" --prop data="2026:120,150,180"
```

`chartType` ∈ bar / column / line / pie / area / scatter (`help docx chart` for axis/legend/series styling). A PNG via `--type picture` is only a fallback for an exotic chart officecli can't build.

## Hyperlinks and bookmarks

External links go via `hyperlink`:

```bash
officecli add "$FILE" "/body/p[2]" --type hyperlink --prop url="https://example.com" --prop text="our site"
```

**Internal links** (to a bookmark) use `--prop anchor=bookmarkName` — not a `#fragment` in `url`:

```bash
officecli add "$FILE" "/body/p[2]" --type hyperlink --prop anchor=chapter1 --prop text="See Chapter 1"
```

Pairing a `PAGEREF` field with visible text is the alternative. See `help docx hyperlink` / `help docx bookmark`.

## When to switch skills

Stay in docx for chapter drafts, ≤ 3 footnotes, ≤ 2 equations, no bibliography/cross-refs. Switch to **`academic-paper`** for citation styles (APA / Chicago / IEEE / GB 7714), in-text↔reference auto-linking, numbered equations with `\ref`, "List of Figures", or auto-updating cross-refs. Switch to **`officecli-word-form`** when the document's purpose is **data capture** — fillable forms, contracts with user-fill slots, questionnaires, mail-merge templates (`<w:sdt>` content controls, `<w:ffData>`, `documentProtection=forms`).

## Raw-set escape hatch (L1 / L2 / L3)

Three tiers of precision; use the lowest that does the job.

- **L1 — high-level props** (`--prop text=…`, `--prop style=Heading1`): your default. Covers 80%.
- **L2 — dotted-attr fallback** (`pbdr.top=`, `ind.left=`, `shd.fill=`, `padding.top=`, `font.size=`): when L1 lacks the knob. Example: `--prop pbdr.bottom="single;6;1F4E79;0"`. Emits schema-valid XML.
- **L3 — `raw-set` with XML**: last resort, no schema protection. Use for internal hyperlinks, composite fields, and other shapes the typed verbs can't express (see references/pitfalls.md for the full XML appendix).

Borders use the format `style;size;color;space`: `single;4;FF0000;1`. Hex colors never start with `#`: `FF0000`. Scheme color names (`accent1..6`, `dark1`/`dark2`, `light1`/`light2`, `hyperlink`) are accepted anywhere a hex color is — prefer hex for stable colors across themes.
