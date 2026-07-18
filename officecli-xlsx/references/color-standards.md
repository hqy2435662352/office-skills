# Financial Model Standards

Scope: budgets, forecasts, 3-statement models, valuation, any `$`-heavy analytical workbook. A customer-support tracker or onboarding template does not need this section.

## Color coding — industry standard

Five core colors used as a language, not decoration. A reviewer should tell what a cell IS by color alone — before reading the formula.

| Color | Role | Example |
|---|---|---|
| Blue text `0000FF` | Hardcoded inputs, scenario variables | `font.color=0000FF` |
| Black text `000000` | ALL formulas and calculations | default |
| Green text `008000` | Cross-sheet links inside this workbook | `font.color=008000` |
| Red text `FF0000` | Links to external files / workbooks | `font.color=FF0000` |
| Yellow fill `FFFF00` | Key assumptions needing review | `fill=FFFF00` |

A reviewer should tell what a cell IS just by its color — before reading the formula. This is a communication contract, not a cosmetic preference.

## Number formatting — standards, not preferences

- **Years** are text, not numbers. Format `2026` not `2,026` — use `numFmt="@"` or set `type=string`.
- **Currency** carries its unit in the header (`Revenue ($mm)`), not in every cell.
- **Zeros display as `-`**, not `0`. Use `$#,##0;($#,##0);"-"`.
- **Percentages** default to one decimal: `0.0%`.
- **Negatives use parentheses**: `(1,234)` not `-1,234`.
- **Valuation multiples** use `0.0x` format (EV/EBITDA, P/E, etc.).

## Assumptions documentation

**Assumptions live in cells, not inside formulas.** `=B5*(1+$B$6)` is correct; `=B5*1.05` is a bug. Document each blue hardcoded input with an adjacent source note in the next cell or a cell comment:

```
Source: Company 10-K, FY2024, Page 45, Revenue Note
Source: Bloomberg, 2026-05-02, AAPL US Equity
Source: Management guidance, Q2 2026 earnings call
```

Any hardcoded number without a source is an undocumented assumption — a reviewer cannot audit it.
