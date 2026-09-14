# Task Orchestration（单任务多 run 批量编排）— 唯一详细契约源

> 本文件是 Task Layer 的**唯一详细契约源**。SKILL.md 只提供入口（何时用 +
> 入口脚本名 + 指向本文件）。本文件描述 contract / invariants / allowed
> behavior；**不描述实现细节**（无 Python 类名、函数名、内部目录结构）。
> 机制问题先查本文件；本文件未回答的问题按 SKILL.md「能力求证」路径处理。
>
> **v3 收敛注（最终形态，ticket 08 落地）**：Gate / Resume / Supersede / 阶段
> 调度器 / Assembly / task 级 Timing 聚合机制已整体退役（ticket 06/07）。Task
> 收敛为**数据组织对象**（无业务决策能力）：`task.yaml`（run 清单 + 引用，零业务
> 映射、零状态）+ `context/`（一次 MOD 决议 + 输入清单）+ `shared/`（staged /
> outline / flatten 缓存，准备一次，多 run 按哈希引用共享）+ `runs/<id>/`（
> 各 run 的私有业务执行证据）。单 run 任务是默认入口，走平铺目录，全程不接触
> Task 结构。

## 0. 定位与边界（为什么有这个层）

Task Layer 解决「单任务多 run」（一个业务任务包含多条 run，如一个客户的多条
产品线/输出，共享同一源工作簿与参数 sheet）的结构性问题：

- **重复准备**：同一 (file, sheet) 被每条 run 重复 flatten（埃及复盘 27 次
  flatten / 38.1 分钟机器时间，占可统计机器阶段约 82%）；
- **无批量编排**：批量 spec 生成、共享准备、Spec Review、进度报告全靠 agent
  手写临时脚本，不可复用、不可验证；
- **run 生命周期无治理**：废弃 run 与活 run 混放，耗时归因靠事后手工汇总。

职责分离（两层不通过共享 spec 连接，通过 manifest 与 artifact 引用连接）：

| 层 | 职责 | 事实载体 |
|---|---|---|
| Task Layer | 生命周期、共享准备、Spec Review（一次确认覆盖多 run） | `task.yaml` / `task_manifest.json` / `task_status.json` / `cache/` / `spec_review.json` / `review_confirm.json` / `outputs/` |
| Run Layer | 业务事实、编译、执行、验证 | `runs/<id>/fill_spec.yaml` / `prepare_manifest.json` / plan / receipt（语义完全不变） |

命名语义是 **Task Orchestration**（不是 batch execution）；不使用
`batch_*` / `multi_run_*` 命名。

**允许 / 禁止**：

- 允许：task 级共享源准备事实（staging、outline、flatten 产物、指纹）；
- 禁止：task 级业务语义 —— 映射、lookup、transform、formula、validation
  rule 永远只在 `runs/<id>/fill_spec.yaml`（MOD 规则指导撰写）。`task.yaml`
  是 MOD Resolution 产物的**消费方**，不是生产方；
- 禁止：task database、DAG engine、worker pool、跨阶段流水线、cache lock、
  阶段调度器、聚合门、打包（assembly）；
- 禁止：修改 Run 层任何契约（fill_spec 仍是唯一业务事实源；Compiler /
  Executor / Deliver 对 task 存在零感知）；
- PPTX 目标当前不支持 task 层（显式拒绝，不静默降级）。

## 1. Task Model（三层文件）

canonical/derived 分离与既有哲学同构：

| 文件 | 定位 | 写者 | 可变性 |
|---|---|---|---|
| `task.yaml` | canonical：Task Definition（run 清单 + 输入事实引用 + 输出命名 + `context_ref`/`shared_input_ref`） | Agent（映射确认后） | 可通过新版本修改 |
| `context/mod_resolution.json` | derived：一次 MOD 裁决（任务级，多 run 共享） | 脚本（mod_nominate `--mod`） | 裁决后仅重裁决可改 |
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
- 业务/状态键（mapping / lookup / transform / formula / validation /
  **policy** / **status**）**禁止**键入 task.yaml，键入即校验拒绝
  （`BUSINESS_RULE_IN_TASK_YAML`，exit 3）——Task 不变量（spec Implementation
  Decisions）：run 清单只允许 run 引用与共享输入引用三类字段，永不重新长成 DSL；
