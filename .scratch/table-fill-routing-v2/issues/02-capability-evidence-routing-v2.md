# 02 — CAPABILITY_EVIDENCE.md：两表重构 + evidence 词表 + Applicability≠Justification

**What to build:** 重构 `table-fill/references/CAPABILITY_EVIDENCE.md` §0：拆成三块——①能力语义表（两行：Grid workload → table-fill SUPPORTED / FillSpec Model APPLICABLE；Non-grid workload → SUPPORTED / NOT_APPLICABLE；注明 `mixed` 是两 workload 组合不占行）；②路由决策表（四行：obvious substantial Grid→fillspec；bounded explicit Grid 无实质收益→officecli_native；Non-grid→officecli_native；substantial Grid + separable Non-grid→combined）；③evidence vocabulary 小词表（七码 canonical 起步）。正式写入 **Applicability ≠ Justification** 定义。087 保留为第一条 evidence；Case C/D 标注"待真实案例 evidence"。列名 "FillSpec Engine" 改 "FillSpec Model"。

**Blocked by:** None — can start immediately.

**Status:** resolved

## Agent Brief

**Category:** documentation（能力语义清债；无代码行为变化）

**Authoritative context:** 以父 Spec「Table Fill — Routing V2」R2-Q5/Q6 锁定决策为准。维持 CAPABILITY_EVIDENCE.md 是能力求证的唯一详细 policy 源；第 1 节三态/Probe/Rescue 与第 0 节任务形态矩阵的"两套正交语义"关系保留。

### Current behavior

- §0 单矩阵三列（Task Shape / table-fill Product / FillSpec Engine / Executor），grid_record 行 FillSpec 列 = SUPPORTED，暗示 grid_record ≡ fillspec（:12-76）；无 Applicability/Justification 区分；无 evidence 词表。

### Desired behavior

- 能力语义表（基础 workload 两行）：
  ```text
  Workload semantics           table-fill Product   FillSpec Model
  Grid / record transformation SUPPORTED            APPLICABLE
  Non-grid Office operation    SUPPORTED            NOT_APPLICABLE
  ```
  `mixed` 不占行，注明 = Grid + Non-grid 的组合（FillSpec 对其天然无法单格表达）；
- 路由决策表（Workload situation → Route）四行，见 spec 四案例映射；
- 正式定义：**Applicability** = 该执行模型能否自然表达此 workload；**Justification** = 即使适用，本次是否值得启用完整 pipeline。二者不相等（三个固定 cell：APPLICABLE 但 NOT JUSTIFIED → officecli_native）；
- evidence vocabulary 表（spec「Evidence vocabulary」七码 + uncertain 三码可选），注明不封闭枚举、晋升条件（真实 benchmark 反复出现且有统计价值）；
- §0.2 保留 087 证据与"evidence ≠ 定义"；新增一句 Case C/D 待第一条真实 evidence；
- 措辞纪律段保留并扩展：`combined` ≠ `hybrid`（FILLSPEC "hybrid overflow" 是另一概念）；`APPLICABLE` ≠ `SUPPORTED`（前者回答模型适用性而非引擎承诺）。

### Acceptance criteria

- 两张表 + 词表可 grep；`grid_record = fillspec` 类 1:1 绑定表述不复存在；`hybrid` 不在路由语境出现；
- 第 1 节三态/Probe/Rescue 语义零改动；
- ticket 03 新增哨兵断言通过；diff 不触及 FILLSPEC.md 与 scripts/。

### Comments

**验收记录（resolved，主 agent 逐票验收）— 验收通过（含 2 个预期红移交给 03）。**

- **改动范围**：仅 `table-fill/references/CAPABILITY_EVIDENCE.md`（§0 重构为：0.1 能力语义表两行 / 0.2 路由决策表四行 / 0.3 Applicability≠Justification 定义 / 0.4 evidence vocabulary 七码+uncertain 三码 / 0.5 087 第一条 evidence（含 Direct/Combined 待真实案例标注）/ 0.6 措辞纪律扩展）。第 1-5 节零改动（git 单 hunk 证实：仅 §0 为 added 块，§1 起为 context）。FILLSPEC.md / scripts / SKILL.md / tests / glossary 零触碰。
- **grep 核对（主 agent 独立验证）**：`APPLICABLE`×12、`NOT_APPLICABLE`×9、`obvious_grid`×2、`combined`×8、`Applicability`×3、`Justification`×3、`FillSpec Model`×4；残留为 0：`FillSpec Engine`、`grid_record = fillspec` 类 1:1 绑定、旧列头组合；`hybrid` 仅出现于 :160-162 的消歧陈述（`combined` ≠ `hybrid`，指明 FILLSPEC "hybrid overflow" 是另一概念，引 FILLSPEC.md:853），路由语境不把 hybrid 当 route 名。
- **临时测试状态（预期，属票 03 范围）**：`pytest table-fill/tests/test_optimization.py` → **2 failed, 340 passed**。两个失败恰为断言旧单矩阵结构的 V1 哨兵：`test_capability_evidence_task_shape_matrix_terms`、`test_capability_evidence_form_content_not_unsupported_drift_guard` —— 由票 03 重写为 V2 两表断言；其余哨兵（含 §1 词断言）全部通过，佐证第 1 节语义零改动。
- **结论**：验收通过，Status 置 resolved。票 02 完成的验收链"ticket 03 新增哨兵断言通过"在 03 落地后终验补全。
