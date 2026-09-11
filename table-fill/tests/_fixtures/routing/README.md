# Routing 回归夹具（axis-neutral grid）— Fixture R 与 Fixture C

Ticket 02（axis-neutral grid routing）的两个 **data-neutral canonical 回归夹具**。
全部后续 Routing / FillSpec / Gate 修改至少跑这两个（spec Testing Decisions）。

- 生成器: `generate_fixtures.py`（openpyxl + 共享字符串机械归一化，见其 docstring）。
  运行 `python tests/_fixtures/routing/generate_fixtures.py` 可再生两份 checked-in
  工作簿。**测试运行时只复制 checked-in 副本，绝不 import 生成器**。

## Data-neutral 约束（违反即失败）

- 无 MXP / ATLAS / TCL / Egypt / Algeria 等真实业务文件名与客户名；
- 无真实型号 / 真实报价 / 真实 Z 码 / 中文业务词；
- 产品占位词 `12K / 18K / 24K`（spec 认可的通用 token）与通用标签
  （Type / Model / Capacity / Qty / Unit Price / Panel looking /
  Parameter / EER / Compressor / Refrigerant…）均无真实业务含义。

## Fixture R — Grouped Row Grid（`fixture_r_grouped_row_grid.xlsx`）

报价形态（与 Case 010 ATLAS 结构同构，data-neutral）：

| 行 | 内容 |
|---|---|
| 1 | 标题 `Product Quotation List`（A1:F1 横向合并） |
| 2 | 六列表头 `Type \| Model \| Capacity \| Qty \| Unit Price \| Panel looking` |
| 3–5 | Group 1 `Wall`：3 条记录行；A3:A5 纵向组 merge（`Wall`）、F3:F5 纵向组 merge（`Classic`）；Unit Price 记录行留空（与 Case 010 数据行密度一致） |
| 6–8 | Group 2 `Portable`：3 条记录行；A6:A8、F6:F8 纵向组 merge |
| 9–11 | 外围 `Total` / `Features` / `Notes`（B:F 横向合并） |

**期望判定**：`grid_record / record_axis=rows / fillspec / ["obvious_grid"]`

经 prepare（--outline + --flatten）后 premod_evidence 的可判定事实：
`表头带: 行 [2] 数据起始行 3`、`合并区(8)`（真实 merged ranges）、
`value_bbox: A1:F11`、`候选列头` 六列齐全。

## Fixture C — Column Record Matrix（`fixture_c_column_record_matrix.xlsx`）

参数表形态（schema axis=rows、record axis=columns 的 grid_record）：

| 行 | 内容 |
|---|---|
| 1 | 标题 `Product Parameter Matrix`（A1:D1 横向合并） |
| 2 | 表头示意行 `Parameter \| 12K \| 18K \| 24K` |
| 3–8 | 字段标签列(A) × 产品列(B/C/D) 数据行（Capacity/EER/Sound/Type/Compressor/Refrigerant） |

**期望判定**：`grid_record / record_axis=columns / fillspec / ["obvious_grid"]`

经 prepare 后 premod_evidence 的可判定事实：
`表头带: 行 [2] 数据起始行 3`（数据起始行首行即数值多数的字段行）、
`候选列头: A=Parameter B=12K C=18K D=24K`（产品 record 列一目可读）、
`value_bbox: A1:D8`。

## 0 探测（Fast Path 行为）记录

`expected_verdicts.json` 中的 `evidence_facts` 列出**仅从 prepare 产物
（`{name}_premod_evidence.md`）即可读出**的判定事实；`probe_free: true`
断言：生成 `grid_record/fillspec/obvious_grid + record_axis` 判定**不要求任何
view / render / query / get / HTML inspect / 临时脚本探测**（Layer 3 测试对
每条事实断言其出现在 evidence 文件中，即"仅凭 evidence 可判定"）。

真实业务文件的 replay 验收（客户参数表 / ATLAS 报价单 / Fast Path 0 探测
replay 记录）本轮**明确排除**（用户禁令）；本夹具对 R/C 是它们的
canonical 替代回归对：R 编码 row-grid 判定、C 编码 column-grid 判定。