- 任务根目录与全部 artifact 名称必须 ASCII。

**撰写时机（Topology Check，ticket 03）**：run 清单 + 引用构成的骨架**可在
Topology Check 阶段（Prepare Outline/Manifest 后、业务映射前）先行建立并
落盘** —— skeleton-first；业务映射/规则永远后置，只进各 run 的
`runs/<id>/fill_spec.yaml`。「task.yaml 是 MOD Resolution 产物的**消费方**，
不是生产方」由此兼容为：task.yaml 消费的是「映射后定的运行事实（本任务几条
run、各自引用什么输入/输出）」，从不生产映射本身 —— **拓扑骨架先行、映射
后置**，二者不矛盾；run 清单/引用后续变化 = 输入事实变化（删除 derived 文件
后重新 --init/--prepare，见 §4），不静默重派生。版本历史 = 文件版本命名，
无迭代/替代抽象。

**共享事实声明是派生语义，不是显式字段**：跨 run 相同的 (file, sheet) 需求
由 run 清单去重得出（eager 预展平的唯一需求集），task.yaml 不设独立共享声明
节 —— 声明的是"有哪些 run 及其引用"，共享集由脚本收集。

### 1.2 `task_manifest.json` — derived Task Prepare Snapshot（脚本写）

语义 = 「这个任务基于什么输入」。`--init` 落盘时记 `frozen_at` 封存：
此后的准备事实（staged 文件 + SHA-256、outline 文本、flatten 缓存引用
`{cache_key, source_hash}`、模板/结构指纹）由 prepare 阶段一次性填齐，**不再
静默重派生**。

**冻结维度 = 输入事实状态（T04）**：run 清单 + 每条 run 的 `source.file` /
`source.sheets` / `target.template` / `target.sheet`（即执行契约：源 / 模板 /
sheet 角色）变化 → `MANIFEST_STALE`（fail-closed，输入事实改变 → 删除
derived 文件重新 --init，见 §4）；`task.yaml` 的**非输入字段**
（`target.output` 输出命名、`notes`/`customer` 元数据、`template_family`
记录）变化**不**触发 `MANIFEST_STALE`，manifest 继续有效，无需删除重初始化。
输入文件的**内容** hash 变化由 prepare prelude 的源漂移检查
（`SOURCE_HASH_DRIFT`，staged 文件 SHA-256 比对）兜底阻塞；MOD 裁决
（`context/mod_resolution.json`，**任务级一次、多 run 共享**）不在 task 冻结
结构内，由 compile（C2/C3）校验 —— compile 侧经 `--mod-resolution
context/mod_resolution.json` 读取同一份裁决记录，runs/<id>/ 不重复携带（ticket 08）。

### 1.3 `task_status.json` — derived Runtime State（脚本写，单一写者）

语义 = 「这个任务运行到了哪里」。每 run 一条 `{state}`，`updated_at` 每次
执行更新。状态集合：

`planned → prepared → compiled → drafted → delivered`。

**status 是生命周期索引，不是真值源**：断点判定一律以 artifact 存在性 + hash
为准（见 §4）。手改 status / manifest → 一致性缺陷（fail-closed）。

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
- **eager 预展平**：阶段 1 对 task.yaml 声明的全部源 sheet 预先展平，每个
  缓存键恰好一个 worker（顺序执行）；命中者零 officecli 调用。禁止 run 内 lazy
  flatten —— 并发写同一缓存键从结构上不存在，**不引入锁**。
- **引用（ticket 08，替代物化）**：缓存产物不逐字节复制进各 run 工作目录 ——
  run 的 `prepare_manifest.json` 展平条目只记录 `cache_key` + `sha256`
  （哈希引用）；compile 从 `cache/<key>/flat.csv` / `meta.json` 按 cache_key
  解析，execute/deliver 的 input hash 核对从 task 级 `staged/` 按名引用。
  禁止 symlink / junction（引用是哈希+键，不是路径软链）。
- **Cache Identity = Run Artifact Identity（哈希引用）**：cache key 是能否复用
  的判定；run 侧消费的 CSV 哈希（`sha256` 取自 `cache/<key>/flat.csv`）就是
  本 run 实际输入的业务身份（Run Artifact Identity）。二者通过 `sha256`+
  `cache_key` 引用绑定，不逐字节复制。
