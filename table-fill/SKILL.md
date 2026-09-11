---
name: table-fill
description: >
  table-fill fills an existing Office template (xlsx or pptx) with data taken
  from existing Excel/PPTX tables — across files or across sheets. Use it when
  the user wants existing source data placed into an existing template, report,
  or quotation shell (e.g. 把数据填到模板里, 从源表出报告), even when they do
  not name the source and target formats. Do NOT use it to create a file from
  scratch, edit a single cell, or merely flatten data that has no template
  target.
license: MIT
compatibility: >
  Required: officecli (on PATH), Python 3.10+ (PyYAML). 不需要 openpyxl / pandas;
  python-pptx 仅用于「PPTX 目标」记录的一次性加行 (其后永久关闭), 不是通用管线。
  officecli 子进程调用一律经 _officecli.officecli() 适配器 (细则见「不变量」)。
  Co-load: officecli-xlsx; pptx 目标加 officecli-pptx; Windows/中文路径加
  officecli-win (subprocess encoding)。
metadata:
  drift-risk: high
  mod-nomination: true
---

# Table Fill (v3)

# Part I — 运行时控制面（Runtime Control Plane）

## 唯一执行顺序权威

本节是 table-fill TASK MODE 下**唯一的阶段顺序来源**。不要自行设计工作流，也不要从 Part III 机制细节 / Part IV 治理 / Part V 参考中推导另一套阶段顺序 — 那些章节只定义进入/退出条件、异常分支、语法与机制；它们永远不会重新定义执行顺序。若后续文字与本节在顺序上冲突，以本节为准。这个单一权威之所以重要: 多套流程描述并存时, Agent 必须自行综合出"合理顺序", 而综合正是偏离 SOP 的根源。

状态编号 (S0–S8) 只是导航辅助, **不是契约**; 契约是阶段名与顺序关系: Workspace before Topology · Compile before Review · Review before Execute。

**严格按照以下SOP执行：**
```
S0 Workspace Init → S1 Topology → S2 Task Shape → S3 MOD Resolution →
S4 FillSpec First Draft → S5 Compile/Repair → S6 Spec Review →
S7 Execute + Verify → S8 Deliver
```

除明确 Exception Route 外, 不跳阶段、不倒序、不插入机制探索。

| 阶段 | 细则所有者 | 不可省契约 |
|---|---|---|
| **S0 Workspace Init** | §1 | `workspace_init.py --init` Job 级一次原子完成 staging → 事实空间; workdir 必须 ASCII; 输入漂移 → 重新 `--init` |
| **S1 Topology** | §2 | Topology: 用任务文本 + workspace_manifest 事实判定 single_run / multi_run, **零新增探测**; lowering 只由 `materialize_run.py` 做 |
| **S2 Task Shape** | §3 | 判定输入 = 任务指令 × 源 evidence × 目标 evidence; 零新脚本 / 零额外 LLM / 零额外探测 |
| **S3 MOD Resolution** | §4 | 按裁决规则**自动采用或**提请用户裁决 → `mod_resolution.json`; 未裁决 (status ∈/ {resolved, none}) 禁止业务推导 |
| **S4 FillSpec First Draft** | §5 | 初稿可以不完整; 只有阻塞项才做最小定向读取; `fill_spec.yaml` 是唯一业务 IR |
| **S5 Compile / Repair** | §6 | Compile Clean 才进 Review; 机械缺陷自动 REPAIR, 业务歧义 ASK / 记 gaps |
| **S6 Spec Review** | §7 | 唯一人工点; `--confirm` 绑定该份 FillSpec 的 sha256, 字节变化即旧确认失效 |
| **S7 Execute + Verify** | §8 | 先复制 staged target 再填充 (模板永不改); 前置门禁 fail-closed; 机器验证全绿才出 draft |
| **S8 Deliver** | §9 | `promote_output.py` 哈希核对复制; 验证后绝不再次填充 |

以上只固定**顺序** (契约 = 阶段名与顺序关系); 各阶段的进入/退出条件、异常分支与机制细节由 Part III 对应 § 拥有, 本节不重复。

## Runtime Stop Rule

完成当前阶段产物 → 立即进入下一阶段, 而不是"读更多文档/验证自己理解/探索其它实现"。
No defect, no exploration. No exception signal, no exception route. No unresolved business ambiguity, no user interruption.

---

**层模型 (每层一个职责, 不混淆)**:

| 层 | 负责什么 | 权威产物 |
|---|---|---|
| Workspace | 物理事实 (role-neutral) | `workspace_manifest.json` |
| Topology | run 划分 (1/N) | run definition(s) |
| Materialization | run projection (lowering) | `prepare_manifest.json` (每 run) |
| FillSpec | 业务 IR | `fill_spec.yaml` |
| Compile | IR 合法性 (静态验证) | `execution_plan.json` / `mapping.md` |
| Spec Review | 人工语义确认 (唯一人工点) | `review_confirm.json` |
| Execute | 机械执行 + 机器验证 | `validated_draft.*` / `draft_receipt.json` |
| Deliver | 哈希核对复制 | `final_receipt.json` |

**五原则**: FillSpec First / Compiler Driven / Task Is Optimization / Run Is Disposable / Minimum Runtime State。**Task vs Run**: Task = task.yaml + runs/ (数据组织容器, 无业务决策能力, 无执行状态机); Run = 一次执行尝试 (spec/plan/output/receipt 全在 `runs/<id>/`); 单 run 平铺 workdir 不进 Task。**Repair = input-version repair**: 行号空洞修复产出新输入快照后经 canonical `workspace_init` 重新进入, 绝不做 workspace 原地突变 (见失败处置表 ROW_GAP_DETECTED)。

## 不变量

1. 来源数据真实且可追溯 — every written value traces to a staged source, a transform, a lookup, or an explicit decision in the spec.
2. 目标模板结构和格式不被破坏 — structural ops (clone/remove/merge) preserve template formatting; clone residue is explicitly nulled.
3. 未决业务歧义不能静默猜测 — ambiguity interrupts at MOD Resolution or is recorded in `gaps` and surfaced at Spec Review.
4. 正式交付前必须经过机器验证 — 验证全绿 + 哈希一致才自动交付 (无 marker、无确认环节)。
5. 输出必须通过结构、值、公式和 readback 验证 — the draft passes validate + issue-delta + compiler-derived readback before delivery.
6. Windows、中文路径和 OfficeCLI 调用必须稳定 — ASCII workdir, UTF-8 subprocess, resident cleanup, retry helpers (all in `scripts/_officecli.py`)。两条规则分开: Office 内容结构解析 (列宽/行号空洞/OLE) 允许直接读 ZIP/XML; 任何 officecli 子进程调用必须经 `_officecli.officecli()` 共享适配器, 禁止裸 subprocess.
7. 同一业务事实只能有一个权威来源 — business semantics live ONLY in fill_spec.yaml; everything else (plan, mapping.md, readback, receipt) is derived.
8. **Manifest 角色中立**: `workspace_manifest.json` 只编码物理事实 (staged identity / outline / 选定业务 sheet 并集的展平条目 + entry-level structure fingerprint), **不编码 source/target 角色**; run 定义把展平条目投影成 source/target, 聚合指纹是 run-local 派生, 不是 workspace 事实。(细则: II-2)

## 权威模型

| 对象 | 类型 | 权威性 |
|---|---|---|
| staged source/target | 输入快照 | 本次运行输入事实 (compile 期绑定 plan.input_hashes; manifest files[].sha256 是 init 期快照) |
| `workspace_manifest.json` | Canonical | 工作区唯一事实空间 — inputs / outlines / flattened (role-neutral, entry-level structure_sha256); 无 target/kind/二元 fingerprints |
| `prepare_manifest.json` | Derived (run-local) | materialize_run 从 workspace 投影: flattened + target entry + 派生 source/target fingerprints (compile-facing 视图) |
| `fill_spec.yaml` | Canonical | 唯一业务语义、映射、转换和追溯事实源 |
| `execution_plan.json` / `mapping.md` | Derived | Compiler 从 FillSpec 物化 (编辑 spec, 从不编辑 plan/mapping) |
| `validated_draft.*` | Derived Result | 已执行并通过验证的候选交付文件 |
| `draft_receipt.json` | Evidence | 输入哈希 (执行时重算 + 绑定) / spec/plan/draft 哈希 + 验证结果 |
| `task_shape.json` | Agent 判定 | 三字段契约: `task_shape` (值域 `grid_record`/`form_content`/`mixed`/`uncertain`) + `route` (`fillspec`/`officecli_native`/`combined`) + `evidence` (短 snake_case code); 兼容扩展可记 `record_axis` (evidence/diagnostic 层, 不改变三字段契约) |
| `review_confirm.json` | Evidence | Spec Review 确认记录 — 绑定 Compile Clean 的 fill_spec sha256 (Execute gate 比对) |
| `final_receipt.json` | Fact Record | 交付证据 (哈希 + zip 检查, 无状态词) |
| final output | Delivery | Validated Draft 的哈希一致副本 |

