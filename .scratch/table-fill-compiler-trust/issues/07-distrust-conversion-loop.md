# 07 — 不信任事件转换纪律 + 埃及案例复盘沉淀

Status: resolved
Type: task
Blocked by: 01, 03, 05

## Comments

- 2026-08-13: 阻塞项 01/03/05 已落地 (commits 8f774f9 / 802121c / be8dbbe),
  本 ticket 已解封。注意 SKILL.md 与 04 有编辑交集, 需在 04 (已落地) 的
  现状上增量编辑。
- 2026-08-13: 已实施 (与本 feature 02-06 整合产物统一提交)。SKILL.md 新增
  「不信任事件转换纪律」小节 (四类触发条件 + 契约漂移为最高优先触发条件 +
  三件套 + 产出物 + 埃及反例成本); KNOWN_TRAPS「已 spike 确认的机械事实」
  新增裸行占位 → append-only 终态行; 本 spec Comments 记录复盘基线
  (run_timing 分解 + XML 勘察相位事实修正); DocCoverageGuard +2。
- 2026-08-13: 执行中发现本仓库工作树缺少 03/04/05 交付物 (digest 样式粒度
  事实 / 布局决策树 + MOD 两段加载 / 显式范围契约 Q&A + 模式), 经用户确认
  一并补齐 — 03: prepare_run.collect_style_granularity + digest「占位行样式」
  结论行 + manifest.style_granularity (不入指纹); 04: FILLSPEC「布局决策树」
  章节 (样式第一判定, 三分支 + 缺陷码对照) + SKILL.md MOD 两段加载改写;
  05: FILLSPEC Q13 显式范围接受边界 + group_aggregates 重编号 Q14 +
  combination_patterns per_group_total_explicit_ranges + 最小变异测试 +
  capabilities 探针 (per_group_total_explicit_ranges)。07 的 KNOWN_TRAPS
  条目与真实产物一致 (不再指向不存在的 digest 事实)。

## 问题

Agent 不信任事件目前是逐例修复, 缺乏制度化转换: 每次 Agent 对执行机制怀疑
超过 ~1 分钟, 事后应强制产出三件套 (编译器检查 + 契约 Q&A + contract test),
把"修复 Agent"变成"修复 skill"。埃及案例 (机器 63s + Agent ~650s) 是首个
完整复盘样本, 三个烧脑点已分别立项 (01/03/05)。

## 修复

1. SKILL.md 增加「不信任事件转换」小节: 触发条件 (Agent 怀疑执行机制
   >1 分钟 / 手工模拟行位移 / 读源码确认执行行为 / 契约结论与实测不符),
   转换动作三件套, 产出物 (缺陷码 + 契约条目 + 回归测试)。契约漂移
   (issue 05 类) 单独列为最高优先触发条件。
2. KNOWN_TRAPS 沉淀埃及案例机械事实: remove_rows 与 add 区不变量
   (issue 01)、裸行占位 → append-only 终态 (issue 03/04)、每组合计显式
   范围触发条件 (issue 05)、组聚合落点结论 (issue 06 完成后补)。
3. 复盘数据归档: run_timing 分解 (机器 63s / mod_resolution 370s = 3 digest
   + 3 CSV + 625 行提名输出 + 用户裁决墙钟 / spec_authoring 166s /
   execute_review 55s / gate_wait 59s) 写入本 feature 目录 Comments,
   作为后续优化的基线。注: XML 勘察发生在 spec_authoring, 不在
   mod_resolution (测试侧事实修正)。

## 验收

- SKILL.md 转换小节存在, 契约漂移为最高优先触发条件;
- KNOWN_TRAPS 新增条目与三个工作流的产物对应;
- 本 spec 的 Comments 记录复盘基线 (含事实修正)。