- **run 只含私有产物 + 哈希引用**：run 目录 = 映射/计划/成品/收据/机器证据 +
  引用清单（`prepare_manifest.json`，sha256 + cache_key）；共享输入
  （raw / outline / 展平）在 task 级 staged / outlines / cache，不复制进
  run 目录（Run Evidence Completeness：输入副本不是完整性的一部分）。
- **Cache 只加速 Prepare**：compile 期 input_hashes 绑定、execute 期重算
  比对、deliver 三方核对语义全部不变；缓存命中 ≠ 指纹豁免。

## 3. 顺序编排（--run 完整编排）

v3 收敛后调度器退役：`--run` 走**单进程顺序执行**，无阶段并、无 barrier、无
worker pool、无 DAG。`--run` 完整编排 5 个阶段（`--prepare` 只走阶段 1–2）：

| # | 阶段 | 性质 |
|---|---|---|
| 1 | source prepare / flatten / cache build | Office 密集（cache miss 时） |
| 2 | run prepare（引用清单组装 + target prepare + manifest 组装） | Office 密集（target flatten） |
| 3 | compile（compile_fill.py, 含转换函数定义静态检查） | 纯文本 |
| 4 | execute（validate / readback / render QA） | Office 密集（单 resident 窗口，天然串行） |
| 5 | deliver（promote_output.py 哈希核对复制，每 run 独立文件） | 纯文件 |

- **顺序执行**：调度器（task_scheduler）退役，阶段内遍历 items 串行调用
  worker，无并发默认值、无并发调参（不进入 task.yaml、不暴露 CLI）。
- **转换函数定义静态检查并入 compile 阶段 (ticket 06, spec D2)**：转换函数
  定义（未知 function / regex pattern 缺失或不可编译 / 缺 replacement / 词表
  形状坏）现在由 compile_fill.py 在编译期报结构化缺陷
  `STATIC_VALIDATION_FAILED`——不再有独立的预检步骤；缺陷 → 该 run 视为
  **compile 阶段失败**，不进 execute。
- **execute 失败自动复核（T03，spec D3）**：execute 子进程失败（含超时/被杀
  窗口）不直接进失败恢复流程——execute worker 先 force flush/close，再用
  officecli get 复核**本次计划写入目标格**（plan 定位，非全文件扫描）；写值
  已存在（readback 假失败）→ `recovered` → 一次自动重跑产出正式
  draft+receipt；确实缺失 → retry 一次；仍失败 → confirmed（保留既有失败
  收敛路径，不伪造 receipt、不跳过验证、不与输入事实变化的重新初始化混淆）。
  复核决策是可 import 纯函数（`task_prepare.review_execute_failure`），详见
  LAYER4_EXECUTE_LOOP.md。
- **单一写者**：worker 只向主进程回报结果（状态码 + 产物路径）；主进程在
  阶段边界统一批量更新 `task_status.json` 一次。
- **失败传播**：任一 run 在阶段失败不阻断同阶段其他 run；阶段结束汇总失败
  清单（带阶段归属），失败 run 按其状态不推进。阶段 1（共享准备）失败
  fail-closed 停止。
- **进度**：阶段边界输出人读进度行（stderr）；stdout 只承载结构化 JSON。
- **用结构消除并发冲突，不靠锁**（阶段 1 eager 预展平保证每缓存键恰一
  worker）。

## 4. 生命周期 / 失败二分

### 4.1 状态机

主路径 `planned → prepared → compiled → drafted → delivered`。
版本历史 = 文件版本命名，无迭代/替代抽象。

### 4.2 断点判定（artifact 证据，不是状态字段）

中断后以产物存在性 + hash 校验确定实际断点，用已有 execution_plan.json 重跑：

| 证据（存在性 + hash） | 断点判定 | 后续行为 |
|---|---|---|
| 无任何产物 | planned | 阶段 1 |
| manifest 有效 + 物化产物 hash 匹配 | prepared | 跳过阶段 2 |
| plan 的 fill_spec hash 匹配 + input_hashes 绑定有效 | compiled | 跳过阶段 3 |
| draft 存在 + receipt 的 draft hash 匹配 | drafted | 直接进 deliver |
| **draft 存在但 receipt 缺失 / 不匹配** | **execute retry（crash window）** | **重跑 execute**，不得直接交付 |

