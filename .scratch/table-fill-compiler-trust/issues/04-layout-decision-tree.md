# 04 — 布局决策树 (以样式为条件) + MOD 规则加载输出形态优化

Status: resolved
Type: task
Blocked by:

## Comments

- 2026-08-13: 已落地 (commit 8f774f9, 与 01 合并提交)。FILLSPEC 新增
  「布局决策树 (以样式为第一判定条件)」章节 (行 500+), 三分支 + 违规形态→
  缺陷码对照; SKILL.md MOD 段改写为两段加载 (提名给摘要, 裁决后加载选中
  MOD 全文), 硬性要求保留。 03

## 问题

布局决策必须以**占位行样式**为第一判定条件, 而非"占位块存在与否" —
带样式 → inplace 才成立; 裸行 → clone-append (克隆携带格式)。按占位块
存在性推荐 inplace 会把 Agent 引入错误路径 (埃及案例实证)。

mod_resolution 相位 370s 的真实构成 (测试侧事实修正): 读 3 digest +
3 展平 CSV + 625 行提名输出 (两候选全量规则集) + 用户裁决墙钟 —
XML 勘察发生在 spec_authoring, 不在 mod_resolution。真正可优化的是
MOD 规则全量加载的**输出形态**, 不是相位纪律。

## 修复

1. FILLSPEC 新增「布局决策树」小节, **以样式为第一判定条件**:
   - digest 报「占位行带样式」→ mode: inplace 成立 (占位区消费);
   - digest 报「占位行裸行」→ clone-append (克隆携带格式, 满足 VAL-007
     格式沿用), 占位行自然下沉保留 (append-only 合法终态, 不写 remove_rows);
   - 模板既有块收缩 (源行数 < 模板行数) → append + remove_rows (≤ base,
     经典场景, 已被 issue 01 检查保护)。
2. 决策树各分支与既有缺陷码对应 (REMOVE_TARGETS_APPEND_ZONE /
   TEMPLATE_ROW_GAP / STRUCTURAL_OP_OUT_OF_ZONE), 文档与编译器裁决同源。
3. MOD 规则加载输出形态优化 (流程纪律, SKILL.md MOD Resolution 段):
   - 提名阶段输出: 候选名 + 命中/待复验信号 + 业务逻辑摘要 + 裁决选项 —
     **不含完整规则集**;
   - 用户裁决后, 才加载选中 MOD 的完整规则注入 FillSpec 撰写上下文;
   - 多候选 (ambiguous) 时裁决选项仍附带各候选的规则证据摘要,
     完整规则在选定后加载;
   - 硬性要求不变: 候选规则进入 spec 撰写上下文前必须已加载 —
     改变的是加载时机与粒度, 不是是否加载。

## 验收

- FILLSPEC 决策树存在, 样式条件在前, 三分支齐全;
- SKILL.md MOD 段含输出形态优化描述, 且"候选规则必须加载后才可写 spec"
  的硬性要求保留;
- DocCoverageGuard 系列测试延伸断言 (决策树关键字 + 样式条件在 FILLSPEC)。
