# QA Reference

**Assume there are problems — QA is a bug hunt, not a confirmation step.** Your first document is almost never correct; zero issues on first inspection means you weren't looking hard enough. Headings look fine until `view outline` shows an H3 directly under an H1; the footer shows "Page 1" until `get --depth 3` reveals a static run, not a field.

## Minimum cycle before "done"

1. `officecli view "$FILE" issues` — empty paras, missing alt text, formatting anomalies.
2. `officecli view "$FILE" outline` — heading hierarchy (no H1 → H3 skips), TOC presence, section count.
3. `officecli view "$FILE" text --max-lines 400` — typos, stray `\$`/`\t`/`\n` literals, placeholder tokens.
4. `officecli validate "$FILE"` — schema check (the Delivery Gate re-runs this on the closed, on-disk file).
5. **Visual pass — whole document as a contact sheet** (vision-capable agents only — if you cannot interpret images, skip this step: steps 1–4 are your ceiling, and flag the document "not visually verified" at handoff). `officecli view "$FILE" screenshot --grid auto -o /tmp/sheet.png`, then Read it. `--grid auto` tiles **every page** into one image (auto column count; `--grid 4` to force) — you *see* pagination, blank pages, heading rhythm, lopsided margins, and TOC/cover placement, not just the DOM. Windows+Word renders each page through real Word; elsewhere HTML. If the screenshot fails, fall back to `view html` and flag cross-page breaks / alignment / rhythm as "not visually verified". Thumbnails only **locate**: confirm any fine call (column alignment, line spacing, indents, dark-on-dark, caption placement) on the suspect page at full resolution with `screenshot --page N` (no `--grid`; real Word on Windows). "validate pass" is not delivery; "looks like a real document" is.
6. If anything failed, fix, then **rerun the full cycle** — one fix commonly creates another problem.

## Delivery Gate (run before handing off — any failure = REJECT, do NOT deliver)

Copy-paste, set `FILE`, and refuse to declare done until every gate prints OK.

```bash
FILE="your-file.docx"

# Gate 1 — schema.
officecli close "$FILE" 2>/dev/null
officecli validate "$FILE" | grep -q "no errors found" || { echo "REJECT Gate 1: validate failed"; exit 1; }
echo "Gate 1 OK"

# Gate 2 — token leak (shell-escape / template tokens / TOC placeholder / literal \$ \t \n). grep -c never false-PASSes.
LEAK=$(officecli view "$FILE" text | grep -cE '(\$[A-Za-z_]+\$|\{\{[^}]+\}\}|<TODO>|xxxx|lorem|Update field to see|\\[\$tn])')
[ "$LEAK" -eq 0 ] && echo "Gate 2 OK" || { echo "REJECT Gate 2: $LEAK leak line(s)"; officecli view "$FILE" text | grep -nE '(\$[A-Za-z_]+\$|\{\{[^}]+\}\}|<TODO>|xxxx|lorem|Update field to see|\\[\$tn])'; exit 1; }

# Gate 3 — live PAGE field exists when a footer is expected.
FLD=$(officecli query "$FILE" 'field[fieldType=page]' --json | jq '.data.results | length')
[ "$FLD" -ge 1 ] && echo "Gate 3 OK" || { echo "REJECT Gate 3: no live PAGE field"; exit 1; }
echo "Delivery Gate PASS"
```

## Field / cached-value spot-check

Fields carry cached values that may be stale or empty at write time — confirm existence by **structure, not text**.

- **Footer PAGE:** `get /footer[N] --depth 3` lists the begin / instrText / separate / cached / end run chain — ≥ 5 runs for one PAGE, ≥ 11 for composite "Page X of Y". A single run with text `"Page"` = field missing; re-add with `--prop field=page`.
- **TOC:** `get /toc[1] --depth 2` shows field structure. Page numbers may read `1 1 1 1` or `Update field to see…` until recalculated.
- **MERGEFIELD:** `query 'field[fieldType=mergefield]'` — one per slot, no literal `{{name}}` elsewhere.

## Honest limit

`validate` catches schema errors, not design errors — a document can pass it with wrong heading hierarchy, fake-Heading-1 sizes, placeholder tokens as body text, or an empty first-page footer on a coverless document. The contact-sheet visual pass (`screenshot --grid`) and the field-structure check are how you catch what validation can't.

## QA display notes (don't chase these)

- `view text` shows `"1."` for every numbered list item regardless of rendered number — actual output increments correctly.
- `view issues` flags "body paragraph missing first-line indent" on cover paragraphs, centered headings, list items, bibliography entries — first-line indent is only required for APA/academic body text; on block-style professional documents these are expected.
