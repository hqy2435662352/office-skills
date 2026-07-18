# Conditional Formatting & Data Validation

## Conditional formatting

Three common flavors, each with its own prop shape (consult `officecli help xlsx cf`):

- **Color scales**: cells shaded on a gradient by value — `type=colorscale` with `minColor` / `midColor` / `maxColor`.
- **Data bars**: in-cell bars showing magnitude — `type=databar`. Set explicit `min` / `max` for consistent scaling across a column; defaults are valid if you omit them.
- **Formula rules** (the `formulacf` element): highlight row when a condition is true — `type=formula` with `formula="$C2>1000"` and a fill/font.

Rule: apply CF sparingly. A workbook where every cell is colored tells the reader nothing.

## Data validation

Input cells in trackers and templates MUST carry data validation. It's cheap and it stops entire classes of downstream bugs. **Three list-source patterns** — pick based on where the allowed values live.

**(a) Inline list** — allowed values are short and fixed in the rule itself.

```bash
officecli add "$FILE" /Sheet1 --type validation \
  --prop sqref="C2:C100" --prop type=list \
  --prop formula1="Yes,No,Maybe" \
  --prop showError=true --prop errorTitle="Invalid" --prop error="Select from list"
```

**(b) Named range (preferred for cross-sheet lookups)** — allowed values live in another sheet and may grow. Define the named range first, then reference it. Use a batch heredoc because `ref` contains `!` and `$`:

```bash
cat <<'EOF' | officecli batch "$FILE"
[
  {"command":"add","parent":"/","type":"namedrange","props":{"name":"StatusList","ref":"Lookups!$A$2:$A$4"}},
  {"command":"add","parent":"/Sheet1","type":"validation","props":{"sqref":"B2:B100","type":"list","formula1":"=StatusList"}}
]
EOF
```

**(c) Direct cross-sheet range** — no named range, raw `Lookups!$A$2:$A$4` inside `formula1`. Also needs a batch heredoc to keep `!` and `$` intact:

```bash
cat <<'EOF' | officecli batch "$FILE"
[
  {"command":"add","parent":"/Sheet1","type":"validation","props":{"sqref":"C2:C100","type":"list","formula1":"Lookups!$A$2:$A$4"}}
]
EOF
```

If you write the cross-sheet variant as `--prop formula1=...` on the shell, the `!` gets shell-mangled into `\!` and the dropdown will silently fall back to no list. Verify with `officecli get "$FILE" /Sheet1/validation[N]` — `formula1=` must show a plain `!`, no backslash.

Other common `type` values: `decimal`, `whole`, `date`, `textLength`, `custom`. See `officecli help xlsx validation`.

## Named ranges

Prefer named ranges over `$B$6` in formulas. They self-document (`GrowthRate` beats `$B$6`) and they let you move the assumption cell without breaking formulas. Because `ref` values contain both `!` and `$`, add them through a batch heredoc:

```bash
cat <<'EOF' | officecli batch "$FILE"
[
  {"command":"add","parent":"/","type":"namedrange","props":{"name":"GrowthRate","ref":"Sheet1!$B$6"}}
]
EOF
```

See `officecli help xlsx namedrange` for the full schema.

## Other elements (one-liners)

- **Tables** (ListObjects) — `add --type table` with a range; gives auto-filter + structured refs. `officecli help xlsx table`.
- **Comments** — `add --type comment`; use for documenting hardcoded assumptions. `officecli help xlsx comment`.
- **Sheet reordering** — `officecli move`, not `swap`. `swap` only works on row/cell paths.

## Print layout

Any sheet the user may print or send as a board pack needs page setup. Default portrait + no fit-to-page splits wide tables and charts mid-way. Pick the fit mode by sheet shape:

```bash
# Summary / chart / dashboard sheet (small, ≤ ~40 rows): fit to a single page.
officecli set "$FILE" "/Summary" --prop orientation=landscape --prop fitToPage=true
# Tall data table (dozens+ rows): fit WIDTH only, let height paginate naturally.
# fitToPage=true here crushes every row onto one page → unreadable (### dates, 5px rows).
officecli set "$FILE" "/Data" --prop orientation=landscape --prop fitToPage=1x0
```

`fitToPage=true` == `1x1` == fit both axes to one page — correct only when the sheet is already short. `1x0` = fit 1 page wide, unlimited pages tall. Trigger: sheet holds a chart, or > 8 columns, or the user's ask mentions print / board / investor.