**execute crash window**：execute 写 draft 与写 receipt 之间崩溃 → draft
存在但 receipt 缺失。此时不得凭 draft 存在性直接交付——验证证据链不完整，
必须重跑 execute。

### 4.3 失败二分

- **输入事实未变** → 重跑或修 spec：
  - execute **之前**（compile 周期内）修 fill_spec → 重编译（既有语义）；
  - execute **之后**发现语义缺陷（宽片泄漏、翻译函数语义选错等）→ 修
    fill_spec → 重编译 → 重执行 → 重新交付（版本历史 = 文件版本命名）；
  - 普通失败重试：execute 失败由 T03 自动复核（flush → get 复核读回计划
    目标格 → 有值即 recovered / 无值 retry 一次 → 确认失败，见 §3）兜底；
    其余阶段失败按失败清单定向重试或修复；
- **输入事实改变**（task.yaml 的**输入事实字段**修改 —— source / template /
  sheet 引用、run 清单；源 hash 漂移、target 模板重建、MOD / 映射裁决变化）
  → **删除 derived 文件后重新 --init/--prepare**，禁止在旧 run 上继续修补。
  task.yaml 纯输出命名 / notes 等非输入字段修改**不属于**输入事实改变，
  不触发 MANIFEST_STALE、无需重新初始化（T04）。

## 5. Spec Review（一次确认覆盖多 run，唯一人工点）

`spec_review.py` 在 fill_spec 初稿后、compile 前运行，一次摘要覆盖全部 run、
一次确认绑定全部 run 的 fill_spec 哈希（改动后旧确认失效，fail-closed）：

- **`--task task.yaml`（生成摘要）**：计算一次覆盖全部 run 的三节业务语言
  摘要（映射 / 转换 / 排除，非 YAML 转储），写 `spec_review.json`（machine
  可读：每 run 的 fill_spec 路径 + sha256）；摘要总是生成、确认总是呈现
  （低摩擦快速确认），仅用户显式 `--skip-review` 才跳过，无 Agent 风险分级；
- **`--confirm`（确认）**：重算当前 fill_spec 哈希与 `spec_review.json` 记录
  比对，任一 run 漂移 → `SPEC_HASH_DRIFT`（exit 3，旧确认失效）；一致 → 写
  `review_confirm.json`（记录确认时间 + 每 run 绑定的哈希）；
- Spec Review 是唯一人工点：确认映射后 verify 全绿即自动交付（deliver 无
  marker、无确认环节）。Task 层不自动确认、不自动交付。

## 5B. Run Isolation / Task Artifact Boundary（ticket 08 / spec D8）

本层收口 run 产物与 task 产物的边界：**每次 run 的运行时产物全部位于自己的
`runs/<id>/` run root**，一条 run 永不隐式读取另一条 run 的
spec/scratch；历史 run 完整可追溯。

**允许共享 / 禁止共享（Task 级契约清单）**：

- **允许共享（task 级）**：staged 原始输入（`staged/`）、task 级 outline
（`outlines/`）、task-local cache（`cache/<key>/`，内容白名单 flat.csv /
meta.json / digest.md）、`context/mod_resolution.json`（一次 MOD 裁决）、
`task.yaml`、`task_manifest.json`、`task_status.json`、
`spec_review.json` / `review_confirm.json`、per-run 交付物（`outputs/`）;
- **禁止共享 / run 私有（逐 run 各自持有，位于 `runs/<id>/`）**：业务执行证据
  6 类 —— 映射（fill_spec）/ 计划（execution_plan + mapping.md +
  source_trace）/ 成品（validated_draft）/ 收据（draft_receipt +
  final_receipt）/ 机器证据（render_qa）/ 引用清单
  （prepare_manifest.json，sha256 + cache_key）。展平的 csv/meta/digest/
  candidates 是**共享产物**，只在 task 级 `cache/`，run 目录不复制（哈希引用）。

