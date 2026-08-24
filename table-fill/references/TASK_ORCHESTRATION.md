# Task Orchestration（单任务多 run 批量编排）— 唯一详细契约源

> 本文件是 Task Layer 的**唯一详细契约源**。SKILL.md 只提供入口（何时用 +
> 三个脚本名 + 指向本文件）。本文件描述 contract / invariants / allowed
> behavior；**不描述实现细节**（无 Python 类名、函数名、内部目录结构）。
> 机制问题先查本文件；本文件未回答的问题按 SKILL.md「能力求证」路径处理。

## 0. 定位与边界（为什么有这个层）

Task Layer 解决「单任务多 run」（一个业务任务包含多条 run，如一个客户的多条
产品线/输出，共享同一源工作簿与参数 sheet）的结构性问题：

- **重复准备**：同一 (file, sheet) 被每条 run 重复 flatten（埃及复盘 27 次
  flatten / 38.1 分钟机器时间，占可统计机器阶段约 82%）；
- **无批量编排**：批量 spec 生成、共享准备、聚合 Gate、进度报告全靠 agent
  手写临时脚本，不可复用、不可验证；
- **run 生命周期无治理**：废弃 run 与活 run 混放，耗时归因靠事后手工汇总。

职责分离（两层不通过共享 spec 连接，通过 manifest 与 artifact 引用连接）：

| 层 | 职责 | 事实载体 |
|---|---|---|
| Task Layer | 生命周期、共享准备、聚合交互 | `task.yaml` / `task_manifest.json` / `task_status.json` / `cache/` / `gate_summary.json` / `outputs/` |
| Run Layer | 业务事实、编译、执行、验证 | `runs/<id>/fill_spec.yaml` / `prepare_manifest.json` / plan / receipt（语义完全不变） |

命名语义是 **Task Orchestration**（不是 batch execution）；不使用
`batch_*` / `multi_run_*` 命名。

**允许 / 禁止**：

- 允许：task 级共享源准备事实（staging、outline、flatten 产物、指纹）；
- 禁止：task 级业务语义 —— 映射、lookup、transform、formula、validation
  rule 永远只在 `runs/<id>/fill_spec.yaml`（MOD 规则指导撰写）。`task.yaml`
  是 MOD Resolution 产物的**消费方**，不是生产方；
- 禁止：task database、DAG engine、worker pool、跨阶段流水线、cache lock；
- 禁止：修改 Run 层任何契约（fill_spec 仍是唯一业务事实源；Compiler /
  Executor / Gate / Promote 对 task 存在零感知）；
- PPTX 目标当前不支持 task 层（显式拒绝，不静默降级）。

## 1. Task Model（三层文件）

canonical/derived 分离与既有哲学同构：

| 文件 | 定位 | 写者 | 可变性 |
|---|---|---|---|
| `task.yaml` | canonical：Task Definition（run 清单 + 输入输出引用 + 输出命名） | Agent（映射确认后） | 可通过新版本修改 |
| `task_manifest.json` | derived：Task Prepare Snapshot（文件登记 + SHA-256 + outline + 缓存引用） | 脚本（prepare 阶段） | 封存后不手改、不静默重派生 |
| `task_status.json` | derived：Runtime State（每 run 生命周期状态索引） | 脚本（单一写者） | 不手改 |

### 1.1 `task.yaml` — canonical Task Definition（Agent 撰写）

Schema：

```yaml
task:
  id: <task id>            # 必填，ASCII
  customer: <客户名>        # project metadata，非业务规则
  notes: <自由说明>         # 可选
runs:
  - id: <run id>           # 必填，任务内唯一
    source:
      file: <相对任务根或绝对路径的源工作簿>
      sheets: [<sheet 名>, ...]   # 必填；阶段 1 eager 预展平的需求来源
    target:
      template: <目标模板路径>
      sheet: <要展平的目标模板 sheet 名>   # 必填（目标结构指纹与需求收集的前提）
      output: <输出文件名，落 <task_root>/outputs/>
    template_family: <模板族声明>   # 可选，仅作记录（D6 未实现）
```

