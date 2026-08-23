# tests/_fixtures/task_orchestration/ — Task Artifact 模型 fixtures（issue 01 + 02）

- `task.yaml` — 合法 task.yaml 示例（3 run 共享源工作簿与目标模板，
  含 UTF-8 中文元数据、sheet 名、template_family 记录）。本目录整体即一个
  可校验/可初始化/可 prepare 的任务根目录（目录名 = 任务根，文件名为 canonical
  的 task.yaml）。`target.sheet` 是 issue 02 阶段 1 eager 预展平的需求来源
  （对应占位工作簿中的真实 sheet 名：源书 R32参数/R410A参数，模板 Sheet1，
  officecli outline 可验证）。
- `task_missing_fields.yaml` / `task_dup_run_id.yaml` / `task_bad_refs.yaml` /
  `task_empty_runs.yaml` / `task_parse_error.yaml` — 非法示例，分别覆盖
  缺字段 / 重复 run id / 引用不存在与输出名不合法 / 空 runs / YAML 语法错误。
- `sources/parameter_book.xlsx`、`templates/filling_template.xlsx` — 最小真实
  工作簿占位文件（sheet: R32参数/R410A参数 / Sheet1）。issue 01 只用于静态
  "引用存在"校验（无 Office 依赖）；issue 02 起真实 sheet 使 --prepare 可在
  安装 officecli 的机器上直接跑通（契约测试本身仍无 Office）。issue 08 的
  E2E 合成工作簿（1 源 3 sheets + 4 run 共享设计）届时将替换/扩充此目录；
  测试运行时绝不通过 openpyxl 现场生成。