# 01 — Task Artifact Schema（task.yaml / task_manifest.json / task_status.json）

Status: resolved
Type: task
Blocked by:

## Comments

- 2026-08-22: 落地于本仓库, commit `d490e57`.
  - `scripts/task_schema.py`: Task Artifact Model 纯函数层（契约测试 seam，
    spec Testing Decision #3）— task.yaml 解析/静态校验、derive 骨架、
    冻结/状态一致性检查。缺陷均带 code/message/corrective_action（fail
    契约同构），exit 3 + defect 清单；task.yaml 缺失/非 ASCII 路径 exit 1。
  - `scripts/prepare_task.py`: `--validate` / `--init`。init 写声明快照
    （run 清单 + 输入/输出引用 + task.yaml 指纹，frozen_at 封存）与 status
    （全部 planned）；不静默重派生（MANIFEST_STALE fail-closed，失败二分
    预留 supersede）；手改侦测（RUN_ID_MISMATCH / STATUS_STALE /
    STATUS_INVALID_STATE）。快照事实容器（staged_files / outlines /
    flatten_cache_refs / fingerprints）由 issue 02 的 prepare 阶段补全。
  - 校验拒绝 business rule 键入 task.yaml（mapping/lookup/transform/
    formula/validation），映射永远在 runs/<id>/fill_spec.yaml。
  - `tests/test_task_orchestration.py`: 28 用例（纯函数 seam + public CLI
    subprocess seam，无 Office）。回归：本文件全绿；全量 469 passed，
    1 pre-existing failure（test_skill_md_failure_cost_quantified —
    SKILL.md 缺 "2 分钟" 措辞，与本次变更无关，git diff HEAD -- SKILL.md
    为空；文档修正在 issue 07）。
  - fixtures: `tests/_fixtures/task_orchestration/` 合法 task.yaml + 5 非法
    示例 + 占位工作簿；issue 08 将替换为合成 E2E 工作簿。
  - 既有单 run 脚本零改动（git status 仅新增文件）。

## 问题

table-fill 目前只有 run 级 artifact（fill_spec.yaml / prepare_manifest.json /
receipt），没有 task 级的事实载体。"单任务多 run"的任务定义、准备快照、运行状态
无处安放，导致批量任务全靠 agent 手写临时脚本和目录约定（埃及 25 目录 12 废弃的
根因之一）。

## 设计

实现 Task Artifact Model（spec S2）：

1. `task.yaml`（canonical Task Definition）：
   - `task.id`、project metadata（customer 等，非业务规则）；
   - `runs[]`：每个 run 的 `id`、`source.sheets[]`、`target.template`、
     `target.output`、模板族声明（仅作记录，D6 不实现）；
   - 不含 mapping/lookup/transform/formula/validation rule（业务映射永远在
     `runs/<id>/fill_spec.yaml`）。
2. `task_manifest.json`（derived Prepare Snapshot，脚本写）：
   - staged files + SHA-256、outline、flatten cache references（`cache_key` +
     `source_hash`，key 是引用不是事实源）、template fingerprints；
   - 语义 = "这个任务基于什么输入"，一旦确定即冻结。
3. `task_status.json`（derived Runtime State，脚本写，单一写者）：
   - `runs: {id: {state, superseded_by?}}`；状态集合：
     planned/prepared/compiled/drafted/gated/promoted/superseded；
   - 语义 = "这个任务运行到了哪里"，每次执行都会变化；
   - 与 task_manifest 分离（输入快照 ≠ 运行状态，变化频率不同）。
4. schema 校验：prepare_task.py 读取 task.yaml 时静态校验（run id 唯一、
   sheets/target 引用存在、输出名合法），失败 exit 3 + defect 清单。
5. 三文件均 UTF-8；task.yaml 由 agent 撰写（映射确认后），manifest/status 禁止
   手改。

## 验收

- 提供合法 task.yaml 示例与非法示例（缺字段/重复 run id/引用不存在），校验
  分别通过/拒绝；
- task_manifest.json 与 task_status.json 由脚本写入后，与 task.yaml 的引用关系
  可追溯（run id 一一对应）；
- 单 run 模式完全不受影响（本 issue 不改任何现有脚本）。