**目录边界（分层契约，ticket 08 最终形态）**：`task_root/ { task.yaml,
task_manifest.json, task_status.json,
context/ (mod_resolution.json — 一次 MOD 裁决),
staged/ (raw 输入, 共享), outlines/ (outline txt, 共享), cache/<key>/ (展平
flat.csv/meta.json/digest.md, 共享), spec_review.json, review_confirm.json,
outputs/ (per-run 交付), runs/<id>/ (该 run 私有业务执行证据 + 引用清单) }`。
`runs/<id>/` 内的产物**绝不**被当作 task 级共享；共享输入（raw/outline/展平）
按 `sha256` + `cache_key` 引用，不逐字节复制进 run 目录。

**prepare 污染 fail-closed（`WORKDIR_POLLUTED`, exit 3）**：prepare（单 run
`prepare_run.py` 入口，或 task 层 `runs/<id>/` 引用清单组装）检测到 workdir
已含本 run 的 post-prepare 生命周期产物（fill_spec.yaml / execution_plan.json /
mapping.md / source_trace.json / draft_receipt.json / validated_draft.* /
final_receipt.json 任一）→ 拒绝并以结构化 defect 报出，corrective_action =
全新 run root 或显式 run-id 目录，**绝不先读旧产物再覆写**。

**豁免（allowlist — prepare 侧 / 环境侧，任何阶段不触发，增量 flatten 合法）**：
`prepare_manifest.json`；
flatten 产物（`*_flat.csv` /
`*_meta.json` / `*_digest.md` / `*_premod_evidence.md` / `*_candidates.yaml`）；
staged 文件 / `*_outline.txt` / `_plan_*.json` / `.preflight_cache.json`；
`mod_resolution.json`（task 级 context/，非 run 触发器）/ `task_shape.json`
（run-scoped 但非触发器）。
行号空洞修复不构成 run 触发缝: 默认由 `workspace_init --init` 在 staging 内部完成
（先于哈希/展平/指纹），独立 `repair_row_gaps.py` 只产出新输入快照并要求重新
`--init` — 两者都不写 run 生命周期产物。`fill_spec.yaml` 是 Agent 手写产物，无脚本
重跑接缝（spec 存在时 plan/receipt 尚不存在；compile 的指纹 / input_hashes 契约对
乱序改动 fail-closed）。


## 7. CLI 用法

任务根目录 = 含 agent 撰写的 `task.yaml` 的目录（ASCII）。

### 7.1 `prepare_task.py`

```
python scripts/prepare_task.py --task-root <dir> --validate
python scripts/prepare_task.py --task-root <dir> --init
python scripts/prepare_task.py --task-root <dir> --prepare
python scripts/prepare_task.py --task-root <dir> --run
python scripts/prepare_task.py --task-root <dir> --adopt-discovery <discovery_manifest.json>
```

| 模式 | 行为 |
|---|---|
| `--validate` | 仅静态校验 task.yaml，不写任何文件 |
| `--init` | 校验 + 首写 `task_manifest.json` / `task_status.json`（全部 planned）；派生文件已存在则校验一致性 |
| `--prepare` | 阶段 1–2：staging + outline（任务级一次）+ eager 展平缓存 + 逐 run 物化与 manifest 组装 |
| `--run` | 阶段 1–5 完整编排（prepare → compile[含转换函数定义静态检查] → execute → deliver）；deliver 阶段对每个已 drafted 的 run 执行哈希核对复制交付到各自 `target.output`（outputs/），无 gate、无确认、无 marker |
| `--adopt-discovery` | 显式继承 Discovery 阶段产物（四条件 AND 判定；成功后执行 prepare，跳过 staging/outline） |

### 7.2 `spec_review.py`（唯一人工点）

```
python scripts/spec_review.py --workdir <dir> --task task.yaml
python scripts/spec_review.py --workdir <dir> --task task.yaml --confirm
```

| 模式 | 行为 |
|---|---|
| `--task task.yaml` | 生成一次覆盖全部 run 的三节摘要 → `spec_review.json`；呈现后停（等待用户确认） |
| `--confirm` | 重算 fill_spec 哈希比对，漂移 → fail-closed 拒绝；一致 → 写 `review_confirm.json` |

### 7.3 退出码（与套件一致）

