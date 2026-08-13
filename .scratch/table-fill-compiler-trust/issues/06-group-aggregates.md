# 06 — group_aggregates 一等化 (契约面稳定后实施, 含 whole_run spike 门)

Status: resolved
Type: task
Blocked by: 05

## Comments

- 2026-08-13: 首次执行会话在 Wave 2 遇 git 冲突 (FILLSPEC.md/compile_fill.py
  与 01+04 轨道并发编辑), 被指示忽略后放弃 — **工作未落地, 无任何痕迹**
  (master 无 group_aggregates)。重新执行时与 02 串行, 且建议在 02 合并后再
  动 compile_fill.py (同文件, 避免 rebase)。
- 2026-08-13 已实施 (commit 待定):
  - compile_fill.py: split_group_aggregates 归一化 (list / {per_group, whole_run}
    两形态) + GROUP_AGGREGATES_INVALID; _emit_block_ops 相位 6 lowering
    (compute_groups 分组 → 组锚点行公式 → nonempty readback); whole_run 门
    (CAPABILITY_NOT_ROLLED_OUT, 多块去重); pptx 门; 10 契约测试 + capabilities
    28/28; FILLSPEC Q13/能力映射表/速查表、KNOWN_TRAPS、SKILL.md、模式文件。
- 2026-08-13 **整合落盘 (协调者执行)**: 02+06 增量并入 active skill 仓库,
  与 05 已落地的 Q13 (显式范围接受边界) 语义合并 — 组聚合小节定为 **Q14**,
  能力映射表「动态组边界」行改为一等 (group_aggregates) 并保留 blocks[]
  变通; 速查表新增 GROUP_AGGREGATES_INVALID / CAPABILITY_NOT_ROLLED_OUT;
  组合模式新增 per_group_total_group_aggregates; 契约测试适配合并形态
  (doc-guard 由 Q13 改 Q14)。全量 180 测试通过, capabilities 矩阵含 7 个
  新探针全部按预期裁决。**会话侧死锁解除, 整合产物待用户统一 commit。**

## 问题

每组合计是报价域每运行必现的组合, 但声明式表达缺失 — 目前靠 issue 05 文档化
的"单块 + 显式范围"变通写法。一等化的正确终态是 group_aggregates, 但它引入
新的锚点/合并交互面, 风险等级高于契约修正, 故殿后实施。

## 修复

1. schema: `mapping.targets[].formulas.group_aggregates`:
   ```yaml
   group_aggregates:
     - group_by: A        # 物化值分组列 (必须有列映射)
       col: V             # 聚合落点列 (组锚点行)
       formula: "IFERROR(ROUND(SUM(T{r1}:T{r2})/SUM(S{r1}:S{r2}),4),0)"
       style: anchor
   ```
2. lowering (复用 compute_groups): 物化 group_by 值 → 连续同值段分组 →
   公式展开到组锚点行 → register nonempty readback; {r1}:{r2} 按组起止展开,
   静态校验不得越块 (AGG_RANGE_INVALID)。
3. 冲突语义 (一格一 owner): 与 group_merges 同列 / 与 per_row 同列 →
   DUPLICATE_TARGET_WRITE; 组聚合列不进 nulls (与 issue 05 查明的触发
   条件一致)。
4. whole_run 跨块总计: 落点语义 (末块尾部 vs 独立行) 需一次 spike
   (独立 scratch, staged 只读) 锁定; spike 前声明 → CAPABILITY_NOT_ROLLED_OUT
   拒绝, spike 后按锁定语义实现并解锁。spike 结论写入 KNOWN_TRAPS。
5. 能力映射表「每组合计」行改为"一等"; capabilities 矩阵 / _probe_fixtures
   增加新条目 (文档、测试、运行时报告三同源)。

## 验收

- 埃及等价 fixture (3 产品组, V 列组聚合): 编译通过, 聚合公式落各组锚点行;
- readback 自动登记各锚点 nonempty;
- 与 group_merges 同列 / per_row 同列 / 聚合列进 nulls → 对应缺陷码拒绝;
- 范围越块 → AGG_RANGE_INVALID;
- whole_run spike 前被结构化拒绝, spike 后编译通过 + contract test;
- CapabilityMappingContractTests 面新增断言 (契约矩阵 + capabilities 同源)。
