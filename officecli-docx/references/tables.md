# Tables

Tables are `/body/tbl[N]` with rows `tr[N]` and cells `tc[N]`. Add with row/column counts, then fill.

```bash
officecli add "$FILE" /body --type table --prop rows=4 --prop cols=3 --prop width=100%
officecli set "$FILE" "/body/tbl[1]/tr[1]" --prop header=true --prop c1=Quarter --prop c2="Revenue" --prop c3="Growth"
officecli set "$FILE" "/body/tbl[1]/tr[1]/tc[1]/p[1]/r[1]" --prop bold=true
```

Row-level `set` supports `height`, `header`, and `c1 / c2 / … / cN` text shortcuts (`cN` generalises to any column count). Cell formatting (bold, fill, color) goes on the cell's paragraph / run — **not** row-level. For per-cell borders, set cell-level `border.*` on the `tc` (`--prop border.bottom="single;6;000000;0"`), or paragraph-level `pbdr.*` on the inner paragraph.

## Horizontal rule

**Horizontal rule = a paragraph bottom border, never a 1-row table.** A table-as-divider renders as an empty min-height box (worst in headers/footers). Use `pbdr.bottom` (`STYLE;SIZE;COLOR`) on the paragraph instead:

```bash
officecli set "$FILE" "/body/p[3]" --prop pbdr.bottom="single;6;2E75B6"
```

## Header row with fill and white bold text

Order matters — populate header cell text FIRST (runs don't exist in empty cells; a `set …/tc[N]/p[1]/r[1]` on an empty cell errors "No r found"), THEN cell fill, THEN run formatting:

```bash
officecli add "$FILE" /body --type table --prop rows=5 --prop cols=4 --prop width=100%
officecli set "$FILE" "/body/tbl[1]/tr[1]" --prop header=true --prop c1=Quarter --prop c2=Revenue --prop c3=Growth --prop c4=Status
for col in 1 2 3 4; do
  officecli set "$FILE" "/body/tbl[1]/tr[1]/tc[$col]" --prop fill=1F4E79
  officecli set "$FILE" "/body/tbl[1]/tr[1]/tc[$col]/p[1]/r[1]" --prop bold=true --prop color=FFFFFF
done
for row in 3 5; do for col in 1 2 3 4; do
  officecli set "$FILE" "/body/tbl[1]/tr[$row]/tc[$col]" --prop fill=D9E2F3      # zebra stripe
done; done
```

## Financial table

Right-align numbers, bold totals, bottom border on total row:

```bash
for row in 2 3 4 5; do for col in 2 3 4; do
  officecli set "$FILE" "/body/tbl[1]/tr[$row]/tc[$col]/p[1]" --prop align=right
done; done
for col in 1 2 3 4; do
  officecli set "$FILE" "/body/tbl[1]/tr[5]/tc[$col]/p[1]/r[1]" --prop bold=true
  officecli set "$FILE" "/body/tbl[1]/tr[4]/tc[$col]/p[1]" --prop pbdr.bottom="single;6;000000;0"
done
```

## Cell with multiple bullets (SWOT / risk matrix)

`c1="a\nb"` gives a `<w:br/>` line break within **one** paragraph — fine for plain multi-line text, but bullets need separate paragraphs. Seed the first via `set c1=`, then `add paragraph` (with `listStyle=bullet`) under the cell per subsequent bullet:

```bash
officecli set "$FILE" "/body/tbl[1]/tr[1]" --prop c1="Installed base of 18k enterprise seats"
officecli add "$FILE" "/body/tbl[1]/tr[1]/tc[1]" --type paragraph --prop text="Margin structure above peer median" --prop listStyle=bullet
officecli set "$FILE" "/body/tbl[1]/tr[1]/tc[1]/p[1]" --prop listStyle=bullet
```

If the seeded line lands at the bottom, re-order: `officecli move "$FILE" "/body/tbl[1]/tr[1]/tc[1]/p[N]" --index 0`.
