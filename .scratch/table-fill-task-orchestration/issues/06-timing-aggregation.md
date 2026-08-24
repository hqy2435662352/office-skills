# 06 — Timing Aggregation（active / superseded 双栏）

Status: resolved
Type: task
Blocked by: 01, 03, 04

## Comments

- 2026-08-24: 落地于本仓库, commit `f596f8c`（5 文件，+597/-17）。
  - `scripts/task_timing.py`: 可 import 纯函数层（无 Office，可单测，
    ticket 设计 3）——`load_entries`（缺失/损坏/非 list → []，复用
    task_resume._try_json）/ `totals`（机器 + agent 双栏毫秒合计，非数值
    与 bool 防御）/ `phase_rows`（kind+phase 分组，复用 aggregate_run_
    timings 语义：count/total/average/min/max/percent_of_kind，
    (-total, kind, phase) 稳定排序）/ `summarize_entries` / `summarize_run`
    / `aggregate_task_timing`——先按状态过滤 run 集（active = 状态 ∈
    prepared..promoted 且非 superseded，ticket 设计 2），再按 kind+phase
    跨 run 分组；active.totals_ms = 本次交付成本，superseded.totals_ms =
    优化收益；excluded 列 planned/未知（不进任一栏，state 透明）；每栏
    per_run 逐 run 汇总（每个 run_timing.json 只解析一次）；**只读**：
    不写任何文件，superseded 证据保留不动（ticket 设计 1 / 验收 2）。
  - `scripts/timing_task.py`: 独立 CLI（ticket 设计 2「或独立脚本输出」）
    ——`--task-root` 输出报告 JSON（与 gate_summary 的 task_timing 块同源）；
    缺 task.yaml exit 1、缺 derived exit 3、OK exit 0；只读。
  - `scripts/task_gate.py`: `timing_totals` 委托 task_timing（计时解析单一
    事实源，双栏既有返回形态不变）；`collect_gate_summary` 增加
    `task_timing` 双栏块；GATE_SUMMARY_SCHEMA_VERSION 1→2。
  - `scripts/gate_task.py`: `--set` stdout 携带 timing 双栏合计（ticket
    设计 4：报告随 Execution Gate 呈现；完整 kind+phase 分组在
    gate_summary.json）。
  - `tests/test_task_orchestration.py`: +13 用例（TestTaskTimingPure 10 /
    TestTimingTaskCLI 3）——验收 1（2 superseded + 4 活 run：双栏正确分栏、
    活 run 不含 superseded 耗时、superseded 单独列出、声明序、kind+phase
    跨 run 聚合）；验收 2（superseded run_timing.json 逐字节不变）；验收 3
    （真实 phase 名演示：superseded flatten 合计 16s vs active 4s =
    埃及 27→7~9 次的对应量化）；phase 分组语义（含 percent_of_kind）；
    gate 集成（collect_gate_summary 双栏块 + 每 run 机器+agent 既有要求
    不变）；CLI 守卫与双栏输出。
  - 全量 633 passed + 1 pre-existing failure（test_skill_md_failure_cost_
    quantified — SKILL.md 措辞，issue 07 范围；git diff HEAD -- SKILL.md
    为空）。
  - 有意解释（与 ticket 字面微差）：excluded 是第三列表（planned/未知），
    不是新栏 —— 双栏语义不变，只保证「不进任一栏」透明可审计；
    superseded 栏也带 per_run（哪条废弃 run 贡献了多少浪费的审计粒度）；
    task 根目录自己的 run_timing.json（task_cache_build/task_gate 等 task
    级相位）不在聚合范围 —— ticket 设计 2 明确「读 task_status.json + 各
    run run_timing.json」。

## 问题

埃及复盘发现：25 个计时目录仅 13 个最终有效，废弃 run 的耗时（重复 flatten、
模板返工）混在总耗时里，无法回答"本次交付成本"与"系统优化收益"两个不同问题。
现有 aggregate_run_timings.py 需要手工 rank 去重才能接近这个答案。

## 设计

实现 task 级 timing 聚合（spec S7 Timing Contract）：

1. **每 run 的 run_timing.json 保留不动**（现有机制 append，证据不删）。
2. **task 级聚合报告**（`prepare_task.py` / `resume_task.py` 或独立脚本输出）：
   - **Active Runs 栏**：只统计活 run（状态 ∈ prepared..promoted 且非
     superseded），按 run 汇总各阶段耗时（复用 aggregate_run_timings 的
     kind+phase 分组语义，先按状态过滤 run 集）；
   - **Superseded Cost 栏**：superseded run 的耗时单独统计（重复 flatten /
     模板返工 / 废弃轮次的量化证据）。
3. 聚合逻辑实现为可 import 的纯函数（读 task_status.json + 各 run
   run_timing.json，无 Office，可单测）。
4. 报告随 Execution Gate 呈现（Gate 报告含机器 + agent 两栏的既有要求不变，
   增加 task 级双栏）。

## 验收

- 合成任务含 2 个 superseded run + 4 个活 run：聚合报告正确分栏，活 run 不含
  superseded 耗时，superseded 栏单独列出；
- superseded run 的 run_timing.json 文件未被删除或改写；
- 聚合报告能回答：本次交付成本（active 栏合计）与优化收益（superseded 栏 = 本
  可避免的浪费，如埃及 27→7~9 次 flatten 的对应耗时）。
