# 04 — UBIQUITOUS_LANGUAGE.md：12 术语注册 + hybrid overflow 消歧

**What to build:** 在仓库根 `UBIQUITOUS_LANGUAGE.md` 补注册 Routing 稳定领域术语（V1 欠账 + V2 新词）：`task_shape` / `route` / `grid_record` / `form_content` / `mixed` / `uncertain` / `fillspec` / `officecli_native` / `combined` / `applicability` / `justification` / `obvious_grid_fast_path`，每条一句核心含义；并明确消歧 `combined`（routing）≠ FILLSPEC "hybrid overflow"（inplace 位置模型）。**evidence code 不注册进 glossary**（它们是判定标签，住 CAPABILITY_EVIDENCE，且允许 benchmark-driven 晋升）。

**Blocked by:** None — can start immediately.

**Status:** resolved

## Agent Brief

**Category:** documentation（领域词汇债清偿；零代码变化）

**Authoritative context:** 以父 Spec「Table Fill — Routing V2」R2-Q7 锁定为准。UBIQUITOUS_LANGUAGE.md 是仓库统一领域语言；本票只加 routing 术语，不清理与 routing 无关的既有词汇。术语定义必须与 SKILL.md/CAPABILITY_EVIDENCE.md 的最终文案一致（本票可与 01/02 并行起草，但合并前需对齐措辞）。

### Current behavior

- UBIQUITOUS_LANGUAGE.md 对 V1 已落地的 `task_shape/route/grid_record/form_content` 零条目——同一概念可能在不同文档各说各话；`hybrid` 一词在 FILLSPEC（hybrid overflow）与候选路由名之间存在过载风险。

### Desired behavior

- 注册 12 个术语（每条一行核心含义 + 归属文件指针），示例语义：
  - `task_shape`：workload 本质（grid_record/form_content/mixed/uncertain），不代表执行方式；
  - `route`：当前 run 的执行选择（fillspec/officecli_native/combined）；
  - `combined`：FillSpec + OfficeCLI 的组合执行，不是第三引擎；
  - `applicability` ≠ `justification`：模型能否自然表达 vs 本次是否值得启用；
  - `obvious_grid_fast_path`：明显 Grid 时不继续 routing 分析的主路径；
- 消歧条目：`combined`（table-fill routing）≠ FILLSPEC "hybrid overflow"（inplace 克隆溢出）；
- **不**注册 evidence code（bounded_explicit_edit 等留 CAPABILITY_EVIDENCE）。

### Acceptance criteria

- UBIQUITOUS_LANGUAGE.md 可 grep 到 12 术语与消歧句；
- 术语含义与 SKILL.md/CAPABILITY_EVIDENCE.md 最终文案无冲突；
- 未改动 scripts/、FILLSPEC.md 与其他 skill 文件。

### Comments

**验收记录（resolved，主 agent 逐票验收）— 验收通过。**

- **改动范围**：仅仓库根 `UBIQUITOUS_LANGUAGE.md`（新增「## Table Fill routing (Task Shape Check — Routing V2)」分节 :72-89 + 「Flagged ambiguities」追加 1 条消歧 :99；79→99 行，除新条目外零改动，实施者 %TEMP% 快照 SHA256 前后对比确认）。
- **12 术语注册（主 agent 逐条比对 SKILL.md §1.5 与 CAPABILITY_EVIDENCE.md §0 文案）**：`task_shape` / `route` / `grid_record` / `form_content` / `mixed` / `uncertain` / `fillspec` / `officecli_native` / `combined` / `applicability` / `justification` / `obvious_grid_fast_path` 全部注册，核心含义与两处源文案一致（含：shape 不代表执行方式、route 三值、combined 组合执行非第三引擎、applicability≠justification（自然表达 vs 值得启用）、obvious_grid_fast_path 固定 evidence `["obvious_grid"]` 且禁止继续 routing 分析）；每条含归属文件指针（SKILL.md §1.5 / CAPABILITY_EVIDENCE.md §0.1-0.3）。
- **消歧**：Flagged ambiguities 新增「combined（table-fill routing）≠ FILLSPEC "hybrid overflow"（references/FILLSPEC.md:853，inplace 位置模型克隆溢出），hybrid 不作为 route 名出现」——满足 AC「combined ≠ hybrid overflow 消歧」。
- **evidence code 不入 glossary**：边界说明句（:89）明确 obvious_grid 等七码 + uncertain 三码为判定标签、归 CAPABILITY_EVIDENCE §0.4、不封闭、benchmark 反复出现才晋升；各 code 仅出现在边界句或 fast path 含义的必要内嵌，未注册为独立词条 —— 满足 AC「evidence code 不入 glossary」。
- **主 agent 微调**：`grid_record` 行 Aliases to avoid 单元格「指文件本身」改为「（误用作）文件本身」（表述清晰化，语义不变）。
- **语言选择说明**：该文件既有内容为英文、源文案为中文，实施者按「沿中文源措辞、语义零漂移」选中文定义并保持既有英文分节标题与三列表格结构——已接受（一致性以语义为准），如需全英文化属后续风格议题，不影响本票验收。
- **结论**：验收通过，Status 置 resolved。
