# table-fill MOD Runtime Core + Attention Map (V1)

> Grilling 产出，2026-08-25 定稿。本 spec 是四张工单（`issues/01~04`）的总契约。
> 所有关键决策均经逐题确认；「Alternatives considered」中列出的方向是**已否决/已搁置**，
> 不是待办。

## 1. Problem

选中 MOD 后，32~38 条业务规则以**平铺、同权重**的六列规则表一次性进入 Agent 的
FillSpec 撰写上下文。`Group`（mapping/business_transformation/validation/other）与
`Gate`（mod_gate/execution_gate）是治理维度，不回答 Agent 真正需要的问题：
"我在做哪个决策的时候，需要想起这条规则？"

参数表 MOD 的 Rules 天然存在认知顺序（SRC→ID→RTE→FLD→TRN→SEC→FMT→CNF→VAL），
但 Runtime 把它们解析成一列等权 Rule，Agent 要自行重建业务主线——注意力被稀释。

同时发现第二个问题：参数表 MOD 多处 Notes 把业务知识权威指向 **MOD 外的另一个
scenario-specific legacy skill**（`tcl-customer-parameter-sheet` 的 field-scope /
template-families / validate_mapping.py）。该 skill 在当前工作区有完整归档
（事实 5），但问题不是文件系统位置，而是**知识依赖边界**——MOD 并非业务知识
自包含，仍是"指向旧 Skill 的薄适配层"。

## 2. Current-state facts（代码核查结论，2026-08-25）

1. **Runtime 不存在"阶段"**。流水线是 `MOD 裁决 → 全量规则注入 → 一次性撰写
   fill_spec.yaml`。FillSpec 撰写是单次上下文，没有 resolve/map/transform/validate
   相位，因此 V1 不做"分阶段投递"。
2. **"规则注入"的物理形态 = Agent 直接读 MOD Markdown**。`mod_nominate.py` 无
   dump 规则的 CLI 出口；`mod_resolution.json` 只含候选摘要与规则证据摘要。
   MOD 文件的章节顺序与排版就是注入顺序——**文件的信息架构就是 Runtime 信息架构**。
3. **"候选规则必须先加载才可写 spec"是 SKILL.md §2 硬契约**。V1 不改变它。
4. **新段对现有机制安全**：`parse_mod_file` 按 `## 段名` 正则抽取
   Applicability/业务逻辑摘要/规则表，未知段被忽略；`mod_capture.py` update 保留
   body 原文；nominate 路径零改动即可兼容。
5. **legacy skill 在仓内有完整归档**：`参数表处理/tcl-customer-parameter-sheet/`
   （SKILL.md + 5 references + 2 scripts）。知识审计可执行。
6. **参数表 08-22 案例已被 `docs/benchmark/table-fill-benchmark-feasibility-report.md`
   §12 判定为 Contaminated**（直接催生了本 MOD 与 Task Orchestration 层），
   只能作失败模式来源，不能作效果对照组。
7. **token 成本无测量管道、无历史基线**（同报告 §13）；且本设计下全量规则仍加载，
   token 只会略增。token 降幅不是、也不能是 V1 指标。

## 3. Goals

- 选中 MOD 后，Agent 先获得 3~6 条业务心智模型（Runtime Core），再按固定认知顺序
  （resolve → map → transform → validate）阅读全量规则（Attention Map）。
- 参数表 MOD 迁移为**业务知识自包含**：不再依赖 legacy skill 作为业务规则/政策权威源。
- Attention Map 具备机器可解析格式与 capture 时硬校验，防止与规则表长期漂移。
- 运行路径行为完全不变：nomination、两段加载、FillSpec、compile、Execution Gate
  均不受影响的向后兼容。

## 4. Non-goals（明确不做）

- 不改 `mod_nominate.py` / `mod_resolution.json` / compile pipeline /
  FillSpec schema / Execution Gate / MOD selection flow。
- 不做 staged rule delivery（`load_rules_for_stage()` 不存在，也不发明）。
- 不做机器候选字段过滤（V2，证据触发）。
- 不做 TCL Domain Policy Pack；不新增跨 MOD 一致性机器检查（见 ADR 0012）。
- 不建 benchmark harness；不做 A/B 重跑。
- **不以降低 token 数量作为 V1 目标**——目标是提高同一上下文中业务知识的可消化性。
- 不删除/重写 32 条现有规则；不压缩规则数量。
- 不给 Attention Map 加 priority/dependency/enforcement/condition 等字段——
  它是目录，不是第二套规则 schema。

## 5. Design

### 5.1 Runtime Core