关键约束（静态校验，defect 清单 + exit 3）：

- run id 唯一；`source.file` / `target.template` 引用必须存在（相对路径按任务
  根目录解析）；
- 业务规则键（mapping / lookup / transform / formula / validation）**禁止**
  键入 task.yaml，键入即校验拒绝；
- 任务根目录与全部 artifact 名称必须 ASCII。

**共享事实声明是派生语义，不是显式字段**：跨 run 相同的 (file, sheet) 需求
由 run 清单去重得出（eager 预展平的唯一需求集），task.yaml 不设独立共享声明
节 —— 声明的是"有哪些 run 及其引用"，共享集由脚本收集。

### 1.2 `task_manifest.json` — derived Task Prepare Snapshot（脚本写）

语义 = 「这个任务基于什么输入」。`--init` 落盘时记 `frozen_at` 封存：
此后的准备事实（staged 文件 + SHA-256、outline 文本、flatten 缓存引用
`{cache_key, source_hash}`、模板/结构指纹）由 prepare 阶段一次性填齐，**不再
静默重派生**。`task.yaml` 自封存后变化 → `MANIFEST_STALE`（fail-closed，
走 supersede 路径，见 §4）。

### 1.3 `task_status.json` — derived Runtime State（脚本写，单一写者）

语义 = 「这个任务运行到了哪里」。每 run 一条 `{state, superseded_by}`，
`updated_at` 每次执行更新。状态集合：

`planned → prepared → compiled → drafted → gated → promoted`，
`superseded` 是保留证据的终止分支。

**status 是生命周期索引，不是真值源**：断点判定一律以 artifact 存在性 + hash
为准（见 §4 矩阵）。手改 status / manifest → 一致性缺陷（fail-closed）。

## 2. Cache Contract（task-local flatten cache）

- **位置与生命周期**：`<task_root>/cache/<key>/`，生命周期绑定 task root
  （task 归档即 cache 归档）。不做 global cache / LRU / TTL / eviction /
  共享 cache flag。
- **缓存键**：`SHA-256` 四分量（staged source hash + sheet name +
  flatten schema version + officecli version），四分量以确定性编码拼接消除
  跨分量边界歧义；键内**不含任务身份**（同一 (file, sheet, 版本) 在任何任务根下
  键相同）。键只定位缓存，**不承载业务语义**；是优化 metadata，不参与任何
  业务事实判断。
- **内容白名单**：只允许 `flat.csv` / `meta.json` / `digest.md`；run 产物
  （spec / plan / receipt / draft）禁止入缓存。
- **eager 预展平**：阶段 1 对 task.yaml 声明的全部源 sheet 预先展平，**每个
  缓存键恰好一个 worker**；命中者零 officecli 调用。禁止 run 内 lazy
  flatten —— 并发写同一缓存键从结构上不存在，**不引入锁**。
- **物化（Materialize）**：缓存产物以单 run 命名逐字节复制进各 run 工作目录
  （`<staged>_<sheet>_flat.csv` 等）后才进入 compile；物化是毫秒级文本复制。
  禁止 `../../cache` 引用路径；禁止 symlink / junction。
- **Cache Identity ≠ Run Artifact Identity**：cache key 是能否复用的判定；
  物化后 run 侧 CSV 的 hash / fingerprint 才是本 run 实际输入的业务身份
  （Run Artifact Identity）。二者是 materialize 关系，不是相等关系。
- **run artifact 自包含**：物化产物进 manifest 后，run 可脱离 task cache
  独立归档、迁移、复验。
- **Cache 只加速 Prepare**：compile 期 input_hashes 绑定、execute 期重算
  比对、promote 三方核对语义全部不变；缓存命中 ≠ 指纹豁免。

## 3. Scheduler Contract（barrier 式阶段调度）

`--run` 完整编排 6 个阶段（`--prepare` 只走阶段 1–2）：阶段内并行、阶段间
barrier、**无跨阶段流水线 / 无 DAG / 无 worker pool**。

