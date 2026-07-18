# Sections, Page Setup & Page Breaks

## Sections and page setup

Document root `/` carries page setup (`pageWidth`, `pageHeight`, margins, in twips). Multi-section documents (landscape insert, columns) add a `section` break — see `help docx section`. Both camelCase (`pageWidth`, canonical) and lowercase alias (`pagewidth`) are accepted; prefer camelCase.

```bash
officecli set "$FILE" / --prop pageWidth=12240 --prop pageHeight=15840 --prop marginTop=1440 --prop marginLeft=1440
# Newspaper-style multi-column flow (columnSpace in twips; 720 = 0.5in):
officecli set "$FILE" / --prop columns=2 --prop columnSpace=720
```

## Forcing page breaks — belt-and-suspenders

Two mechanisms exist; **neither alone is reliable across every viewer**. Depending on viewer and preceding content, `<w:pageBreakBefore/>` may be ignored OR `<w:br w:type="page"/>` rendered as a soft break — opposite failures. Apply BOTH on every H1 you want on a fresh page, the TOC heading, and the cover-closing paragraph:

```bash
officecli add "$FILE" /body --type pagebreak --index <N>          # 1. pagebreak element BEFORE the heading
officecli set "$FILE" "/body/p[<N+1>]" --prop pageBreakBefore=true # 2. on the heading itself
```

`--prop break=newPage` is a shorter alias for `pageBreakBefore=true` (accepts `newPage|page|nextPage|pageBreak`). Same XML, same belt-and-suspenders rule. Preview with `view html` and count pages.

## Rich cover page (≥ 60% filled floor)

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

## Template delivery — separating Template Notes from end-user content

HR / legal / vendor templates carry internal-only guidance ("replace `{{CompanyName}}`") that must NOT ship. Two working patterns:

- **Trailing "Template Notes" section** under a clear `Heading 1` ("Template Notes for HR Users") with all instructions below it; before distribution, `remove` from the heading downward (locate with `query 'paragraph[style=Heading1]:contains("Template Notes")'`).
- **Bookmark-bounded internal section** between `__template_notes_start` / `_end` bookmarks; at delivery `raw-set` removes everything between the anchors.

Delivery gate for templates: after removal, `query 'p:contains("Template Notes")'` AND `query 'p:contains("{{")'` both return empty. If a notes paragraph survives, a downstream employee reads internal language.
