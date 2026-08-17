# MOD Template — Business Rule Schema

<!--
  Do NOT add YAML frontmatter to this file or to any MOD profile.
-->

## Purpose

This file defines the business rules for a single MOD (Mapping Override Document).
Each MOD is a self-contained Markdown file that the L0.5 提名脚本 (`mod_nominate.py`)
读取其 `Applicability` 与 `业务逻辑摘要` 段用于提名卡；用户裁决后规则整体注入
FillSpec 上下文（无状态机, 无 gate_confirmed）。MOD Resolution 用 digest 事实核对结构信号,
冲突则用户重新裁决。

## 文件结构（必须按此顺序）

```markdown
# MOD_<name>

## Purpose            # 版本/可见性/规则数
## Metadata           # Scope Signals / Aliases
## Applicability      # 业务逻辑指纹 — 提名匹配依据 (脚本读取)
## 业务逻辑摘要       # 提名卡数据源 (脚本读取)
## 业务场景上下文     # 业务背景 (可含客户示例, 但规则本体不得硬编码)
| Rule ID | Group | Gate | Description | Applies to | Notes |   # 六列规则表
```

## Applicability（业务逻辑指纹）

只描述**可复用的业务逻辑特征**，不描述具体业务对象身份（客户名/企业名）。

```markdown
## Applicability（业务逻辑指纹 — 提名脚本读取, 匹配依据）

- semantic_type: quotation_summary_migration      # 业务语义
- template_fingerprint: 24col_quote_template; repeated_block_title_header_data; formula_chain:net_price_to_total_margin
- product_domain: residential_split; multi_split; window_unit; duct_unit   # 产品域
- source_pattern: "毛利表*"                        # 文件模式
- target_pattern: "报价汇总*"                      # 文件模式
- scope_notes: 描述性备注(组织/客户信息仅展示, 不参与匹配)
```

## 业务逻辑摘要（提名卡数据源）

3-8 条要点式规则摘要，供用户在提名卡上快速判断"是不是我要的那套逻辑"。
示例：`- 映射: 原型机成本 = 源面价(更新) − 源铜管成本; 上一单报价 → 历史报价`

## 规则表 Schema（六列, 顺序固定）

| Column | Required | Description |
|--------|----------|-------------|
| **Rule ID** | yes | Stable identifier within this MOD (e.g. `R01`, `RTE-001`). |
| **Group** | yes | `business_transformation` / `mapping` / `validation` / `other`. |
| **Gate** | yes | `mod_gate` (提名裁决后即生效) 或 `execution_gate` (L3 后呈现). |
| **Description** | yes | Human-readable rule statement. |
| **Applies to** | yes | Scope expression. |
| **Notes** | no | Implementation hints. 禁止承载裁决方式与单次运行事实数字（见「规则变更治理」）. |

## ⚠️ 规则变更治理（硬性要求，2026-08-10）

**任何向 MOD 添加、修改或删除规则的行为，必须先经用户明确审核确认，不得静默写入。**

- 适用范围：规则本体（Description）、Notes、Applicability、业务逻辑摘要、
  业务场景上下文——一切会改变 MOD 语义的编辑。
- 流程：agent 呈现「拟变更的规则 + 变更理由 + 逐条 diff」→ 用户审核并明确
  确认 → 才允许执行 `mod_capture.py`（create/update）或手工落盘。
- Notes 约束：
  - **禁止承载裁决方式**（如"Gate 采信 13/15 多数派"）——裁决逻辑必须提炼为
    带条件的通用规则进 Description，Notes 只放实施提示。
  - **禁止承载单次运行事实数字**（记录数、金额、日期、比例、sheet 名）——
    数字是证据不是规则，不带入 MOD 文件；确需留证时放外部运行档案。
- 违反信号：Notes 出现具体数字/比例/日期/sheet 名/某次怎么裁决 → 视为污染，须清理。
- 复盘依据：2026-08-10 曾在 FLD-008 Notes 硬编码"13/15 多数派采信"决策，被提炼为
  冲突裁决通则；全部"示例运行"描述已移除（revision 5）。

## Gate 语义

- `mod_gate` 规则: 用户提名裁决后生效, 指导 L1-L3 全流程。
- `execution_gate` 规则: L3 完成时随 Execution Gate 呈现给用户, 属验收核对项
  （如 VAL-001 行覆盖核对、VAL-002 公式链核对）。

## ⚠️ 捕获去污染原则（硬性要求）

捕获业务规则时, **规则本体（Description）禁止包含单次运行的物理坐标**:

| 禁止项 | 示例（错误） | 正确写法 |
|--------|-------------|---------|
| 硬编码目标 sheet 名 | 路由到 `11_FRESH本土` | 路由到"历史块包含该产品族的 sheet"（按运行发现, 不预设） |
| 硬编码行号/坐标 | 第 21-28 行为数据区 | 以表头/块边界识别数据区 |
| 硬编码记录数 | 风管块必须覆盖全部 12 条 | 必须覆盖该产品组的全部型号记录 |
| 硬编码客户名 | 适用于 FRESH 报价 | 按业务语义/结构指纹描述适用域 |

单次运行的具体事实（sheet 名、行号、数量、客户名、金额、日期、比例）**不得进入将随发布推送的 public MOD 文件任何位置**（含 Notes 与业务场景上下文）——它们是运行证据, 不是规则本身; 确需留证时放外部运行档案。业务场景上下文只描述可复用的机制性背景（如"含管/不含管是客户级要求"）。这与 FLD-002（按语义角色映射, 不依赖物理列位置）和 TGT-001（坐标仅是单次发现结果, 不构成身份）的原则一致。

**Public/Private 作用域区分**: customer-owned private MOD 允许承载客户域事实与业务场景上下文（这正是其价值），但必须 `visibility: private` 且不随发布推送。`mod_capture.py` 默认写 `visibility=private`；`--visibility public` 需显式指定且必须先通过去污染校验，故该规则与实现一致。转 public 需外部脱敏审查。

## Capturing a MOD

Capture follows a single-confirmation post-delivery lifecycle. After a verified
table-fill run (all four layers passed), the agent may offer to capture the business
logic as a private MOD.

**Capture lifecycle:**
1. **One user confirmation** — after verified non-trivial delivery, ask whether to
   capture. A verified `MOD=NONE` run is eligible.
2. **Prepare the MOD Markdown file** — draft per this template (Applicability +
   业务逻辑摘要 + 六列规则表), **执行去污染检查**, 呈现给用户审查。
3. **One private capture command** — after confirmation, invoke:
   `python scripts/mod_capture.py --source <MOD.md> --mod-name <NAME> --action create`
   (or `--action update`). Visibility is always `private`.

**State boundaries:**
- `mod_capture.py` never writes `mod_state.yaml` (V2 已彻底废弃该文件).
- On update, backups (`MOD_<name>.md.bak`, `MOD_INDEX.md.bak`) are created before
  mutation, revision is incremented by one, and temp-file replacement is used.

**Manual registration** (one-off, no verified run needed):
1. Copy this template to `references/MOD_<name>.md`.
2. Replace the example table with real rules; run the de-contamination check.
3. Add one row to `references/MOD_INDEX.md` in the Registered MODs table.
4. Increment revision on every confirmed update.