**禁止**: 在 Markdown 和 YAML 中分别维护业务事实; 手写 batch JSON; 手写 checks; 冒烟后删除并重新执行; MOD 拥有独立运行状态; **workspace_manifest 记录 source/target 角色; 手改 manifest 条目哈希后局部续跑** (任何输入事实变化 → 重新 init)。

## Output Files

```
<workdir>/ (ASCII; C:\Temp\tablefill\<task>\):
  workspace_manifest.json   — canonical physical facts (role-neutral, 无 target)
  *_outline.txt / *_premod_evidence.md (role-neutral) / *_flat.csv / *_meta.json / *_candidates.yaml
  prepare_manifest.json     — run-local compiler view (materialize_run 产出; 单 run 平铺 workdir)
  <target>_target_view.md   — run-local target routing evidence (materialize_run 渲染, 与 workspace
                              role-neutral evidence 不同名; 单 run 平铺 workdir, 多 run 在 runs/<id>/)
  mod_resolution.json / task_shape.json / fill_spec.yaml
  spec_review.json / review_confirm.json / execution_plan.json / mapping.md / source_trace.json
  validated_draft.<ext> / draft_receipt.json / final_receipt.json
<task-root>/ (multi-run): task.yaml + runs/<id>/ 含各 run 的 prepare_manifest.json + 上表其余产物
<用户路径>/final.<ext> — Delivery (hash == draft hash; 验证后绝不再次填充)
```

# Part II — 定位与硬约束（Identity · Hard Constraints · Loading）

table-fill = 带版本记录的 Excel 编译器: FillSpec 是唯一业务 IR; Compiler 静态验证并驱动收敛; Execute 唯一一次填充 + 机器验证; Spec Review 是唯一人工点 (Compile Clean 后、Execute 前); 验证全绿即哈希核对自动交付。多 run 共享一次 Workspace Init (Task 只是优化容器), 单 run 走最快路径, 失败即重跑。文档按五段式组织: I 运行时控制面 / II 定位与硬约束 / III 强制流程 / IV 治理 / V 参考路由。

## 硬约束

以下硬约束按**行为**执行, 违反任一即越界 (行为契约, 无物理锁):

1. **FillSpec First Rule**: MOD 决议后, 首个业务动作 = 产出 FillSpec 初稿 (可以不完整); 未产出初稿前**禁止深度能力探索** (--capabilities 全量 dump / --probe / 源码阅读 / 机制求证 / 全文通读 / 读 case 复盘当证据一律后置); 为解除撰写阻塞的**最小定向读取** (`--capability <key>` / pattern index / digest / 指定参考小节) 是合法动作, 如实记入 action log。
2. **错误驱动, 非探索驱动**: 编译缺陷是下一轮探索的唯一入场券 — 只有 compile 返回的 defect.code / corrective_action (或无法用 FillSpec 表达的 transform) 才触发定向查询; 探索型提前查证 (为了保险/怕错/泛化) 不构成合法理由 (`unjustified capability query = 0`)。
3. **模式索引路由优先于全文阅读**: 已知 shape 时先查 `assets/fillspec_patterns.yaml` 得到对应 pattern, **只读该 pattern**, 不读 FILLSPEC.md 全文 (定位细节按问题定向读小节)。模式索引优先于任何全文阅读, 是决议→初稿之间的唯一机制信息来源。
4. **初稿可以不完整**: 首版 FillSpec 允许缺列/缺 selector/缺精确行号等 (非阻塞项); 能由 Compiler 机械检出的问题不是阻塞项 — 「初稿 → 编译缺陷 → 定向修复 → 重编译」是合法且预期的收敛循环, 且**发生在 Spec Review 之前** (Review 只发生在 Compile Clean 上)。

> 决议→初稿之间只允许**解除撰写阻塞所必需的最小动作集**: 读 pattern → 读 digest → 定向读参考小节 / `--capability <key>` → 写 spec → compile。判据是"该动作是否解除具体阻塞", 不是动作数量; **深度探索一律禁止** (--probe / --capabilities 全量 dump / 源码阅读 / 全文通读 / 机制求证 / 读 case 复盘当证据)。动作集由 `scripts/fill_spec_first_validator.py` 按 action log 可执行校验 (拦深度探索与范围外动作)。

**硬约束 5–8（v3 收敛置顶扩展；与 1–4 同等强制，细则仅留指针）**:

5. **机器证据终止**: `execute_batch.py` 已返回机器证据 (`issues_new` / `validate` / readback 含结构 / render) 后, 禁止再用 `officecli issues`、`officecli get` 逐格复核或读 `execution_plan.json`, 也禁止读 case 复盘/测试病历作证据 — 人工复核是冗余探索; 唯一例外 = 异常驱动的定向检查 (render 失败/readback 意外差异 → `officecli get` ≤2 次)。(细则: Part IV 总原则「机器证据终止条件」; 失败分类见失败处置表)
6. **禁读源码**: TASK MODE 不读实现源码、不运行 Skill 测试套件、不修改 Skill、不连续设计实验; 机制问题走 Runtime Navigation Table (`compile_fill.py --capability <key>` / `--capabilities` / FILLSPEC 对应章节 / KNOWN_TRAPS / `officecli help`)。**唯一例外 (recorded: case-004)**: 必需机制事实只存在于 tests/源码时, 允许**一次**定向只读定位该事实, 同时记一条 Capability Gap Discovery; 该读取不得当业务答案、不得扩大为读整套测试或改 Skill。(细则: CAPABILITY_EVIDENCE.md)
7. **MOD 一次加载**: MOD 命中后一次加载 — 提名阶段只给候选名 + 命中/待复验信号 + 业务逻辑摘要 (**不含完整规则集**); 用户裁决后才经 canonical resolver (`references/` + MOD_INDEX 的 Path 列) 从 `canonical_path` 全文加载**选中** MOD 规则 (sha256 校验, 不匹配/缺失/畸形 fail-closed, 绝不回退同名副本)。(细则: II-2)
8. **输入事实只认工作区清单 (manifest)**: 业务输入事实 (staged/哈希/flattened/digest) 唯一来源 = `workspace_manifest.json` (canonical; 其 run-local 派生视图 `prepare_manifest.json` 由 materialize_run 产出) 的机器记录; 漂移 → 拒绝执行并要求重新初始化, **不重新探测、不把 tests/fixtures、历史输出或 scratch 副本当业务事实来源**。(细则: III-1 / 权威模型 / Runtime Navigation Table)

## ⚠️ 依赖加载（必须先执行）

```python
skill(name="officecli-xlsx")    # 路径语法、open/save 生命周期、batch 模式、QA 门禁
# 目标为 PPTX 时追加: skill(name="officecli-pptx")
# Windows/中文路径时追加: skill(name="officecli-win")
```


# Part III — 强制流程（Mandated Flow）

## 工作流 (七个公开命令)

`workspace_init.py --init` (Job 级唯一入口: 一次原子完成 staging/outline/展平/classify/role-neutral premod evidence → 事实空间 `workspace_manifest.json`; **不写 prepare_manifest, 不需要 --target**) → Topology Check (single-run CLI 定义 / multi-run task.yaml) → `materialize_run.py` (Topology 的 lowering: 校验引用 → 派生 run fingerprints → 渲染 target routing view → 生成每 run `prepare_manifest.json`) → Task Shape Check → `mod_nominate.py` → 用户裁决 → [规则加载] → digest → `fill_spec.yaml` (LLM 撰写) → `compile_fill.py` (Compile/Repair Loop → COMPILE CLEAN) → Spec Review (唯一人工点, `spec_review.py`, 摘要 = 映射/转换/排除三节业务语言, 确认绑定 Compile Clean 的 FillSpec 哈希) → `execute_batch.py` (唯一一次填充 + Review/输入哈希双层门禁 + 机器验证) → Validated Draft + receipt → `promote_output.py` (哈希核对复制自动交付)。

### 1. Workspace Init — `workspace_init.py --init` (Job 级唯一入口)

