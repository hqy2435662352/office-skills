# Table Fill — Routing V2：Grid Fast Path + Exception Routing

Status: resolved

> 本 spec 的决策（R2-Q1…Q8）已经过 `/grill-with-docs` 逐项裁决确认。实施授权已给出
> （用户目标指令：按 01→05 逐票派发 subagent 执行，要求调用 implement 技能、顺序实现、
> 主 agent 编排验收），五张票（01–05）已按序执行完毕并逐票验收通过：
> SKILL.md §1.5 重写 / CAPABILITY_EVIDENCE.md §0 两表重构 / test_optimization.py
> 哨兵扩展 / UBIQUITOUS_LANGUAGE.md 12 术语注册落地，四案例演练判定与预期一致
> （Case 1 进入 MOD 前动作序列与 V1 基线完全一致，routing 增量 = 0），
> 哨兵变异验证在案（删 obvious_grid / 能力表改回 SUPPORTED / combined 改回 hybrid
> → 均变红后逐字节还原），`pytest table-fill/tests/test_optimization.py` 全绿
> （349 passed），diff 未触及 `table-fill/scripts/` 与 `references/FILLSPEC.md`。
> 验收记录见各票 `## Comments`。
> 领域决策已立 ADR：`docs/adr/0010-table-fill-task-shape-routing.md`。

## Problem Statement

V1（`.scratch/table-fill-task-shape-routing/`）解决了第一个问题：`form_content` 属于 table-fill 产品能力、但不适用 FillSpec 执行模型，应在 Prepare B 后直接路由到 officecli_native。但后续讨论发现 V1 模型不完整：

1. **客户反馈完整 Grid pipeline 偏慢**——Routing 优化绝不能给正常 Grid 用户再增加任何明显延迟（本轮总原则：Rare-path routing must not tax the common Grid path）。
2. **V1 是 shape→route 1:1**：`grid_record → fillspec` 写死，无法表达 Case C（grid 语义但 trivial，不值得启动完整 Grid pipeline）——"Grid applicable 不等于 Grid justified"。
3. **无 Hybrid 表达**：Case D（80 条产品明细 + Logo/客户名/备注/行高等可分离非 Grid 工作）被强制二选一。
4. **V1 矩阵误导**：`grid_record SUPPORTED SUPPORTED fillspec` 一行暗示 grid_record ≡ fillspec；能力语义与执行路由被揉在一个矩阵里。

## Solution

`task_shape`（workload 本质）与 `route`（执行选择）正式解耦为两个正交维度：

| 维度 | 值域 | 含义 |
|---|---|---|
| `task_shape` | `grid_record` / `form_content` / `mixed` / `uncertain` | 任务本身是什么（`uncertain` 是临时判定态，非稳定类型） |
| `route` | `fillspec` / `officecli_native` / `combined` | 本次怎么执行（`combined` 是 fillspec+officecli 的组合执行，**不是第三引擎**） |

路由架构：

```text
Prepare B（Agent 本来就要读 digest）
        │
   Obvious Grid?  ──YES──► GRID FAST PATH: grid_record/fillspec,
        │                    evidence=["obvious_grid"], 立即进 MOD
        │                    （0 新增动作，禁止继续 routing 分析）
       NO（出现明确异常信号才继续）
        │
   Exception Routing:
        ├─ Direct   : bounded/explicit 写集合 + 无 Grid 实质收益
        │             → grid_record + officecli_native
        ├─ Non-Grid : 内容/版式组合（087 类）→ form_content + officecli_native
        └─ Combined : substantial grid + 明显可分离 non-grid
                      → mixed + combined（否则进 uncertain）
```

**Applicability ≠ Justification**（正式入文）：FillSpec 能表达（APPLICABLE）不等于这次该用（JUSTIFIED）。

## 决策记录（R2-Q1…Q8，已逐项裁决）

