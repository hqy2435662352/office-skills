# tests/_fixtures/task_orchestration/ — Task Artifact 模型 fixtures（issue 01）

- `task.yaml` — 合法 task.yaml 示例（3 run 共享源工作簿与目标模板，
  含 UTF-8 中文元数据、sheet 名、template_family 记录）。本目录整体即一个
  可校验/可初始化的任务根目录（目录名 = 任务根，文件名为 canonical 的
  task.yaml）。
- `task_missing_fields.yaml` / `task_dup_run_id.yaml` / `task_bad_refs.yaml` /
  `task_empty_runs.yaml` / `task_parse_error.yaml` — 非法示例，分别覆盖
  缺字段 / 重复 run id / 引用不存在与输出名不合法 / 空 runs / YAML 语法错误。
- `sources/parameter_book.xlsx`、`templates/filling_template.xlsx` — 最小真实
  工作簿占位文件，只用于静态"引用存在"校验（issue 01 不读内容、无 Office
  依赖）。issue 08 的 E2E 合成工作簿（1 源 3 sheets + 4 run 共享设计）届时
  将替换/扩充此目录；测试运行时绝不通过 openpyxl 现场生成。