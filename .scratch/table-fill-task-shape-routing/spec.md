# Table Fill — Task Shape Routing（087 案例制度化）

Status: resolved

> 本 spec 的设计决策（D1–D10）已经过 `/grill-me` 逐项裁决确认。实施授权已给出，
> 五张票（01–05）已按序执行完毕并由主导 agent 逐票验收：SKILL.md /
> CAPABILITY_EVIDENCE.md / FILLSPEC.md / test_optimization.py 四处文档改动落地，
> 双演练判定与预期一致，`pytest table-fill/tests/test_optimization.py` 全绿
> （342 passed），`git diff` 未触及 `table-fill/scripts/`。验收记录见各票
> `## Comments`。

## Problem Statement

officeval_087（识字《四季小景》→ 分层作业设计）暴露了 table-fill 的第一条**横向边界**：

- 源是二维排版内容（一层作业散在多行多列，含 25 张图/田字格/插图）；
- 目标是固定合并表单页（C4:F4 / C5:F5 / C6:F6 / B3:F3 / B7:F9，无表头+可克隆数据行）；
- 搬运单位是"多格拼成一段文本块 → 塞进一个固定格"，不是"行记录 → 行记录"。

实测证据（见 handoff `C:\Users\Administrator\AppData\Local\Temp\handoff_officeval_087.md` §2 与本仓库代码）：

| 机制 | 事实 |
|---|---|
| 纯 `sets`（无数据块） | `SPEC_SOURCE_CSV`（compile_fill.py:2375）——编译器强制 rows 数据块 |
| `append` 数据块 | `CLONE_SOURCE_IS_ANCHOR` / `CLONE_RESIDUE_UNHANDLED`，会克隆行扩表破坏 8 合并区 |
| `inplace` | 唯一不扩表模式，但要求源行↔目标行 1:1，087 无法成立 |
| props 白名单 | `PROPS_WHITELIST = ("numberformat",)`（compile_fill.py:1314），wrapText/行高/图片均不在此列 |
| digest 信号 | structure_digest.py 无 picture/shape 计数、无行模式重复度 |

结论（已裁决）：这些拒绝码**不是 bug**。它们是引擎在正确表达"这不是 record/grid 数据迁移的世界"。087 的问题不在编译器，在于 **skill 没有在早期把任务分流**——Agent 走完了 FillSpec 探测循环才发现此路不通，浪费了一轮完整 REPAIR 探索。

## Solution

table-fill 定位为 **Spreadsheet-to-template fill 类任务的统一意图入口**，内部按任务形态分流为两条一等执行路径：

```text
用户："把数据/内容填到模板里"
        ↓
table-fill Skill（宽触发，统一入口）
        ↓
Prepare A → Prepare B（digest 已生成）
        ↓
Task Shape Check（LLM 轻量判定，零新脚本）
        ├─ grid_record  → 原 FillSpec/MOD/compile/execute 路径（零改动）
        ├─ form_content → officecli native 路径（一等公民，非 fallback）
        └─ uncertain    → 一次受限补观察 → 重判二选一；仍歧义才 ASK
        ↓
统一 Delivery discipline（各路径按各自 QA 契约交付）
```

三条不可动摇的原则：

1. **form_content 不是 fallback**——不得先跑 FillSpec、失败后再退回 officecli。Task Shape Check 在 Prepare B 后、MOD 前完成，form 任务跳过 MOD/FillSpec 直达 officecli 路径。
2. **编译器契约刻意不动**——不是"本轮风险低所以不动"，而是有意保持两个执行模型正交。FillSpec 引擎 = structured grid/record transformation；表单版式组合是另一个执行模型，不是 FillSpec 欠的 feature。
3. **Rare-path capability must not tax the common path**——95% 的 grid_record 任务运行路径零变化；不新增脚本、不新增 LLM 调用、不新增常规探测。

## 决策记录（/grill-me 已确认）

