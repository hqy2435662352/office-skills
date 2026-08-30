# 04 — 其余三个 MOD 的自包含审计（V1.1，audit-only，非阻塞）

Status: resolved
Type: task
Blocked by: 01（复用其审计方法论与 Disposition 分类表）

## 问题

参数表 MOD 暴露出"MOD 权威指向 legacy skill"的架构债务（ticket 01）。其余三个 MOD
可能同类受染：

- `table-fill/references/MOD_tcl_quotation_summary_migration.md`
- `table-fill/references/MOD_tcl_cost_reply_to_quotation_summary_block.md`
- `table-fill/references/MOD_tcl_pricing_block_to_customer_quotation.md`

## 方案

按 ticket 01 Phase A 的同款方法，逐 MOD 审计：

1. 全文扫描对外部 scenario-specific skill / reference / 脚本路径的权威指向
   （Notes、业务场景上下文、Description 均查；provenance 历史事实不算权威指向）；
2. 对每个命中项判定：是否"缺少它就不知道业务规则是什么"；
3. 输出审计报告（`.scratch/table-fill-mod-attention/other-mods-audit.md`）：

| MOD | Self-contained? | External business dependency | Severity | Recommended next action |
|---|---|---|---|---|

## 边界（硬性）

- **audit-only**：本票只输出问题清单与迁移建议，**不修改任何 MOD**；
- 不得"审计到一个问题顺手修一个"——是否开 V1.1 迁移票由用户另行决定；
- 不评估规则本身业务正确性，只评估知识依赖边界。

## 验收

- 三个 MOD 各有一行结论 + 命中项明细（文件:行号）；
- 报告末尾给出"是否需要 V1.1 迁移票"的建议（仅建议，不自动开票）；
- 三个 MOD 文件零改动（git diff 为空）。

## Answer

Status: resolved（2026-08-30，主导 Agent 验收通过；audit-only 零改动确认）

### 结论（三 MOD，逐行详见 `.scratch/table-fill-mod-attention/other-mods-audit.md`）
- MOD_tcl_quotation_summary_migration → **Yes-with-notes**：0 权威外部依赖；仅 2 处 `2026-08-09` 固化日期（Notes/上下文，次要卫生）。
- MOD_tcl_cost_reply_to_quotation_summary_block → **Yes（全净）**：0 权威外部依赖，无卫生命中。
- MOD_tcl_pricing_block_to_customer_quotation → **Yes-with-notes**：0 权威外部依赖；FLD-003 Description 含示例型号字面量（次要卫生，private 可承载）。
- 建议：**不需要开 V1.1 迁移票**（仅建议）。Runtime Core/Attention Map 对齐属可选增强，由用户另行决定。

### 验收证据（主导 Agent 复核）
- 三文件 SHA-256 before==after 独立复测一致（93F4A6… / 4CA48E… / 11C889…）；git porcelain 为空；无 commit。
- 命中项行号抽检（MOD 1 FRM-008 行含 `2026-08-09 固化`）与报告一致。
- 分类计数：authoritative external dependency × 0；generic capability × 3；provenance × 3；cross-MOD × 0；single-run fact × 2；补充类 marker/example 各 2/1。
