# tests/_fixtures/task_orchestration/e2e/ — issue 08 性能验收合成 fixture

预生成（不用运行时生成、不用客户真实数据；重新生成见
`tests/_fixtures/generate_task_orchestration_e2e.py`，生成后提交工作簿）：

- `sources/parameter_book.xlsx` — 1 个源工作簿，3 sheets（R32参数 /
  R410A参数 / R22参数），每 sheet 表头 + 30 行合成数据（产品线/型号/容量/
  数量/备注）。
- `templates/filling_template.xlsx` — 共享目标模板（Sheet1：标题 + 表头 +
  空数据模板行 + 合计行，append clone 填充结构）。
- `task.yaml` — 4 个 run：r32-cooling/r32-heating 共享 sheet R32参数，
  r410a-cooling / r22-cooling 分别用 R410A参数 / R22参数 → 唯一源需求
  U_source = 3；目标模板共享 → 任务内唯一 (file, sheet) 需求 = 4（cache/
  目录数上限，而非 4 run × 2 sheet = 8 次重复）。

本目录是三层验证第三层（有 Office e2e，`tests/test_task_e2e.py`）的任务根
模板：测试复制本目录为临时任务根，跑 prepare_task → compile → execute →
gate_task → promote 全流程，并做结构性断言（flatten 次数 == 唯一需求数、
缓存命中零重复展平、物化 CSV 与单 run CSV 逐字节一致、plan 等价）。