| # | 决策 | 锁定内容 |
|---|---|---|
| D1 | 产品定位 | 统一意图入口 + 内部分流；`form_content` 在产品层 SUPPORTED，在 FillSpec 层 NOT_APPLICABLE |
| D2 | 分流点 | Prepare B 后 / MOD 前；LLM 读任务文本 + 现有 digest 轻量判定；零新脚本、零额外 LLM 调用、零常规额外探测；grid 任务立即进原 MOD 流程 |
| D3 | 机械信号 | 暂不加 picture/shape 计数、行模式重复度；出现**可归因于缺信号的误判样本**再按证据补（benchmark-driven） |
| D4 | 治理深度 | 薄治理：共享 workdir/source protection/Prepare/Delivery discipline；**不复制** execution_gate/promote/receipt/hash 三元组；officecli-xlsx QA 升级为 form_content 交付前强制条件（先契约，benchmark 验证 adherence 后再考虑机械 enforcement）；仅不可逆操作/真歧义条件 ASK |
| D5 | 固化形态 | 纯文档四件套 + 一致性断言；**无编译器分支、无新缺陷码、无新脚本**。措辞锁死 `NOT_APPLICABLE`（非 UNSUPPORTED、非 Known Rejected）；087 = 矩阵第一条证据，非定义本身 |
| D6 | uncertain | 临时判定态：一次受限补观察（view html + ≤2 次定向 get/query）→ 必须重判二选一；仍存在会实质改变执行模型的歧义才 ASK。**禁止**靠 FillSpec 失败反向发现 form_content |
| D7 | rubric | 不落任何 skill——rubric 不属于正常任务执行输入（修复阶段由用户注入属例外操作） |
| D8 | 落盘 | 每次 run 落极简 `task_shape.json`（task_shape + route + evidence），agent 判定产物，与 prepare_manifest 机器事实分层；未来 Routing Accuracy 观测点 + "为什么没走 FillSpec"审计依据 |
| D9 | 触发词 | 宽触发保留 + description 开头补一句双路径 scope 声明；宽进、早分流；不罗列 087 特征词 |
| D10 | 流程 | 先立 `.scratch/table-fill-task-shape-routing/` spec + tickets（即本目录），实施授权后逐票执行 |

## Task Shape 判据

判定输入永远三项：**任务指令 × 源结构 × 目标结构**。同一文件对不同任务可能走不同路径（如"填 50 条产品明细"→ grid_record；"只填封面客户资料"→ form_content）。

**grid_record 信号**：源有稳定 header + 重复 record 行；目标有可克隆/可重复数据区；映射以列↔列为主；输出行数由源记录数驱动。

**form_content 信号**：目标主体是固定内容区（merged form regions），无可克隆数据行模板；源内容需要跨格/跨行组合后才能写入；主要操作是文本/图片/版面内容填充而非重复 record 映射。

**判定成本约束**：对明确信号一眼可判（digest 读毕即答），不追加 picture scan / HTML render / 额外 query / 第二模型调用。

## 治理模型（form_content 路径）

```text
form_content
    ↓
沿用 table-fill workdir / source protection / Prepare
    ↓
task_shape.json（路由判定落盘）
    ↓
officecli native execution（inspect → atomic edit → adjust）
    ↓
Mandatory QA checklist（officecli-xlsx：validate + view issues + view html + 模板 QA）
    ↓
Delivery report（QA 证据 + 关键内容格摘要 + 改动摘要）
```

- QA checklist 从"建议做"升级为 form_content **交付前强制条件**（第一版为 Skill 契约；只有 benchmark 显示"明知没 QA 仍交付"反复出现才考虑机械 enforcement）。
- 条件 ASK 仅限：覆盖原文件、不可恢复删除、多种合理语义无法判断、明显版面溢出且无压缩策略。
- 明确不继承：execution_gate / promote / FillSpec/plan/draft 哈希三元组 / receipt / 为 form 路径新造 gate 脚本。

## 固化形态（实施范围 = 4 个文件，见 tickets）

