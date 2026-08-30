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
## Metadata           # Scope Signals / Aliases / Display Name / Exclusion Signals
## Applicability      # 业务逻辑指纹 — 提名匹配依据 (脚本读取)
## 业务逻辑摘要       # 提名卡数据源 (脚本读取)
## Runtime Core       # 裁决后业务心智模型（可选，推荐；不进提名卡）
## Attention Map      # 阅读顺序元数据（可选，推荐；机器校验，不进提名卡）
## 业务场景上下文     # 业务背景 (可含客户示例, 但规则本体不得硬编码)
| Rule ID | Group | Gate | Description | Applies to | Notes |   # 六列规则表
## Export Field Policy  # 字段资格政策（可选；使用时位于规则表之后，见已迁移 MOD）
```

## Metadata 段

- `Scope Signals` / `Aliases` / `Exclusion Signals`: 提名匹配事实 (脚本读取)。
- `Display Name` (可选): **中文展示名**, 仅用于提名卡/裁决选项的人读标签 —
  英文 MOD 名始终是机器身份 (匹配/别名/路径/捕获都用它, ASCII 不变)。
  `mod_nominate.py` 解析本字段并输出 `display_name` (缺省回退英文名)。
  编码安全: MOD 文件与提名 JSON 全程 UTF-8 (`ensure_ascii=False` +
  `_utf8_stdio()`), 展示层中文不会引入编码问题。

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

## Runtime Core（可选，推荐）

**用途**: 裁决后执行心智模型 —— Agent 在 MOD 被用户选定（裁决）后、撰写 FillSpec
前必须首先建立的业务世界模型（"我在做这个任务时按什么业务主线思考"）。
位于 `业务逻辑摘要` 之后、`Attention Map` 之前（见「文件结构」）。

- **内容 (authoring guidance，非硬 schema)**: 3~6 条、约 150~300 字的执行导向要点
  （权威源职责、处理顺序、禁止静默推断的边界等）。**不写坐标/案例/历史原因**；
  **不重复**六列规则表与 QA checklist。
- **边界（硬性）**: Runtime Core **不参与 MOD 提名/候选卡生成**，仅在 MOD 被
  选中/确认后与完整选中 MOD 内容一起加载。提名卡仍只用 `Applicability` +
  `业务逻辑摘要`；两者职责不合并——摘要强调**区分度**（帮用户选 MOD），Core
  强调**执行导向**（帮 Agent 做任务），也不替代 `业务逻辑摘要`。
- **机器校验**: 仅"段存在则必须非空"（capture 时检查，见下节）；"3~6 条 /
  150~300 字"是 authoring guidance，不是硬 schema。

## Attention Map（可选，推荐）

**用途**: 阅读顺序元数据 —— 把 MOD 全部规则按四个固定 attention group 组织，
让 Agent 在一次性撰写 FillSpec 时按认知顺序考虑业务问题。它是**呈现与撰写辅助
元数据**，不是执行阶段图，也不是规则投递机制（否定定义见下）。

```markdown
## Attention Map

- resolve: SRC-001, SRC-002      # 先解决身份/权威源问题
- map: FLD-001, FLD-002          # 再建立映射
- transform: TRN-001             # 再做转换
- validate: VAL-001, VAL-002     # 最后核验
```

- **物理格式**: Markdown 列表，沿 `Applicability` 的 `- key: value` 风格，每行
  `- <group>: <ID>, <ID>, ...`。段内每个非空内容行必须匹配该语法；**冒号后无
  ID、或 ID 列表含空元素（如双逗号）= malformed** —— dumb parser 不
  silent-ignore（校验器不能拒收它看不见的行）。
- **四组闭集**: `resolve / map / transform / validate`——是 authoring concerns /
  attention groups，**不是流水线 phase**。
- **重复规则**: 一条 Rule 允许进入多个 group（如安全规则同属 map + validate，
  跨组重复**合法**）；同一 group 内同一 Rule ID **不允许**重复。
- **格式稳定性**: 每个 group 最多出现一次（不引入 append/override 语义）；出现的
  group 必须遵循 resolve → map → transform → validate 相对顺序（允许子集，禁止
  乱序）。
- **否定定义（spec 级，防止实现漂移）**:
  > Attention Map is presentation and authoring metadata, not an execution
  > phase map and not a rule-delivery mechanism. All selected MOD rules remain
  > required to be loaded before FillSpec authoring.
- **capture 硬校验**（`mod_capture.py` create/update 写盘前执行，校验对象是最终
  完整 candidate body；**仅当 MOD 存在 `## Attention Map` 时启用**，旧 MOD 无此段
  → 行为完全不变；全部失败 **exit 3**，违规聚合成一条错误消息，malformed line
  例外——dumb parser 先拒收，错误带行号与纠正提示）:
  1. **malformed line**: 段内每个非空内容行必须匹配 `- <group>: <ID>, <ID>, ...`，
     否则拒收（含冒号后无 ID / 空 ID 元素）；
  2. **dangling**: Map 引用的每个 Rule ID 必须存在于规则表；
  3. **coverage**: 规则表每条 Rule 至少出现在一个 group；
  4. **closed set**: group 名只能是 resolve / map / transform / validate；
  5. **group 唯一**: 每个 group 最多出现一次；
  6. **顺序**: 出现的 group 遵循 resolve → map → transform → validate 相对顺序
     （允许子集）；
  7. **组内重复**: 同一 group 内同一 Rule ID 出现两次 → 拒收；
  8. **跨组重复**: 合法（不做任何拦截——覆盖检查用集合，重复引用不误伤）。
  **Runtime Core** 的"存在则非空"检查在同一校验块内执行（声明段为空 → 拒收）。
  Rules 表物理顺序由作者按"首要 attention group"人工组织（authoring guidance），
  **不做自动重排 formatter**；Map 是唯一机器检查对象。

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
  业务场景上下文、Runtime Core、Attention Map、Export Field Policy——一切
  会改变 MOD 语义的编辑。
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
   (or `--action update`). Visibility defaults to `private`; `--visibility
   public` 只作机械去污染门禁（见上「捕获去污染原则」），仍需外部脱敏审查通过。

**State boundaries:**
- `mod_capture.py` never writes `mod_state.yaml` (V2 已彻底废弃该文件).
- On update, backups (`MOD_<name>.md.bak`, `MOD_INDEX.md.bak`) are created before
  mutation, revision is incremented by one, and temp-file replacement is used.
- On update, the existing `Display Name` (Metadata) is carried into the rebuilt
  header — the stored file always equals the reviewed candidate, never a
  post-capture patch.

**Manual registration** (one-off, no verified run needed):
1. Copy this template to `references/MOD_<name>.md`.
2. Replace the example table with real rules; run the de-contamination check.
3. Add one row to `references/MOD_INDEX.md` in the Registered MODs table.
4. Increment revision on every confirmed update.
