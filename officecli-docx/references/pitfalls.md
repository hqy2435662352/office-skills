# Known Issues & Pitfalls

When something "looks broken", attribute it before chasing: **[AGENT-ERROR]** the document is wrong (fix it) · **[RENDERER-BUG]** the document is correct, a viewer renders it differently (don't chase) · **[SKILL gap]** the skill didn't teach the rule (file an issue).

## Renderer quirks (cross-viewer, [RENDERER-BUG] — don't chase)

Before calling a color/field/chart broken, open the file in the user's target viewer; if it looks correct there it's a viewer quirk.

- **PAGE field may render literal "Page"** (no number) until recalculated — judge by `fldChar` presence, not the digit.
- **TOC cached page numbers may read "1 1 1 1"** until F9.
- **Pie / doughnut fill may collapse to one color** in some viewers (column/bar render fine).
- **Form-control checkboxes may render double-boxed**; **OMML equation baselines** may shift across viewers (XML identical).

## Common pitfalls

| Pitfall | Correct approach |
|---|---|
| `--index` vs `[N]` | `--index` is 0-based; `[N]` paths are 1-based |
| Multiple `add --index N` with the same N | Each insert shifts later content down; reusing N puts later items BEFORE earlier ones — insert in reverse order, or `move --after/--before` anchored on `paraId` |
| Unquoted `[N]` in zsh/bash | Quote every path: `"/body/p[1]"` |
| `[last]` as predicate | Must be `[last()]` with parens |
| Raw twips in spacing | Use unit-qualified values: `12pt`, `0.5cm`, `1.5x` |
| Empty paragraphs for spacing | Use `spaceBefore` / `spaceAfter` |
| Row-level `set` for cell formatting | Row `set` only supports `height`, `header`, `c1..cN` text; format goes on the cell paragraph / run |
| `listStyle` on a run | It's a paragraph property |
| Indent via leading spaces | `indent=720` / `firstLineIndent=360` / `hangingIndent=720` (dotted `ind.left` / `ind.firstLine` also work) |
| Cover page-number suppression via `set differentFirstPage=true` | UNSUPPORTED — add a first-type footer: `--type footer --prop type=first --prop text=""` |
| `--type pagebreak` OR `pageBreakBefore` alone not breaking | Apply BOTH (see references/sections.md) |
| Multiple bullet paragraphs in one cell | `c1="a\nb"` makes a `<w:br/>` line break (one paragraph); for separate bullet paragraphs use recipes.md recipe (e) |
| `raw-set` when dotted-attr would work | Prefer L2 dotted-attr over L3 raw-set |
| Next paragraph inherits the previous Heading style | Set explicit `--prop style=Normal` on the following paragraph |
| Modifying a file open in Word | Close it in Word first |
| Echo into batch breaks on `$`/`'` | Heredoc with single-quoted delimiter: `cat <<'EOF' \| officecli batch …` |

## Raw-set XML appendix (L3 patterns)

`raw-set` injects literal OOXML — no schema protection. Element order in `<w:pPr>`: `pStyle`, `numPr`, `spacing`, `ind`, `jc`, `rPr` (last). Smart quotes as entities (`&#x2018;`/`&#x2019;`/`&#x201C;`/`&#x201D;`). Add `xml:space="preserve"` to any `<w:t>` with leading/trailing spaces. RSIDs are 8-digit hex. Use "Claude" as the author for tracked changes/comments unless the user names another.

### Tracked-change insertion / deletion

Prefer the high-level `--prop revision.type=ins|del` on a run; raw-set only for what the typed path can't express (rejecting/restoring another author's change, below). Replace the whole `<w:r>…</w:r>`, never inject tags inside a run; copy the original `<w:rPr>` into both to preserve formatting. Inside `<w:del>` use `<w:delText>` (and `<w:delInstrText>` for instructions):

```xml
<w:r><w:t>The term is </w:t></w:r>
<w:del w:id="1" w:author="Claude" w:date="2026-01-01T00:00:00Z"><w:r><w:delText>30</w:delText></w:r></w:del>
<w:ins w:id="2" w:author="Claude" w:date="2026-01-01T00:00:00Z"><w:r><w:t>60</w:t></w:r></w:ins>
<w:r><w:t> days.</w:t></w:r>
```

When deleting ALL content of a paragraph/list item, also mark the paragraph mark deleted (`<w:del/>` inside `<w:pPr><w:rPr>`) — otherwise accepting changes leaves an empty paragraph. To **reject another author's insertion**, nest your `<w:del>` inside their `<w:ins>`; to **restore their deletion**, add a `<w:ins>` after it (don't modify theirs).

### Internal hyperlink to a bookmark

Prefer the high-level `--prop anchor=` path; raw-set only for custom run styling the command can't express:

```xml
<w:hyperlink w:anchor="chapter1"><w:r><w:rPr><w:rStyle w:val="Hyperlink"/></w:rPr><w:t>See Chapter 1</w:t></w:r></w:hyperlink>
```

### Composite field in one run

Two fields the single-command path can't compose — the `fldChar begin / instrText / separate / value / end` chain:

```xml
<w:r><w:fldChar w:fldCharType="begin"/></w:r>
<w:r><w:instrText xml:space="preserve"> PAGE </w:instrText></w:r>
<w:r><w:fldChar w:fldCharType="separate"/></w:r>
<w:r><w:t>1</w:t></w:r>
<w:r><w:fldChar w:fldCharType="end"/></w:r>
```

### Comment markers

Comment markers are siblings of `<w:r>`, NEVER inside one (reply threading and resolved-state are high-level — `--prop parentId=`/`done=`, see references/advanced.md):

```xml
<w:commentRangeStart w:id="0"/><w:r><w:t>annotated text</w:t></w:r><w:commentRangeEnd w:id="0"/>
<w:r><w:rPr><w:rStyle w:val="CommentReference"/></w:rPr><w:commentReference w:id="0"/></w:r>
```

### Force field recalc

Force field recalc on open with `officecli set "$FILE" /settings --prop updateFields=true` (writes `<w:updateFields w:val="true"/>`; covers the layout-dependent fields PAGE / PAGEREF / NUMPAGES / TOC page numbers — no raw-set needed). For SEQ numbering, prefer `set / --prop recalcFields=seq`, which writes correct cached values now without waiting on Word.

### Help pointer

When in doubt: `officecli help docx`, `officecli help docx <element>`, `officecli help docx <verb> <element>`, `--json` for agents. Help is the authoritative schema; this skill is the decision guide.
