# Lists & Tab Stops

## Lists (bullets, numbered, multi-level)

For single-level bullets/numbers, set `listStyle` on the paragraph (`listStyle` is a paragraph prop, NOT a run prop — common mistake):

```bash
officecli add "$FILE" /body --type paragraph --prop text="First item" --prop listStyle=bullet
```

For multi-level (legal-style 1 / 1.1 / 1.1.1), add an `abstractNum`, then a `num`, then reference the `numId` per paragraph:

```bash
officecli add "$FILE" /numbering --type abstractnum --prop format=decimal     # → abstractNum id=0
officecli add "$FILE" /numbering --type num --prop abstractNumId=0             # → num id=1
officecli add "$FILE" /body --type paragraph --prop text="Section one" --prop numId=1 --prop ilvl=0
```

IDs are 0-based: the first `abstractNum` is id=0; the `num` references it via `abstractNumId=0` and is itself assigned id=1. A non-existent `abstractNumId` errors, so check ids after creating. Verify with `officecli query "$FILE" 'paragraph[numId>0]'`. See `help docx abstractnum` / `help docx num` for level and format options.

## Tab stops (signature lines, leader rows)

Tab stops are a first-class `tab` child of the paragraph; `pos` accepts `6in`/`6cm`/twips, `val` ∈ `left`/`center`/`right`, `leader` ∈ `none`/`dot`/`hyphen`/`underscore`. See `help docx tab`.

```bash
officecli add "$FILE" "/body/p[1]" --type tab --prop pos=6in --prop val=right --prop leader=dot
```

**Leader caveat.** `leader=dot` alone emits no dots — the leader renders only when a real `<w:tab/>` character sits in a run between the text and the tab stop. Put one there with `\t` in the text: define the stop (`add tab --prop pos=6in --prop val=right --prop leader=dot`), then `--prop text="Chapter 1\t12"` — the `\t` becomes the `<w:tab/>` and dots fill to the right-aligned page number. (Literal `text="Chapter 1 ......... 12"` also ships, but a real tab stop aligns cleanly.)
