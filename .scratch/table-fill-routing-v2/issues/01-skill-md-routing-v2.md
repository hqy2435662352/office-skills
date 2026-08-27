# 01 — SKILL.md：Routing V2 流程（Level 0 / Exception Routing / Combined 契约 / 值域）

**What to build:** 重写 `table-fill/SKILL.md` §1.5 为 Routing V2：(a) 保留"Prepare B 后、MOD 前"时点与"任务指令 × 源 digest × 目标 digest"三输入；(b) 前置 **Obvious Grid Fast Path**——读毕 digest 即明显常规 Grid 时立即 `grid_record/fillspec`、evidence 固定 `["obvious_grid"]`、直接进原 MOD，**禁止继续 routing 分析**（0 新增动作验收线）；(c) 仅出现明确异常信号才进 Exception Routing：Direct（双必要条件 + "OfficeCLI batch 能否成为完整执行计划"自检句 + "明显便宜≠算出便宜"stop-rule）、Non-Grid（V1 form_content 保留）、Combined（最小契约：单一 Final Gate 在全部写完成后、ownership 仅 Agent 约束、仅"明显可分区"启用、Grid first 默认）；(d) 更新 task_shape.json 值域表（shape 四值 × route 三值 × 典型 evidence）与权威模型表行；(e) description 双路径句小改（不罗列案例特征词）。

**Blocked by:** None — can start immediately.

**Status:** resolved

## Agent Brief

**Category:** enhancement（Skill-level routing；无代码行为变化）

**Authoritative context:** 以父 Spec「Table Fill — Routing V2」R2-Q1…Q4 锁定决策为准。本票只改 SKILL.md 文案与工作流结构；V1 的 form_content 工作流段、uncertain 处理、task_shape.json 落盘要求全部保留并在其上升级，不推翻。

### Current behavior

- §1.5 是 V1 三态分流：grid_record→fillspec / form_content→officecli_native / uncertain 补观察，shape→route 1:1（:124-134）；form_content 工作流（:136-151）；authoritative model 表 task_shape.json 行（:73）写三字段但未锁值域。
- 无 Fast Path 显式概念（虽有"零额外探测"声明）；无 Direct、无 Combined 表达。

### Desired behavior

- §1.5 重写为 Level 0 + Exception Routing 结构（本 spec 路由架构图）；
- Level 0 措辞必须是 **Obvious Grid Fast Path**（不是"default-to-Grid fallback"），并写明可观测动作不变式验收线：0 新增 LLM 调用 / officecli 调用 / inspect / render / 脚本 / scoring / decomposition / feature extraction，不写 signal checklist、不长篇 reasoning；
- Direct 双必要条件与两个锚点句（"OfficeCLI batch 本身能不能成为完整执行计划"、"Direct 必须明显成立，若需复杂比较则停止路由优化，走 Grid 主路径或 uncertain"）；
- Combined 最小契约按 spec「Combined 最小契约」段：执行顺序 Grid first → officecli finishing（不得修改或失效 Grid-owned region/structural invariants）→ Unified QA → 单一 Final Gate → promote；注明 Grid 治理机制不变、Gate 时点延后为最终 draft 完成门；
- task_shape.json 值域：shape `grid_record/form_content/mixed/uncertain`，route `fillspec/officecli_native/combined`；evidence 短 snake_case code、最小充分证据；
- 四案例映射表与反例锚点（3 记录但需 lookup/clone/公式→Grid；200 行不值得讨论 Direct）入文；
- description 只做必要小改，不罗列 087 特征词；FILLSPEC/scripts 不动。

### Acceptance criteria

- §1.5 含 Fast Path、Direct、Combined、值域表、四案例；grid 主路径（MOD→FillSpec→compile→execute→Gate→promote）语义未被改动；
- 未新增任何脚本/命令；`hybrid` 一词不在路由语境出现；
- ticket 03 新增哨兵断言通过；diff 不触及 FILLSPEC.md 与 scripts/。

### Comments

**验收记录（resolved，主 agent 逐票验收）— 验收通过。**

- **实施授权**：用户目标指令（Routing V2 按 01→05 逐票派发 subagent 执行）即 spec 头部注记所等的"用户下令"，本票据此进入执行。
- **改动范围**：仅 `table-fill/SKILL.md`（frontmatter description 必要小改 + §1.5 整节重写为 Routing V2 + :74 权威模型表 task_shape.json 行值域更新）。FILLSPEC.md / scripts/ / CAPABILITY_EVIDENCE.md / tests / glossary / .scratch / docs 与执行前基线一致，零触碰（git status 对比确认）。
- **内容核对（对照票 Desired behavior 逐条）**：
  - Level 0（:152-164）：措辞为 **Obvious Grid Fast Path**（"默认主路径, 不是 fallback"），含 stop-rule「禁止继续 routing 分析」与可观测动作不变式验收线（0 新增 LLM 调用 / officecli 调用 / inspect / render / 脚本 / scoring / decomposition / feature extraction）；"不写 signal checklist、不分级打分、不长篇 reasoning"。
  - Direct（:171-181）：双必要条件 + 自检句「OfficeCLI batch 本身能不能成为完整执行计划」+ stop-rule 锚点句「Direct 必须明显成立; 若判断 Direct 需要复杂成本估算, 则停止路由优化, 走 Grid 主路径或 uncertain」+「明显便宜 ≠ 算出便宜」；single-cell 触发排除保留。
  - Non-Grid：V1 form_content 工作流逐字保留（:239-254），一等路径不是 fallback。
  - Combined 最小契约（:196-219）：Prepare → mixed decomposition → Grid 执行+readback/结构验证 → OfficeCLI finishing（不得修改/失效 Grid-owned region/structural invariants）→ Unified QA → 单一 Final Gate → promote → delivery；Gate 语义升级为最终 draft 完成门（preserve promote 哈希绑定）；ownership 仅 Agent 执行约束（不建 DSL/ownership 文件）；仅明显可分区启用，缠绕→uncertain；Grid first 默认非绝对；统一 QA。
  - 值域表（:140-145）：shape 四值 × route 三值 × 典型 evidence；`direct` 永不作为 shape；`combined` 明确"不是第三引擎"。
  - 四案例映射表（:221-232）与 spec 完全一致 + 反例锚点（3 records 需 lookup/clone/公式→Grid；200 行不值得讨论 Direct）。
- **措辞纪律**：SKILL.md 全文 `hybrid` 0 处；`combined` 全部指组合执行。
- **测试**：`pytest table-fill/tests/test_optimization.py` → **342 passed**（与 V1 基线同数，零回归）；subagent 全量 `pytest table-fill/tests/` 647 passed + 10 subtests（1 个既有 warning 与本票无关）。
- **结论**：验收通过，Status 置 resolved。ticket 03 新增哨兵断言在此文案之上由后续票扩展。
