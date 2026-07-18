# chart-gen Integration Test Report

**Date**: 2026-07-10
**Tester**: Sisyphus
**Plan**: `.omo/plans/chart-gen-skill-design.md` §6 Step 4
**Result**: 8/8 PASSED

---

## Test Fixtures

| File | Description | Data |
|------|-------------|------|
| `test_fixtures/single_column.xlsx` | Single-series sales data | Sheet1: Month (A) + Revenue (B), 12 rows |
| `test_fixtures/multi_line.xlsx` | Multi-series time data | Sheet1: Week (A) + Product A/B/C (B-D), 10 rows |
| `test_fixtures/pie.xlsx` | Category/proportion data | Sheet1: Category (A) + Value (B), 5 rows |
| `test_fixtures/chinese_sheet.xlsx` | Chinese sheet name | Sheet "经营数据": 月份 (A) + 销售额 (B) + 利润 (C), 6 rows |
| `test_fixtures/blank_no_chart.xlsx` | No-chart negative test | Sheet1: X (A) + Y (B), 5 rows, no charts |
| `test_fixtures/sales.xlsx` | Pre-existing chart | Sheet1 with chart[1] (column), 2 series, 12 rows |

---

## Test Results

### Test 1: Single-series Column Chart
**Command**:
```bash
officecli add test_fixtures/single_column.xlsx /Sheet1 --type chart \
  --prop chartType=column --prop dataRange=Sheet1!A1:B13 \
  --prop title="Revenue by Quarter" --prop preset=corporate --prop anchor=D2:J18 --json
officecli set test_fixtures/single_column.xlsx /Sheet1/chart[1] --prop legend=bottom --json
python chart-gen/scripts/verify_output.py --output test_fixtures/single_column.xlsx --workdir 展平元数据输出/
```
**Result**: PASSED (exit 0)
- Chart created: `/Sheet1/chart[1]`, type=column
- valuesRef verified: `Sheet1!$B$2:$B$13`
- Legend override applied (bottom)

### Test 2: Multi-series Line Chart
**Command**:
```bash
officecli add test_fixtures/multi_line.xlsx /Sheet1 --type chart \
  --prop chartType=line --prop dataRange=Sheet1!A1:D11 \
  --prop title="Product Trends by Week" --prop preset=corporate --prop anchor=F2:L18 --json
python chart-gen/scripts/verify_output.py --output test_fixtures/multi_line.xlsx --workdir 展平元数据输出/
```
**Result**: PASSED (exit 0)
- Chart created: `/Sheet1/chart[1]`, type=line
- valuesRef verified: `Sheet1!$B$2:$B$11`

### Test 3: Pie Chart
**Command**:
```bash
officecli add test_fixtures/pie.xlsx /Sheet1 --type chart \
  --prop chartType=pie --prop dataRange=Sheet1!A1:B6 \
  --prop title="Sales by Category" --prop preset=corporate --prop anchor=D2:J18 --json
python chart-gen/scripts/verify_output.py --output test_fixtures/pie.xlsx --workdir 展平元数据输出/
```
**Result**: PASSED (exit 0)
- Chart created: `/Sheet1/chart[1]`, type=pie
- valuesRef verified: `Sheet1!$B$2:$B$6`

### Test 4: Illegal Jump Blocked by Gate
**Command**:
```bash
# No proposal in workdir
python chart-gen/scripts/layer_gate.py --target 2 --workdir 展平元数据输出/
```
**Result**: PASSED (exit 1)
- Error: "Missing prerequisite: no *_chart_proposal.yaml found"
- Four-segment error format correct (`[LAYER_GATE_ERROR]`)
- Workdir file listing included in error output

### Test 5: EXIT GATE Pass (verify_output after chart add)
**Command**: See Tests 1, 2, 3
**Result**: PASSED (exit 0 for all three chart types)
- All three verify_output runs after chart creation returned exit 0
- Chart existence, object readability, and series binding all confirmed

