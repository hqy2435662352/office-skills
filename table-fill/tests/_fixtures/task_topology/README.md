# tests/_fixtures/task_topology/ — Ticket 03 Early Task Topology fixtures

Ticket 03（spec D3）的 data-neutral 拓扑骨架 fixtures：

- `task_text.md` — Topology Check 的**拓扑输入信号**（data-neutral 任务文本：
  「3 个系列 / 每个系列一个」→ 规划 3 条 run；零业务细节要求）。
- `task_topology_3runs.yaml` — **3-run 拓扑骨架**（run 清单 + 引用；零业务键；
  `shared_source` / `shared_template` 是跨 run 相同的 (file, sheet) 需求、
  由 run 清单**派生**，不设独立共享声明节 —— 与 TASK_ORCHESTRATION §1.1
  一致）。通过 `prepare_task --validate` 与 `--init`。
- `task_topology_1run.yaml` — **1-run 骨架**（单 run 不强制 Task 层；1-run
  skeleton 同样通过同一静态校验与 `--init`，run 语义零回归）。
- 引用复用 `../task_orchestration/sources/parameter_book.xlsx` 与
  `templates/filling_template.xlsx`（合成占位工作簿）—— 测试在临时任务根
  组装时从该目录复制；测试运行时绝不现场生成工作簿，也不打开工作簿
  （静态校验 + `--init` 无 Office 依赖）。

Acceptance bullet 1（真实业务多系列 replay）本轮排除（用户禁止真实业务
运行）；静态证明 + 契约文字 pin 见 `tests/test_task_topology.py`（并记录于
`.scratch/table-fill-axis-neutral-grid-runtime/issues/03-early-task-topology.md`）。