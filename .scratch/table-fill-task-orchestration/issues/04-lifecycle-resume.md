# 04 — Lifecycle / Resume / Supersede（状态机 + 断点判定 + resume_task.py）

Status: resolved
Type: task
Blocked by: 01, 03

## Comments

- 2026-08-24: 落地于本仓库。
  - `scripts/task_resume.py`: 断点判定核心（spec S7 矩阵；无 Office 的纯
    文件系统 + SHA-256 seam）——`gather_run_facts`（产物存在性 + hash
    证据：manifest files/flattened、spec/plan 绑定、receipt/draft、
    gate markers、final_receipt）、`classify_run_facts`（planned /
    prepared / compiled / drafted / **execute_retry（crash window）** /
    gated / confirmed / promoted / superseded / blocked + needs 阶段
    子序列）、`schedule_resume`（判定 → {stage: [runs]}，空阶段不执行）、
    `resume_with_ctx`（compile→execute→gate barrier 恢复编排：阶段 1
    item 域 = 缓存键；--rebuild 时 superseded 索引复位；单一写者；失败
    隔离；blocked 不继续旧 run）、`resume_task`（生产入口，prepare_
    task_level 放宽 RUN_STATE_GUARD）、supersede 纯函数
    （`validate_supersede` / `supersede_status`）。
  - `scripts/resume_task.py`: 唯一恢复/supersede 入口 —— `--resume`
    （按产物证据定断点继续剩余阶段；跳过 promoted/superseded；gated
    等待确认不绕过；confirmed 等 gate_task 展开 promote；不自动 promote，
    fail-closed）；`--supersede --map 旧run=新run`（唯一被授权在 task.yaml
    变化后重派生 task_manifest.json 快照的入口 + 状态演进：旧 run 标
    superseded + superseded_by 链接新版本，产物完整保留）。
  - `scripts/task_prepare.py`: `prepare_task_level` 增加 `allowed_states`
    参数（RUN_STATE_GUARD 放宽供 resume 使用，默认语义不变）；漂移失败
    消息点名 resume_task.py --supersede。
  - `tests/test_task_orchestration.py`: 60 条新用例（场景矩阵含 crash
    window、调度纯函数、fake worker 恢复编排、rebuild 复位、真实 worker
    阶段 1 键域与物化、supersede 校验矩阵与 CLI 全流程、源漂移阻塞 +
    supersede 建议）。
  - 手动 e2e（本机 officecli 1.0.144，fixture 真实工作簿）：--prepare
    3 键 3 miss → --resume 对 prepared run 只跑 compile barrier（阶段
    1/2 跳过）并聚合 FILL_SPEC_MISSING（exit 3）；追加 r32-heating_v2
    声明 → --supersede 全流程 PASS → --resume 阶段 1 全命中零 officecli、
    阶段 2 只物化 v2、旧 run 跳过；重复 --supersede 被 SUPERSEDE_INVALID
    拒绝；事后 --init 一致性 PASS。
  - 全量 596 passed，1 pre-existing failure（test_skill_md_failure_cost_
    quantified — SKILL.md 措辞，issue 07 范围；git diff HEAD -- SKILL.md
    为空）。
