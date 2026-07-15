# Charts

Pick chart type per the chart-choice decision table in `references/design.md`. Full prop list (chartType enum, `seriesN.*`, `data=`/`categories=`, axis options): `help pptx add chart`.

## Multi-series with brand colors

```bash
officecli add "$FILE" /slide[3] --type chart --prop chartType=column \
  --prop series1.name=Revenue --prop series1.values="42,45,48" --prop series1.color=1E2761 \
  --prop series2.name=Growth  --prop series2.values="2,7,7"    --prop series2.color=CADCFC \
  --prop categories="Q1,Q2,Q3" \
  --prop x=2cm --prop y=4cm --prop width=20cm --prop height=10cm
```

## Series after creation

Series can be added after chart creation with `add --type series`.

## Gotchas

1. Chart titles with `()`, `[]`, `TBD` ship as literal text — audit before delivery (Gate 2b).
2. Some viewers normalize chart colors to theme defaults — verify in the target viewer during Gate 3.
3. Single-quote chart titles that contain `$` to prevent shell expansion: `--prop title='FY26 Revenue ($M)'`.