| # | 阶段 | 并发默认 | 性质 |
|---|---|---|---|
| 1 | source prepare / flatten / cache build | 2 | Office 密集（cache miss 时） |
| 2 | run prepare（物化 + target prepare + manifest 组装） | 2 | Office 密集（target flatten） |
| 3 | compile | 4（spec 区间 4–8，实现常量取 4） | 纯文本 |
| 4 | execute（validate / readback / render QA） | 2 | Office 密集（实证安全上限） |
| 5 | gate（逐 run 建立 pending 证据） | 1 | 串行；聚合呈现见 §5（一次人机交互） |
| 6 | promote | 2 | 纯文件 |

- **并发默认值是 implementation constant**：不进入 task.yaml、不暴露 CLI
  调参（环境稳定性参数不得污染任务定义）。execute 并发 = 2 是 Office 稳定性
  边界（Windows resident 进程持文件锁，3 并发曾实证 `validate_state=fail`），
  不是理论吞吐参数——环境经验，非架构契约。
- **单一写者**：worker 只向主进程回报结果（状态码 + 产物路径）；主进程在
  阶段边界统一批量更新 `task_status.json` 一次——阶段内并发时状态文件**零
  并发写**。
- **失败传播**：任一 run 在阶段失败不阻断同阶段其他 run；阶段结束汇总失败
  清单（带阶段归属），失败 run 按其状态不推进。阶段 1（共享准备）失败
  fail-closed 停止。
- **进度**：阶段边界输出人读进度行（stderr）；stdout 只承载结构化 JSON。
- **用结构消除并发冲突，不靠锁**（阶段 1 eager 预展平保证每缓存键恰一
  worker）。

## 4. Lifecycle / Resume / Supersede

### 4.1 状态机

主路径 `planned → prepared → compiled → drafted → gated → promoted`；
`superseded` 是保留证据的终止分支（input fact changed 触发）。

### 4.2 断点判定矩阵（artifact 证据，不是状态字段）

resume 以产物存在性 + hash 校验确定实际断点：

| 证据（存在性 + hash） | 断点判定 | 后续行为 |
|---|---|---|
| 无任何产物 | planned | 阶段 1 |
| manifest 有效 + 物化产物 hash 匹配 | prepared | 跳过阶段 2 |
| plan 的 fill_spec hash 匹配 + input_hashes 绑定有效 | compiled | 跳过阶段 3 |
| draft 存在 + receipt 的 draft hash 匹配 | drafted | 直接进 gate |
| **draft 存在但 receipt 缺失 / 不匹配** | **execute retry（crash window）** | **重跑 execute**，不得直接 gate |
| `.gate3_pending` 有效（hash 三元组） | gated | 等待确认，不绕过 |
| `final_receipt.json` 存在 | promoted | 跳过 |
| superseded | superseded | 跳过（除非显式 rebuild） |
| 源文件 hash 与 manifest 不符 | blocked | 阻塞 + 建议 supersede，不继续旧 run |

**execute crash window**：execute 写 draft 与写 receipt 之间崩溃 → draft
存在但 receipt 缺失。此时不得凭 draft 存在性直接 gate/promote——验证证据链
不完整，必须重跑 execute。

### 4.3 失败二分

- **输入事实未变** → 阶段重试或 REPAIR（修 fill_spec → 重编译，与单 run
  语义一致）；
- **输入事实改变**（task.yaml 修改、源 hash 漂移、target 模板重建、MOD/
  映射裁决变化）→ **supersede 该 run**（run 级，非 task 级），禁止在旧 run
  上继续修补。

### 4.4 Supersede 契约

- 旧 run 产物**完整保留**（fill_spec / manifest / plan / draft / receipt /
  timing），状态标 `superseded` + `superseded_by` 链接新 run 版本（如
  `<id>_v2`）；新 run 是独立版本；
- 是**唯一**被授权在 task.yaml 变化后重派生输入快照的路径（解冻）；
- superseded 按状态过滤，**不删文件**。

### 4.5 Resume 契约

- `resume_task.py --resume` 是**唯一恢复入口**：task 入口、run 粒度断点、
  产物 hash 校验定断点、恢复后继续剩余阶段（compile → execute → gate
  barrier）；
