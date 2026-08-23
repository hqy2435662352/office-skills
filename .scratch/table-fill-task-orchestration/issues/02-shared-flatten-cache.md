# 02 — Shared Flatten Cache（缓存键 + eager 预展平 + 物化）

Status: resolved
Type: task
Blocked by: 01

## Comments

- 2026-08-22: 落地于本仓库, commit `cdff56b`（issue 01 的 `d490e57` 之上），
  审查修复 `cdad87a`（code-review 双轴：Standards 零硬违规 + Spec 核心契约
  全部忠实，详见下）。
  - `scripts/flatten_cache.py`: 缓存键纯函数 seam（SHA256 混合四分量，
    长度前缀编码消除跨分量边界歧义，见下；键内无任务身份）；`<task_root>/cache/<key>/`
    白名单产物 flat.csv/meta.json/digest.md；eager `build_cache_entry` worker
    复用 flatten_workbook/classify_columns/structure_digest（subprocess，
    prepare_run.py 零改动）；`materialize_entry` 逐字节复制 + candidates
    物化时再生成 + 目标条目 digest 以 --target 再生成，并把 run 侧 meta.file
    重定向到 run 自身 staged 副本（run artifact 自包含；Cache Identity ≠
    Run Artifact Identity）。
  - `scripts/task_prepare.py`: staged 命名（ASCII，冲突 _2/_3…，非 ASCII
    fail-closed）、`collect_demands`（唯一需求数 U=3 与 issue 08 设计一致）、
    `assemble_run_manifest`（compile-facing 与单 run 同构，flattened 条目只多
    cache_key/sha256）；`run_prepare` 编排（staging → outline 复用 → eager
    缓存 → 物化 → run manifest → status planned→prepared）。
  - `prepare_task.py --prepare`: fail-closed 前置（TASK_MANIFEST_MISSING /
    MANIFEST_STALE / SOURCE_HASH_DRIFT / CACHE_REF_DRIFT / RUN_STATE_GUARD /
    SHEET_NOT_FOUND / STAGED_NAME_NON_ASCII / ENTRY_NAME_DUPLICATE）。
  - `task_schema.py`: task.yaml 新增必填 `target.sheet`（阶段 1 收集 (file,
    sheet) 需求对的前提；校验 + manifest derive 同步）。
  - 测试：`tests/test_task_orchestration.py` 31 条新用例（预约定纯函数
    seam：键/需求/staged 命名/物化/manifest 组装 + 无 Office CLI guard +
    键边界不碰撞回归）；无 Office 层全绿（499 passed 含既有用例）。全量
    506 passed，1 pre-existing failure（test_skill_md_failure_cost_quantified
    — SKILL.md 措辞，issue 07 范围）。
  - 手动 e2e（本机 officecli 1.0.144，fixture 占位工作簿真实 sheet）：
    验收 1 cache/ 目录数 == 3 == 唯一需求数；验收 2 物化 CSV 与单 run flatten
    逐字节一致（SHA256 相等）+ 同 sheet 集合下 run manifest 的
    files/flattened/fingerprints/row_gaps/style_granularity 全等（仅多
    cache_key/sha256）；验收 4 第二次 --prepare hits=3/misses=0（零 flatten，
    cache 零新增，物化 hash 不变）；SHEET_NOT_FOUND / RUN_STATE_GUARD /
    缓存删除后自愈重建（确定性重展平，产物 hash 不变）均复验通过。
