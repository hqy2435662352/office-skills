# CSV / Bulk Import

## Native import command (preferred)

Fastest path; loads a CSV into a sheet in one call. `--header` sets AutoFilter + freeze pane on row 1. Widths and `numFmt` still need a follow-up pass.

```bash
officecli import "$FILE" /Sheet1 --file data.csv --header
officecli import "$FILE" /Sheet1 --file data.tsv --format tsv --header
officecli import "$FILE" /Sheet1 --stdin --start-cell B2 < data.csv
```

## Python + batch fallback

Use when you need custom type coercion, formula injection, or the CSV lives inside another data pipeline. Recipe for 600-6000+ cells:

```python
# gen_batch.py — produces batch chunks of 80 value-set ops each
import csv, json
ops = []
with open("data.csv") as f:
    reader = csv.reader(f)
    for r, row in enumerate(reader, start=1):
        for c, val in enumerate(row):
            col = chr(ord('A') + c)
            ops.append({"command":"set","path":f"/Data/{col}{r}",
                        "props":{"value": val}})
for i in range(0, len(ops), 80):
    print(json.dumps(ops[i:i+80]))
```

```bash
python gen_batch.py | while IFS= read -r chunk; do
  printf '%s\n' "$chunk" | officecli batch "$FILE"
done
```

Outcome: 648-row retail CSV (6490 cells) loads in ~30s, zero failures. Tune: start at 80 ops/chunk, drop to 40 if any chunk fails. Numeric type inference and formulas come later via targeted `set` — batch in this recipe is pure value injection.