- **不自动跳过 Gate、不自动 promote**（fail-closed 不变）；
- 跳过 promoted / superseded run；`--rebuild` 显式把 superseded run 按产物
  证据重新进入主路径；对已确认（confirmed）run 不重复呈现，等待
  gate_task 展开 promote。

## 5. Gate Aggregation（一次人机交互 + 逐 run 审计）

`gate_task.py` 的两个相位（与单 run Execution Gate 的 set/confirm 语义一致，
fail-closed 不变）：

- **`--set`（聚合呈现）**：收集全部产物证据 ∈ drafted/gated 的 run，生成
  `<task_root>/gate_summary.json`：
  - 每 run：id、输出名、关键校验摘要（readback / 来源覆盖 / issue delta /
    structural / render_qa / validate）、spec/plan/draft SHA-256；MOD 裁决
    摘要；缺口（gaps）列全；
  - 未完成 run 不入呈现（excluded 列出，含 reasons）；
  - 已确认待 promote 的 run 单独呈现为 `confirmed` 未决项（授权已落账，
    不重复呈现）；
  - 含 task 级 timing 双栏（见 §6）；
  - Draft 就绪但无有效 pending 的 run 先补 `execution_gate --set`（每个
    run 的 pending 绑定自己的哈希三元组）再收集；
  - 呈现后**停**：不自动确认、不自动 promote。
- **`--confirm`（逐 run 确认展开）**：
  - 按呈现集合（task.yaml 声明顺序）逐 run `execution_gate --confirm`，
    每个 run 的 `.gate3_confirmed` 独立绑定自己的哈希三元组；
  - **任一 run 确认失败 → 整体停止并报告该 run**（不静默跳过、不继续确认、
    不进入 promote）；
  - 全部确认后逐 run `promote_output.py --final`，输出落
    `<task_root>/outputs/<target.output>`；final hash == 已确认 draft hash
    由 promote 校验；HASH_DRIFT 拒绝逻辑与单 run 完全不变；同路径双 run
    冲突 fail-closed 拒绝；
  - promote 并发 2；`task_status.json` 在 promote 边界写盘一次（单一写者）；
  - 幂等：无任何待确认/待交付内容时 NOOP 成功退出。

## 6. Timing 双栏（active / superseded cost）

- 每 run 的 `run_timing.json` 保留不动（现有机制 append，证据不删）；
- task 聚合报告分两栏（术语见 CONTEXT.md：**Active Cost / Superseded Cost**）：
  - **Active Cost 栏**：活 run（状态 ∈ prepared..promoted 且非 superseded）
    的耗时 —— 本次交付成本；
  - **Superseded Cost 栏**：superseded run 的耗时 —— 可避免的浪费/返工成本
    （优化价值的量化证据）；
  - excluded（planned / 未知状态）不进任一栏，透明列出；
- 两栏均带 per-run 明细（哪条废弃 run 贡献了多少）；按 kind + phase 跨 run
  分组（count / total / average / min / max / percent_of_kind）；
- 聚合逻辑**只读**：不写任何文件，superseded 证据保留不动；
- 报告随 Gate 呈现（`gate_task --set` stdout 携带双栏合计；完整分组在
  `gate_summary.json`）；独立 CLI 也可随时查看（见 §7）。

## 7. CLI 用法

任务根目录 = 含 agent 撰写的 `task.yaml` 的目录（ASCII）。

### 7.1 `prepare_task.py`

```
python scripts/prepare_task.py --task-root <dir> --validate
python scripts/prepare_task.py --task-root <dir> --init
python scripts/prepare_task.py --task-root <dir> --prepare
python scripts/prepare_task.py --task-root <dir> --run
```

| 模式 | 行为 |
|---|---|
| `--validate` | 仅静态校验 task.yaml，不写任何文件 |
| `--init` | 校验 + 首写 `task_manifest.json` / `task_status.json`（全部 planned）；派生文件已存在则校验一致性 |
| `--prepare` | 阶段 1–2：staging + outline（任务级一次）+ eager 展平缓存 + 逐 run 物化与 manifest 组装 |
| `--run` | 阶段 1–5 完整编排（prepare → compile → execute → gate）；gate 阶段逐 run 建立 pending 证据后在阶段边界停（不聚合呈现、不自动确认） |

