# 03 — Stage Orchestrator（barrier 调度 + 并发控制 + 单一写者）

Status: resolved
Type: task
Blocked by: 01, 02

## Comments

- 2026-08-23: 落地于本仓库, commit `844a6e7`（issue 02 的 `cdad87a` 之上）。
  - `scripts/task_scheduler.py`: 编排核心（纯逻辑，无 Office）——阶段表 +
    并发默认值 implementation constant（2/2/4/2/1/2，compile 取常量 4；
    不进 task.yaml、无 CLI 调参旗标，`--help` 结构化断言）；`run_stage`
    barrier 式阶段执行（ThreadPoolExecutor 阶段内并行、with 块阶段间
    barrier、无流水线；结果按 items 顺序，确定性）；worker 失败隔离
    （StageError / 任意异常 / fail() 的 SystemExit / 非 dict 返回 → 归一
    failed 结果，同阶段其他 item 不受影响）；`apply_stage_status` 阶段
    边界批量状态推进（单一写者语义：返回新 status，写盘由调用方在边界
    执行一次；失败 run 不推进、superseded 不触碰、幂等）；`aggregate_failures`
    跨阶段失败清单（带 stage 归属）；`阶段 x/y 开始/完成` 进度行。
  - `scripts/task_prepare.py`: `run_prepare` 拆分为任务级串行 prelude
    （`prepare_task_level`：staging/outline/需求收集/sheet 校验/缓存键/
    RUN_STATE_GUARD → ctx）+ 阶段 1 worker（`cache_build_worker`，每缓存
    键恰好一个 worker，hit 跳过零 officecli）+ 阶段 2 worker
    （`run_prepare_worker`，单 run 物化 + manifest，只写本 run 目录，并行
    安全）+ 阶段 1 后串行固化 `finalize_cache_facts`（指纹/漂移/封存补全
    ——依赖缓存 meta，与 issue 02「先展平、后指纹」相对顺序一致）+ 阶段
    3–5 subprocess worker（compile_fill / execute_batch / execution_gate，
    现有脚本零改动）+ `run_staged_pipeline` 阶段编排（--prepare 用阶段
    1+2，--run 用阶段 1–5；阶段边界 apply+写盘一次；阶段 1 失败
    fail-closed 停；进度行走 progress 可注入）。
  - `scripts/prepare_task.py`: 新增 `--run` 模式（阶段 1–5 barrier 编排，
    gate 呈现 --set 后停，不自动确认/不自动 promote，exit 0 带 gate
    pending 提示；失败 exit 3 + 跨阶段失败清单）；`--prepare` 改走阶段
    编排且 run 级失败不再 fail-fast（同阶段其他 run 不受影响，失败清单
    统一 exit 3，任务级缺陷仍在 prelude fail()）；进度行走 stderr（stdout
    纯 JSON）。
  - 测试：`tests/test_task_orchestration.py` 31 条新用例（阶段常量 +
    13 run 规模 barrier 顺序 + 阶段内并发 == 默认值 + 失败隔离/归一 +
    边界状态推进 + 单一写者 watchdog（阶段内状态文件字节稳定、边界恰写
    一次）+ 进度行格式 + --run CLI 前置守卫）。无 Office 层全绿（90 passed
    含既有用例）。全量 536 passed，1 pre-existing failure
    （test_skill_md_failure_cost_quantified — SKILL.md 措辞，issue 07 范围；
    git diff HEAD -- SKILL.md 为空）。
  - 手动 e2e（本机 officecli 1.0.144，fixture 占位工作簿）：--prepare
    阶段 1+2 barrier 顺序执行、并发 2、cache/ 目录数 == 3 == 唯一需求数、
    第二次 --prepare hits=3/misses=0（零 flatten）；--run 到 compile 阶段
    因 fixture 无 fill_spec 全量 FILL_SPEC_MISSING 聚合（exit 3，失败清单
    带 stage/run 归属，status 落 prepared 边界值）——完整成功路径（真
    fill_spec → execute → gate → promote）在 issue 08 的 e2e 层。
  - 验收核对：barrier 顺序 + 并发默认值（单测结构断言）；worker 失败
    同阶段隔离 + 失败清单汇总（单测 + e2e FILL_SPEC_MISSING）；task_status
    阶段边界更新 + 无并发写（单测 watchdog + 结构断言 worker 载荷不含
    status 路径）；既有脚本零改动（git diff：compile_fill/execute_batch/
    execution_gate/promote_output/prepare_run/flatten_* 等单 run 脚本 diff
    为空，仅新增 task_scheduler.py；mod_capture.py 的工作区改动是与本
    issue 无关的既有 WIP，未入本提交）。
  - 延迟到 issue 04/05：失败 run 的处置分支（重试/REPAIR/supersede）、
    gate_summary 聚合呈现与逐 run 确认展开、promote 阶段 worker 挂载
    （本 issue 的阶段表已含 promote 并发常量 2，--run 在其前停是
    fail-closed 设计，不是缺口）。