- MOD 新章节 `## Runtime Core`，位于 `业务逻辑摘要` 之后、`Attention Map` 之前。
- 内容：3~6 条、约 150~300 字，回答"Agent 开始做任务时必须首先建立什么业务世界模型"。
  不写坐标/案例/历史原因；不重复 QA checklist。
- 机器校验只有一个：段存在则必须非空（capture 时检查）。"3~6 条/150~300 字"是
  MOD_TEMPLATE.md 的 authoring guidance，**不是硬 schema**。
- **边界（硬性）**：Runtime Core MUST NOT participate in MOD nomination or
  candidate-card generation. It is loaded only after the MOD has been
  selected/confirmed, together with the full selected MOD content.
  提名卡仍只用 Applicability + 业务逻辑摘要；两者职责不合并——摘要强调**区分度**
  （帮用户选 MOD），Core 强调**执行导向**（帮 Agent 做任务）。

### 5.2 Attention Map

- MOD 新章节 `## Attention Map`，位于 `Runtime Core` 之后、`业务场景上下文` 之前。
- 物理格式：Markdown 列表，沿用 Applicability 的 `- key: value` 解析风格：

  ```markdown
  ## Attention Map

  - resolve: SRC-001, SRC-002, ID-001, ID-002
  - map: FLD-001, FLD-002, SEC-001
  - transform: TRN-001, FMT-001
  - validate: VAL-001, VAL-002
  ```

- 组名**闭集**，固定为 `resolve / map / transform / validate`——不是流水线 phase，
  是 authoring concerns / attention groups。
- 一条 Rule 允许进入多个 group（如安全规则同属 map + validate）；同一 group 内
  不允许重复写同一 Rule ID。
- 格式稳定性三条（保证"笨 parser + 硬校验"长期可靠）：段内每个非空内容行必须
  匹配 `- <group>: <ID>, ...` 语法（parser 不得 silent-ignore）；每个 group 最多
  出现一次（不引入 append/override 语义）；出现的 group 必须遵循
  resolve → map → transform → validate 的相对顺序（允许子集，不允许乱序）——
  Map 是阅读顺序元数据，乱序会削弱其意义。
- **否定定义（spec 级，防止实现漂移）**：
  > Attention Map is presentation and authoring metadata, not an execution
  > phase map and not a rule-delivery mechanism. All selected MOD rules remain
  > required to be loaded before FillSpec authoring.
- 解析器：`_mod_catalog.py` 新增 `parse_attention_map()`，输出
  `dict[str, list[str]]`；段不存在返回 `None`（区分"无 Map"与"空 Map"）。
  保持"笨"——不解析 priority/dependency/enforcement；每个 group 至多一行，
  dict 语义干净。
- capture 硬校验（`mod_capture.py` create/update 写盘前，校验对象是**最终完整
  candidate body**）：
  1. **malformed line**：段内每个非空内容行必须匹配规定语法，否则拒收——
     校验器不能拒收它看不见的行，silent-ignore 会使"dangling/coverage 全过"
     失去意义；
  2. **dangling**：Map 引用的每个 Rule ID 必须存在于规则表；
  3. **coverage**：规则表每条 Rule 至少出现在一个 group；
  4. **closed set**：group 名只能是四个固定值；
  5. **group 唯一**：每个 group 最多出现一次；
  6. **顺序**：出现的 group 必须遵循 resolve → map → transform → validate
     相对顺序（允许子集）；
  7. **组内重复**：同一 group 内同一 Rule ID 出现两次 → 拒收；
  8. **跨组重复**：合法。
  全部失败 exit 3。仅在 MOD **存在** `## Attention Map` 时启用；旧 MOD 无此段
  → 行为完全不变。
- Rules 表物理顺序由作者按"首要 attention group"人工组织（ authoring guidance），
  **不做自动重排 formatter**；Map 是唯一机器检查对象。

### 5.3 全量加载不变量（Full-rule loading invariant）

- SKILL.md §2 硬契约不变：选中 MOD 的全部规则仍必须在 FillSpec 撰写前加载。
- SKILL.md 仅增加一条轻量撰写纪律：在一次性撰写 FillSpec 时，按 Attention Map 的
  认知顺序（resolve → map → transform → validate）考虑业务问题；无 Map 的 MOD
  维持现状。
- V1 要验证的假设：**问题是"Rule 缺乏优先级与认知结构"，而不是"Rule 数量太多"**。

### 5.4 MOD 业务知识自包含不变量（Self-contained MOD invariant）

> Selected MODs MUST be business-knowledge self-contained. They may depend on
> table-fill generic execution contracts and utilities, but MUST NOT require
> another scenario-specific Skill as an authoritative source of business rules
> or policy.

