# Known Issues & Pitfalls

## The cross-sheet `!` trap

Shells (bash history expansion, zsh splitting) and CLI arg parsing mangle `!` in `Sheet1!A1` into `\!`. A formula containing `\!` is silently broken — it renders as literal text and references nothing.

**Fix.** Use a batch heredoc with single-quoted delimiter (`<<'EOF'`), which disables all shell expansion:

```bash
cat <<'EOF' | officecli batch "$FILE"
[{"command":"set","path":"/Summary/B2","props":{"formula":"Revenue!B13"}}]
EOF
```

**Verify.** After writing, `officecli get` the cell; `formula=` must show a plain `!` with no backslash.

## CLI bug backlog

CLI constraints and gaps to work around — not defects in the output file.

- **Chart series are immutable after create** — to add/change a series: `remove` + `add` with the full series list. (Position is mutable: `set chart[N] --prop anchor=` / `x/y/width/height`.) `remove chart[N]` shifts subsequent indices down; re-add appends at end.
- **Cross-sheet formula batches run fine through a resident** — a prior "deadlocks even at 3-5 ops" caution no longer reproduces. Pure value-set batches stay reliable at 50-80+ ops too. If you ever hit a hang, fall back to a non-resident one-big-batch or individual `set`. **Multiple resident processes on the same file/machine can still contend** — expect non-deterministic hangs if another agent/session holds a resident on the same file.
- **Conditional formatting naming asymmetry** — the element name for `--type` is `conditionalformatting`; the path suffix is `/cf[N]`. Use `officecli help xlsx conditionalformatting` for schema, `/cf[N]` for paths.
- **Sheet `position` prop on add** — help says Add processes `position`, but the prop is often ignored. Reorder with `officecli move --index` / `--after` / `--before` after creating the sheet.
- **`remove /sheet[N]` cascade guard** — rejects sheet remove/rename when the sheet is referenced by validation / conditional format / sparkline / hyperlink / named range on another sheet. Remove those dependent elements first, then remove the sheet.
- **Batch JSON rejects cell `color` alias** — inside batch `props`, `"color": "FF0000"` errors `ambiguous in cell context — use 'font.color' (text) or 'fill' (bg)`. The CLI at shell level accepts `--prop color=...` / `--prop size=14` as aliases on non-cell elements, but inside batch JSON on a cell always write the full dotted name: `"font.color"`, `"font.size"`, `"font.name"`.

## Renderer caveats (cross-viewer color fidelity)

`officecli view html` is the right tool for structural QA (overflow, truncation, placeholder leakage, layout) — Read the returned HTML path. Some chart rendering details vary across the viewer the end user opens the file in. Observed divergences:

- **Pie / doughnut fill colors may collapse to a single theme tint** in some viewers (slices look "all white" or "all one color"). The file may be fine in the user's target viewer.
- **Line chart / column chart series colors may drift** from the workbook theme in some viewers.
- **Form-control checkboxes may render as double-boxed** in some viewers.

Before calling a color or chart "broken", open the file in the user's actual target viewer. If it looks correct there, the problem is viewer rendering, not data — do not chase it. The CLI's structural checks (`###`, truncation, placeholder text, layout) remain authoritative.

## Escape layers (shell quoting is above; these are the extras)

`$` is the shell layer (single-quote it, above). `\n` / `\t` in a prop value ARE interpreted by the CLI into a real newline / tab. Two more layers:

- **JSON level (batch).** Standard JSON escapes — `"\n"`, `"\t"`, `"\""`. A real backslash in the final string is `"\\\\"`.
- **Excel level.** `\n` in a cell is a real line break — pair with `--prop wrapText=true` so Excel shows the wrap. Works in a shell-quoted prop directly (`--prop value='a\nb'`); `"\n"` inside batch JSON gives the same. When in doubt, `officecli get` the cell and compare character-for-character.

## Other common pitfalls

| Pitfall | Fix |
|---|---|
| `--name "foo"` | All attrs go through `--prop`: `--prop name="foo"` |
| Guessing a prop name | `officecli help xlsx <element>` — don't improvise |
| `--prop color=...` on a cell | Ambiguous — use `font.color` (text) or `fill` (bg). Also applies inside batch JSON: always use full dotted names, never shell aliases |
| `#FF0000` hex colors | Drop the `#`: `FF0000` |
| `--index` vs `[N]` | `--index` is 0-based (array); `[N]` paths are 1-based (XPath) |
| Unquoted `[N]` in zsh/bash | Quote every path: `"/Sheet1/row[1]"` |
| Sheet name with spaces | Quote full path: `"/My Sheet/A1"` |
| Year showing as `2,026` | `--prop type=string` or `numFmt="@"` |
| Modifying a file open in Excel | Close it in Excel first |
| `swap` not reordering sheets | `swap` is for rows/cells. Use `move --after` / `--before` / `--index` for sheets |
| Cached values missing after write | New formulas get cached values when a human opens the file; `validate` accepts them either way |
