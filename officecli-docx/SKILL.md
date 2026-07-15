---
name: officecli-docx
description: "Use this skill any time a .docx file is involved -- as input, output, or both. This includes: creating Word documents, reports, letters, memos, or proposals; reading, parsing, or extracting text from any .docx file; editing, modifying, or updating existing documents; working with templates, tracked changes, comments, headers/footers, or tables of contents. Trigger whenever the user mentions 'Word doc', 'document', 'report', 'letter', 'memo', or references a .docx filename."
---

# OfficeCLI DOCX Skill

## Setup

If `officecli` is missing:

- **macOS / Linux**: `curl -fsSL https://d.officecli.ai/install.sh | bash`
- **Windows (PowerShell)**: `irm https://d.officecli.ai/install.ps1 | iex`

Verify with `officecli --version` (open a new terminal if PATH hasn't picked up). If install fails, download a binary from https://github.com/iOfficeAI/OfficeCLI/releases.

## ⚠️ Help-First Rule

**This skill teaches what good docx looks like, not every command flag. When a property name, enum value, or alias is uncertain, consult help BEFORE guessing.**

```bash
officecli help docx                         # List all docx elements
officecli help docx <element>               # Full element schema (e.g. paragraph, field, numbering, watermark, toc)
officecli help docx <verb> <element>        # Verb-scoped (e.g. add field, set section)
officecli help docx <element> --json        # Machine-readable schema
```

Help is pinned to the installed CLI version. When this skill and help disagree, **help is authoritative**.

## Mental Model

A `.docx` is a ZIP of XML parts (`document.xml`, `styles.xml`, `numbering.xml`, `header*.xml`, `footer*.xml`, `comments.xml`, …). Everything the user sees — headings, tables, page numbers, TOC, tracked changes — is XML inside that ZIP. `officecli` gives you a semantic-path API (`/body/p[1]/r[2]`) over it, so you almost never touch raw XML; when you must, use `raw-set` (see references/pitfalls.md).

## Shell & Execution Discipline

docx paths contain `[]`; some prop values contain `$`. Both are shell metacharacters.

1. **Shell.** ALWAYS quote element paths: `"/body/p[1]"`, not `/body/p[1]` (zsh/bash glob `[N]`). Single-quote any value containing `$`: `--prop text='$50M'`. Unquoted `$50M` is stripped to `M`.
2. **CLI (`text=`).** The two-char escapes `\n` and `\t` ARE interpreted in `--prop text=` — `\n` becomes a `<w:br/>` soft line break, `\t` a `<w:tab/>`. Double them (`\\n`) for a literal backslash-n. This applies to row-level table `c1…cN` shortcuts too.
3. **JSON (batch).** A real newline can also be passed as `"\n"` in the JSON string of a `batch` heredoc.

**Incremental execution.** `officecli` mutates the file on every call. Run commands one at a time and check each exit code. After any structural op (new style, table, TOC, section break) run `get` on it before stacking more.

**Open/save lifecycle:** `officecli open <file>` at the start, `officecli save <file>` at the end to flush to disk. For many paragraphs of one style, use `batch`. **Flush only at the non-officecli boundary:** officecli's own reads always see your edits; run `save`/`close` only before a non-officecli program reads the file.

**`$FILE` convention.** All commands use `"$FILE"` — set it once (`FILE="your-doc.docx"`). Never copy a literal filename template into output — always substitute your actual target.

### Windows 平台

[REQUIREMENT] 必须在 Windows 上使用 Python `subprocess.run()` 调用 officecli，禁止使用 PowerShell 管道（中文输出损坏为 `"���"`，大输出触发 `ChildProcess.kill`）。详细调用模式（Python subprocess / PowerShell 备选 / 中文路径兜底）见 `@references/PLATFORM_WINDOWS.md`。

[REQUIREMENT] 文件路径包含中文时，必须先 `Copy-Item` 到纯英文路径再操作。

## Requirements for Outputs

Deliverable standards every document MUST meet.

**Clear hierarchy.** Every non-trivial document has Title → Heading 1 → Heading 2 → body. If `view outline` shows one flat list, the hierarchy is missing.

**Explicit heading sizes** (Word default style sizes drift between templates): **H1 ≥ 18pt** (20pt for long reports), H2 = 14pt bold, H3 = 12pt bold, body = 11–12pt, line spacing 1.15–1.5x. Prefer `style=Heading1` over inline sizes so a retheme touches the definition once — but set explicit sizes when you can't trust the template's styles.

**One body font, one accent.** One readable body font (Calibri, Cambria, Georgia, Times New Roman); accent color for heading emphasis or table headers.

**Spacing through properties.** Use `spaceBefore` / `spaceAfter` on paragraphs. Rows of empty paragraphs break pagination and are flagged by `view issues`.

**Typographic quality.** New content uses curly quotes (`'` `'` `"` `"`), not ASCII. En-dash `–` for ranges (`2024–2026`), em-dash `—` for parenthetical breaks.

**Headers, footers, page numbers on any document > 1 page.** Page numbers go through a live `PAGE` field (`--prop field=page`), never the literal text "Page 1" — the CLI injects `<w:fldChar>` for you.

**Preserve existing templates.** When editing a file that already has a look, match it — existing conventions override these guidelines.

### Visual delivery floor (applies to EVERY document)

Before declaring done, run `officecli view "$FILE" html` and Read the returned HTML path to confirm ALL of these:

- **No placeholder tokens rendered as data.** `$xxx$`, `{var}`, `{{name}}`, `<TODO>`, `lorem`, `xxxx` must never appear in a heading, body, cover, TOC, caption, header, or footer.
- **No truncated titles or overflowing cells.** Widen the column or set `wrapText` rather than trimming content.
- **TOC present when the document has 3+ headings** (`--type toc`).
- **Cover page ≥ 60% filled, last page ≥ 40% filled.**
- **No `\$`, `\t`, `\n` literals in document text.** If `view text` shows these, a shell-escape layer leaked — delete the paragraph and re-enter it.

If any fails, STOP and fix before declaring done.

## Common Workflow

Six steps. Every non-trivial build follows this shape.

1. **Open.** `officecli open "$FILE"` (new: `officecli create "$FILE"` first).
2. **Orient.** `officecli view "$FILE" outline` — heading tree, section count, existing TOC/watermark/tracked changes.
3. **Build incrementally.** Structural first, content next, formatting last. After each structural op, `get` it back before stacking.
4. **Format to spec.** Explicit heading sizes, spacing, widths, alignment, tabs, list indents — formatting is part of the deliverable.
5. **Save, then trust structure over cached text.** `officecli save "$FILE"`. TOC/PAGE/NUMPAGES/SEQ/PAGEREF fields carry cached values that may be stale. Confirm fields *exist* (`get --depth 3` finds `<w:fldChar>`) rather than trusting visible text.
6. **QA — assume there are problems.** You are done after one fix-and-verify cycle finds zero new issues, not when your last command exited 0. See references/qa.md.

## Quick Start

Minimal viable docx: a heading, a body paragraph, a subheading, and a footer with a live page-number field.

```bash
FILE="review.docx"
officecli create "$FILE"
officecli open "$FILE"
officecli add "$FILE" /body --type paragraph --prop text="Q4 2026 Review" --prop style=Heading1 --prop size=20pt --prop bold=true --prop spaceAfter=12pt
officecli add "$FILE" /body --type paragraph --prop text="Revenue grew 18% year-over-year, ahead of plan." --prop size=11pt --prop spaceAfter=8pt
officecli add "$FILE" /body --type paragraph --prop text="Key Drivers" --prop style=Heading2 --prop size=14pt --prop bold=true --prop spaceBefore=12pt --prop spaceAfter=6pt
officecli add "$FILE" /body --type paragraph --prop text="Enterprise renewals, upsell, and a new EMEA region." --prop size=11pt
officecli add "$FILE" / --type footer --prop type=default --prop size=9pt --prop text="Page " --prop field=page
officecli set "$FILE" "/footer[1]/p[1]" --prop align=center
officecli save "$FILE"
officecli validate "$FILE"
```

Verified: `validate` returns `no errors found`; `get /footer[1] --depth 3` shows the 5-run PAGE field chain.

## Reading & Analysis

Start wide, then narrow. `outline` tells you what's there; `get`/`query`/`view` once you know where to look.

```bash
officecli view "$FILE" outline            # heading tree, section count, table/image counts, watermark, tracked-changes presence
officecli view "$FILE" html               # first visual check — returned HTML path for Read
officecli view "$FILE" text --start 1 --end 80   # text with [/body/p[N]] paths
officecli view "$FILE" annotated          # values + style/font/size + warnings per run
officecli view "$FILE" stats              # paragraph counts, font usage, style distribution
officecli view "$FILE" issues             # empty paras, missing alt text, spacing anomalies
```

**Inspect one element.** XPath-style semantic paths (1-based). Always quote. Use `[last()]` (with parens) for the last element.

```bash
officecli get "$FILE" /                          # document root: metadata, page setup
officecli get "$FILE" "/body/p[1]"                # one paragraph
officecli get "$FILE" "/body/p[1]/r[1]"           # one run (character-level formatting)
officecli get "$FILE" "/body/tbl[1]" --depth 3    # table with rows and cells
officecli get "$FILE" "/footer[1]" --depth 3      # footer — check for fldChar
officecli get "$FILE" "/styles/Heading1"          # style definition
officecli get "$FILE" /numbering --depth 2        # numbering abstractNum + num bindings
```

**Query across the document.** CSS-like selectors. Operators: `=`, `!=`, `~=` (contains), `>=`, `<=`, `[attr]` (exists).

```bash
officecli query "$FILE" 'paragraph[style=Heading1]'       # all H1s
officecli query "$FILE" 'p:contains("quarterly")'         # text match
officecli query "$FILE" 'p:empty'                         # empty paragraphs
officecli query "$FILE" 'image:no-alt'                    # accessibility gaps
officecli query "$FILE" 'paragraph[size>=24pt]'           # numeric comparison
officecli query "$FILE" 'field[fieldType!=page]'          # fields other than PAGE
```

**Large documents.** Navigate by heading with `view outline` and jump with `query`; don't dump the whole body into context.

## Creating & Editing

Verbs: `add`, `set`, `remove`, `move`, `swap`, `batch`, `raw-set` (last-resort XML). Ninety percent of a build is paragraphs, runs, tables, a TOC, and a footer.

### Paragraphs, runs, styles

A paragraph (`p`) is a block; a run (`r`) is a span of consistent character formatting inside it. Set paragraph-level props (style, alignment, spacing, indent) on the `p`; set font/size/color/bold on the `r`.

```bash
officecli add "$FILE" /body --type paragraph --prop text="Executive Summary" --prop style=Heading1 --prop size=18pt --prop bold=true --prop spaceAfter=12pt
officecli set "$FILE" "/body/p[1]/r[1]" --prop color=1F4E79
```

Use `spaceBefore`/`spaceAfter` for vertical spacing — never chains of empty paragraphs. For left indent: `indent=720` (twips), `firstLineIndent=360` for first line, `hangingIndent=720` for hanging.

### Tables

Tables are `/body/tbl[N]` with rows `tr[N]` and cells `tc[N]`. Add with row/column counts, then fill. Row-level `set` supports `height`, `header`, and `c1/c2/…/cN` text shortcuts. Cell formatting (bold, fill, color) goes on the cell's paragraph/run — **not** row-level.

```bash
officecli add "$FILE" /body --type table --prop rows=4 --prop cols=3 --prop width=100%
officecli set "$FILE" "/body/tbl[1]/tr[1]" --prop header=true --prop c1=Quarter --prop c2="Revenue" --prop c3="Growth"
officecli set "$FILE" "/body/tbl[1]/tr[1]/tc[1]/p[1]/r[1]" --prop bold=true
```

**Horizontal rule = a paragraph bottom border, never a 1-row table.** Use `pbdr.bottom` (`STYLE;SIZE;COLOR`):

```bash
officecli set "$FILE" "/body/p[3]" --prop pbdr.bottom="single;6;2E75B6"
```

**Table recipes** (header row with fill, financial table alignment, zebra stripes, bullets in cells) → references/tables.md.

### Lists, tab stops, fields, headers/footers, TOC, images, charts, hyperlinks, sections, page breaks, cover pages

All of these have their own reference files with full recipes. See the Reference Files table below for which file to read per task.

### Raw-set escape hatch (L1 / L2 / L3)

Three tiers of precision; use the lowest that does the job.

- **L1 — high-level props** (`--prop text=…`, `--prop style=Heading1`): your default. Covers 80%.
- **L2 — dotted-attr fallback** (`pbdr.top=`, `ind.left=`, `shd.fill=`): when L1 lacks the knob. Emits schema-valid XML.
- **L3 — `raw-set` with XML**: last resort, no schema protection. Full XML appendix → references/pitfalls.md.

Borders use format `style;size;color;space`: `single;4;FF0000;1`. Hex colors never start with `#`. Scheme color names (`accent1..6`, `dark1`/`dark2`, `light1`/`light2`, `hyperlink`) accepted anywhere hex is.

## QA

**Assume there are problems — QA is a bug hunt, not a confirmation step.** Your first document is almost never correct; zero issues on first inspection means you weren't looking hard enough.

### Minimum cycle before "done"

1. `officecli view "$FILE" issues` — empty paras, missing alt text, formatting anomalies.
2. `officecli view "$FILE" outline` — heading hierarchy, TOC presence, section count.
3. `officecli view "$FILE" text --max-lines 400` — typos, stray `\$`/`\t`/`\n` literals, placeholder tokens.
4. `officecli validate "$FILE"` — schema check.
5. **Visual pass — whole document as a contact sheet** (vision-capable agents only). `officecli view "$FILE" screenshot --grid auto -o /tmp/sheet.png`, then Read it. `--grid auto` tiles **every page** into one image. If the screenshot fails, fall back to `view html` and flag cross-page breaks/alignment/rhythm as "not visually verified". Thumbnails only **locate**: confirm any fine call at full resolution with `screenshot --page N`. "validate pass" is not delivery; "looks like a real document" is.
6. If anything failed, fix, then **rerun the full cycle** — one fix commonly creates another problem.

Full Delivery Gate script, field spot-check, honest limits, and QA display notes → references/qa.md.

## Reference Files

| Task | Read |
|------|------|
| Tables (header rows, financial tables, zebra, bullets in cells) | references/tables.md |
| Fields (PAGE, NUMPAGES, MERGEFIELD, REF, SEQ, PAGEREF), TOC | references/fields.md |
| Headers, footers, page numbering, Page X of Y | references/headers-footers.md |
| Lists, tab stops, multi-level numbering | references/lists.md |
| Sections, page setup, columns, cover, page breaks | references/sections.md |
| Advanced (equations, footnotes, comments, tracked changes, watermark, images, charts, hyperlinks) | references/advanced.md |
| Report recipes (cover, financial table, bullets in cells) | references/recipes.md |
| Full QA cycle, delivery gates | references/qa.md |
| Known issues, pitfalls, renderer quirks, raw-set XML | references/pitfalls.md |

## Known Issues & Pitfalls

When something "looks broken", attribute before chasing: **[AGENT-ERROR]** fix it · **[RENDERER-BUG]** don't chase · **[SKILL gap]** file an issue.

Most common pitfalls:

| Pitfall | Correct approach |
|---|---|
| `--index` vs `[N]` | `--index` is 0-based; `[N]` paths are 1-based |
| Unquoted `[N]` in zsh/bash | Quote every path: `"/body/p[1]"` |
| `[last]` as predicate | Must be `[last()]` with parens |
| Empty paragraphs for spacing | Use `spaceBefore` / `spaceAfter` |
| Row-level `set` for cell formatting | Row only supports `height`, `header`, `c1..cN`; format goes on cell paragraph/run |
| `listStyle` on a run | It's a paragraph property |
| `--type pagebreak` OR `pageBreakBefore` alone not breaking | Apply BOTH (see references/sections.md) |
| Next paragraph inherits previous Heading style | Set explicit `--prop style=Normal` |

Full pitfalls table, renderer quirks, and the raw-set XML appendix → references/pitfalls.md.

### Help pointer

When in doubt: `officecli help docx`, `officecli help docx <element>`, `officecli help docx <verb> <element>`, `--json` for agents. Help is the authoritative schema; this skill is the decision guide.