1. `table-fill/SKILL.md` — scope 声明 + Task Shape Check 步骤 + form_content 工作流段 + task_shape.json 落盘指令（ticket 01）
2. `table-fill/references/CAPABILITY_EVIDENCE.md` — 任务形态×执行路由两层矩阵 + 判据（ticket 02）
3. `table-fill/references/FILLSPEC.md` — layout composition non-goal 一句（ticket 03）
4. `table-fill/tests/test_optimization.py` — 路由术语一致性断言（ticket 04）
5. 验证：离线测试 + 双演练（ticket 05）

**能力矩阵（两层语义，ticket 02 的权威表述）**：

```text
Task Shape      table-fill Product   FillSpec Engine     Executor
-----------------------------------------------------------------------
grid_record     SUPPORTED            SUPPORTED           fillspec
form_content    SUPPORTED            NOT_APPLICABLE      officecli_native
```

措辞纪律：`NOT_APPLICABLE` 不得写成 `UNSUPPORTED`（后者会被未来解读为"capability gap 待补"）；`form_content` 不得写成 `Known Rejected`（Known Rejected 是"契约已拒绝的行为"，而 form_content 是产品支持的另一条执行路径）。

## Non-goals（明确不做）

- 不给 FillSpec 加 wrapText / 行高 / 图片 / 纯 sets 能力；props 白名单保持 `numberformat` only
- 不改 prepare_run（不加 picture/shape/行模式信号——D3 暂缓，等误判证据）
- 不建 routing 脚本 / 打分器 / classifier / 任何 enforcement runtime
- 不为 form_content 造 gate/promote/receipt/哈希治理
- 不新增编译器缺陷码（现有 SPEC_SOURCE_CSV / CLONE_SOURCE_IS_ANCHOR 等拒绝行为保持原样）
- 不把 rubric 读入任何 skill 的 SOP（D7）
- 不重跑 087 端到端（本次是文档级改动；端到端重跑属 benchmark 验证，另行排期）

## Acceptance Criteria（全 feature 完成定义）

1. 4 个文件按 tickets 修改完成；`git diff` 不触及 `table-fill/scripts/` 任何文件
2. `pytest table-fill/tests/test_optimization.py` 文档一致性类测试全绿（含新增断言）
3. 双演练：087（源文件 + 模板原件跑真实 prepare_run）纯文本 Task Shape Check 判 `form_content`；一个既有 grid workdir（报价单类）判 `grid_record`
4. 未来若有人把 `form_content` 改写成 Known Rejected / UNSUPPORTED，测试 04 变红
5. 087 的 digest 证据与编译器拒绝码作为矩阵的第一条 evidence 记录在案，不成为定义

## Evidence / References

- handoff：`C:\Users\Administrator\AppData\Local\Temp\handoff_officeval_087.md`（任务、实测边界、officecli 复用能力、交付状态）
- 代码事实：`table-fill/scripts/compile_fill.py:1314`（PROPS_WHITELIST）、`:2375`（SPEC_SOURCE_CSV）；`table-fill/scripts/structure_digest.py`（digest 字段面）；`table-fill/SKILL.md:4-8`（宽触发）
- 既有机制：`CAPABILITY_EVIDENCE.md` 三态 + Standard Evidence Path；`test_optimization.py:5089+` 文档关键词断言面
- 087 交付：`D:\benchmarks\OmegaUse-OfficeVal\agent-work\officeval_087\input\分层作业设计.xlsx`（hash `95CFC9CE…`，validate 通过）；模板原件 `C:\Temp\tablefill\officeval_087\target_fenceng.xlsx`

## Tickets

| # | 票 | 范围 | Blocked by |
|---|---|---|---|
| 01 | SKILL.md：双路径 scope 声明与 Task Shape Check 工作流 | SKILL.md | — |
| 02 | CAPABILITY_EVIDENCE.md：任务形态×执行路由两层矩阵 | CAPABILITY_EVIDENCE.md | — |
| 03 | FILLSPEC.md：layout composition non-goal 一句 | FILLSPEC.md | — |
| 04 | test_optimization.py：路由术语一致性断言 | tests | 01, 02 |
| 05 | 双演练验证（087 form_content / 报价单 grid_record） | 验证 | 01, 02, 04 |