- 2026-08-22: code-review（Standards + Spec 双轴）结论与处置——
  - 采纳：① classify→digest 子进程对提取共享 helper（flatten_cache 内部；
    prepare_run.py 按契约不动）；② prepare_task --init/--prepare 前置去重
    （`_load_derived`）；③ 缓存键改长度前缀编码（spec 字面"+"纯拼接存在
    sheet/version 跨分量边界歧义，如 sheet 'X' + schema_v=11 与 sheet 'X1' +
    schema_v=1 碰撞；语义"混合四分量、无任务身份"不变）；④ 死代码
    task_cache_dir 删除、ref_order 命名、双重 next 提取、两新脚本补导入
    纪律。已入 `cdad87a`。
  - 保留（有意 fail-closed 决策）：PPTX task 层拒绝（eager 展平机制是
    xlsx 语义，显式拒绝优于静默乱跑）；RUN_STATE_GUARD 与漂移守卫（保护
    issue 01 冻结契约，issue 04 supersede 语义的前置护栏而不是替代）；
    ENTRY_NAME_DUPLICATE（单 run 的按名合并会静默丢条目，task 层显式拒绝
    更符合 fail-closed 哲学）；物化时 candidates/digest 纯文本再生成（不入
    缓存白名单，零 officecli —— 与"毫秒级文本复制"一致，"Office 密集"
    只在缓存构建阶段）；flattened 条目 sha256 元数据（验收 "仅多 cache_key"
    与设计点 6 "记录 sha256" 冲突，以设计点 6 为准——物化 CSV 的 hash 正是
    run 业务身份；Compiler 按已知键字典读取，多字段惰性容忍，契约等价性由
    issue 08 的 compile 等价测试锁定）。
  - 延迟到 issue 08（其验收清单已有）：compile plan 等价测试、单 run↔task
    CSV 逐字节一致的结构化断言、cache 目录数结构断言（本 issue 已手动
    e2e 复验，未固化为 Office 依赖的回归测试）。

## 问题

同一源工作簿在同一任务内被多个 run 反复 flatten（埃及 27 次 / 38.1 分钟 / 机器
阶段 82%）。flatten 产物（flat.csv / meta.json / digest.md）与 run 绑定，无法
跨 run 复用。

## 设计

实现 Task-local Flatten Cache（spec S3/S4/S5）：

1. **缓存位置与生命周期**：`<task_root>/cache/<key>/`，生命周期绑定 task root
   （task 归档即 cache 归档）；不做 global cache/LRU/TTL/eviction。
2. **缓存键**：`SHA256(staged_source_hash + sheet_name + flatten_schema_version +
   officecli_version)`；键内不含任务身份（未来升级全局缓存零迁移）。实现为可
   import 的纯函数（契约测试 seam）。
3. **eager 预展平**：阶段 1 由 prepare_task.py 解析 task.yaml 收集全部需要的
   (file, sheet) 对，每键恰好一个 worker 展平入库；**禁止 run 内 lazy flatten**
   ——缓存写冲突从结构上消除，不引入锁。
4. **缓存内容**：只允许 flat.csv / meta.json / digest.md；run 产物禁止入缓存。
5. **物化（Materialize）**：run 创建时把缓存产物复制进 run workdir（毫秒级文本
   复制），命名与单 run 约定一致（`<staged>_<sheet>_flat.csv` 等）；物化后 run
   artifact 自包含，可脱离 cache 独立归档复验。禁止 `../../cache` 引用、禁止
   symlink/junction。
6. **Cache Identity ≠ Run Artifact Identity**：manifest 的 flattened 条目记录
   `name/source/sheet/sha256/cache_key`（key 是 provenance metadata）；物化 CSV
   的 hash/fingerprint 才是 run 的业务身份。
7. prepare_task 复用 prepare_run 的底层函数（flatten_workbook / structure_digest
   / classify_columns），不改 prepare_run.py 本体。

## 验收

- 同一 (file, sheet) 在任务内只展平一次：cache/ 目录数 == 唯一需求数（见 issue
  08 性能验收）；
- 物化 CSV 与单 run 模式 flatten 产物逐字节一致（hash 相等）；
- run manifest 的 compile-facing 字段（files/flattened/fingerprints）与单 run
  同构，仅多 cache_key 字段；
- 第二次 prepare_task（缓存命中）零 officecli flatten 调用（cache 目录零新增、
  物化产物 hash 不变）。
