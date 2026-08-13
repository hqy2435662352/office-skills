# 01 — Compiler 拦截 append 块 remove_rows 越界 (REMOVE_TARGETS_APPEND_ZONE)

Status: resolved
Type: task
Blocked by:

## Comments

- 2026-08-13: 已落地 (commit 8f774f9, 与 04 合并提交)。compile_fill.py 静态验证段
  新增 REMOVE_TARGETS_APPEND_ZONE (行 442-457), corrective_action 为
  append-only 语义; contract tests 并入 test_optimization.py (+166 行含 01/04
  测试)。全量 152 测试绿。

## 问题

append 块的 `remove_rows` 声明 > base_last_row 时, 编译器接受 spec 并产出自毁
plan: 先 add (插入 base_last_row 之下) 推移行号, 后 remove 用裸模板坐标删除 —
命中刚插入的新块。最终行数断言 (rows + adds − removes) 恒等, 抓不住。probe 实证
(2026-08-13): base=10 + remove_rows [12,13,14] + 3 数据行克隆 → COMPILED OK,
ops = add×4 → remove 14/13/12 (正是新数据行) → set 写被删行。埃及 11_FRESH本土
案例 Agent 为此手工模拟 30+ 次行位移。

## 修复

1. 静态验证段 (布局之后、ops 生成之前) 增加检查: 每个 append 块 (含隐式单块
   与 blocks[] 中每个非 inplace 块) 的 remove_rows 条目 > base_last_row →
   缺陷 `REMOVE_TARGETS_APPEND_ZONE`, 携带行号、块标签、拦截理由。
2. **corrective_action 语义 (测试侧修正)**: 首选「append-only 是合法终态 —
   占位行自然下沉保留, 无需删除」; 仅当占位行携带单元格样式时才提示
   mode: inplace 为条件选项 (样式事实来自 issue 03 的 digest 样式粒度)。
   **禁止无条件指向 inplace** — 埃及案例占位行是裸行, inplace 填入即
   无边框块, 违反 VAL-007 格式沿用。
3. inplace 块的 remove_rows 不在此检查范围 (inplace 消费编译器推导的 Trim)。
4. FILLSPEC 速查表加一行该错误码 (含 append-only 语义)。

## 验收

- 埃及等价 fixture: append 克隆 + 越界 remove_rows → exit 3 + 缺陷码;
- remove_rows ≤ base_last_row 经典场景 → 编译通过, plan 不变;
- blocks[] 多块: 任一块越界 → 拒绝; 全合法 → 通过;
- contract test 落入既有契约测试面 (test_optimization.py), 不新增测试基建;
- stderr 缺陷清单含行号与理由。
