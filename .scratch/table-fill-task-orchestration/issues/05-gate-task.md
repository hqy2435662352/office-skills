# 05 — gate_task（聚合呈现 + 逐 run 确认展开）

Status: resolved
Type: task
Blocked by: 01, 03

## Comments

- 2026-08-25: 落地于本仓库, commit `7bee65b` + 审查修复 `c34c750`（双轴
  code-review 处置见下）。
  - `scripts/task_gate.py`: 聚合 Gate 纯函数层（无 Office，可单测；
    spec Testing Decision #8 的 seam）——
    - summary：`timing_totals`（机器 + agent 双栏，SKILL.md §Observability
      契约）、`mod_summary`（MOD 裁决紧凑摘要）、`spec_mod`（fill_spec
      的 selected_mod）、`receipt_validation`（readback / 来源覆盖 /
      issue delta / structural / render_qa / validate 摘要）、
      `collect_gate_summary`（呈现集合 = 产物证据 ∈ drafted/gated；
      未完成 run 不入呈现、gaps 列全；已确认待 promote 在
      summary["confirmed"] 单独呈现为未决项；promoted/superseded 在
      excluded）；
    - confirm 展开：`confirm_plan`（呈现集合 vs 当前证据的守卫：stale /
      not_presented 阻塞、skipped_terminal 跳过、授权顺序按 task.yaml
      声明）、`run_confirm_expansion`（确认阶段串行 fail-fast —— 任一
      run 确认失败即整体停止并报告该 run，不静默跳过、不继续确认、不
      进入 promote；promote 阶段经 task_scheduler.run_stage("promote")，
      并发默认 2 = STAGE_CONCURRENCY 常量；task_status.json 只在 promote
      边界写盘一次 —— 单一写者）、`default_confirm_worker` /
      `default_promote_worker`（execution_gate.py --confirm /
      promote_output.py --final 的 subprocess 薄适配）、
      `refresh_gate_summary`（gate.state 演进落账）。
  - `scripts/gate_task.py`: CLI —— `--set` 聚合呈现（Draft 就绪但无有效
    pending 的 run 先 execution_gate --set，每个 run 的 pending 绑定自己
    的哈希三元组；索引与证据对齐推进 gated，与 resume --rebuild 的
    _INDEX_STATE_FOR 同一原则）、生成 gate_summary.json、呈现后停
    （fail-closed：不自动确认、不自动 promote）；`--confirm` 按呈现集合
    逐 run confirm + promote（输出落 `<task_root>/outputs/<target.output>`，
    final hash == 已确认 draft hash 由 promote_output 校验）。
  - 测试：`tests/test_task_orchestration.py` 26 条新用例（summary 纯函数
    seam、13 run 只含 drafted 且缺口列全、confirm_plan 守卫矩阵、
    expansion 注入 worker + 真实 execution_gate --confirm subprocess、
    CLI 全链路 --set→--confirm→promote（真实 promote_output，outputs/
    落盘 + final_receipt + 幂等 GATE_NOOP）、呈现后篡改阻塞、模拟确认
    失败停止 + 不 promote、promote 前被拒 GATE_NOT_CONFIRMED、
    refresh 状态演进）。无 Office 层全绿（176 passed 含既有用例）。
  - 全量回归：621 passed，1 pre-existing failure
    （test_skill_md_failure_cost_quantified — SKILL.md 措辞，issue 07
    范围；git diff HEAD -- SKILL.md 为空）。
  - 验收核对：① 13 run 合成：gate_summary 只含 drafted/gated，缺口列全
    （state + reason）；② 确认后逐 run .gate3_confirmed 独立绑定自己的
    哈希三元组（CLI 全链路断言：confirmed hashes == gate_hashes(run_dir)，
    三元组互异）；③ 模拟确认失败：整体停止并报告该 run，不继续 promote
    （spy 断言 promote 零调用 + 无 outputs/）；④ 确认前 promote 被拒：
    promote_output.py 直接调用 GATE_NOT_CONFIRMED（fail-closed 保持）；
    ⑤ execution_gate.py / promote_output.py / compile_fill.py /
    execute_batch.py 零改动（git diff 为空）。
  - 有意解释（与 ticket 字面微差，已在代码 docstring 固化）：呈现集合
    含 gated —— prepare_task --run/resume 的 gate 阶段已逐 run
    execution_gate --set，--set 聚合不重写（幂等），否则 --run 后
    --set 会呈现空集合；confirmed 不入 runs（授权已落账，不重复呈现），
    单独 summary["confirmed"] 呈现为「已确认待 promote」未决项。

