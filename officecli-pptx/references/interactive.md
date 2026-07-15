# Interactive Elements

## Animations

Use per the Animation floors in `references/design.md` (purposeful, degrades gracefully, verify live). Preset names + duration syntax: `help pptx animation`.

```bash
officecli set "$FILE" "/slide[2]/shape[@name=HeroCard]" --prop animation=fade-entrance-400
officecli set "$FILE" "/slide[2]/shape[@name=HeroCard]" --prop animation=none    # clear all
```

## Hyperlinks, tooltips, slide-jump

- `--prop link=slide[N]` — in-deck jump (1-based; target slide must exist).
- `link=nextslide` / `firstslide` / `lastslide` / `previousslide` / `endshow` — named navigation.
- `link=https://...` — external URL.
- `--prop tooltip="..."` — hover text.

## Tables

`--type table --prop rows=N --prop cols=M`. Row-level `set` supports `height` and `c1/c2/c3` (seed cell text). Header-row styling is table-level (`firstRow=true` / `headerFill=`), not a row prop. Cell formatting lives on the cell paragraph / run. **Populate rows BEFORE setting table-level font** — font cascade gets reset by row ops.

## Placeholders

`"/slide[N]/placeholder[title]"` / `placeholder[body]`. Available only when the slide uses a layout with placeholders (not `layout=blank`).

## Groups

Address children via `"/slide[N]/group[@name=G]/shape[1]"`. Survives reordering better than positional indexes.

## Zoom slide

`--type zoom --prop target=N` (one link per target; alias `slide`). Emit N separate zoom shapes for a multi-target nav hub. Zoom is a runtime feature — `view html` shows the static geometry; the zoom interaction runs only in a live presentation viewer.

## Slide comments

Reviewer annotations anchored at `/slide[N]/comment[M]`. Full lifecycle (`add / set / get / query / remove`). Props: `text`, `author`, `initials` (auto-derived), `date` (ISO 8601, defaults to UtcNow), `x` / `y` (length anchor).

```bash
officecli add "$FILE" "/slide[2]" --type comment --prop author="Alice" --prop text="Tighten this bullet" --prop x=20cm --prop y=3cm
officecli query "$FILE" 'comment' --json | jq '.data.results | length'   # count all review comments
officecli remove "$FILE" "/slide[2]/comment[1]"                           # resolve after addressing
```