| # | 决策 | 锁定内容 |
|---|---|---|
| Q1 | 正交解耦 | shape×route 两维度；shape 新增且仅新增 `mixed`（workload 本质）；`direct` 永不作为 shape；route 用 `combined` 而非 `hybrid`（与 FILLSPEC "hybrid overflow" 消歧） |
| Q2 | Level 0 | Obvious Grid Fast Path 寄生在读 digest 的既有动作上——非新步骤、非 default-to-grid fallback；evidence 固定 `["obvious_grid"]`；验收=可观测动作不变式（0 新增 LLM 调用/工具/inspect/render/脚本/scoring/decomposition/feature extraction）；"不得继续分析" stop-rule |
| Q3 | Direct | 双必要条件：①目标写集合执行前 bounded/explicit（自检句："OfficeCLI batch 本身能不能成为完整执行计划"）；②无需 Grid 专业能力（record-driven iteration/dynamic rows、clone/placeholder/inplace、lookup、formula/aggregate、group merge、FillSpec 结构治理）。**明显便宜 ≠ 算出便宜**（判断 Direct 需要复杂成本估算就停止路由优化）。触发层 single-cell 排除不动 |
| Q4 | Combined | 正式支持 mixed/combined，不建第三引擎；**单一 Final Gate 延后至全部写操作完成后**（Grid 治理机制不变，Gate 语义从 Grid 完成门升级为最终 draft 完成门，preserve promote 哈希绑定）；ownership 仅 Agent 执行约束（不建 DSL/ownership 文件，task_shape.json 不扩成 region manifest）；仅"明显可分区"启用，否则 uncertain；Grid first 为默认顺序非绝对；统一 QA |
| Q5 | Evidence | snake_case 短 code + 最小充分证据（不锁数量上限）；canonical 八码起步不封闭枚举（新理由在真实 benchmark 反复出现才晋升）；Routing Accuracy 口径 = task_shape+route 正确性，evidence 只做误判诊断 |
| Q6 | 矩阵 | 拆两张表：能力语义表两行（FillSpec Model 列 `APPLICABLE`/`NOT_APPLICABLE`，mixed 不占行）+ 路由决策表四行；`Applicability ≠ Justification` 入文 |
| Q7 | 落点 | 四文件：SKILL.md（流程）/ CAPABILITY_EVIDENCE.md（权威语义）/ test_optimization.py（守架构不变量）/ UBIQUITOUS_LANGUAGE.md（12 术语注册+消歧）；**FILLSPEC.md 不动、scripts 零改、prepare_run 零改**；evidence code 不进 glossary |
| Q8 | 验证 | 四案例纯文本演练 + 进 MOD 前工具动作序列与 V1 基线一致 + 哨兵变异验证 |

## 四案例映射（canonical examples，写入文档）

| Case | task_shape | route | evidence |
|---|---|---|---|
| 复杂报价单（数十~数百 records + lookup/formula/clone/aggregate） | `grid_record` | `fillspec` | `["obvious_grid"]` |
| 3~5 个固定 cell 映射（甚至 30 cell 固定区域复制，无 record-driven 语义） | `grid_record` | `officecli_native` | `["bounded_explicit_edit","no_material_grid_benefit"]` |
| 087（多格内容重组/图片/版式/固定 merged form） | `form_content` | `officecli_native` | `["content_composition","layout_or_object_work"]` |
| 产品明细 80 records + Logo/客户名/备注/行高 | `mixed` | `combined` | `["substantial_grid_workload","separable_non_grid_workload"]` |

反例锚点（防数量阈值思维）：只有 3 条记录但需要 lookup/clone/公式/group merge → Grid；200 行明显 Grid 不值得讨论 Direct。

## Combined 最小契约（ticket 01 落点）

```text
Prepare → mixed decomposition → Grid 数据/结构执行 + readback/结构验证
→ OfficeCLI finishing（仅触及明确可分的 non-grid workload，
   不得修改或失效 Grid-owned region / structural invariants）
→ Unified QA（Grid 数据与结构仍正确 + finishing 正确 + validate/issues/html）
→ 单一 Final Gate（锁最终 draft）→ promote → delivery
```

- ownership：一个 side effect 一个 executor owner，第一版仅 Agent 执行约束；
- 启用前提：①Grid workload 确实值得 Grid；②Non-grid workload 清晰可分离；
  两个 workload 高度缠绕 → 进 uncertain，不硬拆；
