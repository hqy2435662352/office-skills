# 01 — SKILL.md：双路径 scope 声明与 Task Shape Check 工作流

**What to build:** 在 `table-fill/SKILL.md` 落实统一意图入口语义：(a) description 开头补一句双路径 scope 声明（保留全部现有宽触发词）；(b) 工作流在 Prepare Stage B 之后、MOD 提名之前插入 Task Shape Check 步骤（LLM 读任务文本 + 现有 digest 轻量判定，零新脚本/零额外 LLM 调用/零常规额外探测）；(c) 新增 form_content 工作流段（officecli native 执行 + officecli-xlsx QA 升级为交付前强制条件 + 交付呈报 + 条件 ASK）；(d) 每次 run 落极简 `task_shape.json` 的指令。

**Blocked by:** None — can start immediately.

**Status:** resolved

## Agent Brief

**Category:** enhancement（Skill-level routing，不属 FillSpec Compiler 能力开发）

**Authoritative context:** 以父 Spec「Table Fill — Task Shape Routing（087 案例制度化）」的 D1/D2/D4/D6/D8/D9 锁定决策为准。本票是实施范围合同：只改 SKILL.md 文案与工作流结构，不迁移现有 grid 流程语义、不重新裁决产品设计。

### Current behavior

- SKILL.md 触发词宽（"fill/populate/map"、"把数据填到模板里"），必然命中 087 类表单任务，但正文无分流概念，Agent 进入后默认直奔 grid SOP（MOD → FillSpec → compile）。
- 087 实测：纯 sets → `SPEC_SOURCE_CSV`；append → `CLONE_SOURCE_IS_ANCHOR` 扩表；inplace 需源行 1:1——Agent 走完一轮 FillSpec 探测才确认此路不通（form_content 被"失败后 fallback"到 officecli，违反 D1）。
- 无路由判定记录：workdir 里没有 fill_spec.yaml 时无法区分"有意绕过 FillSpec"与"中途失败/漏执行"。

### Desired behavior

- description 开头补一句 scope 声明（大意）：table-fill 是 spreadsheet-to-template fill 的统一意图入口；Prepare 后按 task shape 分流——structured grid/record transformation 走 FillSpec 路径，fixed-form/layout content filling 走 officecli-native 路径。**不罗列 087 特征词（merged cells/images/row height 等不进 description）。**
- 工作流插入 Task Shape Check（Prepare B 后、MOD 前）：
  - `grid_record` → 立即进入原 MOD 流程（95% 任务运行路径零变化，不追加任何探测）；
  - `form_content` → 跳过 MOD/FillSpec，走 officecli native 路径；
  - `uncertain` → 一次受限补观察（view html + ≤2 次定向 get/query）→ 必须重判二选一；仍存在会实质改变执行模型的歧义才 ASK。禁止靠 FillSpec 失败反向发现 form_content。
- 判定输入 = 任务指令 × 源 digest × 目标 digest（不是只看文件长什么样）；对明确信号一眼可判，不追加扫描/渲染/第二模型调用。
- form_content 工作流段：沿用 workdir/source protection/Prepare → 落 `task_shape.json` → officecli 编辑（inspect→atomic edit→adjust）→ **强制**完成 officecli-xlsx QA checklist（validate + view issues + view html + 模板 QA）→ 交付呈报（QA 证据 + 关键内容格摘要 + 改动摘要）。正常任务不默认人审；仅覆盖原文件/不可恢复删除/语义歧义/版面溢出无策略时条件 ASK。
- `task_shape.json`（每次 run，含 grid）极简三字段：`task_shape` + `route` + `evidence`。不塞 confidence/hash/QA 结果/execution history。它是 agent 判定产物，与 prepare_manifest 机器事实分层；不修改 prepare_run。

### Acceptance criteria

- SKILL.md 含 Task Shape Check 步骤、两条一等路径、uncertain 处理、task_shape.json 落盘指令、form_content 强制 QA 与交付呈报；
- 现有 grid 流程（MOD → FillSpec → compile → execute → Gate → promote）文本语义不被改动；
- 未新增任何脚本/命令；未把 form_content 描述成"失败后 fallback"；
- ticket 04 的新增断言通过。

### Comments

**交付说明（只改 `table-fill/SKILL.md` 一个文件）**

改动点清单（What to build 四项全部落地）:

1. **description 双路径 scope 声明** — frontmatter `description:` 开头补一段（保留全部既有宽触发词与段落），声明 table-fill 是 spreadsheet-to-template fill 的统一意图入口，Prepare 后按 task shape 分流：structured grid/record transformation → FillSpec 路径，fixed-form/layout content filling → officecli-native 路径。未罗列任何 087 特征词（merged cells/images/row height/wrapText 均不进 description）。
2. **Task Shape Check 工作流步骤** — 在「## 工作流」示意图之后、`### 2. MOD Resolution` 之前新增 `### 1.5 Task Shape Check (Prepare B 后、MOD 前)`：位置在 Prepare Stage B 之后 / MOD 提名之前；判定输入 = 任务指令 × 源 digest × 目标 digest；明确信号一眼可判（零新脚本、零额外 LLM 调用、零常规额外探测）；三态分流表（`grid_record` → 原 MOD/FillSpec 流程零变化；`form_content` → 跳过 MOD/FillSpec 走 officecli native 一等路径，非 fallback；`uncertain` → 一次受限补观察 view html + ≤2 定向 get/query → 必须重判二选一，仍歧义才 ASK）。
3. **form_content 工作流段** — 新增 `#### form_content 工作流 (officecli native 路径)`：沿用共享 workdir / source protection / Prepare → 落 `task_shape.json` → officecli native execution（inspect → atomic edit → adjust）→ 强制 officecli-xlsx QA checklist（validate + view issues + view html + 模板 QA，从"建议做"升级为交付前强制条件）→ 交付呈报（QA 证据 + 关键内容格摘要 + 改动摘要）。正常任务不默认人审；条件 ASK 仅限 4 类（覆盖原文件/不可恢复删除/语义歧义/版面溢出无策略）；明确不继承 execution_gate/promote/receipt/hash 三元组。
4. **task_shape.json 落盘指令** — 每次 run（含 grid_record）落极简三字段 `task_shape` + `route` + `evidence`；已写入「权威模型」表（Agent 判定，与 prepare_manifest.json 机器事实分层）+「Output Files」清单。不塞 confidence/hash/QA/execution history；未修改 prepare_run；未新增任何脚本/命令名。

自查结果:

- 术语齐全：Task Shape Check / grid_record / form_content / officecli-native / task_shape / uncertain / NOT_APPLICABLE / task_shape.json 全部存在。
- form_content 从未与 "Known Rejected" / "UNSUPPORTED" 绑定表述（唯一 "UNSUPPORTED/Known Rejected" 出现在否定句 "NOT_APPLICABLE — 不是 UNSUPPORTED, 不是 Known Rejected"，正是措辞纪律要求的写法）。
- 现有 grid 流程（MOD Resolution / FillSpec / Compile / Draft Execution / Execution Gate / Promote）文本语义零改动 — 全部为新增插入，未改写既有段落。
- 文档一致性测试子集：`pytest table-fill/tests/test_optimization.py -k DocCoverageGuard -q` → **92 passed, 247 deselected**。