### Test 6: EXIT GATE Fail (no chart in file)
**Command**:
```bash
python chart-gen/scripts/verify_output.py --output test_fixtures/blank_no_chart.xlsx --workdir 展平元数据输出/
```
**Result**: PASSED (exit 1)
- Error: `[FATAL] No charts found in output file`
- JSON error report on stderr with code=FATAL_ERROR
- Corrective action included: "Re-run Step 3 chart generation"

### Test 7: User Rejection → Re-analyze
**Commands**:
```bash
# Phase A: proposal exists, confirmed: false
python chart-gen/scripts/layer_gate.py --target 3 --workdir 展平元数据输出/
# → exit 3

# Phase B: after user confirms, set confirmed: true
python chart-gen/scripts/layer_gate.py --target 3 --workdir 展平元数据输出/
# → exit 0
```
**Result**: PASSED
- Phase A: exit 3 (retryable) — "Proposal NOT confirmed ... 'confirmed' flag is False"
- Phase B: exit 0 (pass) — "Proposal confirmed ... Human Gate 1: CONFIRMED"

### Test 8: Chinese Sheet Name + Chinese Title
**Commands**:
```bash
officecli add test_fixtures/chinese_sheet.xlsx /经营数据 --type chart \
  --prop chartType=column --prop dataRange=经营数据!A1:C7 \
  --prop title=月度经营数据趋势 --prop preset=corporate --prop anchor=E2:K18 --json
python chart-gen/scripts/verify_output.py --output test_fixtures/chinese_sheet.xlsx --workdir 展平元数据输出/
```
**Result**: PASSED (exit 0)
- Chart created on Chinese sheet name "/经营数据/chart[1]"
- Chinese title "月度经营数据趋势" applied
- valuesRef with Chinese sheet name verified: `经营数据!$B$2:$B$7`
- Chinese sheet name in dataRange worked WITHOUT double quotes (via Python subprocess)

---

## Summary

| # | Test | Exit Code | Status |
|---|------|-----------|--------|
| 1 | Single-series column chart | 0 | ✅ PASSED |
| 2 | Multi-series line chart | 0 | ✅ PASSED |
| 3 | Pie chart | 0 | ✅ PASSED |
| 4 | Illegal jump blocked | 1 | ✅ PASSED |
| 5 | EXIT GATE pass | 0 | ✅ PASSED |
| 6 | EXIT GATE fail | 1 | ✅ PASSED |
| 7 | Rejection → re-analyze | 3→0 | ✅ PASSED |
| 8 | Chinese sheet + Chinese title | 0 | ✅ PASSED |

**Total**: 8/8 passed. No script bugs found during testing — `verify_output.py`'s `_unwrap_result()` fix (documented in `issues.md`) was pre-existing and validated by all chart tests. `layer_gate.py` correctly handles all gate states (missing proposal, unconfirmed, confirmed).

---

## Findings

### 1. Chinese sheet name in dataRange: no quoting needed via Python subprocess

`KNOWN_TRAPS.md` #4 states that Chinese sheet names in `--prop dataRange=` need double quotes. In practice, when calling officecli via Python `subprocess.run()`, UTF-8 Chinese characters pass through correctly without quoting. The quoting requirement only applies to raw PowerShell/CMD invocations where shell interpretation may corrupt multibyte characters.

This was verified in Test 8: `dataRange=经营数据!A1:C7` worked correctly, producing `valuesRef=经营数据!$B$2:$B$7`.

### 2. No script bugs found

Both `layer_gate.py` and `verify_output.py` performed correctly across all test scenarios:
- `layer_gate.py` correctly blocks Step 2 without proposal (exit 1), blocks Step 3 without confirmation (exit 3), and passes Step 3 when confirmed (exit 0)
- `verify_output.py` correctly detects missing charts (exit 1), validates chart existence + type + series binding (exit 0)
- The `_unwrap_result()` fix (merging `format.*` keys up) handles all three officecli response shapes correctly for column, line, and pie charts

### 3. No regressions

Pre-existing `sales.xlsx` chart[1] (column, 2 series) passes `verify_output.py` exit 0 after all testing. No files outside `test_fixtures/` and `展平元数据输出/` were modified.
