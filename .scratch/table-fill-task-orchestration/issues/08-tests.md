# 08 — Tests（三层验证 + 预生成合成 fixture）

Status: resolved
Type: task
Blocked by: 02, 03, 04, 05, 06

## Comments

- 2026-08-24: 落地于本仓库（仅测试 + fixture，零脚本/文档改动）+ 双轴
  code-review 处置记录（见下）。
  - **fixture**：`tests/_fixtures/task_orchestration/e2e/` —— 预生成合成
    工作簿提交入库：1 源书 parameter_book.xlsx（3 sheets R32参数/R410A参数/
    R22参数 × 表头 + 30 行）+ 共享模板 filling_template.xlsx（Sheet1，
    append clone 结构）+ task.yaml（4 个 run：r32-cooling/r32-heating 共享
    sheet A，r410a-cooling/r22-cooling 分别用 B/C → U_source = 3）。测试
    运行时绝不现场生成（无 openpyxl 依赖）；`generate_task_orchestration
    _e2e.py` 是开发期再生成器（README 明示，测试不 import）。
  - **契约测试**（无 Office，`tests/test_task_orchestration.py` issue 08
    节）：TestContractCompileEquivalence —— 同一编译输入（同一 spec +
    staged/CSV/meta/digest/candidates）下，task 形态 manifest（物化条目带
    cache_key/sha256）与单 run 形态 manifest（经 prepare_run._entry_for 真实
    seam 重建）各自经 public CLI compile_fill.py 编译 → plan 的
    input_hashes/fingerprints/operations 完全一致；compile-facing 字段
    （files/outlines/flattened/target/fingerprints）去元数据后同构。
    TestSharedSheetMaterializeIdentity —— 同一缓存键物化进两个 run →
    flat.csv 逐字节一致（cache identity ≠ run artifact identity 的单元层
    背书；与单 run 的逐字节对账在 e2e 层）。
  - **恢复测试**：issue 04 已落地同文件场景矩阵（无产物→planned、manifest
    有效→prepared、plan 有效→compiled、draft+receipt→drafted、draft 无
    receipt→execute retry、gate pending→等待确认不绕过、final receipt→
    promoted、superseded→skip、源 hash 漂移→阻塞+supersede 建议），本票
    未重复。
  - **性能验收**（有 Office，新 `tests/test_task_e2e.py`，skipIf officecli
    缺失）：断言 1 cache/ 目录数 == 唯一 (file,sheet) 需求数（4 = 3 源
    sheet + 1 共享目标模板，而非 4 run × 2 = 8 次重复展平）；断言 2 第二
    次 prepare 缓存零新增（hits=4/misses=0）+ 物化 CSV hash 不变；断言 3
    物化 CSV 与单 run CSV 逐字节一致 + manifest 双向对账 + plan 等价（期望
    行数从产物真值推导，不硬编码）；完整流程 prepare_task --init/--prepare
    → 逐 run fill_spec → --run（compile/execute/gate）→ gate_task
    --set/--confirm → outputs/ 落盘 + 全 promoted + 幂等 noop；另加
    crash window 恢复 e2e（删 receipt → resume 重跑 execute + 重呈现 gate，
    不自动越过 Gate）。
  - **Office 竞态实证**（KNOWN_TRAPS「Office 并发 = 2」）：execute 并发 2
    下 officecli batch 偶发 BATCH_CHUNK_FAILED（rc=1 空 stderr；本机实测约
    1/4 轮次）。e2e 按产品恢复路径处理：失败 run 产物证据判定 crash window
    → resume_task --resume 重跑 execute → gate；断言不依赖零竞态。
  - 测试：test_task_orchestration.py 190 passed；test_task_e2e.py 4
    passed；全量 640 passed（+10 subtests，含既有 mxp/precision/repair_gaps
    e2e）。
  - 验收核对：① 三层全绿（officecli 缺失时层次 3 优雅 skip，层次 1/2 无
    Office 全绿 ✓）；② 契约测试证明 Compiler 视角 task ≡ 单 run（plan
    input_hashes/fingerprints/operations 一致 ✓）；③ 恢复矩阵覆盖 crash
    window 且 gate 不被绕过 ✓；④ 结构性断言 flatten 次数 == 唯一需求数、
    物化 CSV hash 不变 ✓；⑤ 不测真实埃及 13 run / 墙钟 SLA / PDF /
    D4/D6 ✓。
  - **口径说明（ticket「cache/ == 3」vs 实现 4）**：spec Testing Decision
    #5 的「唯一需求数」按 (file, sheet) 计，S4 把 target flatten 走同一缓存
    键机制 → 3 源 sheet + 1 共享目标模板 = 4；ticket 的 U=3 只计唯一源
    sheet。偏差在 e2e docstring/断言注释/task.yaml/两个 README 全面明示。
  - **code-review 处置**（双轴）：
    - Standards 轴：无硬违规；处置 judgment-call 气味 —— 消除
      `_seed_contract_cache` 未用返回与误导命名（统一 `_contract_cache_key`
      seam）、`single_run_manifest` O(n²) 循环改推导式、e2e 与单元测试的
      去元数据逻辑合并为本地 helper、OUTPUTS 从 fixture task.yaml 读取
      （杜绝重复声明漂移）、期望行数从源 CSV 真值推导（去硬编码 34/A34）。
    - Spec 轴：契约层单 run 侧原为「任务 manifest 反剥」（循环论证）→
      改用 prepare_run._entry_for 真实 seam 重建（非 Office 可测的 Run
      Layer 自身形态）；drive_to_gate 的 JSON 解析加守卫（stdout 非 JSON
      时显式断言失败而非裸 ValueError）；e2e 条目对账改双向（同名条目去
      元数据全等 + 集合相等）捕获单 run 侧多余/改名条目。yaml_dump 的
      JSON 兜底移除（PyYAML 是 compile_fill 硬依赖，缺失须显式暴露）。
      生成器脚本超出 ticket 字面要求，保留为开发期可复现性资产并在
      README 明示只读用途。