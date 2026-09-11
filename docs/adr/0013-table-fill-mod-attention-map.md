<!-- 编号偏差说明: 票面 03 假定"ADR 0012(现有最大 0011)", 但
     docs/adr/0012-table-fill-first-draft-convergence.md 已存在, 故本 ADR 编号为
     0013。除编号外, 内容与票面方案一致。 -->

# 0013-table-fill-mod-attention-map

# MOD Runtime Core + Attention Map in Table Fill

## Status

Accepted on 2026-08-25 following a grilling session with the `grilling` and
`domain-modeling` skills. This ADR authorizes specification and planning for the
Table Fill MOD Runtime Core + Attention Map V1 (spec in
`.scratch/table-fill-mod-attention/spec.md`); Tickets 01–04 implement it.

## Context

After a MOD is selected, its 32–38 business rules enter the FillSpec authoring
context as one flat, equal-weight six-column rule table. `Group` and `Gate` are
governance dimensions; they do not answer the question the agent actually needs:
"which rule do I need to recall while making which decision". The readability
problem is presentation (rules lack priority and cognitive structure), not rule
count.

The parameter-sheet MOD additionally carried its business-knowledge authority in
Notes pointing at another scenario-specific legacy skill
(`tcl-customer-parameter-sheet` field-scope / template-families /
validate_mapping.py) — a knowledge-dependency boundary: the MOD was a thin
adapter to a legacy skill, not business-knowledge self-contained.

The runtime has no phases: the pipeline is MOD adjudication → full rule
injection → one-shot `fill_spec.yaml` authoring. Rule injection is the agent
reading the MOD markdown, so the file's information architecture *is* the
runtime information architecture. There is no token measurement pipeline and no
historical baseline (benchmark report §12/§13), and full rules remain loaded, so
token cost only grows slightly under this design.

## Decision

1. **Rules continue to be fully loaded before FillSpec authoring**（全量加载
   契约不变）: SKILL.md §2 的"候选规则必须加载后才可写 spec"硬性要求不变, V1
   不改变加载的时机与粒度语义（两段加载: 提名只给摘要, 裁决后才加载选中 MOD
   完整规则）。
2. **Runtime Core is post-selection only**（不进提名卡）: MOD 新章节
   `## Runtime Core`（位于 业务逻辑摘要 之后、Attention Map 之前, 3~6 条 /
   150~300 字的执行心智模型）仅在 MOD 被选中/确认后与完整选中 MOD 内容一起
   加载; 提名卡仍只用 Applicability + 业务逻辑摘要, `mod_nominate.py` 零改动,
   新段不进 `mod_resolution.json`。
3. **Attention Map is attention metadata, not execution phases**: 新章节
   `## Attention Map` 是呈现与撰写辅助元数据（阅读顺序），不是执行阶段图，
   也不是规则投递机制——spec 级否定定义:
   > Attention Map is presentation and authoring metadata, not an execution
   > phase map and not a rule-delivery mechanism. All selected MOD rules remain
   > required to be loaded before FillSpec authoring.
4. **Attention Map has four closed groups**: `resolve / map / transform /
   validate`——authoring concerns / attention groups, 不是流水线 phase; 允许
   子集, 相对顺序固定（resolve → map → transform → validate）; 一条 Rule 可
   跨组重复（合法）, 组内禁重。
5. **No staged rule delivery in V1**: 不发明 `load_rules_for_stage()`; Runtime
   无阶段相位, staged delivery 会改动"先加载后写 spec"硬契约, 且无证据表明
   "分组后的全量"仍干扰业务理解。
6. **MODs are business-knowledge self-contained**: Selected MODs MUST be
   business-knowledge self-contained — they may depend on table-fill generic
   execution contracts and utilities, but MUST NOT require another
   scenario-specific Skill as an authoritative source of business rules or
   policy. 自包含 ≠ 删除 legacy 脚本: 只要 MOD 不再把
   `validate_mapping.py` 等当业务知识权威源、缺少它不会让 Agent 不知道规则,
   可暂留; 其机器校验是否迁入 table-fill 属 V2 评估。
7. **No TCL Domain Policy Pack in V1/V1.1**: 不引入三层知识体系组合层; 组合
   层增加加载概念, 4 MOD / 136 规则体量下无漂移事故证据, 自包含优先。
8. **Cross-MOD duplication is currently accepted**: 重复规则（如 Z Code exact
   match 出现在多个 MOD）**允许**, 靠 Notes 交叉引用 + 人工编辑纪律维护;
   不引入 Domain Pack 组合层, 不加跨 MOD 一致性机器检查。
9. **Machine enforcement is evidence-triggered V2 work**: 不做机器候选字段过滤
   （GLOBAL_DENY 前置过滤）与跨 MOD 一致性机器检查; 即使 V1 GO, 也只有当真实
   任务反复显示"Agent 理解原则但稳定违反某一确定性规则"时, 才把该类规则机
   器化（spec §7.4: V2 不自动启动）。

**Revisit triggers**（当前不满足, 不得仅因下列现象出现就重启）:

- **Domain Policy Pack**: 仅当跨 MOD 规则漂移造成**真实业务事故**（错误
  FillSpec/输出），或重复规则维护明显成为**持续负担**时重启——**"发现重复"
  本身不是重启条件**;
- **Staged rule delivery**: 仅当真实 holdout 观察证明"分组后的全量"仍**稳定**
  造成业务理解错误（spec §7.4 的 Stop 条件触发）时重启。

## Considered Options

- **Staged rule delivery（机器分阶段投递）** — rejected (V1): Runtime 无阶段
  相位; 须改动"先加载后写 spec"硬契约; 无证据表明"分组后的全量"仍干扰。
- **TCL Domain Policy Packs（三层知识体系）** — shelved, 见上文 Revisit
  triggers: 4 MOD / 136 规则体量下无漂移事故证据; 组合层增加加载概念;
  自包含优先。
- **Benchmark harness / 污染案例 A/B** — rejected: benchmark 报告已建议"本轮
  不实现"; 08-22 案例已被判 Contaminated, 只能作失败模式来源, 不能作效果
  对照组, A/B 无对照效力。
- **机器候选字段过滤（GLOBAL_DENY 前置过滤）** — deferred to V2
  （evidence-triggered）: 属执行架构改动, V1 只动信息架构。
- **Token 降幅指标** — rejected: token 无测量管道且本设计下全量规则仍加载,
  token 只会略增; V1 目标是提高同一上下文中业务知识的**可消化性**, 不是减量。

## Consequences

- 接受有限重复与人工一致性维护: 跨 MOD 重复规则靠 Notes 交叉引用 + 人工编辑
  纪律; V1/V1.1 不做 Domain Pack 与跨 MOD 一致性机器检查。
- V1 token 略增可接受（目标是可消化性，不是减量）; 不以 token 数量作为 V1
  指标, 也不压缩规则数量。
- Attention Map 是唯一机器检查对象（capture 时硬校验, 全部失败 exit 3）;
  运行时无 read-gate——与 v2.5 每一行为规则的信任模型一致（gates 抓错误,
  不抓恶意）。
- 不含新章节的 legacy MOD 行为完全不变（create/update/nominate/load 的合法性
  与用户可观察行为保持不变, 兼容性由 ticket 02 测试锁定）;
  运行路径（nomination、两段加载、FillSpec、compile、Execution Gate）零改动。