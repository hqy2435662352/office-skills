# 02 — FILLSPEC「执行顺序保证」契约 + plan 机械事实栏

Status: resolved
Type: task
Blocked by: 01

## Comments

- 2026-08-13: 首次执行会话在 Wave 2 遇 git 冲突 (FILLSPEC.md 与 04 轨道并发
  编辑), 被指示忽略后放弃 — **工作未落地, 无任何痕迹** (master 无「执行顺序
  保证」章节, compile_fill.py 无机械事实栏)。重新执行时与 06 串行, 避免再与
  其他轨道并发编辑 FILLSPEC.md。
- 2026-08-13 已实施 (commit 待定, 随本票提交):
  - FILLSPEC 新增「执行顺序保证 (Execution Order Contract)」章节, E1-E4 四条
    锁定声明 (op 顺序不变量 / remove 目标身份 / 自底向上理由 / 坐标翻译边界);
  - compile_fill.py 新增 `derive_mechanical_facts` (从契约派生, 非自由文本):
    execution_plan.json.mechanical_facts + mapping.md「执行机械事实」栏 — 含
    removes 与 add 区关系 (≤ base → 不被 add 推移)、锚点链依赖 (after/from
    引用行 + TEMPLATE_ROW_GAP 背书)、shift 结论 (inplace 区/trim/overflow);
  - tests: ExecutionOrderContractTests 8 用例 (E1-E4 每条件 + 混合形态顺序 +
    机械事实重算一致性 + mapping.md 渲染 + 锚点链 gap 背书), DocCoverageGuardTests
    +2 防章节误删;
  - KNOWN_TRAPS「已 spike 确认的机械事实」新增 remove/add 交互行 (埃及案例
    重放 oracle);
  - 代码审查 (两轴) 已执行: E1 全局顺序声明在 inplace 混合形态下不成立 →
    修正为两形态精确表述 (append-only: clear→add→remove→merge→fill; inplace
    混合: append 块全部操作→sets→inplace 结构→inplace 值写); add_zone 拆分
    append/overflow 插入行; trim 行生成提取共享 helper 去重。
  - 验证: test_optimization.py 164 通过 (含并发会话 ticket 06 group_aggregates
    用例); 全量 166 通过。
- 2026-08-13 **整合落盘 (协调者执行)**: 02+06 增量从
  `table-fill-v25-customer-quotation-candidate/` 并入 active skill 仓库
  (`~/.config/opencode/skills/table-fill/`), 与已落地的 01/03/04/05 语义合并
   (FILLSPEC Q14 并入、能力映射表/速查表/KNOWN_TRAPS/组合模式交叉更新)。
   全量 180 测试通过, capabilities 矩阵 28/28。**本票与 06 的会话侧死锁
   (互等对方先提交) 已解除 — 无需任何一方再提交, 整合产物待用户统一 commit。**
- 2026-08-13 提交状态: **暂未提交** — 工作区存在另一会话的并发改动 (ticket 06
  group_aggregates, 与 02 交织在相同文件), 用户决定等待对方会话先提交。

## 问题

Agent 的执行机制疑问 ("add 之后 remove 的目标是谁? 执行器会不会重排/翻译?")
没有 oracle — 答案只存在于源码, 导致读源码/手工模拟 (埃及案例 ~1/3 spec 时间
浪费在 remove_rows 顺序研究上)。issue 01 拦住了危险 spec, 但合法场景的
"为什么安全"仍需书面契约让 Agent 第一次 lookup 就命中。

## 修复

1. FILLSPEC 新增「执行顺序保证」Q&A 章节, 内容锁定 (每条件配 contract test):
   - op 全局顺序不变量: clear → add → remove → merge → fill, 理由
     (值写入穿插 add 破坏行簿记 → duplicate_row);
   - add 后 remove 的目标身份: remove_rows 是模板坐标, 不随 add 推移
     (issue 01 的检查保证两者不交互);
   - remove 底上序理由;
   - 坐标翻译边界: ops 用模板坐标, readback 用最终坐标 (inplace 场景)。
2. mapping.md / execution_plan 增加「机械事实」栏 (从契约派生, 非自由文本):
   本次 plan 的 removes 与 add 区关系、锚点链依赖、shift 结论。
3. 每条件在 test_optimization.py 契约面有断言 (文档声称与编译器行为 lockstep)。

## 验收

- FILLSPEC 章节存在且每条声明有对应 contract test;
- mapping.md 输出含机械事实栏 (对 append 运行: removes ≤ base 结论显式可见);
- 埃及案例重放: Agent 无需读源码即可回答 remove/add 交互问题。