- 2026-08-25: code-review（Standards + Spec 双轴）处置——
  - 采纳：① --set 呈现循环的 StageError 未归一 —— 现捕获转
    GATE_SET_FAILED 结构化缺陷 + exit 3（与 --confirm 同契约，不再裸
    traceback）；② refresh_gate_summary 的 presentation_mismatch 分支
    死代码（blocked 在调用方 refresh 前 fail）—— 删除 + 补纯函数测试
    （含 noop 判定改为「无任何确认/交付」而非 plan 形状，与 CLI 的
    GATE_NOOP 条件一致）；③ _try_json 与 task_resume 逐字节重复 ——
    改直接导入复用。
  - Spec 轴采纳：④ confirmed-but-unpromoted 从 excluded 拆出 ——
    `.gate3_confirmed` 即呈现 + 授权的证据链（presented_at + 绑定三元
    组），是「已确认待 promote」的未决项不是终态：summary["confirmed"]
    单独呈现；confirm_plan 对 confirmed 不做呈现集合守卫（授权已落账，
    promote 重试幂等），呈现守卫只约束待确认的 gated run —— 同时消除
    「部分确认失败后重 --set → confirmed run 不在新呈现集合 → 死锁」的
    边界。
  - 保留（有意设计）：run_confirm_expansion 内部在 promote 边界写
    task_status.json —— 与 task_resume.resume_with_ctx 的既有 seam 同构
    （边界写盘 + 可注入 worker 无 Office 单测；「交互由 CLI 承接」指
    argparse/stdout/exit 层）；summary 哈希 = trio_current —— 呈现集合
    的 run 恒有 pending 三元组 == 当前三元组（gated 证据判定即要求相等；
    drafted 在收集前刚 --set），呈现态即绑定态；OUTPUT_COLLISION 守卫
    （ticket 未要求但 promote 原子替换下同路径双 run 会静默覆盖交付物，
    10 行 fail-closed）；`<task_root>/outputs/` promote 目标（ticket 只说
    --final，输出命名来自 issue 01 的 target.output；issue 07 的
    TASK_ORCHESTRATION.md 将固化该契约）。

## 问题

埃及案例实际做了"13 个 execution_gate --set → 一次性呈现 → 用户一次确认 → 13
个 --confirm"，但这是 agent 手工编排，未制度化；且聚合不得削弱逐 run 授权粒度。

## 设计

实现 gate_task.py（spec S6 阶段 5 / Implementation Decision 31）：

1. **聚合呈现**：`gate_task.py --set` 收集全部 drafted run 的验证摘要生成
   `gate_summary.json`：
   - 每 run：id、输出名、行数、关键校验结果（readback/coverage/validate）、
     spec/plan/draft SHA-256；
   - 未完成 run（非 drafted）不入呈现，并在摘要中列出缺口；
   - 呈现形态对齐现有 Execution Gate 内容要求（MOD 裁决、缺口、来源覆盖、
     timing 双栏），一次人机交互。
2. **逐 run 确认展开**：用户确认后，对每个 run 逐个执行 `execution_gate.py
   --confirm`（复用现有脚本与 fail-closed 语义），每个 run 的 `.gate3_confirmed`
   绑定自己的哈希三元组；任一 run 确认失败即停止并报告，不静默跳过。
3. **promote 衔接**：确认后逐 run 调用 `promote_output.py --final`（现有脚本，
   HASH_DRIFT 拒绝逻辑不变）；promote 并发 2。
4. **gate_summary 生成与 confirm 展开逻辑实现为可 import 的纯函数**（无 Office，
   可单测）；交互部分由 CLI 入口承接。

## 验收

- 13 run 合成任务：gate_summary.json 只含 drafted run，缺口列完整；
- 确认后逐 run 生成 .gate3_confirmed，每个 run 哈希三元组独立绑定；
- 模拟某 run 确认失败：整体停止并报告该 run，不继续 promote；
- 确认前 promote 被拒（fail-closed 保持）；
- 现有 execution_gate.py / promote_output.py 零改动。
