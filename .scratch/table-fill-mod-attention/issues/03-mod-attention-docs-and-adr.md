# 03 — MOD_TEMPLATE / SKILL / CONTEXT 文档更新 + ADR 0012

Status: resolved
Type: task
Blocked by: 01, 02（文档记录的是最终落地形态，须在内容迁移与机制实现定型后撰写）

## 问题

同一机制决策需要面向两类读者规范化落盘：SKILL/MOD_TEMPLATE/CONTEXT 告诉未来
Agent 与 MOD 作者"现在应该怎么做"；ADR 0012 告诉未来维护者"为什么这么做、为什么
没做 staged delivery 与 Domain Pack"。

## 方案

### 1. `table-fill/references/MOD_TEMPLATE.md`

- 「文件结构」更新为：`Purpose → Metadata → Applicability → 业务逻辑摘要 →
  Runtime Core → Attention Map → 业务场景上下文 → 规则表`（新段标"可选，推荐"）。
- 新增 Runtime Core 小节：用途（裁决后执行心智模型）、guidance（3~6 条、150~300
  字、不写坐标/案例/历史）、边界（**不进提名卡、不替代业务逻辑摘要**；
  机器校验仅"存在则非空"）。
- 新增 Attention Map 小节：格式（`- group: ID, ID`）、四组闭集
  （resolve/map/transform/validate）、跨组允许/组内禁重、capture 硬校验清单，
  并写入否定定义原文：
  > Attention Map is presentation and authoring metadata, not an execution
  > phase map and not a rule-delivery mechanism. All selected MOD rules remain
  > required to be loaded before FillSpec authoring.
- 「规则变更治理」适用范围句补充：Runtime Core / Attention Map / Export Field
  Policy 章节同样属于"会改变 MOD 语义的编辑"，走 diff 审核流程。

### 2. `table-fill/SKILL.md` §2（MOD Resolution 段）

在"规则注入时机"后追加一条轻量撰写纪律（约 5 行，不改动任何硬性流程）：

- 全量加载契约不变：选中 MOD 的全部规则仍在 FillSpec 撰写前加载；
- 若 MOD 含 `## Runtime Core`：先建立其业务心智模型再撰写；
- 若 MOD 含 `## Attention Map`：一次性撰写 FillSpec 时按
  resolve → map → transform → validate 的认知顺序考虑业务问题（注意力分组，
  不是流水线阶段，不减少任何规则的加载）。

### 3. `CONTEXT.md` 新增三词条（ glossary 格式，含 _Avoid_ ）

- **Runtime Core**: A short post-selection section of a MOD stating the
  business mental model the agent must establish before FillSpec authoring;
  it never participates in MOD nomination.
  _Avoid_: Nomination summary, staged loading
- **Attention Map**: A MOD's machine-validated reading-order metadata mapping
  the four fixed attention groups (resolve/map/transform/validate) to rule IDs;
  presentation and authoring metadata, not an execution phase map or a
  rule-delivery mechanism.
  _Avoid_: Runtime stage, rule subset delivery
- **Self-Contained MOD**: A MOD that carries all business knowledge required
  for its scenario; it may depend on table-fill generic execution contracts and
  utilities, but not on another scenario-specific skill as an authoritative
  source of business rules or policy.
  _Avoid_: Thin adapter to a legacy skill

### 4. `docs/adr/0012-table-fill-mod-attention-map.md`

按既有 ADR 格式（参照 0008/0011），冻结本轮 grilling 决策：

**Decision（至少这 9 条）**：

1. Rules continue to be fully loaded before FillSpec authoring（全量加载契约不变）。
2. Runtime Core is post-selection only（不进提名卡）。
3. Attention Map is attention metadata, not execution phases。
4. Attention Map has four closed groups: resolve / map / transform / validate。
5. No staged rule delivery in V1。
6. MODs are business-knowledge self-contained。
7. No TCL Domain Policy Pack in V1/V1.1。
8. Cross-MOD duplication is currently accepted（Notes 交叉引用 + 人工编辑纪律维护）。
9. Machine enforcement is evidence-triggered V2 work。

**Alternatives considered**：staged rule delivery；Domain Policy Pack；
benchmark harness / 污染案例 A/B；机器候选字段过滤；token 降幅指标。

**Revisit triggers**：

- Domain Pack：仅当跨 MOD 规则漂移造成真实业务事故（错误 FillSpec/输出），或重复
  规则维护明显成为持续负担时重启——**"发现重复"本身不是重启条件**；
- staged delivery：仅当真实 holdout 观察证明"分组后的全量"仍稳定造成业务理解
  错误（spec §7.4 的 Stop 条件触发）时重启。

**Consequences**：接受有限重复与人工一致性维护；V1 token 略增可接受
（目标是可消化性，不是减量）。

## 验收

- 四处文档/ADR 落盘，术语与 CONTEXT.md 既有词汇无冲突（Runtime 已被
  Task Runtime State 等占用——Attention Map 不冠 Runtime 前缀）；
- SKILL.md 改动仅限 §2 追加纪律，现有硬性契约文字零删改；
- ADR 编号 0012（现有最大 0011），格式与既有 ADR 一致；
- MOD_TEMPLATE.md 的新章节说明与 02 实现的校验行为逐条一致（无文档-代码漂移）。

## Answer

Status: resolved（2026-08-30，主导 Agent 验收通过；ticket 01/02 完成后撰写）

### 交付（4 处）
1. `table-fill/references/MOD_TEMPLATE.md` — 文件结构图更新为 Purpose→Metadata→Applicability→业务逻辑摘要→Runtime Core（可选，推荐）→Attention Map（可选，推荐）→业务场景上下文→规则表→Export Field Policy（可选，位于规则表后）；新增 Runtime Core 小节（用途/guidance 3-6条 150-300字/边界不进提名卡/机器校验仅存在则非空）与 Attention Map 小节（格式、四组闭集、跨组允许组内禁重、否定定义原文、capture 硬校验 8+1 条逐条对应实现）；规则变更治理适用范围句补充三新章节；新增 Display Name 保留的 State boundaries 条目。
2. `table-fill/SKILL.md` — §2「规则注入时机」块后纯追加 5 行撰写纪律（全量加载契约不变 / Runtime Core 先建心智模型 / Attention Map 按 resolve→map→transform→validate 认知顺序）；零删改（diff 核验：唯一新增 hunk，删除行全属既有工作区改动）。
3. `CONTEXT.md` — 新增 Runtime Core / Attention Map / Self-Contained MOD 三词条（ticket 原文，_Avoid_ 风格一致；无术语冲突，Attention Map 未冠 Runtime 前缀）。
4. `docs/adr/0013-table-fill-mod-attention-map.md` — 新增 ADR。**编号偏差**：票面假定"现有最大 0011"已过期（0012-first-draft-convergence 已存在），实际编号 0013，偏差已注释；9 条 Decision、Considered Options、Revisit triggers、Consequences 与票面一致。

### 验收证据（主导 Agent 复核）
- 文档-代码一致性：MOD_TEMPLATE 的 capture 硬校验清单与 `_validate_attention_metadata`/`parse_attention_map_lines` 逐条核对一致（含 malformed 行号、聚合 exit 3、legacy 早退、Runtime Core 非空）。
- 测试：`test_optimization.py -k "SKILL or contract or TOOL_TRAPS or glossary or MOD_TEMPLATE"` → 140 passed；mod 相关 4 文件 → 118 passed。
- git：scripts/、tests/、references/ MOD 文件零改动；MOD_TEMPLATE 为本次唯一编辑文本文件（CONTEXT/docs/adr 为 untracked 基线）。
