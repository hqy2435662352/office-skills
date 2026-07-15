# Fields & TOC

## Fields (PAGE / NUMPAGES / DATE / MERGEFIELD / REF)

Fields are live values computed at render time. `fieldType` picks the field; `name` supplies the target (merge name or `ref` bookmark); `format` / `instr` add switches.

| Field | Use | Example |
|---|---|---|
| `page` | current page number | `--prop field=page` on footer, or `--prop fieldType=page` inline |
| `numpages` | total pages | `--prop field=numpages` / `--prop fieldType=numpages` |
| `date` | today | `--prop fieldType=date --prop format='yyyy-MM-dd'` |
| `mergefield` | template merge token | `--prop fieldType=mergefield --prop name=CustomerName` |
| `ref` | cross-reference to a bookmark | `--prop fieldType=ref --prop name=bookmarkName` |

Full `fieldType` enum (30+ values incl. `pageref`, `seq`, `styleref`, `docproperty`, `createdate`, …) is in `help docx field`. **There is NO `fieldInstr` fieldType** — use the `instruction` prop for raw field instruction text when typed shortcuts fall short. Picture switches (`MERGEFIELD Amount \# "#,##0.00"`, `DATE \@ "yyyy年MM月"`) go via `--prop instruction='…'` (mergefield's `format` prop is ignored with a warning — use `instruction`).

```bash
officecli add "$FILE" "/body/p[3]" --type field --prop fieldType=mergefield --prop name=customer_name
# Renders «customer_name» — visible placeholder, replaced in Word at mail-merge time.
```

**MERGEFIELD templates: never render placeholder literals.** A `{{customer_name}}` or `$NAME$` shown as body text is a failed template the recipient sees — insert a real MERGEFIELD (above), or confine literal tokens to an obvious instruction paragraph. Confirm with `query 'field[fieldType=mergefield]'`.

### SEQ / PAGEREF / TOC field values

officecli doesn't store rendered field values at write time. Recompute by what each path needs:

- **SEQ numbering** (`Figure 1/2/3`): `officecli set "$FILE" / --prop recalcFields=seq` counts SEQ fields in body document order and writes the cached values (`evaluated` flips true; switches/formats in `help docx document`). Heading-relative `\s` and SEQ in headers/footers defer to Word.
- **PAGE / PAGEREF / NUMPAGES / TOC page numbers** need pagination, which officecli has no engine for — `officecli set "$FILE" /settings --prop updateFields=true` defers them to Word on open.

Use both on a multi-figure document. Academic papers: see the `officecli-academic-paper` skill.

## Table of Contents

For any document with 3+ headings:

```bash
officecli add "$FILE" /body --type toc --prop levels="1-3" --prop title="Table of Contents" --prop hyperlinks=true --index 0
```

Page numbers render automatically (`--prop pageNumbers=true` toggles them explicitly). Address the TOC directly: `/toc[1]` or `/tableofcontents` resolve to the first TOC field for `get`/`set`/`remove` without hand-walking XPath.

### TOC delivery step (mandatory before handoff)

The live TOC field is a placeholder until recalculated. Some viewers populate it on first open; others show the literal `Update field to see table of contents` until the reader recalculates. Pick by recipient:

- **Will recalculate (or press F9):** run `officecli set "$FILE" /settings --prop updateFields=true` so Word recomputes the TOC (and all fields) on open, and/or add a visible "Press F9 to refresh the TOC and page numbers" instruction. Done.
- **Cannot / will not recalculate:** use the **static TOC fallback** — see references/recipes.md, recipe (f).

Ship-check: `officecli query "$FILE" 'p:contains("Update field to see")'` must return empty whenever the reader won't recalculate. A match means switch to recipe (f).