- **`workspace_init.py --init`** (每 Job 一次): `--files "源|ascii名,..." --sheets "file.xlsx:S1,S2;..." --task <文本>` — 环境预检+暂存+outline+展平+classify+role-neutral premod evidence+digest 延后一次原子完成; 写 `workspace_manifest.json` (唯一事实空间, canonical, **角色中立**)。workdir 必须 ASCII; staged/outline 幂等 (同哈希跳过), 失败/漏列 → 重新 `--init`。
- **`--sheets` = 本 Job 的 flattened business scope** (Selective Flatten Invariant): 只展平任务明确纳入的业务 sheet 并集 — 角色中立 ≠ 全簿展平; 禁止 "先全部 flatten, 反正后面再选" (历史 sheet 混入 / context 污染 / MOD 提名噪音)。选定业务 sheet 时不依赖 topology 判定: 任务文本已足以回答 "哪些 sheet 属于本 Job 事实空间", 不需要先知道 run 划分。
- **Sheet Scope ≠ Run Role**: sheet 选择 (哪些 sheet 进事实空间) 发生在 init 内; source/target 角色 (某 run 中谁是谁) 发生在 topology/materialization 后。sheet selection ≠ topology, sheet selection ≠ source/target 赋值。
- flatten 产出每 sheet 一个 `{name}_premod_evidence.md` (**role-neutral**: 只含路由+MOD 提名所需最小结构事实, 剥离解题材料; 无 target 视角的占位/克隆段 — 那是 run-local target routing view 的内容); full `{name}_digest.md` 在 MOD 解锁后才生成 (见 MOD Resolution)。
- **无 `--target`**: target 角色不进入初始化接口, 也不进入 workspace_manifest (manifest `target`/`kind`/二元 fingerprints 一律不存在; 同一工作簿可在一个 run 作 target、另一 run 作 source, 文件级贴角色标签是粒度错误)。
- 验证与继承: `--verify` 重算全部 staged 输入哈希与 manifest 比对 (漂移 → exit 3 + 提示重新初始化); `--inherit-from <dir>` 显式继承另一已 init 工作区的事实空间 (逐条哈希核对复制, 不匹配 fail-closed)。
- 行号空洞修复**不在此处**: 见失败处置表 `ROW_GAP_DETECTED` — repair 产出**新输入快照**后重新 `--init`, 绝不原地改 staged 后局部续跑。
- 不得仅为 lookup/inheritance 索引把 sheet flatten 进 manifest — 索引由 `build_inheritance_index.py` 直接读 staged workbook (见 FILLSPEC「Fill source use vs lookup-only use」)。

### 2. Topology + Run Materialization (S1, 硬性)

**Topology (几个 run)**: 判定时点在 **Workspace Init 之后** (事实空间已建立, 无 flatten-before-topology 约束 — 初始化本身拓扑中立且每 Job 只跑一次)。输入仅允许: 任务文本 + workspace_manifest 事实; 零新增探测。分支: **single_run** (默认) → 一个 run definition; **multi_run** (多系列/多产品/每 X 一个输出等信号) → task.yaml 运行清单。

**Run Materialization (Topology 的机械 lowering, 不是独立决策阶段)**: `materialize_run.py` — 唯一 lowering 边界, 把已决议的 run definition 投影为 run-local compiler view:

```bash
# 单 run (CLI 瞬时定义, 不创建 task.yaml):
python scripts/materialize_run.py --workdir <dir> --sources source_HOME,source_MULTI --target customer_SPEC
# 多 run (task.yaml 持久清单, 一次 materialize 全部):
python scripts/materialize_run.py --workdir <task-root> --task task.yaml
```

它只做四件事: ① **Resolve** — sources/target entry 名与 workspace_manifest 比对; ② **Validate** — 引用 entry 必须在已初始化 scope 内, 否则 `RUN_ENTRY_NOT_IN_WORKSPACE` (exit 3, 绝不偷偷增量 flatten 缺失 sheet; 修正 = 以完整业务 sheet 并集重新 --init); ③ **Project** — 从 workspace entry facts 生成 run-local `prepare_manifest.json` (含派生 `source_structure`/`target_structure` 指纹 — 聚合发生在 run 层, workspace 只存 entry-level structure_sha256); ④ **Render** — 对 target entry 从已有 meta/csv/candidates 渲染 **target routing view** (`structure_digest --pre-mod --target` 纯重渲染: 占位行/克隆源样式段; **0 probing · 0 flatten · 0 extraction**, 只是 presentation projection), 写 run-local `<target>_target_view.md` (与 workspace role-neutral `<target>_premod_evidence.md` 不同名, 不覆盖 workspace 产物); 源 entry 的 evidence 直接复用 workspace role-neutral 产物。

**禁止 materialize_run 做**: Topology reasoning / Task Shape reasoning / MOD 提名 / sheet 发现 / 增量 flatten / workspace 修改 / cache·reuse 发现 / 业务映射 / FillSpec 生成 / 写回 workspace_manifest。**禁止新增 run-definition 落盘文件** (单 run CLI 瞬时输入即止; 多 run 由 task.yaml 承担)。两种输入形态归一为同一最小内部 RunDefinition 后走同一实现 — 不是双轨。

**Task 容器 (multi-run)**: `task.yaml` 只含 run 清单 (每条 run: id + source file/sheets + target template/sheet + output 命名), 零业务映射、零状态 (schema 校验见 `task_schema.py`, 业务键 fail-closed 拒绝)。Task 层**没有执行状态机、没有调度器、没有生命周期状态文件**; 公开流程。`materialize_run.py --task task.yaml` 一次遍历全部 runs 分别产出 `runs/<id>/prepare_manifest.json` (纯 for 循环, 无调度/状态/重试机制; 任一 run 不合法即 fail-closed 停止)。materialize 不修改 task.yaml (read → validate → materialize)。

### 3. Task Shape Check (S2, 每 run; Prepare 后、MOD 前) — Routing V2

判定输入永远三项: **任务指令 × 源 premod_evidence × 目标 premod_evidence** (目标证据 = materialize 渲染的 run-local target routing view); 明确信号一眼可判 — 读毕 evidence 即答, **零新脚本、零额外 LLM 调用、零常规额外探测** (不追加 picture scan / HTML render / 额外 query)。同一文件对不同任务可走不同路径。

```text
读毕 source/target evidence
   ├─ Obvious Grid (稳定 schema axis + 重复 record axis + 可克隆数据区, record_axis ∈ {rows, columns})
   │     └─► Level 0 FAST PATH: grid_record/fillspec, evidence=["obvious_grid"], 立即进 MOD — 0 新增动作
   └─ 仅明确异常信号 → Exception Routing:
         ├─ Direct   : grid_record + officecli_native (bounded/explicit 写集)
         ├─ Non-Grid : form_content + officecli_native (087 类)
         └─ Combined : mixed + combined (否则进 uncertain)
```

`task_shape` (workload 本质) 与 `route` (执行选择) 正交 — **Applicability ≠ Justification**: `direct` 永不作为 shape — 它是执行决策, 不是 workload 本质; `task_shape` 与 `route` 不再 1:1 绑定; route 值域仅 fillspec / officecli_native / combined; combined 是组合执行不是第三引擎; evidence 一律短 snake_case code、最小充分。task_shape 值域不变: matrix/column-grid/grouped-grid 只是结构属性/evidence 标签, 不是新 shape 值 — 最终 shape 仍是 grid_record, 值域仍是四值。

| task_shape | 含义 | 合法 route | 典型 evidence |
|---|---|---|---|
| `grid_record` | 稳定 schema axis + 重复 record axis, `record_axis ∈ {rows, columns}` (Row-Record Grid 与 Column-Record Matrix Grid 同属 `grid_record`); 输出单元数由源记录数驱动 | `fillspec` (Fast Path) / `officecli_native` (Direct) | `obvious_grid` / `bounded_explicit_edit`+`no_material_grid_benefit` |
| `form_content` | 固定内容区 (merged form regions), 无可克隆数据行模板 | `officecli_native` | `content_composition` / `layout_or_object_work` |
| `mixed` | substantial grid + 可分离 non-grid | `combined` | `substantial_grid_workload`+`separable_non_grid_workload` |
| `uncertain` | 无明确信号 (临时判定态) | — (不落执行 route) | `insufficient_routing_evidence` / `conflicting_workload_signals` / `task_intent_ambiguous` |

#### Axis-Neutral Grid 契约 (grid_record 定义, 双轴锚点)

