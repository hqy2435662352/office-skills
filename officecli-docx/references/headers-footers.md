# Headers & Footers

## Basic page numbering

Single-command pattern — the CLI injects `<w:fldChar>`, so you never compose the field by hand:

```bash
# Empty first-page footer — auto-enables differentFirstPage so the cover has no page number
officecli add "$FILE" / --type footer --prop type=first --prop text=""
# Default footer with live page number
officecli add "$FILE" / --type footer --prop type=default --prop align=center --prop size=9pt --prop text="Page " --prop field=page
```

When both exist, the default footer is `/footer[2]`; alone it is `/footer[1]`. **Verify**: `get --depth 3` must show `fldChar` children, not just a run with literal `"Page"` (`view outline` prints "Footer: Page" for both live fields AND static text — don't rely on it). Do NOT `set --prop differentFirstPage=true` — that prop is unsupported (rejected with exit 2, not silently); adding a first-type footer flips the bit.

## Page X of Y footer

Composite PAGE + NUMPAGES. The official `help docx footer` recipe:

```bash
officecli add "$FILE" / --type footer --prop type=default --prop text="Page " --prop align=center --prop size=9pt
officecli add "$FILE" "/footer[1]/p[1]" --type field --prop fieldType=page
officecli add "$FILE" "/footer[1]/p[1]" --type run --prop text=" of "
officecli add "$FILE" "/footer[1]/p[1]" --type field --prop fieldType=numpages
officecli get "$FILE" "/footer[1]/p[1]" --depth 1 | grep -o fldChar | wc -l   # expect ≥ 4; use grep -o ... | wc -l, NOT grep -c (single-line XML returns 1)
```
