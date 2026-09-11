<!-- 编号沿续: docs/adr/ 现有最大 0013 (MOD Attention Map); 本 ADR 为
     对 ADR-0010 (Task Shape Routing) 的细化, 吸收上一轮 Ticket 02
     (grouped-grid 路由契约, 原计划 ADR-0014) 并扩展 axis-neutral 定义。 -->

# 0014-table-fill-axis-neutral-grid-routing

# Axis-neutral Grid Routing in Table Fill

## Status

Accepted on 2026-08-31 as part of the axis-neutral grid runtime feature
(`.scratch/table-fill-axis-neutral-grid-runtime`, Ticket 02, Phase A). This ADR
refines ADR-0010: `grid_record` is defined axis-neutral while the task_shape
value domain stays untouched.

## Context

ADR-0010 defined routing around a row-record-biased grid: "稳定 header + 重复
record 行", 输出行数由源记录数驱动. A real parameter-sheet task
(客户参数表: 参数字段沿行轴展开、12K/18K/24K 产品 record 沿列轴重复) was
then *legitimately* classified `form_content` — not a misjudgment, an ontology
gap: "输出行数 = 模板固定参数 schema" matched the fixed-cardinality wording of
form_content. The same session also exposed 纵向 group merge (multi-record
shared group display values) being misread as "fixed form slot" evidence
(previous round Ticket 02, absorbed here). Both are interpretation gaps in the
contract text, not detector or classifier failures.

## Decision

1. **`grid_record` is axis-neutral**: `grid_record` = 稳定 schema axis +
   重复 record axis 的二维数据, `record_axis ∈ {rows, columns}`。列头行/字段
   标签列衡量 schema axis; 产品列/记录行衡量 record axis。**Row-Record Grid**
   (record 在行) 与 **Column-Record Matrix Grid** (record 在列 =
   "schema axis=rows、record axis=columns") 同为 `grid_record`, 不是两种 shape。
2. **Cardinality 锚点扩列**: 目标主体重复输出单元数量由源 records 数量/筛选
   结果驱动 — N records → N target rows (line grid) 或 N target columns
   (matrix grid); 固定行数本身不构成 form 证据; 外围结构 (metadata / Total /
   Notes / group merge / inplace 占位区) 不改变 Grid 身份。上一轮 grouped-grid
   契约保留吸收: 纵向 merge 仅用于组级展示值时不构成 `form_content` 信号,
   merge 数量不是 Non-Grid 依据。
3. **Form 对照不变**: 典型 `form_content` 是有限、预定义、cardinality 固定的
   语义 slot (姓名→B3、日期→F4…); 分界是 slot cardinality 固定 vs record
   cardinality 数据驱动, 不是 merge 的存在与否。
4. **task_shape 值域不变**: `matrix` / `column-grid` / `grouped-grid` 只是
   帮助解读的结构属性/evidence 标签, 不是新 shape 值; shape 仍是
   `grid_record`, 值域仍是 {grid_record, form_content, mixed, uncertain}。
   兼容扩展: `task_shape.json` 可记录 `record_axis` (rows/columns) —
   evidence/diagnostic 层, 不改变三字段契约, 也不把 task_shape.json 当作
   schema 权威 (它是 Agent 撰写的 artifact)。
5. **这是契约文本, 不是新分类器**: 落点为 SKILL.md §1.5 Task Shape Check
   的契约文字 (稳定措辞, 供后续 contract-text 测试 pin)。禁止记录数量阈值;
   禁止 Routing V3 / scoring classifier / 第二层分类器; Fast Path 0 新增动作
   不变, 判定只依赖 task 指令 × premod_evidence。

## Considered Options

- **新增 shape 值 (matrix / column-grid)** — rejected: 值域冻结是 ADR-0010
  与多轮 Code Review 的硬约束; 新增值会波及 route/evidence 矩阵并破坏
  shape×route 正交; 二轴同构本可被一个定义吸收。
- **专门检测器/分类器 (matrix detector)** — rejected: 重复"路由税"反模式
  (ADR-0010 已拒对称分类); 既有 evidence (表头带/候选列头/字段标签列+产品列)
  已把两轴事实交给 LLM 判定, 不需要新机制。
- **记录数量阈值** — rejected (与 ADR-0010 一贯): 数量既不是 Grid 的优点也
  不是 form 的证据 (3 条记录需要 clone/lookup 仍是 Grid)。
- **打开所有预定义 span 的 task_shape.json schema** — rejected: record_axis
  走兼容字段, 不为诊断扩建 schema; 三字段契约保持权威。

## Consequences

- SKILL.md §1.5 契约文字更新并保持稳定; 上一轮 grouped-grid 契约文本被吸收
  而非删除。
- 两个 data-neutral canonical 回归夹具: R (Grouped Row Grid, A/F 纵向组
  merge, Total/Features/Notes 外围) → grid_record/rows; C (Column Record
  Matrix, 字段标签列 × 12K/18K/24K 产品列) → grid_record/columns; 三层验证
  (检测器契约 / Pre-MOD Evidence 四要素 / routing verdict)。
- 客户参数表形态不再被"合理地"误判 form_content; 最终执行路径 (MOD 生命周期
  解耦, Ticket 05) 不在本 ADR 范围。
- 真实业务文件 replay (客户参数表 / ATLAS 报价单 / Fast Path 0 探测) 本轮
  明确排除; 夹具 R/C 是它们的 canonical 判定替代回归对。