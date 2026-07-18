# Common Pitfalls

Sanity-check cheatsheet — what breaks on the first try. Design + shell traps.

| Pitfall | Correct approach |
|---|---|
| Unquoted `[N]` in zsh/bash | Always quote paths: `"/slide[1]"`. zsh globs unquoted `[1]` → `no matches found` — #1 first-use stumble |
| `--name "foo"` | All attributes go through `--prop`: `--prop name="foo"` |
| `/shape[myname]` (bare name in brackets) | Use `@name=` selector: `/shape[@name=myname]` or `/shape[@id=10007]` |
| Paths 1-based vs `--index` 0-based | `/slide[1]` = first slide; `--index 0` = first position |
| `$` in `--prop text=` | Single-quote: `--prop text='$15M'`. Double-quoted `"$15M"` gets shell-expanded to `M` |
| `\n` / `\t` in `--prop text=` | Interpreted by the CLI: `\n` = paragraph break, `\t` = tab. Double `\\n` for a literal |
| Batch heredoc `$` in values | Unquoted `<<EOF` expands `$SLIDE` but also eats currency `$`. Escape as `\$` — `"text":"\$1.42"` — which still lets `$SLIDE` expand |
| `query --json` output path | Results wrap in `.data.results[]` — `jq -r '.data.results[0].format.id'`, NOT `.[0].id` |
| Font cascade reset on table rows | Populate rows BEFORE setting table-level font (row ops reset the cascade) |