- `grid_record` = **稳定 schema axis + 重复 record axis** 二维数据: 列头行 (row-grid) 或左侧字段标签列 (column-grid/matrix) 衡 schema axis; 产品列或重复记录行衡 record axis。
- **Row-Record Grid** (record 在行) 与 **Column-Record Matrix Grid** (record 在列 = schema axis=rows、record axis=columns) **同为 `grid_record`**, 不是两种 shape。
- **Cardinality 锚点 (扩列)**: N records → N target rows (line grid) 或 N target columns (matrix grid); **固定行数本身不构成 form 证据**; 外围结构 (顶部 metadata/Total/底部 Notes/纵向 group merge/inplace 占位区) 不改变 Grid 身份。
- **Grouped-grid 吸收**: 纵向 merge 仅用于多条 records 共享组级展示值时不构成 form_content 信号 — **merge 数量本身不得作为 Non-Grid 依据**。
- **Form 对照**: 典型 form_content = 有限、预定义、cardinality 固定的语义 slot; 分界是 slot cardinality 固定 vs record cardinality 数据驱动, 不是 merge 存在与否。
- **task_shape 值域不变**: matrix/column-grid/grouped-grid 不是新 shape 值; task_shape.json 可记 record_axis (evidence/diagnostic 层, 不改变三字段契约, 也不重定义 task_shape.json 的 schema 权威性 — 它是 Agent 撰写的 artifact)。
- **禁止**: 以记录数量阈值作为路由依据 (任何形式); 禁止新增分类器/评分层/第二层路由判定; 判定只依赖 task 指令 × source evidence × target evidence, Fast Path 0 新增动作不变, 不追加探测。

#### Level 0 — Obvious Grid Fast Path（默认主路径，不是 fallback）

读毕 evidence 即明显常规 Grid → 立即 `grid_record` + `fillspec`, evidence 固定 `["obvious_grid"]`, 直接进 MOD Resolution → FillSpec → Compile — 95% 任务零变化。**禁止继续 routing 分析** (stop-rule): 不写 signal checklist、不分级打分、不长篇 reasoning; 可观测动作不变式 = 0 新增 LLM/officecli/inspect/render/脚本调用。仅出现明确异常信号才进 Exception Routing:
- **Direct — `grid_record` + `officecli_native`**: 目标写集执行前 bounded/explicit (自检句: "OfficeCLI batch 本身能不能成为完整执行计划") + 无需 Grid 专业能力 (record iteration/dynamic rows/clone/placeholder/inplace/lookup/formula/group merge)。stop-rule 锚点句: "Direct 必须明显成立; 若判断 Direct 需要复杂成本估算, 则停止路由优化, 走 Grid 主路径或 uncertain" — 明显便宜 ≠ 算出便宜。单格编辑不是 table-fill 任务。
- **Non-Grid — `form_content` + `officecli_native`**: 固定内容区/版式组合 (087 类), 一等路径不是 fallback — 禁止靠 FillSpec 失败反向发现 form_content。流程: 共享 workdir/暂存保护 → 落 task_shape.json → officecli native execution (经 `_officecli.officecli()` 适配器, 目标模板永不被修改, 编辑副本) → **强制完成 officecli-xlsx QA checklist** (validate + view issues + view html + 模板 QA, 交付前强制) → 交付呈报 (QA 证据 + 关键内容格摘要 + 改动摘要)。条件 ASK 仅限: 覆盖原文件/不可恢复删除/多种合理语义无法判断/明显版面溢出无压缩策略。不继承 deliver/receipt/哈希三元组。
- **uncertain (临时态)**: 一次受限补观察 (view html + ≤2 次定向 get/query) → 必须重判进 Fast Path 或 Exception; 仍存在实质歧义才 ASK (evidence 同上三码, 临时态不扩设计)。

#### Combined 最小契约 (mixed + combined)

```text
Prepare → mixed 分解 → Grid 数据/结构执行 + readback/结构验证 → OfficeCLI finishing (仅触及明确可分的 non-grid workload, 不得修改或失效 Grid-owned region) → 统一 QA (Grid 数据与结构仍正确 + finishing 正确 + validate/issues/html) → verify 全绿 → 哈希核对复制交付
```

OfficeCLI finishing 在 verify 之前执行 — verify 延后至全部写操作完成; ownership: 一个 side effect 一个 executor owner (第一版仅 Agent 执行约束, 不建 DSL); 默认 Grid first; 两 workload 高度缠绕 → uncertain, 不硬拆。

#### 四案例映射 (canonical examples)

| Case | task_shape | route | evidence |
|---|---|---|---|
| 复杂报价单 (数十~数百 records + lookup/formula/clone/aggregate) | `grid_record` | `fillspec` | `["obvious_grid"]` |
| Column-Record Matrix 参数表 (字段在行、产品 record 在列) | `grid_record` | `fillspec` | `["obvious_grid"]` |
| 3~5 个固定 cell (至 30 cell 固定区域, 无 record 语义) | `grid_record` | `officecli_native` | `["bounded_explicit_edit","no_material_grid_benefit"]` |
| 087 (多格内容重组/图片/版式/固定 merged form) | `form_content` | `officecli_native` | `["content_composition","layout_or_object_work"]` |
| 80 records 明细 + Logo/客户名/备注/行高 | `mixed` | `combined` | `["substantial_grid_workload","separable_non_grid_workload"]` |

反例锚点 (防数量阈值思维): 只有 3 条记录但需 lookup/clone/公式/group merge → Grid (记录少 ≠ Direct)。每次 run (含 Fast Path) 落极简 `task_shape.json` (三字段; form_content 在 FillSpec 语境为 NOT_APPLICABLE — 产品层 SUPPORTED, 引擎层 NOT_APPLICABLE, 不是 UNSUPPORTED)。

### 4. MOD Resolution (S3, 任务级一次) — `mod_nominate.py`

```bash
python scripts/mod_nominate.py --workdir <dir> --task "<任务文本>" --files "s.xlsx,t.xlsx" --out mod_resolution.json
```

输出结构化 JSON, status ∈ {none, resolved, ambiguous, conflict}; 裁决规则: 用户明确指定 MOD NONE → 直接记录不中断; 用户指定某 MOD 且排除信号未触发 → 直接采用 (记录 revision, 用户裁决优先); 唯一候选信号全过 → 自动采用; 多候选不同业务含义 → 询问; 单候选含 pending/missed/未知排除 → 询问 (fail-closed); MOD 与表结构冲突 (排除信号命中) → 询问降级/替换/覆盖 — 不再读 MOD 全文核对排除信号是否误报 (领域判断不改变裁决机制), 直接呈现冲突信号+候选+选项 → fail-closed ASK。`selected_mod` 写进 fill_spec.yaml 前, 裁决先落盘 `mod_resolution.json` (`--mod <NAME|NONE>` 重跑写盘; NAME 越界 fail-closed; NONE 写 resolved+selected:NONE) — 无 mod_state, 没有独立 Gate; FillSpec 的 selected_mod 必须与最终裁决一致 (编译器 C2/C3 机械校验; 未裁决 → C4 MOD_UNRESOLVED 拦截)。**MOD 生命周期与 route 解耦**: Prepare → Pre-MOD Evidence → Task Shape → MOD Nomination/Resolution → 加载 selected MOD 规则 → 业务推导 → 选择/执行 executor (fillspec / officecli_native / combined 视 shape 而定; form_content 命中业务 MOD 时 MOD 仍然生效 — **「form_content → 跳过 MOD」不再是合法路径**; FillSpec 语境 NOT_APPLICABLE 只表示引擎层不适用, 不是业务规则不适用)。
**Canonical MOD Resolver 契约**: `mod_resolution.json` 每条候选/选定记录至少含 {name, canonical_path, revision, sha256} — canonical_path 一律目录表派生 (references/ + MOD_INDEX 的 Path 列), sha256 = 写盘时 canonical 文件哈希; 规则加载经 `load_rules_for_selected_mod()` (脚本与 Agent 只消费 canonical resolver 返回的 canonical 版本) → resolver 读 canonical_path 锁定文件并校验 sha256, 不匹配/缺失/畸形 → fail-closed (exit 3 + corrective_action, 绝不回退同名副本); `--check-canonical` 机械复核; 旧记录缺新字段 → re-resolve from catalog (记录 resolution_action); **禁止 glob 同名文件、禁止 scratch/history/legacy 副本作规则来源**。
**规则注入时机 (硬性)**: 候选 MOD 规则**必须加载后才可写 spec** — 两段加载 (改变加载时机与粒度, 不因输出形态优化放宽): 提名阶段每个候选只给「候选名 + 命中/待复验信号 + 业务逻辑摘要 + 裁决选项」— **不含完整规则集**; 用户裁决后才经 canonical resolver 从选中 MOD 全文加载完整规则注入 FillSpec 撰写上下文 (spec 的 columns/formulas/lookups/rows/decisions 必须与 MOD 一致, 偏离记 decisions; MOD NONE/status=none 无注入)。MOD 含 Runtime Core → 先建立业务心智模型; 含 Attention Map → 按 resolve→map→transform→validate 认知顺序一次撰写。**MOD 规则变更必须经用户审核** (先呈现拟变更规则+理由+逐条 diff, 明确确认后才写入; 见 MOD_TEMPLATE.md)。
**MOD ASK 必问清单 (硬性, 一次性枚举)**: 因 MOD 冲突/歧义询问时**一轮问全**, 禁止二轮补问 — 记录显示第二轮补问的成因是第一轮枚举不全 (case-009 漏问"原型机成本源列"), 所以要问满清单, 而不是留到下一轮。必问: 成本口径 (**原型机成本源列**: 面价 vs 散件, 含管口径); 缺失稳定属性 (无数值费用列 → 0 还是留空, 记 gaps); 费用组成 (净价公式链引用哪些费用列); 输出文件形态 (单块 vs 多块/目标 sheet/最终路径/是否保留模板既有块)。以上是模板 — 按任务增删, 但已知缺关键映射不得省略。**答问后新浮现的歧义不得靠猜, 也不另起一轮**: 记 gaps, 由 Spec Review (唯一人工点) 一次呈现。
**Business Reasoning Barrier (硬性)**: Prepare 完成后 Barrier 关闭, 直到 `mod_resolution.json` status ∈ {resolved, none} 才解锁 (二元锁, 无例外分级)。**允许读**: `*_premod_evidence.md` / `*_outline.txt`; 裁决期间读 `mod_resolution.json`; `uncertain` 路由的受限补观察 (view html + ≤2 次定向 get/query) 只允许回答 task shape。**禁止读**: `*_flat.csv` / `*_meta.json` / `*_candidates.yaml`。**禁止做**: 生成 `*_digest.md`; column mapping / 公式口径推导 / inheritance / selector / 业务 ASK / FillSpec 推导或撰写。Barrier 未开: 只识别任务与规则, 不解决任务。