| Exit | 含义 | 处置 |
|---|---|---|
| 0 | PASS | 继续 |
| 1 | Fatal（env / file：task root 缺失、非 ASCII 路径、officecli 不可用、task.yaml 不可读） | 修正环境/路径后重跑 |
| 3 | Retryable（静态校验缺陷、封存/状态一致性缺陷、阶段失败清单、源漂移阻塞、Spec Review 哈希漂移） | 读结构化缺陷（code + message + corrective_action）定向处置后重跑 |

失败输出契约（双通道）：

- **守卫级错误**（进度行之前：缺文件、task.yaml 不可读、一致性/漂移缺陷）→
  结构化 JSON 走 **stderr**，与单 run 套件 `fail()` 契约一致；
- **阶段失败清单**（`--prepare` / `--run` 的 run 级失败）→ 结构化 ERROR JSON
  走 **stdout**（含 `defects` 列表，带阶段归属）；
- **人读进度行只走 stderr**；stdout 只承载 JSON（成功 RESULT 或阶段失败清单）。

## 7.4 单 run Fast Path（ticket 08）

单 run 任务是**默认入口**，走平铺目录，全程不生成 `task.yaml` / `runs/` /
run 清单文件，不接触任务编排结构（无 Task 容器）。五个公开命令在单一 workdir
内顺序执行：

```
workspace_init.py --init      # 事实空间（staged/outline/flatten/digest/manifest）
mod_nominate.py --mod ...     # MOD 裁决（workdir/mod_resolution.json）
fill_spec.yaml                # FillSpec 初稿（MOD 决议后首个业务动作）
spec_review.py --confirm      # 唯一人工点（可选，显式 --skip-review 跳过）
compile_fill.py               # 编译（plan + mapping + source_trace）
execute_batch.py              # 一次性填充 + 机器验证（validated_draft + receipt）
promote_output.py             # deliver：哈希核对复制到 outputs/
```

单 run 路径以 `workspace_init.py --init` 为唯一入口（T12 接线收口）的平铺
workdir，MOD 裁决落 workdir 根（非 task context）；`prepare_run.py` 两阶段保留为拓扑不确定时的 outline-only Discovery 与 task 层内部机制（兼容路径）。它与 Task
层的区别仅是**不建 Task 容器**：无 run 清单、无多 run 编排，编译/执行/交付
的产物契约完全相同。

## 8. 典型流程（allowed sequence）

1. Agent 撰写 `task.yaml`（run 清单 + 引用；映射确认后）；
2. `prepare_task.py --init`（必要时先 `--validate`）；
3. `prepare_task.py --prepare`（阶段 1–2：staging + outline + 展平缓存 +
   逐 run 物化）；
4. 逐 run 撰写 `runs/<id>/fill_spec.yaml`（MOD 规则指导）；
5. `spec_review.py --task task.yaml` → **唯一一次**人机交互：一次摘要覆盖
   全部 run → 用户一次确认 → `--confirm`（绑定全部 run 的 fill_spec 哈希）；
6. `prepare_task.py --run`（compile → execute → deliver）：verify 全绿自动
   交付每 run 到 `outputs/<target.output>` —— per-run 独立输出是唯一交付形态
   （无合并产物）；
7. **语义缺陷** → 修 `runs/<id>/fill_spec.yaml` → 重编译 → 重执行 → 重新
   交付（版本历史 = 文件版本命名）；
8. 输入事实变化（task.yaml 输入事实字段 / 源 / 模板 / MOD 裁决）→ 删除
   derived 文件后 `--init`/`--prepare` 重新初始化 → 新版本继续。

## 9. 与 spec 的关系

本文件对应 spec「Task Orchestration Layer」的契约/行为面（S1–S7 的
contract/invariants/allowed behavior；S8 验证的证据面 —— 断点判定、Cache
命中语义；S9 文档职责分离即本文件与 SKILL.md / KNOWN_TRAPS / CONTEXT.md /
ADR-0009 的分工）。S8 的测试分层与性能验收的测试侧定义在 spec（Testing
Decisions），不属于本文件范围。实现决策背景与验收见
`.scratch/table-fill-task-orchestration/spec.md`（issue tracker 本地文件）。
