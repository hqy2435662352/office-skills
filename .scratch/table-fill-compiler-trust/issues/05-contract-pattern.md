# 05 — 契约一致性修正: per_group 显式范围聚合的触发条件 + 复制即用模式

Status: resolved
Type: task
Blocked by:

## Comments

- 2026-08-13: 已落地 (commit be8dbbe + 9091990)。查明触发条件: 聚合列进 nulls
  → 锚点格先清空后写公式 → DUPLICATE_TARGET_WRITE (特征 "first as empty"),
  zero compiler change; FILLSPEC 新增 Q13 (行 370-404) + 能力映射表改写;
  combination_patterns.yaml 新增 per_group_total_explicit_ranges 复制即用模式
  (行 114-146); 最小变异实验测试入库 (test_per_group_total_trigger_minimal_mutation)。

## 问题

capabilities 矩阵声称 `per_group_total_hardcoded_ranges` → DUPLICATE_TARGET_WRITE,
但实际同形 spec (单块 + 显式范围聚合) **编译、执行、readback 627/627 全部通过**
(埃及案例最终方案)。被拒 fixture 里存在另一个未文档化的触发因素 — 大概率是
聚合列进了 nulls 导致锚点双写 ("first as empty")。契约与实际行为漂移迫使
Agent 走 3 次 probe + 2 次完整编译去重新发现真相。

## 修复 (零编译器改动)

1. **查明触发条件**: 对 capabilities 矩阵的 `per_group_total_hardcoded_ranges`
   fixture 做最小变异实验 (probe 面), 定位被拒的确切因素 — 假设: 聚合列进
   nulls → 锚点格先清空后写公式 → DUPLICATE_TARGET_WRITE; 验证"同形 spec
   去掉该因素即通过"。
2. **契约文档化**: FILLSPEC 组合行为契约 Q&A 增加条目: 单块多显式范围聚合
   (每组合计写法) 的接受边界 — 聚合列不进 nulls、不与 group_merges 同列、
   范围不越块 → 编译通过; 违反任一 → 对应缺陷码。fixture 注释同步写明触发
   因素。
3. **复制即用模式**: combination_patterns.yaml 增加「每组合计 (单块 +
   显式范围聚合, 含 V/W 同块写法)」片段 — 埃及案例最终通过形态的脱敏模板
   (改列名即可)。
4. probe 验证: 模式片段改列名后直接编译通过 (验证"零 probe 起步"承诺)。
5. 契约一致性回归断言: capabilities 矩阵输出 == 实际编译结果 (fixture 漂移
   类问题以测试闭环)。

## 验收

- 被拒 fixture 的触发条件查明并写入 FILLSPEC Q&A + fixture 注释;
- 埃及同形通过形态有文档背书与 contract test;
- 组合模式片段复制后改列名即可编译 (probe 实证);
- capabilities 矩阵的该条目与同形 spec 实际行为一致 (回归断言);
- 编译器行为零改动 (本 ticket 不改 compile_fill.py)。