### 5. FillSpec Authoring (S4) — `fill_spec.yaml` (LLM 撰写)

**规则优先于数据**: 不逐行复制源数据; Compiler 从 flattened CSV 物化行值。Schema 见 `references/FILLSPEC.md`, 可复制模板见 `assets/fill_spec_template.yaml`。骨架: task (intent + selected_mod 与 mod_resolution 一致) / inputs (staged 名 + source_sheets + target_sheet + platform) / fingerprints (必须与 materialize 产出的 prepare_manifest 完全一致) / mapping.targets[] (base_last_row + clone_roles + rows + columns + lookups + formulas + merges + nulls + remove_rows) / decisions / gaps / lineage / validation (required_coverage + key_outputs + required_empty)。Matrix 一等表达与 v2.5 位置模型 (mode: inplace/sets/group_merges/group_aggregates/columns[].props) — Matrix 物化产出 **source lineage 一等输出** (`plan.source_trace` + workdir `source_trace.json`: 每格 {target, source, transform_chain})。MOD Attention Map 对齐: resolve→`record_map` / map→`field_map` / transform→`transform_chain` / validate→`validation` 断言。全量 schema 见 FILLSPEC「矩阵映射 (matrix)」「v2.5: Row Layout Mode」「布局决策树」「组合行为契约」「常见编译错误速查」; literal sets 只留客户名/日期/fixed footer/显式 override (BULK_SOURCE_DERIVED_LITERAL_FALLBACK 审计)。
**本场景最小文献面 (单块核价/报价块 append)**: 不要全文通读 FILLSPEC — 只读「布局决策树」+「组合行为契约」Q5 (0-口径二分) / Q8 (克隆源) / Q12 (merges×aggregates) / Q19 (聚合不自动建合并区) + 下方公式约定 (ROUND 精准原则) + `combination_patterns.yaml` → `single_quotation_block_append` (单块骨架, rows.selectors 必须排除表头行)。
**公式约定**: 派生数值公式默认 `ROUND(...,2)` 防浮点残值 (text_overflow 属 REPAIR 预期路径)。**0-口径二分**: 入公式链的纯数据字段缺失 → 写数值 0 (`value: "0"` 常量), 不得写空串; 仅独立展示、不入公式链的字段缺失才可留空并记 gaps — 禁止 `IF(J="","",...)` 空白渲染公式。**ROUND 要精准不要扫射**: 只加在真产生残值的运算 (减法/乘法/除法/SUM → ROUND(...,2); 比率 → ROUND(...,4)); 纯加法保持原式。**ROUND 优先序 (比 officecli-xlsx「preserve existing templates」复刻视角优先)**: 新增数据块派生数值公式一律按上方精准原则写。**精度约定**: 直接写入数值 >4 位小数或 >12 位有效数字 → 编译期 NUMERIC_OVERFLOW_RISK, 加 round4/round2; precision: keep 需列宽实测背书。
**撰写规程 (先写后编译循环)**: MOD Resolution 完成后立即写首版 (下一项主要产物就是它); 命中 canonical pattern 直接实例化骨架, 不寻找相似案例; 只有阻塞项才延迟首次 Compile (阻塞项 = 不回答就无法用 FillSpec 表达业务结果的业务未知); 能由 Compiler 机械检出的问题不是阻塞项 (交给 defect 暴露后按 corrective_action 修, 不得手工预证明); 已被用户指令或 Selected MOD 解决的语义不得重开 ASK; 冲突消解裁决序: 本次用户明确指令 > Selected MOD > canonical pattern 默认语义 (结构合法性由 Compiler 裁决)。写 spec → `compile_fill.py` (~0.1s) → stderr 缺陷清单 (code + corrective_action) 即权威反馈 → 定向修 → 重编译; **禁止以源码阅读替代编译验证**。
**Authoring 允许/禁读清单 (canonical)**: **Allowed（默认允许）**: business data read / MOD rules read / fill_spec template（`assets/fill_spec_template.yaml`）/ write/edit fill_spec / compile。**Forbidden by default**: 完整 FILLSPEC.md 读取（按问题定向读小节除外）/ 完整 TASK_ORCHESTRATION.md 读取 / FAILURE_CLASSES.md 读取 / capabilities matrix dump（`--capabilities`）/ source-code inspection。**Pattern Index（`assets/fillspec_patterns.yaml`）**: 已知 shape 时只读对应 pattern, 不要读 FILLSPEC 全文 — `grid_record: rows→columns / columns→matrix (fields: [field_map, record_map]) / grouped_grid→blocks`; 即 `grid_record + record_axis=columns` → `matrix.field_map + record_map`, 无理由再打开 FILLSPEC 全文。该文件禁止示例/schema 解释/exception/tutorial/capability 矩阵 (contract test 机械固定), 也不得仅为建索引而展平。
**Capability Query 合法触发 (首次 Compile 前仅三类)**: A. 确实不知道当前 shape 的 canonical pattern; B. 用户要求明显处于能力边界的操作; C. Pattern Index 明确标记 requires_capability_check。不合法: "为了保险确认一下" / "怕出错先查一下" / 无具体问题的泛化探索。全局验收措辞: `unjustified capability query = 0`。
**能力求证 (按需加载, 正常 Run 不预读)**: happy path 不先 probe、不预读 capability 材料; 只有产生单一可证伪 Capability Question 时才读 CAPABILITY_EVIDENCE.md 并沿其算法: Known Supported → 直接用 (Run Verification 全部执行: formal compile、Validated Draft、readback、结构验证、Render QA); Known Rejected → 找 Known Equivalent Adaptation; Capability Unknown → 非阻塞忽略/等价适配, 只有**昂贵架构分叉**才动用 Extra Capability Probe (`--probe`, 骨架用 `make_probe_spec.py`, 输出 ACCEPTED/REJECTED)。预算判据 (recorded: case-005 probe 膨胀): 单一可证伪问题 + 预期新增证据 + 不重复已失败或等价方法 — 满足即用, 不满足即不用; 每 Run 一次是**默认软预算**, 不是"用完必须停"。Bounded Rescue 使用前按 CAPABILITY_EVIDENCE.md §4 合同 (预声明 question/plan/verdict → scratch 黑盒实验 → workdir Run-local 记录 → 交付呈报一句; 无 Sufficient Evidence 时仅安全路径间的业务取舍才 ASK, 否则 STOP)。Probe 与 Rescue 是两种不同的架构分叉, 各需独立判据; 普通 compile defect 走 REPAIR 不升级。
- **TASK MODE 禁区 (硬性)**: 不读实现源码、不运行测试套件、不修改 Skill、不连续设计实验; 实验 (含 Bounded Rescue) 一律用独立 scratch 文件, staged 文件只读。
- **YAML 纪律**: 含 `: `/引号/特殊字符的字符串统一加引号 — decisions/gaps 条目含 `: ` 时给**整行** (含冒号) 加双引号 (漏写 → SPEC_NON_STRING_ITEM exit 3)。