- 2026-08-24: code-review（Standards + Spec 双轴）处置——
  - 采纳：① 阶段 1 item 域修复 —— schedule 用 run id 表达需求，执行时
    转 `ctx["unique_keys"]`（cache_build_worker 按键寻址，与 run_staged_
    pipeline 同契约；原实现把 run id 喂给缓存键 worker 会在真实路径
    KeyError）+ 真实 worker 回归（TestResumeRealWorkers：3 键全命中零
    officecli → run_prepare 真实物化 → compile 聚合 FILL_SPEC_MISSING，
    无 WORKER_RAISED）；② --rebuild 索引复位 —— superseded run 先复位到
    与判定对齐的合法状态（`_INDEX_STATE_FOR`，写盘一次），否则
    apply_stage_status 的前驱集永不推进 superseded 索引（测试覆盖
    reactivate 全流程与按产物接续两路径）；③ 失败二分边界文档化 ——
    execute 前的 spec/plan 改动 = 输入事实未变的 REPAIR 循环（降级重
    compile，与单 run「修 spec → 重编译」一致）；execute 后的绑定漂移
    = 输入事实改变（blocked → supersede）；④ RUN_STATE_GUARD 消息不再
    硬编码 "prepare"；⑤ `_DECL_FIELDS` 改 (section, key) 对（去掉点串
    partition 解析）；⑥ RESUME_MANIFEST_NAME 别名删除；⑦ 证据语义文档化
    —— gate marker 只引导调度不授权（gated/confirmed 判据 = 三元组
    相等），promoted 判据 = final_receipt 存在性（ticket 矩阵字面语义）。
  - 保留（有意设计）：resume_with_ctx 与 run_staged_pipeline 的阶段循环
    不复用同一函数（item 域来源与失败排除语义不同：schedule+failed_runs
    vs prev_ok，共享会引入调参面）；run_supersede 不调用 _load_derived
    （它必须跳过 check_frozen —— MANIFEST_STALE 正是 supersede 存在
    的理由，已在 docstring 注明）；pre-execute spec/plan 改动按矩阵降级
    重试而非 blocked（单 run REPAIR 语义，审查的反读已裁定）。

## 问题

埃及案例的 run 目录四代并存（runs / runs_heating / runs_heating_v2 /
runs_header_v2）、中断后无法确定从哪继续、废弃 run 与活 run 混在一起——根源是
没有 run 生命周期与断点恢复机制。

## 设计

实现 Run Lifecycle / Resume / Supersede（spec S7）：

1. **状态机主路径**：planned → prepared → compiled → drafted → gated →
   promoted；`superseded` 是保留证据的终止分支。
2. **status 是生命周期索引，不是真值源**：断点判定 = artifact 存在性 + hash
   校验，实现为**无 Office 的纯函数**（可 import、可单测）：
   - 无产物 → planned → 阶段 1；
   - manifest 有效 + 物化产物 hash 匹配 → prepared → 跳过阶段 2；
   - plan 的 fill_spec_sha256 匹配 + input_hashes 绑定有效 → compiled → 跳过
     阶段 3；
   - draft 存在 + receipt.draft_sha256 匹配 → drafted → 直接进 gate；
   - **draft 存在但 receipt 缺失/不匹配（execute crash window）→ 重跑 execute**；
   - .gate3_pending 有效（hash 三元组）→ gated → 等待确认，不绕过；
   - final_receipt 存在 → promoted → 跳过；
   - superseded → 跳过（除非显式 rebuild）；
   - 源文件 hash 与 manifest 不符 → 阻塞并建议 supersede。
3. **失败二分**：输入事实未变 → 阶段重试或 REPAIR；输入事实改变（task.yaml
   修改、源 hash 漂移、target 模板重建、MOD/映射裁决变化）→ supersede 该 run
   （run 级，非 task 级）。
4. **Supersede**：旧 run 产物完整保留（fill_spec/manifest/plan/draft/receipt/
   timing），状态标 superseded + `superseded_by` 链接新 run 版本；新 run 是独立
   版本（如 `<id>_v2`）。
5. **resume_task.py**：唯一恢复入口；task 入口、run 粒度断点、产物 hash 校验
   定断点、恢复后继续剩余阶段（compile barrier → execute barrier → gate）；
   **不自动跳过 Gate、不自动 promote**（fail-closed 不变）。

## 验收

- 断点判定场景矩阵全部通过（issue 08 的恢复测试层），含 execute crash window
  用例；
- 模拟中断：各阶段中断后 resume_task.py 均从实际断点继续，且跳过 promoted /
  superseded run；
- 源文件 hash 变化时 resume 阻塞并给出 supersede 建议，不继续旧 run；
- resume 后 gate 仍处于 pending 等待人工确认（不绕过）。