### 7.2 `gate_task.py`

```
python scripts/gate_task.py --task-root <dir> --set
python scripts/gate_task.py --task-root <dir> --confirm
```

| 模式 | 行为 |
|---|---|
| `--set` | 聚合呈现 → `gate_summary.json`；呈现后停（等待用户确认） |
| `--confirm` | 逐 run 确认 + promote（并发 2） |

### 7.3 `resume_task.py`

```
python scripts/resume_task.py --task-root <dir> --resume [--rebuild]
python scripts/resume_task.py --task-root <dir> --supersede --map old=new [--map ...]
```

| 参数 | 行为 |
|---|---|
| `--resume` | 断点恢复：继续剩余阶段；跳过 promoted/superseded；不绕过 Gate、不自动 promote |
| `--rebuild` | 与 `--resume` 配合：superseded run 按产物证据重新进入主路径（显式重建） |
| `--supersede --map old=new` | 失败二分处置：标记旧 run superseded + `superseded_by` 链接 + 重派生输入快照（唯一解冻路径）；`--map` 可重复 |

### 7.4 `timing_task.py`（独立只读报告 CLI，spec S7 授权的"独立脚本输出"）

```
python scripts/timing_task.py --task-root <dir>
```

任意时刻查看 active / superseded 双栏聚合报告（与 `gate_summary.json` 的
`task_timing` 块同源）；只读，不写任何文件。任务层编排命令面是上方三个脚本；
本脚本是 issue 06 落地的补充报告入口。

### 7.5 退出码（与套件一致）

| Exit | 含义 | 处置 |
|---|---|---|
| 0 | PASS | 继续 |
| 1 | Fatal（env / file：task root 缺失、非 ASCII 路径、officecli 不可用、task.yaml 不可读） | 修正环境/路径后重跑 |
| 3 | Retryable（静态校验缺陷、封存/状态一致性缺陷、阶段失败清单、源漂移阻塞） | 读结构化缺陷（code + message + corrective_action）定向处置后重跑 |

失败输出契约（双通道）：

- **守卫级错误**（进度行之前：缺文件、task.yaml 不可读、一致性/漂移缺陷）→
  结构化 JSON 走 **stderr**，与单 run 套件 `fail()` 契约一致；
- **阶段失败清单**（`--prepare` / `--run` 的 run 级失败）→ 结构化 ERROR JSON
  走 **stdout**（含 `defects` 列表，带阶段归属）；
- **人读进度行只走 stderr**；stdout 只承载 JSON（成功 RESULT 或阶段失败清单）。

## 8. 典型流程（allowed sequence）

1. Agent 撰写 `task.yaml`（run 清单 + 引用；映射确认后）；
2. `prepare_task.py --init`（必要时先 `--validate`）；
3. `prepare_task.py --run`（阶段 1–5 完整编排；gate 阶段只逐 run 建立
   pending 证据，不呈现）；中断可随时 `resume_task.py --resume`；
4. `gate_task.py --set` → **唯一一次**人机交互：向用户呈现
   `gate_summary.json`（含 timing 双栏）→ 用户确认；
5. `gate_task.py --confirm`（逐 run 确认 + promote 到 `outputs/`）；
6. 输入事实变化（task.yaml/源/模板/MOD 裁决）→ `resume_task.py --supersede --map old=new` → 新版本继续。

## 9. 与 spec 的关系

本文件对应 spec「Task Orchestration Layer」的契约/行为面（S1–S7 的
contract/invariants/allowed behavior；S8 验证的证据面 —— 断点矩阵、Gate
校验摘要、cache 命中语义；S9 文档职责分离即本文件与 SKILL.md / KNOWN_TRAPS /
CONTEXT.md / ADR-0009 的分工）。S8 的测试分层与性能验收的测试侧定义在 spec
（Testing Decisions），不属于本文件范围。实现决策背景与验收见
`.scratch/table-fill-task-orchestration/spec.md`（issue tracker 本地文件）。