### 6. Compile / Repair Loop (S5) — `compile_fill.py` (唯一 Compiler)

```bash
python scripts/compile_fill.py --spec fill_spec.yaml --workdir <dir>
```

产出 `execution_plan.json` + `mapping.md` (均派生); 静态验证全部由编译器执行 (含转换函数定义检查: 未知 function / regex_replace pattern 不可编译或缺失 / 缺 replacement / controlled_translation 词表形状坏 → STATIC_VALIDATION_FAILED 结构化缺陷) — agent 不需要手工预检; 失败输出结构化 defect 清单并 exit 3 (不生成 plan), 错误码 → 修复对照见 FILLSPEC「常见编译错误速查」与 FAILURE_CLASSES.md。

**Compile/Repair 循环 — 机械与业务二分 (硬性)**: Compile 只回答 "IR 是否合法", 不回答 "是不是用户要的" — **Compile before Review ≠ 业务决策 before Review**。

- **允许自动修复并重编译的 (机械类)**: YAML/schema 问题 · locator 结构 · fingerprint 不匹配 · 重复写 owner · 公式/merge/nulls 结构冲突 · Compiler 已给出 deterministic corrective_action 的机械问题。修复后仅重编译 (仍在 S5, 不打扰用户)。
- **defect 实为业务歧义时**: 两种业务映射都合理 / 缺失值填 0 还是空 / 字段归属何目标角色 / 用户究竟要哪种输出语义 → **不得"修到能编译"**, 走业务 ambiguity 处理: 单独 ASK 或记 gaps 由 Spec Review 呈现 (见失败处置表)。

**结构/层级缺陷预算 (硬性)**: Compile 返回结构/层级类缺陷 (KEY_OUTPUT_UNWRITTEN / CLONE_RESIDUE_* / BLOCK_KEY_STRUCTURE_INVALID / block·继承·合并结构 / 静默丢弃候选) 时, **默认第一轮先查 `combination_patterns.yaml` / FILLSPEC「组合行为契约」对应 Q 找可复制模式/骨架** (改列名即用), 查不到再按 corrective_action 定向修 — 不自由改 spec 碰运气; **命中 canonical pattern → 直接实例化**, 不再读 case 复盘/测试病历重推组合。

PASS → **COMPILE CLEAN** → 进入 Spec Review。

### 7. Spec Review (S6, 唯一人工点) — `spec_review.py`

对 **COMPILE CLEAN 的 fill_spec** 生成摘要 (映射/转换/排除三节业务语言, 真实表头/角色词汇, **不是 YAML 转储**); 总是生成、确认总是呈现 (低摩擦快速确认); **确认绑定当前 fill_spec 的 sha256 (sha256 与 execution_plan.fill_spec_sha256 同口径)**, 改动后旧确认失效 (fail-closed)。放在 Compile Clean 之后、Execute 之前 — 用户审的是**即将执行的那一份 IR**: Compiler 已消化机械问题, 人工只审业务语义; 机械修复循环不会破坏 hash 绑定。多 run 一次摘要覆盖全部 run, 一次确认绑定全部 run 的 spec (每 run 哈希分别记录)。

```bash
# 单 run:
python scripts/spec_review.py --workdir <dir> --spec fill_spec.yaml
python scripts/spec_review.py --workdir <dir> --spec fill_spec.yaml --confirm
# 多 run (一次覆盖全部 run):
python scripts/spec_review.py --workdir <task_dir> --task task.yaml [--confirm]
```

**Review 绑定语义 (硬性)**: 用户确认的是**这一个具体 FillSpec IR** (不是 "这个任务我看过") — `review_confirm.json` 记录确认时间 + 每 run 绑定的 fill_spec sha256。确认后 FillSpec 任何字节变化 → 旧确认失效 → 必须重新 [Compile Clean → Spec Review] (Review hash 绑定的是即将执行的整份 IR)。

**`--skip-review`**: TASK MODE **禁止使用** (仅 CLI 兼容保留, 不作 canonical path) — Execute 门禁会把 "跳过" 与 "漏做" 同等视为无确认 (见下); 用户确有需求 → 与用户确认是否属 Skill Development 范畴后再议。

### 8. Execute + Verify (S7) — `execute_batch.py` (唯一一次填充)

```bash
python scripts/execute_batch.py --plan execution_plan.json --template t.xlsx --workdir <dir> [--round N]
```

**前置门禁 (fail-closed, 复制模板之前)**: ① **Review 门禁**: `review_confirm.json` 必须存在 (否则 `SPEC_REVIEW_MISSING` exit 3) 且 `review_confirm.fill_spec_sha256 == execution_plan.fill_spec_sha256` (否则 `SPEC_REVIEW_STALE` exit 3) — 一切路径自动收敛: Review 后没改 spec → execute; Review 后改了 spec → 旧确认 stale → 重编译 → 必须 Review 新版本 → execute; 用户确认的始终是即将被执行的那一份 IR; ② **输入哈希核对**: staged 输入 vs plan.input_hashes (compile 期绑定), 漂移 → INPUT_HASH_DRIFT (exit 3), 在复制模板**之前**拒绝。

③ 复制 staged target → validated_draft.<ext> (模板永不被修改); ④ 按 plan 执行 (≤50 op/chunk, 执行尾部显式 close 刷盘); ⑤ `officecli validate` 先于 issue delta (validate 刷新编辑并强制公式求值); ⑥ issue delta vs 模板基线, 只认新增; ⑦ readback 全部由 Compiler 派生 — 值比较数字归一化**只限真数值形态** (容忍 138.00 vs 138, $1,234.5 vs 1234.5, 12.5%), 字母数字标识 (SKU/型号/Z 码) 按文本精确比较, 公式格断言非空, nulls 断言 EMPTY; **禁止手写 checks**; ⑧ 结构 readback: FINAL_ROW_COUNT_MISMATCH + group_merges 边界 (GROUP_BOUNDARY_MISMATCH); ⑨ Render QA: `--render png|html|none` (默认 html, 只渲染 plan.render_qa.region; 纯文本模型用 html 结构检查, 不得声称视觉验证; 失败 → RENDER_QA_FAILED); ⑩ 写 draft_receipt.json (哈希为执行时重算 + input_hash_check 绑定, spec/plan/draft 哈希, op 计数, coverage/readback/structural/render_qa/issue delta/validate) — **Draft 保留不删除**。执行细节见 LAYER4_EXECUTE_LOOP.md / FAILURE_CLASSES.md。

**失败二分 (硬性, 决定是否重新 Review)**:
- **执行/环境类缺陷 (FillSpec 不变)**: officecli timeout / 文件锁占用 / 临时 IO error / render service unavailable → `fill_spec_sha256 unchanged` → 修复运行条件 (重试/清理 resident/重跑) → **直接重 execute, 不需要重新 Review** (用户确认的 IR 没变);
- **FillSpec-affecting 缺陷**: locator 不成立 / 目标结构需变化 / 写入策略需变化 / 映射逻辑需调整 → 修改 FillSpec → **必须重新 [Compile Clean → S6 Spec Review → S7 Execute]** (bytes changed → 旧确认在逻辑上不存在)。旧 SKILL 的 "execute 失败不询问用户直接重执行" 只适用于第一类。

机器验证后: **当前 FillSpec 对应的唯一人工确认点已完成** — 验证全绿 + 哈希一致 = 可交付; 新 FillSpec revision 会有自己的人工点, "唯一人工点" 不是 Job 生命周期一次的承诺。readback 全绿 + 哈希一致后禁止重复逐格人工验证。

### 9. Deliver (S8) — `promote_output.py`（哈希核对复制，唯一 post-verify 写入入口）

```bash
python scripts/promote_output.py --workdir <dir> --final <用户要求的最终路径>
```

读 draft_receipt.json → 三方哈希核对 (receipt vs 当前文件; 输入侧 plan.input_hashes / receipt 执行时值 / 当前 staged, 任一漂移 → exit 3) → 原子复制 draft → final (验证 final hash == draft hash) → 最小 ZIP/结构确认 (pptx 查 presentation.xml) → 写 final_receipt.json (事实记录, 无 PASS/WAITING/APPROVED 状态词)。**验证后绝不再次执行填充。**

## Task Orchestration (单任务多 run)