- 默认 Grid first（结构稳定后做固定坐标/layout 编辑更安全），非绝对。

## Evidence vocabulary（canonical 起步，不封闭）

| Code | 含义 |
|---|---|
| `obvious_grid` | 明显常规 Grid，Fast Path 唯一 evidence |
| `bounded_explicit_edit` | 写集合执行前已明确且有限 |
| `no_material_grid_benefit` | Grid pipeline 无实质收益 |
| `content_composition` | 核心是内容组合而非 record transformation |
| `layout_or_object_work` | 核心涉及版式/图片/Shape 等 non-grid 对象 |
| `substantial_grid_workload` | 存在值得启用 Grid 的工作 |
| `separable_non_grid_workload` | 同时存在清晰可分离的 non-grid 工作 |

uncertain 可用：`insufficient_routing_evidence` / `conflicting_workload_signals` / `task_intent_ambiguous`（临时态，不扩设计）。

## Non-goals（除非发现事实明确证明必须，否则不做）

不新建 task_classifier.py；不改 prepare_run 增 routing feature；不引入 picture/shape count、row repeat score、Grid complexity score、route confidence 数值；不建 routing state machine、Hybrid executor、ownership DSL、routing gate、routing receipt；不为 officecli 路径复制 Grid governance；不改 FillSpec schema 吸收 officecli 能力；不加 wrapText/行高/图片 props；不为简单任务建第三套 DSL；不为 rare path 增加正常 Grid 的工具调用；不借 Routing 优化重构 Grid Pipeline（**Routing 慢 ≠ Grid 自身慢**）。FILLSPEC.md 本轮零改动。

## Acceptance Criteria（全 feature 完成定义）

1. 四文件按 tickets 修改；`git diff` 不触及 `table-fill/scripts/` 与 `table-fill/references/FILLSPEC.md`
2. `pytest table-fill/tests/test_optimization.py` 全绿（含扩展哨兵）
3. 四案例演练判定与预期一致；Case 1 验收 = 进入 MOD 前的工具动作序列与 V1 基线完全一致（routing 增量 = 0 的可观测证据）
4. 哨兵变异验证：改回 `hybrid`、删路由表、矩阵改回 `SUPPORTED`、删 `obvious_grid` 均变红
5. UBIQUITOUS_LANGUAGE.md 注册 12 术语 + combined ≠ hybrid overflow 消歧；evidence code 不入 glossary

## Evidence / References

- V1 spec 与 tickets：`.scratch/table-fill-task-shape-routing/`（全部 resolved）
- ADR：`docs/adr/0010-table-fill-task-shape-routing.md`
- 当前文档事实：`table-fill/SKILL.md` §1.5（V1 三态分流，:115-151）、权威模型表（:73）；`table-fill/references/CAPABILITY_EVIDENCE.md` §0（V1 单矩阵，:12-76）；`table-fill/tests/test_optimization.py` DocCoverageGuardTests（:4905+，路由哨兵 :5142-5190）
- 术语冲突事实：FILLSPEC "hybrid overflow"（references/FILLSPEC.md:853，inplace 位置模型，与本轮路由 `hybrid` 候选词冲突）
- 既有性能观测：run_timing.json（machine 相位）+ note_phase.py（agent 相位）——墙钟仅作参考，行为不变式是权威验收
- 087 材料与 V1 演练产物：`C:\Temp\tablefill\route_rehearsal_087_v2\`、`route_rehearsal_grid_v2\`

## Tickets

| # | 票 | 范围 | Blocked by |
|---|---|---|---|
| 01 | SKILL.md：Routing V2 流程（Level 0 / Exception / Combined 契约 / 值域） | SKILL.md | — |
| 02 | CAPABILITY_EVIDENCE.md：两表重构 + evidence 词表 + Applicability≠Justification | CAPABILITY_EVIDENCE.md | — |
| 03 | test_optimization.py：哨兵扩展（守架构不变量） | tests | 01, 02 |
| 04 | UBIQUITOUS_LANGUAGE.md：12 术语注册 + 消歧 | glossary | — |
| 05 | 四案例演练（含动作不变式验收 + 哨兵变异） | 验证 | 01, 02, 03 |
