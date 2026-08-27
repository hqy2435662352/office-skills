# 03 — test_optimization.py：哨兵扩展（守架构不变量，不守每句文案）

**What to build:** 在 `table-fill/tests/test_optimization.py` DocCoverageGuardTests 扩展 V1 路由哨兵为 Routing V2 版，守住架构不变量：①shape 值域四值；②route 值域三值（含 `combined`）且路由语境禁止 `hybrid`；③`grid_record + officecli_native` 与 `mixed + combined` 为合法组合（防改回 1:1 绑定）；④能力语义表两行且 FillSpec Model 列为 `APPLICABLE`/`NOT_APPLICABLE`；⑤`Applicability ≠ Justification` 声明存在；⑥`obvious_grid` Fast Path 存在；⑦Combined Final Gate 顺序关键句存在。保持既有 grep/矩阵行作用域断言风格，不新增测试基建。

**Blocked by:** 01, 02 — 断言对象由这两票引入。

**Status:** resolved

## Agent Brief

**Category:** test（文档契约一致性；沿用既有 grep 断言面）

**Authoritative context:** 以父 Spec「Table Fill — Routing V2」R2-Q7 锁定为准：tests 守**架构不变量**，不逐句守文案（防哨兵变成 300 行关键词泥潭）。V1 三个路由哨兵（:5142-5190）在升级范围内。

### Current behavior

- V1 哨兵断言：SKILL.md 路由词（Task Shape Check/grid_record/form_content/officecli native/task_shape/uncertain/NOT_APPLICABLE/task_shape.json）、CAPABILITY_EVIDENCE 矩阵词（Task Shape/SUPPORTED/NOT_APPLICABLE/Executor/fillspec/officecli_native + form_content 行作用域断言）、form_content 防措辞回退（矩阵行作用域 assertNotIn UNSUPPORTED/Known Rejected）。
- 无 combined/hybrid/mixed/direct/APPLICABLE 的守卫。

### Desired behavior

- 更新或新增测试方法（保持行作用域负断言技巧，防纪律句自伤）：
  - SKILL.md：Task Shape Check、`obvious_grid`、`officecli_native`、`combined`、`mixed`、`bounded_explicit_edit`（或 Direct 双必要条件的锚点句）、`task_shape.json`；
  - CAPABILITY_EVIDENCE.md：能力表两行语义（`APPLICABLE` 与 `NOT_APPLICABLE` 同在）、路由表四行关键对（`fillspec`/`officecli_native`/`combined`）、`Applicability` 与 `Justification` 区分声明、`obvious_grid` 词表行；
  - 防回退：SKILL.md 与 CAPABILITY_EVIDENCE.md 路由语境不得出现 `hybrid` 作为 route（FILLSPEC 的 hybrid overflow 不在此断言范围——只扫两个被改文件的路由段/全文中与 route 相关的行，实现时用最小作用域避免误伤）；能力表 grid 行不得再写 FillSpec Engine=SUPPORTED 的旧式绑定；
- 变异自检（手工验证一次后还原）：删 `obvious_grid`、矩阵改回 SUPPORTED、`combined` 改回 `hybrid`、删 Direct 锚点句 → 对应断言变红。

### Acceptance criteria

- `pytest table-fill/tests/test_optimization.py` 全绿（含新增与 V1 存量）；
- 变异自检记录写入本票 `## Comments`；
- 未改动 scripts/ 与 FILLSPEC.md。

### Comments

- **变异自检：4/4 项变红→还原确认**（2026-02-21，均由本票实施者手工执行：改 → 跑目标单测确认 FAILED → 按字节还原 → 再跑确认 passed；两文件 SHA256 与变异前逐字节一致）：
  1. SKILL.md 全文件 `obvious_grid` → `obviousXgrid`（Fast Path evidence 删除模拟）→ `test_skill_md_obvious_grid_fast_path` 红 → 还原绿 ✅
  2. CAPABILITY_EVIDENCE.md 0.1 能力表 grid 行 FillSpec Model 列 `APPLICABLE` 改回 `SUPPORTED` → `test_capability_evidence_task_shape_matrix_terms` 红 → 还原绿 ✅
  3. SKILL.md §1.5 route 值域表 mixed 行 route 单元格 `combined` 改回 `hybrid` → `test_skill_md_routing_route_domain_three_values_no_hybrid` 红 → 还原绿 ✅
  4. SKILL.md §1.5 路由分流 ASCII 块删除 Direct 锚点组合句（`grid_record + officecli_native`）→ `test_skill_md_routing_legal_shape_route_combinations` 红 → 还原绿 ✅
- 终态：`pytest table-fill/tests/test_optimization.py -q` = 349 passed, 0 failed；仅 `table-fill/tests/test_optimization.py` 为本票持久改动文件（git 范围证明见实施报告）。

**验收记录（resolved，主 agent 逐票验收）— 验收通过。**

- **测试（主 agent 独立复跑）**：`pytest table-fill/tests/test_optimization.py -q` → **349 passed**（执行前 340 passed + 2 failed —— 两个 V1 旧矩阵哨兵已由本票重写为 V2 两表断言，V1 存量 340 全绿无回归，净增 9 方法）。
- **7 条不变量 → 哨兵映射**（抽查代码确认实现质量）：① shape 四值 → `test_skill_md_routing_shape_domain_four_values`（值域表行作用域）；② route 三值禁 hybrid → `test_skill_md_routing_route_domain_three_values_no_hybrid` + `test_capability_evidence_route_domain_three_values_no_hybrid`（CE 侧只扫 0.2 路由表行、显式跳过 0.6 消歧句并反向断言消歧句存在，防删句过关）；③ 合法组合防 1:1 → `test_skill_md_routing_legal_shape_route_combinations`（ASCII 块行作用域）；④ 能力表两行 APPLICABLE/NOT_APPLICABLE → `test_capability_evidence_task_shape_matrix_terms`（重写；行数=3 表头+两行锁定、mixed 不占行、§0 无 Executor 列名、能力表行不绑路由词）；⑤ Applicability≠Justification → `test_capability_evidence_applicability_not_justification`；⑥ obvious_grid Fast Path → `test_skill_md_obvious_grid_fast_path`；⑦ Combined Final Gate 顺序 → `test_skill_md_combined_final_gate_order`（assertLess 保序 + 顺序句）。辅助方法 `_skill_routing_section` / `_capability_evidence_section0` / `_table_rows_after` 沿用既有 grep 风格、零新增基建。
- **旧职责保留**：form_content 防措辞回退负断言从旧矩阵单行扩展为 §0 内全部 form_content 语境行（0.1/0.2/0.6 行作用域，已核实无反例词同现自伤）。
- **变异自检 4/4（本票 Comments 有实施者记录，主 agent 复核无残留）**：删 obvious_grid / APPLICABLE 改回 SUPPORTED / combined 改回 hybrid / 删 Direct 锚点句 → 对应哨兵变红 → 逐字节还原后变绿；SKILL.md 与 CAPABILITY_EVIDENCE.md 变异前后 SHA256 一致（另有实施者自纠：初版变异用子串替换致假绿，改用非子串后确认真红）。
- **范围**：仅 `table-fill/tests/test_optimization.py` 持久改动；FILLSPEC.md / scripts/ / SKILL.md / CAPABILITY_EVIDENCE.md 零持久触碰；未 commit、未新增脚本/依赖。
- **结论**：验收通过，Status 置 resolved；spec AC4 哨兵变异条件满足。