**何时用**: 任务文本显式 multi-run 时 (多系列/多产品/每 X 一个输出) — run 清单持久载体 + runs/ 目录容器; 单 run 任务不进 Task, 仍走上方公开命令。

### Task Public Contract

1. **`task.yaml` 只声明 run 清单**: 每条 run = id + source.file/sheets + target.template/sheet + output; 零业务映射、零状态字段 (schema: `task_schema.py`; 业务键 fail-closed 拒绝)。共享输入 = 同一 workspace_manifest 事实空间, 不设独立共享声明节。
2. **Materialize**: `materialize_run.py --task task.yaml` 一次遍历全部 runs → 每 run `runs/<id>/prepare_manifest.json` + target routing view (fail-closed: 引用不在 workspace scope → RUN_ENTRY_NOT_IN_WORKSPACE)。无调度/状态/重试机制。
3. **Run workdir**: 每条 run 的全部运行时产物 (spec/plan/draft/receipt/scratch) 在 `runs/<id>/`, 永不隐式读取另一 run 产物。
4. **Spec Review（一次覆盖多 run）**: `spec_review.py --task task.yaml` 摘要覆盖全部 run, `--confirm` 绑定全部 run 的 FillSpec 哈希 (改动后旧确认失效, fail-closed); Task 层不自动确认、不自动交付。
5. **Deliver（每 run 独立文件）**: 每个已 drafted 的 run 哈希核对复制到各自 output — N run → N 个独立文件, 无合并产物; per-run 独立输出是唯一合法形态。
6. **Execute 串行纪律**: Task 内 execute 必须串行 — 多 run 并发曾触发 BATCH_CHUNK_FAILED (单 Office resident 窗口)。
7. **中断重跑**: 用已有 execution_plan.json 重跑; 无 resume/supersede 状态机。输入事实改变 = 删除 derived 文件后重新 --init。

### Run Isolation / Task Artifact Boundary（ticket 08）

- 每次 run 独立 run root; 共享输入 (staged/outline/展平) 按**哈希引用** (workspace_manifest) 共享, 不逐字节复制进 run 目录; 历史 run 完整可追溯。
- MOD 决议任务级一次: context/mod_resolution.json 一份多 run 共享 (Job-level 默认); 单 run 放 workdir。
- 污染目录 fail-closed: materialize 检测到 run 生命周期产物 (spec/plan/receipt) → WORKDIR_POLLUTED (exit 3, corrective_action = 使用全新 run root 或显式 run-id 目录); flatten 产物不是生命周期产物。
- task/run 边界: runs/<id>/ 全部是 run 级私有产物; task 级只有 task.yaml + workspace_manifest + runs/; 二者绝不互当。

> **详细契约参考**: `references/TASK_ORCHESTRATION.md`（异常诊断、开发或机制求证时定向查阅对应章节 — 正常 happy path 禁止全文读取）。

## PPTX 目标

- `platform: pptx` + `target_sheet: slide[N]/table[@id=M]` (id 来自 outline); `workspace_init.py --init` 直接展平 pptx 表格 (每格值写入, 无克隆/合并/公式); FillSpec 的 columns target 仍用列字母 (A..Z) — Compiler 自动映射为 tc[索引]; **pptx 单元格属性是 `text`, 不是 xlsx 的 `value`**。
- **支持矩阵 (issue 06 fail-closed)**: PPTX 当前能力 = 列值填充 (`columns`) + DOM-path `sets` (`/slide[N]/table[@id=M]/tr[X]/tc[Y]`, 值/清空均可); 其余声明 — formulas / merges·group_merges / nulls / remove_rows / mode: inplace / columns[].props — 一律编译期拒绝 (PPTX_CAPABILITY_NOT_ROLLED_OUT, corrective_action 点名声明), 不再静默丢弃。
- `first_data_row` 声明首个数据 tr; 匹配行数必须等于可填行数 (无克隆, 行需预先存在); 越界 → PPTX_TARGET_ROWS_OUT_OF_BOUNDS; 需加行时用 python-pptx 一次性创建后永久关闭 (禁止在 officecli 操作后重新 import)。key_outputs/required_empty 用完整路径。
- 模板自身 validate 失败 (如 chart schema 扩展) 按基线噪音记录 (template-baseline), issue delta 仍是权威新缺陷检查。

# Part IV — 治理（Governance & Discipline）

## 总原则: 思考按需升级

