# 04 — test_optimization.py：路由术语一致性断言

**What to build:** 在 `table-fill/tests/test_optimization.py` 既有文档关键词断言面（test_skill_md_capability_resolution_trigger 等，5089 行起）新增一致性测试：断言 SKILL.md 含 Task Shape Check / task_shape / form_content / officecli_native / grid_record 等路由术语；断言 CAPABILITY_EVIDENCE.md 含矩阵术语（SUPPORTED / NOT_APPLICABLE / Task Shape / Executor）；防漂移断言——form_content 不得与 Known Rejected 或 UNSUPPORTED 绑定表述。零运行时成本，防未来误删/误改回。

**Blocked by:** 01, 02 — 断言对象由这两票引入。

**Status:** resolved

## Agent Brief

**Category:** test（文档契约一致性；不新增测试基建，沿用既有 grep 断言面）

**Authoritative context:** 以父 Spec「Table Fill — Task Shape Routing」D5 为准。既有断言模式：`test_optimization.py` 通过读取 SKILL_ROOT 下文档、断言关键词存在性来防误删（见 test_skill_md_capability_resolution_trigger / test_capability_evidence_reference_owns_policy）。

### Current behavior

- 现有文档测试覆盖三态、Probe/Rescue、Standard Evidence Paths 等术语，未覆盖路由层术语。
- 无防漂移断言：未来若有人把 form_content 改写回 Known Rejected / UNSUPPORTED，测试不会变红。

### Desired behavior

- 新增 1–2 个测试方法：
  1. SKILL.md 断言：含 "Task Shape Check"、"grid_record"、"form_content"、"officecli_native"（或 ticket 01 最终落定的等价术语）、"task_shape"、"uncertain"；
  2. CAPABILITY_EVIDENCE.md 断言：含 "NOT_APPLICABLE"、"Executor"、"SUPPORTED"、矩阵行语义；并断言文档中 form_content 出现的上下文不含 "Known Rejected"/"UNSUPPORTED"（防措辞回退；如实现复杂，最低要求为断言 "NOT_APPLICABLE" 存在 + "form_content is NOT Known Rejected" 类声明句存在）。
- 沿用既有 fixture/读取方式，不新建测试基建、不引入第三方断言库。

### Acceptance criteria

- `pytest table-fill/tests/test_optimization.py` 全绿（含新增）；
- 删除 SKILL.md 中 "Task Shape Check" 或把矩阵改写成 UNSUPPORTED 时，对应测试变红（可用最小变异手工验证一次）；
- 未改动 scripts/ 下任何文件。

### Comments

**交付说明（issue 04）**：在 `table-fill/tests/test_optimization.py` 的
`DocCoverageGuardTests` 类内新增 3 个路由术语一致性断言（零运行时成本，纯文本
读取 + `assertIn`，沿用既有 `_skill_md_text` / `_capability_evidence_text` 读取方式）：

1. `test_skill_md_task_shape_routing_terms` — 断言 SKILL.md 含 "Task Shape Check"、
   "grid_record"、"form_content"、"officecli native"、"task_shape"、"uncertain"、
   "NOT_APPLICABLE"、"task_shape.json"（"officecli native" 为 SKILL.md 实际落定写法，
   与 CAPABILITY_EVIDENCE.md 矩阵的 `officecli_native` 下划线写法分开断言）。
2. `test_capability_evidence_task_shape_matrix_terms` — 断言 CAPABILITY_EVIDENCE.md
   含 "Task Shape"、"SUPPORTED"、"NOT_APPLICABLE"、"Executor"、"fillspec"、
   "officecli_native"；并断言 form_content 矩阵行同时携带 NOT_APPLICABLE 与
   officecli_native。
3. `test_capability_evidence_form_content_not_unsupported_drift_guard` — 防措辞回退：
   form_content 矩阵行作用域内 `assertNotIn("UNSUPPORTED")` / `assertNotIn("Known
   Rejected")`（限定矩阵行而非全文，因 §0.3 纪律句本身含这两个反例词）；并断言
   文档存在措辞纪律声明（NOT_APPLICABLE + "≠" + UNSUPPORTED + Known Rejected 共存）。

**变异验证**：
- 变异 A（临时把 SKILL.md 中 "Task Shape Check" 替换为占位词）→
  `test_skill_md_task_shape_routing_terms` 变红（AssertionError: 'Task Shape Check'
  not found），随即还原。
- 变异 B（临时把 CAPABILITY_EVIDENCE.md 矩阵行 form_content 的 NOT_APPLICABLE 改
  成 UNSUPPORTED）→ `test_capability_evidence_form_content_not_unsupported_drift_guard`
  与 `test_capability_evidence_task_shape_matrix_terms` 双双变红，随即还原。
- 还原后确认 SKILL.md / CAPABILITY_EVIDENCE.md 锚定词（`### 1.5 Task Shape Check`、
  `form_content    SUPPORTED            NOT_APPLICABLE      officecli_native`）恢复原状；
  最终改动仅落在 test_optimization.py 与本票文件，未触碰 scripts/ 与两份文档。

**全量测试**：`python -m pytest table-fill/tests/test_optimization.py -q` → 342 passed
（含既有全部测试 + 新增 3 个）。