- 2026-08-23: code-review（Standards + Spec 双轴）处置——
  - 采纳（commit `2f7501e`）：① prepare_task.py 导入顺序先 sys.path.insert
    再打 E402（模块导入安全）；② run_stage 移除 concurrency 覆盖旋钮
    （并发 = implementation constant，无运行时调参，spec S6）；③ worker
    结果缺合法 status → WORKER_INVALID_RESULT（不再静默当成功，失败二分
    不被哑成功掩盖）；④ task_prepare 悬空 utc_now_iso 导入删除（updated_at
    已移交 apply_stage_status）；⑤ STAGE_LABELS gate/promote 措辞不再宣称
    未实现语义（聚合/worker 在 issue 05）；⑥ gate 词汇与状态机一致：
    --run JSON gate.state=presented（= task_status 的 gated 待确认），
    code TASK_RUN_GATE_PRESENTED；⑦ 阶段失败路径双流契约：stdout 单
    ERROR JSON + exit 3、stderr 只承载人读进度行（fail() 纯 JSON 契约在
    守卫路径保持）；⑧ 测试更名/重写为诚实断言：run_stage 签名结构断言
    （无 status/concurrency 旋钮、模块无 write_text）+ 缺 status 归一用例。
  - 保留（有意设计）：_run_child 与 flatten_cache._run_script 不合并
    （失败契约不同：逐 item StageError vs 整进程 fail()；_officecli.py
    保持零改动以守「scripts/ 下仅新增文件」验收）；RUN_STATE_GUARD 移入
    prelude（先于缓存构建，guard 优先级高于 drift 报告，避免无谓
    officecli）；--prepare run 级失败改为同阶段隔离 + 失败清单聚合（ticket
    设计 5 的传播语义覆盖 prepare 阶段，任务级缺陷仍 fail()）；子进程
    capture_output 保留 stderr 尾供缺陷呈现（进度机制 = 阶段边界摘要，
    对齐 ticket 设计 6）；extra report 键（failures/stages）与 `阶段 x/y
    开始` 行是编排的观测面，不改变既有键语义。
  - 全量回归：536 passed + 1 pre-existing failure（test_skill_md_failure_
    cost_quantified，issue 07 范围）；test_precision_keep_wide_column_
    passes_e2e 在满套件中一次偶发失败，单独重跑通过（Office e2e 并发
    环境噪声，本 diff 不触及 execute/precision 路径）。

## 问题

批量任务需要阶段化调度，但 table-fill 没有编排层；若引入 DAG/流水线/worker pool
会破坏"文件即状态"的简洁哲学，且 Office 并发超过 2 已有实证失败（validate_state
fail）。

## 设计

实现 Staged Orchestration Scheduling（spec S6）：

1. **barrier 式阶段批处理**，阶段内并行、阶段间 barrier、无跨阶段流水线：

   | 阶段 | 并发 | 性质 |
   |---|---|---|
   | 1. source prepare/flatten/cache build | 2 | Office 密集 |
   | 2. run prepare（物化 + target prepare + manifest 组装） | 2 | Office 密集 |
   | 3. compile | 4~8 | 纯文本 |
   | 4. execute（validate/readback/render QA） | 2 | Office 密集 |
   | 5. gate_task 聚合呈现 | 1 | 串行 |
   | 6. promote | 2 | 纯文件 |

2. **并发默认值是 implementation constant**：不进入 task.yaml、不暴露 CLI 调参
   （环境稳定性参数不得污染任务定义）。
3. **单一写者**：worker 只向主进程回报结果（状态码 + 产物路径），主进程在阶段
   边界统一批量更新 task_status.json——阶段内并发时状态文件零并发写。
4. **调度实现**：Python 线程池 + subprocess 调用现有脚本（compile_fill.py /
   execute_batch.py 等本就是独立进程入口）；不改造现有脚本。
5. 失败传播：任一 run 在阶段失败不阻断同阶段其他 run；阶段结束汇总失败清单，
   失败 run 按 issue 04 的失败二分处置（重试/REPAIR/supersede）。
6. 进度报告：阶段边界输出 `阶段 x/y 完成` 摘要（对齐复盘"超过 60 秒主动说明
   进度"的要求）。

## 验收

- 13 run 规模合成任务：各阶段按 barrier 顺序执行，阶段内并发符合默认值；
- 模拟 worker 失败：同阶段其他 run 不受影响，失败清单正确汇总；
- task_status.json 在阶段边界更新，期间无并发写（单一写者）；
- 现有脚本零改动（git diff 检查 scripts/ 下仅新增文件）。
