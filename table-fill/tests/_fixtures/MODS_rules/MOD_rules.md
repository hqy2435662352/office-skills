## Applicability
- semantic_type: quotation

## 业务逻辑摘要
- 原型机成本 = 源面价 − 铜管成本

| Rule ID | Group | Gate | Description | Applies to | Notes |
|---|---|---|---|---|---|
| FLD-006 | business_transformation | mod_gate | 目标原型机成本等于源面价(更新)减源铜管成本。 | 原型机成本、面价(更新)、铜管成本 | 与 FLD-007 共同保证结算价等于面价 |
| FRM-002 | business_transformation | mod_gate | 结算价等于原型机成本加铜管成本。 | 结算价 | 与成本拆分规则逐行核对 |
