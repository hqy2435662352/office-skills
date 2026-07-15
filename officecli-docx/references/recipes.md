# Report-Level Recipes

Patterns that come up on every long-form report. Each has been executed and `validate`-passed.

## (a) Rich cover page — hit the ≥ 60% filled floor

Stack a confidentiality banner, title, subtitle, client/project/date block, and a key-themes strip, then force the next section onto a new page:

```bash
officecli add "$FILE" /body --type paragraph --prop text="CONFIDENTIAL — CLIENT USE ONLY" --prop align=center --prop size=9pt --prop color=C00000 --prop spaceAfter=24pt
officecli add "$FILE" /body --type paragraph --prop text="Strategic Growth Review" --prop style=Title --prop size=32pt --prop bold=true --prop align=center --prop font=Cambria --prop spaceAfter=8pt
officecli add "$FILE" /body --type paragraph --prop text="FY26 Outlook and Scenario Planning" --prop italic=true --prop size=16pt --prop align=center --prop spaceAfter=36pt
officecli add "$FILE" /body --type paragraph --prop text='Prepared for: Acme Corp. Leadership Team' --prop align=center --prop size=11pt
officecli add "$FILE" /body --type paragraph --prop text='Engagement: 2026-04 — 2026-06' --prop align=center --prop size=11pt --prop spaceAfter=36pt
officecli add "$FILE" /body --type paragraph --prop text="Key themes: 1) margin resilience, 2) EMEA expansion, 3) capital allocation." --prop align=center --prop italic=true --prop size=10pt
officecli add "$FILE" /body --type pagebreak
officecli set "$FILE" "/body/p[last()]" --prop pageBreakBefore=true
```

## (b) Page X of Y footer — composite PAGE + NUMPAGES

Add the footer paragraph, then three child ops build `Page <X> of <Y>` live. The official `help docx footer` recipe.

```bash
officecli add "$FILE" / --type footer --prop type=default --prop text="Page " --prop align=center --prop size=9pt
officecli add "$FILE" "/footer[1]/p[1]" --type field --prop fieldType=page
officecli add "$FILE" "/footer[1]/p[1]" --type run --prop text=" of "
officecli add "$FILE" "/footer[1]/p[1]" --type field --prop fieldType=numpages
officecli get "$FILE" "/footer[1]/p[1]" --depth 1 | grep -o fldChar | wc -l   # expect ≥ 4; use grep -o ... | wc -l, NOT grep -c (single-line XML returns 1)
```

## (c) Header row with fill and white bold text

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

## (d) Financial table — right-align numbers, bold totals, bottom border on total row

```bash
for row in 2 3 4 5; do for col in 2 3 4; do
  officecli set "$FILE" "/body/tbl[1]/tr[$row]/tc[$col]/p[1]" --prop align=right
done; done
for col in 1 2 3 4; do
  officecli set "$FILE" "/body/tbl[1]/tr[5]/tc[$col]/p[1]/r[1]" --prop bold=true
  officecli set "$FILE" "/body/tbl[1]/tr[4]/tc[$col]/p[1]" --prop pbdr.bottom="single;6;000000;0"
done
```

## (e) Cell with multiple bullets (SWOT / risk matrix)

`c1="a\nb"` gives a `<w:br/>` line break within **one** paragraph — fine for plain multi-line text, but bullets need separate paragraphs. Seed the first via `set c1=`, then `add paragraph` (with `listStyle=bullet`) under the cell per subsequent bullet:

```bash
officecli set "$FILE" "/body/tbl[1]/tr[1]" --prop c1="Installed base of 18k enterprise seats"
officecli add "$FILE" "/body/tbl[1]/tr[1]/tc[1]" --type paragraph --prop text="Margin structure above peer median" --prop listStyle=bullet
officecli set "$FILE" "/body/tbl[1]/tr[1]/tc[1]/p[1]" --prop listStyle=bullet
```

If the seeded line lands at the bottom, re-order: `officecli move "$FILE" "/body/tbl[1]/tr[1]/tc[1]/p[N]" --index 0`.

## (f) Static TOC fallback (cross-viewer reliability)

When delivering to viewers that don't auto-recalculate, the live TOC field renders the literal `Update field to see table of contents`. No CLI-only pipeline can pre-populate a TOC field the way Word does on save. Workaround: remove the TOC field, keep a visible heading, hand-write one dot-leader line per heading.

```bash
officecli query "$FILE" 'p:contains("Update field to see")'        # note the /body/p[N] paths, then:
officecli remove "$FILE" "/body/p[N]"                              # repeat per hit
officecli add "$FILE" /body --type paragraph --prop text="Contents" --prop style=TOCHeading --prop size=14pt --prop bold=true --index <pos>
officecli add "$FILE" /body --type paragraph --prop text="1. Executive Summary ......................................... 3" --prop size=11pt --index <pos+1>
# … one per heading. Page numbers manual; eyeball positions via view html. Live --type toc remains correct for recipients who recalculate.
```