不重新推导确定性状态、不重复评估已解决决策 — 脚本/digest/失败记录已给出的事实不得重新推导; 验证即证据; `officecli get` 只在机器证据**覆盖不到**的断言上使用 (不重复已覆盖的断言, 见下), 并优先自动化; 失败优先读 `_draft_failure.json` 的 defect_class, 禁止自由实验。
**机器证据终止条件 (硬性)**: `execute_batch.py` 已返回 `issues_new` / `validate` / readback (含结构 readback) / render 后, **禁止**再用 `officecli issues`、读 `execution_plan.json`、`officecli get` 逐格复核**它已经覆盖的断言** (坐标、组边界、readback 值、`precision: keep` 列的列宽均有编译期或结构机检背书), 也**禁止读 case 复盘 / 测试病历作证据** — 重复已覆盖的复核是冗余探索。但**机器证据不是充分条件**: render 只产出产物 (`status: "produced"`), 视觉与结构结论属于 Agent (`execute_batch.py`: the verdict is the agent's); 已记录的反例是交付件净价列显示全 0$ 而 readback/issue/validate 全绿 (KNOWN_TRAPS), 以及 readback 假失败必须人工复核。因此: 机器证据**覆盖不到**的目标 (交付件里的公式结果、版式与视觉、与观测矛盾之处) **必须**做异常驱动的定向检查; 覆盖到的不得重复。`get` 与 `issues` 是两回事 — 机器证据已覆盖时 `officecli issues` 一律禁止。
## Help-first
当 officecli 属性名、参数语义、元素能力或 merge/remove 行为不确定时, 先跑 `officecli help <format> <element>` 再生成相关 ops (Standard Evidence Path); **绝不猜测未经确认的命令语义**。已实测机械事实 (spike 四坑: 行删除残留 vMerge、unmerge 多步、`merge.down=N` 总跨度 N+1、validate 对合并残留视而不见) 见 references/KNOWN_TRAPS.md — 运行时不再重新发现。
## Exit Code Protocol
exit 0 = Pass, proceed; exit 1 = Fatal (**非瞬时**环境错误: file missing / 依赖不可用 / 权限), STOP + report to user; exit 3 = Retryable: 读 stderr 的 defect/corrective_action 定向修复后重跑 — 修复是预期步骤, 不询问用户。**瞬时环境故障** (officecli timeout / 文件锁占用 / resident 窗口冲突 / render 服务不可用) 不按 exit 1 终止, 按失败处置表判 RECOVER: 清理运行条件后直接重 execute; 第 2 次连续失败才分类为 ASK/STOP。
## 失败处置表 (每次失败先分类, 再行动)

| 失败类型 | 处置 |
|---|---|
| 编译缺陷 — 机械类 (compile_fill.py exit 3) | **REPAIR**: 按 stderr 的 corrective_action 一次性修完清单上全部缺陷 → 重编译 (S5 内, 不打扰用户)。不询问用户 |
| 编译缺陷 — 业务歧义 (两映射都合理/填 0 还是空/字段归属/输出语义) | **ASK / gaps**: 单独询问用户或记 gaps 由 Spec Review 呈现 — **禁止"修到能编译"** |
| 执行/环境失败, FillSpec 不变 (officecli timeout/锁占用/IO error/render 不可用) | **RECOVER**: 修复运行条件 → 直接重 execute — **不需要重新 Review** (review_confirm 绑定不变) |
| 执行失败, 需改 FillSpec (locator 不成立/结构需变/写入策略需变/映射需调整) | **REPAIR 全链**: 修 spec → 重新 Compile Clean → **重新 Spec Review** → 重新 execute (旧确认已失效) |
| `ROW_GAP_DETECTED` (行号空洞) | **REPAIR via canonical re-init**: `repair_row_gaps.py` 在**副本**上修复 → 产出 repaired input snapshot → 旧 workspace 作废 → 以 repaired snapshot 为输入重新 `workspace_init --init` → 重新 materialize (各 run view) → 刷新 FillSpec fingerprints → Compile → Spec Review → Execute。**绝不原地改 staged 后局部续跑、绝不增量 flatten、绝不 patch manifest 继续跑**; 有效 topology/run 定义/MOD/映射决策可 replay, 不重推业务推理 (replay ≠ 全脑重启) |
| MOD 冲突/歧义 (mod_nominate.py) | **ASK**: 单独询问用户 (降级/替换/覆盖), 不与其他问题捆绑 |
| 连续第 2 次失败 (同一任务) | 重新分类: 多个安全解释 → **ASK**; 无可证明安全计划 → **STOP** |
| 不可证明安全的操作 | **STOP**: 解释 + 推荐正确的领域能力 (如 STRUCTURAL_OP_OUT_OF_ZONE → inplace+trim) |

**禁止**: 把 REPAIR 类与 ASK 类捆绑呈报; 提供"简化任务/手动 officecli 处理/暂停任务"等放弃选项; 以"时间/复杂度"为由绕过或忽略失败门禁。
## Runtime Governance / Evidence Scope Guard（Ticket 09 硬契约）

table-fill 开始后 (Input Intake 之后) 的运行时治理硬契约, 按行为执行 (无文件读取防火墙, 落实 = 契约文字 + contract test + session replay)。

#### Runtime / Development Asset Boundary + 双 Mode 契约（Skill Development vs Table-Fill Task Mode）

tests/fixtures/benchmark expected/historical snapshots 的合法性由**当前 Mode** 决定: **Skill Development Mode** (用户明确把主要目标改为诊断/修改/扩展/评测 table-fill) 下它们才是开发工作的 SoT; **Table-Fill Task Mode** (业务热路径) 下它们**不属于任何合法 Evidence Path** — 即使目的只是"确认机制 HOW" **也不构成例外**; 测试里的历史确认值不是当前业务事实的来源。
**合法业务事实来源枚举** (Task Mode 只允许这六类): ① 用户指令; ② Primary = staged 源文件 (经 manifest 绑定的 flattened 产物); ③ Template = 目标模板结构/指纹/样式事实; ④ selected MOD = mod_resolution.json 裁决的 canonical 版本; ⑤ MOD 受控规则与 lookup; ⑥ 用户显式指定资产。**Mode 开关（唯一）**: 唯一开关 = 用户显式把主要目标切换为 Skill Development; Task Mode 下任何"目的性"理由 **不构成例外**。
**Runtime Navigation Table（问题 → 去处；Task Mode 下唯一合法去处）**:

| 问题 | 去处 |
|---|---|
| 机制语法 / 能力边界 (如 matrix.field_locator) | `compile_fill.py --capability <key>` |
| 组合接受性 / rollout 状态 | `compile_fill.py --capabilities` |
| 具体 spec 是否被接受 | formal compile (`--spec …`) / 仅架构分叉用一次 `--probe` |
| FillSpec 语法 / schema / 代码 | FILLSPEC 对应章节（按问题定位） |
| 已实测机械陷阱 | KNOWN_TRAPS 条目 |
| OfficeCLI 接口 / 参数语义 | `officecli help <format> <element>` |
| 当前 draft 值 / 结构 / 渲染 | readback / 结构验证 / Render QA |
| **tests/fixtures** | **Task Mode 禁止**（仅 Skill Development Mode 可读） |

#### Source Scope Guard（用户声明来源边界）
用户声明边界时, search 的**上界**是 declared scope + selected MOD; 业务答案只能来自合法来源枚举, 不得来自 legacy skill / 历史输出 / scratch 副本 — **禁止无授权递归全盘搜索**。`runtime code/docs` 只界定"搜索能到哪里", **不构成**读实现源码的许可 (源码读取仍受硬约束 6 与 case-004 单次例外约束)。越界搜索被记录/阻止 (严重度足以污染证据边界时 fail-closed)。
#### Runtime Tool Contract（禁止无授权 openpyxl 直写业务）
table-fill 开始后禁止无授权 openpyxl 直写业务执行 — 业务填充唯一执行器是 `execute_batch.py`; Python 仅限 Skill Development/diagnostics/tests。officecli 子进程调用必须经 `_officecli.officecli()` 适配器。
#### Exploration Stop Rule（探索预算与停止条件）
obvious_grid → routing probes = 0 (Fast Path stop-rule 保留); 非 obvious 仅限既有 uncertain 受限补观察, 且每次补观察必须针对一个具体未决问题 (默认 1 次为**软预算**, 不因次数本身中止有效调查); 仍不确定 → ask/ambiguous。**禁止无限 view/render/script/glob/legacy search 直到"感觉理解"**。
#### Barrier Enforcement（Task Shape / MOD Resolution 完成前的闸门）
Task Shape / MOD Resolution 完成前不允许 field mapping / business selector / output generation — 业务推导必须以 task_shape 判定 + `mod_resolution.json` (status ∈ {resolved, none}) 为前提。
## 不信任事件与契约漂移 (记录在案, 制度化交给 Skill Development)

对执行机制/契约产生怀疑是正常信号; TASK MODE 只在当前 Run **记录** (一条 Capability Gap Discovery: 问题 + 证据 + 结论/未解决), 需要跟进时任务后形成 needs-triage 项, **不在业务热路径上做制度化改造**。触发条件: 机制怀疑 > ~1 分钟; 手工模拟行位移/坐标推算 (一次以上); 读源码确认执行行为; 契约结论与实测不符。**最高优先 — 契约漂移** (issue 05 类): 文档/能力矩阵声称的接受性 vs 实际编译行为不一致 → 记录为 Contract Drift, 用证据链最直接的通道继续当前 Run; 两个权威通道冲突且无法判定 → ASK/STOP, 不以自造实验仲裁。
**制度化 (三件套, 只在用户明确切换的 Skill Development 中进行)**: ① 编译器检查 — 把怀疑点变成静态缺陷码 (exit 3 + corrective_action); ② 契约 Q&A — 结论写进 FILLSPEC「组合行为契约」/「执行顺序保证」/能力映射表; ③ contract test — 以最小 fixture 固定行为。产出物三者同源缺一视为未完成, KNOWN_TRAPS 同步沉淀机械事实 (重放 oracle)。
## Observability
**本流程没有时间限制**: 交付报告按步骤事实呈报 (脚本出口状态码、机器证据、defect 清单), 不设时间栏也不要求计时证据 (run 级计时已退役); 时间不是执行约束 — 禁止以"时间限制/任务太复杂"为由建议放弃、简化任务或跳过门禁。

# Part V — 参考路由（Reference Router）

**先决纪律 (渐进式披露)**: 机制问题按 Runtime Navigation Table 与下表**定向查阅**, 不全文通读; references 是细节唯一权威源, SKILL 正文不重复细节。**判据是"读最小文献面"**: 无具体问题不预读、不全文通读、只读触发条件点名的那几个小节 — 一个真实问题可以跨多份文件的小节 (recorded: 报价场景同时读 FILLSPEC 指定小节 + `assets/combination_patterns.yaml` 是必要且不可删的), 因此不设"零文件/单文件"上限; 但禁止把 references 当启动加载项, 禁止"常态运行实际使用七件套"式预读。

## Troubleshooting

| 文件 | 何时读 (定向查阅, 不全文通读) |
|---|---|
| `references/FILLSPEC.md` | FillSpec schema / 布局决策树 / 矩阵映射 / 组合行为契约 Q1-Q19 / 执行顺序保证 / 常见编译错误速查 |
| `references/KNOWN_TRAPS.md` | 已实测机械陷阱 (officecli 四坑/0-口径教训/ROUND 反例/索引重建/外部引用) |
| `references/FAILURE_CLASSES.md` | defect_class → standard_fix 标准修复映射 |
| `references/TOOL_TRAPS.md` | Windows/bash/officecli 工具摩擦与适配器规则 |
| `references/OFFICECLI_REFERENCE.md` | 路径语法、batch JSON、编码规则 (xlsx value/numberformat; pptx text) |
| `references/LAYER4_EXECUTE_LOOP.md` | 执行收敛循环与失败记录 schema |
| `references/TASK_ORCHESTRATION.md` | Task 层唯一详细契约源（异常/开发/机制求证时定向查阅 — 正常 happy path 禁止全文读取） |
| `references/CAPABILITY_EVIDENCE.md` | Capability 三态 / Standard Evidence Paths / Probe·Rescue 合同 / Capability Gap Discovery (按需加载, 正常 Run 不预读) |
| `references/MOD_TEMPLATE.md` + `MOD_INDEX.md` + `MOD_*.md` | MOD 文件格式 / 目录表 / 各业务规则 (canonical resolver 按目录表加载) |
| `references/LAYER1_OLE_HANDLING.md` | OLE/结构解析例外 (直接读 ZIP/XML 的边界) |

> **After editing this skill or any of its scripts/references:**
> Quit and restart OpenCode for changes to take effect. OpenCode loads
> skill content once at startup and does not hot-reload edited files.