- 边界：自包含 ≠ 删除 legacy 脚本。如 `validate_mapping.py` 可暂留，只要 MOD 不
  把它当业务知识权威源、缺少它不会让 Agent 不知道规则是什么；其机器校验是否迁入
  table-fill 是 V2 问题。
- 跨 MOD 重复规则（如 Z Code exact match 出现在 3 个 MOD）**允许**，靠 Notes
  交叉引用 + 人工编辑纪律维护；不引入 Domain Pack 组合层。

### 5.5 参数表 MOD 知识迁移（ticket 01 执行）

两阶段票内流程：

- **Phase A — Knowledge Audit**：审计 `参数表处理/tcl-customer-parameter-sheet/`
  全部文件，输出 Legacy Knowledge Disposition 表（legacy item → classification →
  destination → reason），分类五选一：业务原则→MOD Rules/Runtime Core；
  业务事实/词表/字段政策→MOD 知识章节；通用能力→table-fill；业务专用机器校验→
  暂留（V1 non-goal）；一次性资产→不迁移。
- **Phase B — MOD Migration**：只迁移 Phase A 明确裁决过的内容（票内硬约束），
  新增 Runtime Core / Attention Map / Export Field Policy，规则表按首要 group
  重排，清除对 legacy skill 的权威引用（SEC-001/RTE-001/FLD-001/TRN-003/ID-004/
  RTE-004 的 Notes 与业务场景上下文）。
- Export Field Policy 必须区分 **GLOBAL_DENY（公司层面不可外发）** 与
  **TEMPLATE_NOT_REQUESTED（本次模板未请求）**——不得把一次埃及模板的选择提升为
  全局公司政策。
- **护栏**：只有跨任务稳定的字段资格事实才能迁入 Export Field Policy；具体
  国家/客户本次需要哪些参数，仍是由用户请求和 Customer Template 在运行时确定
  的事实，不得固化进 MOD。`Conditional` 条目必须能说明稳定的 condition 是什么，
  否则不迁入，继续作为 runtime 决策事实——防止该章节慢慢长成
  "埃及不要什么、阿尔及利亚要什么"的市场实例集，重新长成旧 Skill。
- 治理：MOD 规则变更走既有硬性流程——逐条 diff 呈报用户审核 → 确认后
  `mod_capture.py --action update`（revision 1→2）。private 可见性不变。

## 6. Compatibility

- 不含 `## Runtime Core` / `## Attention Map` 的 legacy MOD 不触发任何新增
  validation；其既有 create/update/nominate/load 的合法性与用户可观察行为保持
  不变（兼容性测试锁定，ticket 02）。
- 新段不进 `mod_resolution.json`，不进提名卡；`mod_nominate.py` 零改动。
- MOD 文件结构与解析对 `test_mod_roundtrip.py` / `test_mod_decontamination.py`
  现有用例保持绿色。

## 7. Validation

### 7.1 静态验收（现在完成，不等真实任务）

- `parse_attention_map()` + capture 硬校验全绿，含"无 Map 旧 MOD 行为不变"回归；
- 参数表 MOD 的 Attention Map 对全部 32 条规则 100% 覆盖、无 dangling ID；
- Legacy Knowledge Audit 完成，Disposition 表经用户审核；
- 参数表 MOD 自包含：不再把 `tcl-customer-parameter-sheet` 作为权威业务知识依赖；
- 现有 nomination / MOD Gate / FillSpec / compile / Execution Gate 路径行为不变。

### 7.2 预注册 Holdout 观察协议（冻结文本，供下一次真实参数表任务使用）

08-22 案例是 Contaminated（事实 6），只用于提前冻结 failure taxonomy，**不作效果
对照**。验证点 = 下一次真实、未污染的参数表任务（如新产品线/新市场/柜机批次，
benchmark 报告 §8 已列为天然 holdout 采集通道）。跑测时逐条核对 F1~F8，并区分：

> **业务理解错误** vs OfficeCLI/编码/schema 语法/工具缺陷——后者不计入指标。

**三态记录（防止"未被测试 ≠ 通过"的误判）**：跑测时 F1~F8 每一项必须记录为——

- `exercised`：本次任务真实挑战了该维度（如多模板族并存 → F2 被挑战）；
- `not applicable`：本次任务未涉及该维度（如只有一种模板族 → F2 未被挑战）；
- `failure observed`：该维度发生失败，记录证据。

**"未观察到失败"不等于"已证明不会发生"**——只有 exercised 维度的 0 failure
才算有效证据。

### 7.3 F1–F8 失败分类（预注册）

- F1 产品/源列身份误判
- F2 模板族首次路由错误
- F3 INTERNAL_ONLY / 内部状态进入迁移或输出
- F4 客户可见中文残留
- F5 模板旧值被当成事实
- F6 缺失事实被自行补写
- F7 因业务逻辑理解错误产生重复澄清
- F8 因业务逻辑理解错误导致 FillSpec / compile 后结构性返工
  （如：单冷→冷暖反转、选错产品列、客户字段范围误判、先映射内部字段后删除、
  把模板旧值当来源；工具语法修正不算）

### 7.4 Go / Stop 标准

**V1 GO**（下一次真实 holdout 任务满足全部）：

- 所有本次 **exercised** 的 Critical failure dimensions 均为 0 failure
  （F1~F6 中的身份错、模板族错导致参数缺失、内部信息外泄、中文外泄、编造技术
  事实 = Critical；"多问一次问题"不与泄漏同权）；
- 0 个 Critical new failure（由 Core/Map 诱发的新严重理解偏差、规则遗漏或冲突）；
- 首次业务解析正确确定 Source Authority / Product Scope / Template Family /
  Field Eligibility 原则，无因 MOD 理解错误产生的结构性 FillSpec 返工（每发生
  一次反转记一次 business reasoning correction）；
- Attention Map 未导致 Agent 把跨组 Rule 误当"只属于某组"而忽略其跨领域效力；
- **Inconclusive 出口**：若关键维度（如 F2 模板族、F3 内部字段、F6 缺失补写）
  大部分为 not applicable——本次任务对机制挑战太弱——结果记为
  **Inconclusive**，既非 GO 也非 Stop，等待下一个更有挑战的真实任务；
  不得让一个特别简单的新任务意外成为"V1 验证通过"的证据。

**V1 Stop / Rework**（任一发生）：

- 历史 Critical failure 复发；
- 出现明显由新信息结构诱发的新错误；
- Agent 仍需反复通读完整 MOD、多次重建业务模型（Core/Map 未解决平铺问题）；
- Attention Map 导致规则跨域效力被忽略。

**结果模糊时**：接受 V1 的低成本信息架构改进（静态安全、无副作用即不回滚），
**停止后续机制复杂化**。

**V2 不自动启动**：即使 GO，也只有当真实任务反复显示"Agent 理解原则但稳定违反
某一确定性规则"时，才把该类规则机器化（如 candidate filtering）。

## 8. Rollout

```text
02 Parser + Capture 校验 ──┐
                           ├─→ 01 参数表 MOD 迁移（Audit 可与 02 并行；
01 ────────────────────────┘    capture update 等 02 完成）
 ├─→ 03 Docs + ADR（V1 收口）
 └─→ 04 其余 MOD 自包含审计（V1.1，非阻塞，audit-only）
```

下一批真实参数表任务 = 第一个有效 observation point，按 §7.2 协议观察后决定
stop / 保持 / 证据触发 V2。

## 9. Alternatives considered（已否决/已搁置）

| 方案 | 处置 | 理由 |
|---|---|---|
| 机器分阶段投递（staged rule delivery） | 否决（V1），ADR 记录 | Runtime 无阶段相位；须改动"先加载后写 spec"硬契约；无证据表明"分组后的全量"仍干扰 |
| 降低注入 token / 压缩规则到 12 条 | 否决 | token 无测量管道且本设计略增 token；规则本身有业务价值，问题在呈现不在数量 |
| TCL Domain Policy Packs（三层知识体系） | 搁置，ADR 记录 revisit trigger | 4 MOD / 136 规则体量下无漂移事故证据；组合层增加加载概念；自包含优先 |
| Benchmark harness / 污染案例 A/B | 否决 | 报告已建议"本轮不实现"；08-22 已污染无对照效力 |
| 机器候选字段过滤（GLOBAL_DENY 前置过滤） | 搁置至 V2（证据触发） | 属执行架构改动，V1 只动信息架构 |
| 字段资格政策留在 legacy skill references | 否决 | 违反自包含不变量；该现状是待清理的架构债务 |
| Export Field Policy 逐字段写成 Rule | 否决 | 字段政策是业务事实集合，专用章节承载，不把 20 个字段膨胀成 20 条 Rule |
| capture 自动重排规则表 | 否决 | 一条 Rule 可属多组，物理排序是 authoring guidance，不做 formatter |
| 04 审计顺手重构另外 3 个 MOD | 否决 | 防止 audit 膨胀成框架重构；是否开迁移票另行决定 |
