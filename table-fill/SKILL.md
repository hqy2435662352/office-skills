---
name: table-fill
description: >
  table-fill is the unified intent entry for spreadsheet-to-template fill; after
  Prepare it routes by task shape — obvious grid/record transformation takes the
  FillSpec fast path, fixed-form/layout content filling goes the officecli-native
  path, with bounded direct grid edits and mixed workloads routing as exceptions.

  Use this skill whenever the user asks to fill, populate, map, or transfer data
  between spreadsheet tables — in any direction (xlsx→pptx, xlsx→xlsx, pptx→pptx,
  pptx→xlsx; PPTX targets: column value fills + DOM-path sets — formulas/merges/
  nulls/remove_rows are compile-time rejected). Activate immediately on phrases like "fill", "populate", "map", "展平",
  "读源数据→填模板", "把数据填到模板里", or "从源表出报告", even if the user does not
  explicitly name the source and target formats. Also activate when the user describes
  a multi-file data transfer workflow involving Office tables.

  Do NOT activate when the user wants to create a new file from scratch, edit a single
  cell, or build a general-purpose spreadsheet or slide deck — those are not table-fill
  tasks.

  COMPLIANCE: v2.5 workflow — Prepare → MOD Resolution → FillSpec → Compile → Validated
  Draft → Execution Gate → Promote. The Execution Gate before promotion is MANDATORY.
  MOD Resolution interrupts only on genuine semantic ambiguity.
license: MIT
compatibility: >
  Required: officecli (on PATH), Python 3.10+ (PyYAML). No openpyxl, no pandas,
  no python-pptx in the pipeline — officecli 子进程调用一律经 _officecli.officecli()
  适配器 (规则见「不变量 6」)。Must co-load: officecli-xlsx; Recommended:
  officecli-win (Windows subprocess encoding workaround)
metadata:
  drift-risk: high
  gate-count: 1
  mod-nomination: true
---

# Table Fill (v2.5)

## ⚠️ 依赖加载（必须先执行）

```python
skill(name="officecli-xlsx")    # 路径语法、open/save 生命周期、batch 模式、QA 门禁
# 目标为 PPTX 时追加: skill(name="officecli-pptx")
# Windows/中文路径时追加: skill(name="officecli-win")
```

## 不变量

1. 来源数据真实且可追溯 — every written value traces to a staged source, a
   transform, a lookup, or an explicit decision in the spec.
2. 目标模板结构和格式不被破坏 — structural ops (clone/remove/merge) preserve
   template formatting; clone residue is explicitly nulled.
3. 未决业务歧义不能静默猜测 — ambiguity interrupts at MOD Resolution or is
   recorded in `gaps` and surfaced at the Execution Gate.
4. 正式输出前必须经过 Execution Gate — promotion refuses while the gate is pending.
5. 输出必须通过结构、值、公式和 readback 验证 — the draft passes validate +
   issue-delta + compiler-derived readback before the gate.
6. Windows、中文路径和 OfficeCLI 调用必须稳定 — ASCII workdir, UTF-8 subprocess,
   resident cleanup, retry helpers (all in `scripts/_officecli.py`). 两条规则分开:
   Office 内容结构解析 (列宽/行号空洞/OLE) 允许直接读 ZIP/XML; 任何 officecli
   子进程调用必须经 `_officecli.officecli()` 共享适配器, 禁止裸 subprocess.
7. 同一业务事实只能有一个权威来源 — business semantics live ONLY in fill_spec.yaml;
   everything else (plan, mapping.md, readback, receipt) is derived.

## 权威模型

| 对象 | 类型 | 权威性 |
|---|---|---|
| staged source/target | 输入快照 | 本次运行输入事实 (compile 期绑定到 plan.input_hashes; prepare_manifest.json 的 files[].sha256 是 outline 期快照, repair 后过期) |
| `fill_spec.yaml` | Canonical | 唯一业务语义、映射、转换和追溯事实源 |
| `execution_plan.json` | Derived | Compiler 从 FillSpec 物化的操作和验证预期 |
| `mapping.md` | Derived | Compiler 生成的人类审查视图 (编辑 spec, 从不编辑它) |
| `validated_draft.*` | Derived Result | 已执行并通过验证的候选交付文件 |
| `draft_receipt.json` | Evidence | 输入哈希 (执行时重算 + 绑定比对) / Spec/Plan/Draft 哈希 + 验证结果 |
| `task_shape.json` | Agent 判定 | Task Shape Check (Routing V2) 分流判定, 三字段: `task_shape` (值域 `grid_record`/`form_content`/`mixed`/`uncertain`) + `route` (`fillspec`/`officecli_native`/`combined`) + `evidence` (短 snake_case code, 最小充分); 与 `prepare_manifest.json` 机器事实分层，不含 confidence/hash/QA/execution history |
| `.gate3_pending` | Skill-only Marker | 流程提示, 不是可信授权状态 |
| final output | Delivery | Validated Draft 的提升副本 (哈希一致) |

**禁止**: 在 Markdown 和 YAML 中分别维护业务事实; 手写 batch JSON; 手写 checks;
冒烟后删除并重新执行; MOD 拥有独立运行状态。

## 工作流 (五个公开命令)

`prepare_run.py` (outline → premod_evidence) → Task Shape Check → `mod_nominate.py`
→ 用户裁决 → [--mod 落盘 → 规则加载] → digest 生成 → `fill_spec.yaml` (LLM 撰写)
→ `compile_fill.py` (plan+mapping+验证) → `execute_batch.py` (唯一一次填充) →
Validated Draft + receipt → Execution Gate (唯一 Human Gate) → `promote_output.py`
(hash 验证复制)

### 1. Prepare — `prepare_run.py` (两个阶段)

```bash
# 阶段 A: 环境预检 + 暂存 + outline (MOD Resolution 的证据 + 选 sheet 依据)
python scripts/prepare_run.py --workdir <ascii_dir> \
  --files "C:\...\毛利表.xlsx|source_maoli.xlsx,C:\...\报价汇总.xlsx|target_baojia.xlsx" \
  --outline

# 阶段 B: flatten + classify + premod evidence + fingerprints (机械, 不等 MOD)
python scripts/prepare_run.py --workdir <dir> --flatten \
  --sheets "source_maoli.xlsx:FRESH订家用机型毛利情况;target_baojia.xlsx:11_FRESH本土" \
  --target target_baojia.xlsx
```

- workdir 必须 ASCII (`C:\Temp\tablefill\<task>\`)。所有 artifact 名称 ASCII 化。
- 输出 `prepare_manifest.json` (文件哈希、flattened sheets、premod evidence、fingerprints)。
- flatten 产出每个 sheet 一个 `{name}_premod_evidence.md`（只含 task-shape 路由 + MOD 提名所需的最小结构事实，剥离一切解题材料）；full `{name}_digest.md` 在 MOD Resolution 解锁后才生成（见 Barrier 区块与 §2 解锁程序）。
- 阶段 B 之前先读 outline 确认 sheet 名; 一次 outline 只跑一次, 不重复。
- **`--sheets` 按任务文本一次列出全部源 sheet** (文件间用 `;` 分隔,
  `file.xlsx:S1,S2;file2.xlsx:S3`) — 漏列触发 TARGET_NOT_FLATTENED, 补跑即可。
- flatten 可**多次调用增量展平** (先源后目标或分 sheet 批次, 兜底不是常态):
  manifest 按 name 合并, 新覆盖旧, 不互相覆盖。
- **不得仅为构建 lookup/inheritance 索引而把 sheet flatten 进当前
  manifest** — 索引直接用 `build_inheritance_index.py` 读 staged
  workbook (契约见 FILLSPEC「Fill source use vs lookup-only use」)。
- **行号空洞修复 (TEMPLATE_ROW_GAP)**: `scripts/repair_row_gaps.py --workdir <dir>`
  物化缺失行元素后**自动重跑 flatten (仅目标 sheet) 同步 manifest 指纹** —
  flatten 不需手工重跑; 唯一动作 = 更新 spec 的 target_structure 指纹
  (抄 repair 输出 JSON 的 `fingerprints.target_structure`, 或 `--patch-spec`
  一步完成) + 重编译 (见 FILLSPEC Q16)。

### 1.4 Business Reasoning Barrier（硬性）

Prepare 完成后 Barrier **关闭**，直到 `mod_resolution.json` 的
`status ∈ {resolved, none}` 才**解锁** — 二元锁，无例外分级。以下三张清单全部按
**行为**执行，不是认知建议；Barrier 关闭期间违反任一清单即越界。

**允许读**:

- 读 `*_premod_evidence.md`、`*_outline.txt`;
- 裁决期间读 `mod_resolution.json`（只为呈现裁决选项与记录最终选择）;
- `uncertain` 路由的受限补观察（view html + ≤2 次定向 get/query）**只允许回答 task shape**，不用于任何解题分析。

**禁止读**:

- `*_flat.csv`、`*_meta.json`、`*_candidates.yaml`。

**禁止做**:

- 生成 `*_digest.md`;
- 进行 column mapping、公式·口径推导、inheritance、selector、业务 ASK、FillSpec 推导或撰写。

**Barrier 未开：只识别任务与规则，不解决任务。**

解锁程序见 §2 MOD Resolution（本区块只立规则，不在这里重复解锁步骤）。

### 1.5 Task Shape Check (Prepare B 后、MOD 前) — Routing V2

Prepare Stage B (flatten+premod evidence) 完成后、MOD 提名之前, 先按 **任务形态** 路由
(Routing V2: **Level 0 Obvious Grid Fast Path** + **Exception Routing**)。判定
输入永远三项: **任务指令 × 源 premod_evidence × 目标 premod_evidence** (不是
只看文件长什么样) — 这是对 ADR-0010 原表述的修正, 见 ADR-0011 后果节。
对明确信号**一眼可判** — 读毕 premod_evidence 即答, **零新脚本、零额外 LLM 调用、零常规
额外探测** (不追加 picture scan / HTML render / 额外 query / 第二模型调用)。同一
文件对不同任务可走不同路径 (如"填 50 条产品明细"→ grid_record; "只填封面客户
资料"→ form_content)。

```text
Prepare B (读毕 premod_evidence, Agent 本来就要读)
   ├─ Obvious Grid (稳定 header + 重复 record 行 + 可克隆数据区)
   │     └─► Level 0 FAST PATH: grid_record/fillspec, evidence=["obvious_grid"],
   │          立即进 MOD — 0 新增动作, 禁止继续 routing 分析
   └─ 仅出现明确异常信号 → Exception Routing:
         ├─ Direct   : grid_record + officecli_native (bounded/explicit 写集)
         ├─ Non-Grid : form_content + officecli_native (087 类)
         └─ Combined : mixed + combined (否则进 uncertain)
```

`task_shape` (workload 本质) 与 `route` (执行选择) 是两个正交维度, 不再 1:1
绑定 — **Applicability ≠ Justification** (FillSpec 能表达 ≠ 这次该用)。值域:

| task_shape | 含义 | 合法 route | 典型 evidence |
|---|---|---|---|
| `grid_record` | 稳定 header + 重复 record 行; 目标可克隆/可重复数据区; 映射以列↔列为主; 输出行数由源记录数驱动 | `fillspec` (Fast Path) / `officecli_native` (Direct) | `obvious_grid` / `bounded_explicit_edit`+`no_material_grid_benefit` |
| `form_content` | 固定内容区 (merged form regions), 无可克隆数据行模板; 源内容需跨格/跨行组合 | `officecli_native` | `content_composition` / `layout_or_object_work` |
| `mixed` | substantial grid workload + 明显可分离 non-grid workload | `combined` | `substantial_grid_workload`+`separable_non_grid_workload` |
| `uncertain` | 无明确信号 (临时判定态, 不是稳定类型) | — (不落执行 route) | `insufficient_routing_evidence` / `conflicting_workload_signals` / `task_intent_ambiguous` |

`direct` **永不作为 shape 出现** — 它是执行决策, 不是 workload 本质。route 值域
仅 `fillspec` / `officecli_native` / `combined`; `combined` 是 fillspec +
officecli_native 的组合执行, **不是第三引擎**。evidence 一律短 snake_case code、
最小充分证据 (不写长句论据)。

#### Level 0 — Obvious Grid Fast Path (默认主路径, 不是 fallback)

读毕 premod_evidence 即明显常规 Grid — 稳定 header + 重复 record 行 + 目标可克隆数据区,
映射以列↔列为主, 输出行数由源记录数驱动 (如四案例 Case 1 复杂报价单) → **立即**
`grid_record` + `fillspec`, evidence 固定 `["obvious_grid"]`, 直接进入原 **MOD
Resolution → FillSpec → Compile** 流程 — 95% 任务运行路径零变化, 不追加任何
探测。

**禁止继续 routing 分析** (stop-rule): Fast Path 寄生在"读 premod_evidence"既有动作上,
不是新分类步骤 — **不写 signal checklist、不分级打分、不长篇 reasoning**。
**可观测动作不变式验收线**: 与本任务 V1 基线相比, **0 新增 LLM 调用 / officecli
调用 / inspect / render / 脚本 / scoring / decomposition / feature extraction** —
任何"为了确认是 Grid 才做"的追加动作都违反 Fast Path, 必须直进 MOD。
**Barrier 解锁的 digest 生成命令不计入新增动作（它不是 routing 分析）**。

仅出现**明确异常信号**才进 Exception Routing (正常 Grid 任务不需要任何异常
判断, 也不做对称分类):

#### Exception Routing

- **Direct — `grid_record` + `officecli_native`**: grid 语义但 trivial, 不值得
  启动完整 Grid pipeline (如 3~5 个固定 cell 映射, 甚至 30 cell 固定区域复制,
  无 record-driven 语义)。**双必要条件**:
  ① 目标写集合执行前 **bounded/explicit** — 自检句: **"OfficeCLI batch 本身
  能不能成为完整执行计划"** (batch 即完整执行计划, 无需动态推导);
  ② **无需 Grid 专业能力** — 不需要 record-driven iteration / dynamic rows、
  clone / placeholder / inplace、lookup、formula / aggregate、group merge、
  FillSpec 结构治理。**stop-rule 锚点句**: "Direct 必须明显成立; 若判断 Direct
  需要复杂成本估算, 则停止路由优化, 走 Grid 主路径或 uncertain" — **明显便宜
  ≠ 算出便宜**。触发层 single-cell 排除不变 (单格编辑不是 table-fill 任务,
  见 description 触发排除)。
- **Non-Grid — `form_content` + `officecli_native`**: 目标主体是固定内容区 /
  版式组合 (087 类), 主要操作是文本/图片/版面内容填充而非重复 record 映射。
  V1 form_content 工作流原样保留 (见下), 一等路径, **不是 fallback** — 禁止靠
  FillSpec 失败反向发现 form_content。
- **Combined — `mixed` + `combined`**: substantial grid workload + 明显可分离
  non-grid workload (如 80 条产品明细 + Logo/客户名/备注/行高)。执行契约见
  「Combined 最小契约」; 两个 workload 高度缠绕 → 进 uncertain, **不硬拆**。

**uncertain (临时判定态)**: 既非明显 Grid、也无明确异常信号时 — 一次受限补观察
(view html + ≤2 次定向 get/query) → 必须重判进入 Fast Path 或 Exception 分支;
此补观察**只允许回答 task shape**（见 Barrier 区块「允许读」），不用于解题分析;
仍存在会实质改变执行模型的歧义才 ASK (evidence 用
`insufficient_routing_evidence` / `conflicting_workload_signals` /
`task_intent_ambiguous`, 临时态, 不扩设计)。

#### Combined 最小契约 (mixed + combined)

```text
Prepare → mixed decomposition → Grid 数据/结构执行 + readback/结构验证
→ OfficeCLI finishing (仅触及明确可分的 non-grid workload,
   不得修改或失效 Grid-owned region / structural invariants)
→ Unified QA (Grid 数据与结构仍正确 + finishing 正确 + validate/issues/html)
→ 单一 Final Gate (锁最终 draft) → promote → delivery
```

- **Grid 治理机制不变**: Grid 段的 MOD / FillSpec / Compile / readback / 结构
  验证规则原样适用; **Gate 语义从 Grid 完成门升级为最终 draft 完成门** —
  officecli finishing 在 Gate 之前执行, **preserve promote 的哈希绑定** (Gate
  锁定 finishing 后的最终 draft)。
- **ownership: 一个 side effect 一个 executor owner** — Grid 段归 Grid 执行器,
  finishing 归 officecli; 第一版仅为 Agent 执行约束 (**不建 DSL / ownership
  文件, task_shape.json 不扩成 region manifest**)。
- **启用前提**: ① Grid workload 确实值得 Grid; ② non-grid workload 清晰可分离;
  两个 workload 高度缠绕 → 进 uncertain, 不硬拆。
- **默认 Grid first** — 结构稳定后做固定坐标/layout 编辑更安全; 非绝对
  (明显独立的 finishing 先做亦可, 但任何 finishing 不得修改或失效 Grid-owned
  region / structural invariants)。
- **统一 QA**: Grid 数据与结构仍正确 + finishing 正确 + validate/issues/html
  一次做完; 单一 Final Gate 通过后才 promote / delivery。

#### 四案例映射 (canonical examples)

| Case | task_shape | route | evidence |
|---|---|---|---|
| 复杂报价单 (数十~数百 records + lookup/formula/clone/aggregate) | `grid_record` | `fillspec` | `["obvious_grid"]` |
| 3~5 个固定 cell 映射 (甚至 30 cell 固定区域复制, 无 record-driven 语义) | `grid_record` | `officecli_native` | `["bounded_explicit_edit","no_material_grid_benefit"]` |
| 087 (多格内容重组/图片/版式/固定 merged form) | `form_content` | `officecli_native` | `["content_composition","layout_or_object_work"]` |
| 产品明细 80 records + Logo/客户名/备注/行高 | `mixed` | `combined` | `["substantial_grid_workload","separable_non_grid_workload"]` |

**反例锚点 (防数量阈值思维)**: 只有 3 条记录但需要 lookup/clone/公式/group merge
→ Grid (记录少 ≠ Direct, 数量不是路由依据); 200 行明显 Grid 不值得讨论 Direct —
Fast Path 无疑问, 0 新增动作。

每次 run (含 Fast Path) 落极简 `task_shape.json` (见下), 记录 `task_shape` +
`route` + `evidence` 三字段。`form_content` 在 FillSpec 语境为 `NOT_APPLICABLE`
(产品层 SUPPORTED, 引擎层 NOT_APPLICABLE — 不是 UNSUPPORTED, 不是 Known
Rejected)。

#### form_content 工作流 (officecli native 路径)

1. **沿用共享 workdir / source protection / Prepare** — 与 grid 路径同一 workdir
   与暂存保护, staged 文件只读, Prepare 已生成 premod_evidence（完整 digest 解锁后才有）。
2. **落 `task_shape.json`** — 分流判定产物 (三字段, 见下)。
3. **officecli native execution** — inspect → atomic edit → adjust, 经
   `_officecli.officecli()` 适配器调用 (见不变量 6), 目标模板永不被修改 (编辑
   副本)。
4. **强制完成 officecli-xlsx QA checklist** — `validate` + `view issues` +
   `view html` + 模板 QA (见 `officecli-xlsx/references/qa.md`)。此 checklist
   从"建议做"升级为 form_content **交付前强制条件**: QA 未完成不得交付。
5. **交付呈报** — QA 证据 + 关键内容格摘要 + 改动摘要。

正常任务**不默认人审**。条件 ASK 仅限: 覆盖原文件、不可恢复删除、多种合理语义
无法判断、明显版面溢出且无压缩策略。form_content 路径**不继承** execution_gate /
promote / receipt / FillSpec plan/draft 哈希三元组 — 只为 QA 证据与交付呈报负责。

### 2. MOD Resolution — `mod_nominate.py` (条件中断)

```bash
python scripts/mod_nominate.py --workdir <dir> --task "<任务文本>" \
  --files "source_maoli.xlsx,target_baojia.xlsx" \
  --outline "source_maoli_outline.txt,target_baojia_outline.txt" \
  --digest "source_maoli_FRESH_premod_evidence.md,target_baojia_11_FRESH_premod_evidence.md" \
  --out mod_resolution.json
```

输出结构化 JSON, `status` ∈ `none | resolved | ambiguous | conflict`。裁决规则:

| 情况 | 行为 |
|---|---|
| 用户明确指定 `MOD NONE` | 直接记录 `NONE`, 不中断 |
| 用户明确指定某 MOD, 且排除信号未触发 | 直接采用, 记录 revision — 用户裁决优先, 豁免 missed/pending 信号 |
| 唯一候选, 信号全过 (无 missed), 无排除、无待决事实 | 自动采用 (status=resolved) |
| 多候选产生不同业务含义 | 暂停询问用户 |
| 单候选仍有未验证业务事实 (pending)、或可验证未命中信号 (missed)、或含未知排除条件 | 视为歧义, 询问用户 (fail-closed, 不静默放行) |
| MOD 与实际表结构冲突 (排除信号触发) | 询问降级/替换/覆盖 |

`selected_mod` 写进 fill_spec.yaml 之前, 裁决先落盘到 `mod_resolution.json`（`--mod` 重跑写盘）— 没有 mod_state, 没有独立 Gate; FillSpec 的 `selected_mod` 必须与 `mod_resolution.json` 最终裁决一致（编译器 C2/C3 机械校验）。
MOD 文件格式与捕获流程见 `references/MOD_TEMPLATE.md` / `mod_capture.py`。

**Barrier 解锁程序 (硬性, 对应 §1.4)**: Barrier 保持关闭直到
`mod_resolution.json` 的 `status ∈ {resolved, none}` — 裁决后未带 `--mod` 重跑、
盘上仍是 `ambiguous`/`conflict` 时, 编译器 C4 (`MOD_UNRESOLVED`) 会拦截。

- **裁决落盘**: 用户裁决后**必须带 `--mod <NAME|NONE>` 重跑 `mod_nominate.py`**
  把选择写盘 — `NAME` 须属当前候选集 (越界 fail-closed); 冲突覆盖的选择记为
  `overridden_exclusions` 而非 conflict; `--mod NONE` 写 `resolved` + `selected: NONE`。
- **`resolved + selected MOD`**: 先 `load_rules_for_selected_mod()` 加载该 MOD
  全文规则, 再对每个需要的 sheet 运行 `structure_digest.py`（目标 sheet 加
  `--target`）生成 `{name}_digest.md`。
- **`resolved + NONE` / `status=none`**: 直接生成 digest（无规则注入）。

**规则注入时机 (硬性)**: 候选 MOD 的规则**必须加载后才可写 spec** — 此硬性
要求不变, 改变的是加载时机与粒度, 不是是否加载。为控制 mod_resolution 相位
信息过载 (真实构成: 读 premod_evidence + 用户裁决墙钟 — 提名输出曾含
625 行两候选全量规则), 加载分两段:

- **提名阶段 (mod_nominate.py 输出)**: 每个候选只给「候选名 + 命中/待复验
  信号 + 业务逻辑摘要 + 裁决选项」— **不含完整规则集**;
- **用户裁决后**: 才加载**选中** MOD 的完整规则 (`mod_resolution.json` 候选的
  `rules` 字段或 MOD 文件全文) 注入 FillSpec 撰写上下文;
- **多候选 (ambiguous)**: 裁决选项仍附带各候选的**规则证据摘要** (关键映射/
  公式链差异, 足够裁决判断), 完整规则在选定后加载 — 不加载全量规则直接呈现
  裁决。

选中 MOD 完整规则注入后, 映射关系、公式链、路由、字段继承、校验规则全部
进入 spec 撰写上下文:

- spec 的 `columns`/`formulas`/`lookups`/`rows`/`decisions` 必须与 MOD 规则
  一致 (如 FLD-006 原型机成本 = 源面价 − 铜管成本, FRM-001..008 公式链);
- 任何偏离必须记入 `decisions` 并写明理由;
- **禁止在未加载 MOD 规则的情况下猜测映射关系与数据关系** — 候选规则进入
  spec 撰写上下文前必须已加载 (硬性, 不因输出形态优化放宽);
- `MOD NONE` 或 status=`none` (无候选) 时无规则注入, 按无 MOD 流程撰写。

**撰写纪律 (轻量, 不改动上述硬性流程)**: 以下三条只影响撰写时的认知组织方式:

- 全量加载契约不变: 选中 MOD 的全部规则仍在 FillSpec 撰写前加载;
- 若 MOD 含 `## Runtime Core`: 先建立其业务心智模型再撰写;
- 若 MOD 含 `## Attention Map`: 一次性撰写 FillSpec 时按 resolve → map →
  transform → validate 的认知顺序考虑业务问题 (注意力分组, 不是流水线阶段,
  不减少任何规则的加载)。

**MOD 规则变更必须经用户审核 (硬性)**: 任何向 MOD 添加、修改、删除规则
(规则本体/Notes/Applicability/摘要/场景上下文) 的行为, agent 必须先呈现
「拟变更规则 + 理由 + 逐条 diff」, 用户明确确认后才允许写入。MOD 内容禁止
承载单次运行事实数字与裁决方式 (见 MOD_TEMPLATE.md「规则变更治理」)。

**MOD conflict 且排除信号命中 → 不再读 MOD 全文核对排除信号是否误报** (Case
09 P1 第 1 项): 排除信号已命中的裁决结局是 **fail-closed ASK (降级/替换/覆盖)** —
领域判断不改变裁决机制, 再读 MOD 全文只会做结局不变的确认动作。直接把
「冲突信号 + 候选 + 裁决选项」呈现给用户。

**MOD ASK 必问清单 (硬性, 一次性枚举)**: 因 MOD 冲突/歧义询问用户时, 必须
**一轮问全**以下关键映射, 禁止靠第二轮补问 (Case 09 P1 第 2 项):
- **成本口径**: 原型机成本源列 / 面价 vs 散件 (含管口径 — 原型机成本是否含
  管理费/运费/关税);
- **缺失稳定属性**: 源缺角色/无数值的费用列 (财务费用/OA 信保/返点/其他费用)
  → 数值 0 还是留空、记入 gaps;
- **费用组成**: 净价公式链 (如 J-K-L-M-N) 引用哪些费用列及其来源角色;
- **输出文件形态**: 单块 append vs 多块、写入目标 sheet/最终文件路径、是否
  保留模板既有数据块。
以上条目只是模板 — 按任务实际增删, 但不得在已知缺关键映射时省略。

### 3. FillSpec — `fill_spec.yaml` (LLM 撰写)

**规则优先于数据**: 不逐行复制源数据; Compiler 从 flattened CSV 物化行值。
Schema 见 `references/FILLSPEC.md`, 可复制模板见 `assets/fill_spec_template.yaml`。
要点:

- `task.intent` + `task.selected_mod` (来源信息, 不是状态) — 撰写前提: spec 的
  `selected_mod` / `selected_mod_revision` 必须等于 `mod_resolution.json` 最终裁决
  （C2/C3 会机械校验，裁决缺失/未裁决/不一致都无法编译）。
- `inputs`: staged 文件名 + source_sheets + target_sheet (+ `platform: xlsx|pptx`)
- `fingerprints`: 必须与 prepare_manifest.json 完全一致, 否则编译拒绝
- `mapping.targets[]`: 每个目标 sheet 一个条目 —
  `base_last_row` (来自 digest) + `clone_roles` (spacer/title/header/data,
  各带 template_row) + `rows` (source 展平名 + selectors) + `columns`
  (源列→目标列, 支持 lookup/transform/常量/多列求和/`fallback`/`transforms`
  链) + `lookups` + `formulas` (per_row + aggregates + group_aggregates,
  用 `{r}`/`{r1}:{r2}`/`{n}` 模板) + `merges` + `nulls` (克隆残留置空)
  + `remove_rows`
- **v2.5 位置模型**: data role 可 `mode: inplace` (占位区消费,
  `start_row`/`capacity`/`template_row` 显式, N>capacity→克隆补差,
  N<capacity→尾部 trim); 目标级 `sets` (模板坐标绝对写, `value: null`=清空);
  块级 `group_merges` (按 group_by 物化值分组重建合并, 非锚点显式清空,
  singleton 不合并); 块级 `group_aggregates` (按 group_by 物化值分组,
  聚合公式落组锚点行, 组边界数据驱动 — 每组合计一等能力);
  `columns[].props`/`sets[].props` 白名单 V1=numberformat。
  schema_version 2→2.5 (mode 缺省 = append, 向后兼容)。全量 schema 见
  references/FILLSPEC.md「v2.5: Row Layout Mode」。
- **布局决策先查决策树**: inplace vs clone-append vs 收缩 三选一以 digest
  样式粒度事实为第一判定条件 (带样式→inplace, 裸行→clone-append, 占位行
  自然下沉), 不凭占位块存在性猜 — 见 FILLSPEC「布局决策树」。
- **本场景最小文献面 (单块核价块/报价块 append)**: 不要全文通读 FILLSPEC
  (Case 08 R3 收敛) — 只读 FILLSPEC「布局决策树」+「组合行为契约」
  Q5 (0-口径) / Q8 (克隆源) / Q12 (merges×aggregates) / Q19 (聚合不自动建合并区)
  + 本文件「公式约定」ROUND 精准原则 + `combination_patterns.yaml` →
  `single_quotation_block_append` (单块骨架, rows.selectors 必须排除表头行)。
  `selectors` 段另含表头行守卫警告 `HEADER_ROW_CONSIDERED_DATA` 的机械事实。
- `decisions` / `gaps` / `lineage` 必填 — 追溯事实
- `validation`: `required_coverage` (源行必须被消费) + `key_outputs`
  (readback 采样格, 必须是被写入的格) + `required_empty`

**公式约定**: 派生数值公式默认带 `ROUND(...,2)` 防浮点残值 (如
7747.50000000001 → 溢出列宽触发 text overflow); 0-口径行 (报价缺失) 保留
公式链。**0-口径二分**: **入公式链的纯数据/非公式字段缺失 → 写数值 0
（列映射 `value: "0"` 常量），不得写空字符串** — 空串进入净价等公式求值链
会被按非空文本判错误 → `#VALUE!` → IFERROR 兜底成 0（Case 07 §8 教训）；
仅**独立展示、不入任何公式链**的字段缺失才可留空并记入 gaps — 禁止
`IF(J="","",...)` 空白渲染公式。
**ROUND 要精准, 不要扫射**: 只加在**真产生残值**的运算上 — 减法/乘法/除法/
SUM 聚合 (如 O/S/T 用 ROUND(...,2), 比率 U/V/W 用 ROUND(...,4)); 无残值的
纯加法 (如 R 结算价=P+Q) 保持原式 — 给加法加 ROUND 会把 168.7151 截成
168.72, 毛利 (O−R)×数量 被放大 (实测漂移 18.37)。
**ROUND 优先序 (比 officecli-xlsx 复刻视角优先)**: 新增数据块 (含块级
aggregates/per_row) 的**派生数值公式**一律按上面 table-fill ROUND 精准原则写,
**即使模板既有公式无 ROUND、即使 officecli-xlsx「preserve existing
templates」建议精确复刻** — table-fill 的 ROUND 精准原则优先。`text_overflow`
属 REPAIR 预期路径 (FAILURE_CLASSES standard_fix), 命中按 ROUND 精准原则修,
不是未知能力探测。

**精度约定**: 直接写入的数值若 >4 位小数或 >12 位有效数字 (如源面价
`168.715100569657`), Compiler 在**编译期**报 `NUMERIC_OVERFLOW_RISK` —
加内置 `transform: round4` (或 round2) 即可, 不必等到执行期溢出再修一轮;
`precision: keep` 显式接受长精度, 但**需要列宽实测背书** — prepare 已采集
模板列宽 (`meta.column_width`) 时, 编译器估算 keep 列最宽渲染值并与列宽
比较: 超出 → `PRECISION_KEEP_NARROW_COLUMN` (exit 3, corrective_action 改用
round4); 列宽未知 → 豁免 + `PRECISION_KEEP_WIDTH_UNVERIFIED` 警告。

**撰写规程 (先写后编译循环)**:

- **首版收敛原则 (硬性)**:
  - MOD Resolution 完成后，立即以当前证据撰写首版 `fill_spec.yaml` —
    下一项主要产物就是它。
  - 命中 canonical pattern 时直接实例化其骨架 (改替换表占位即得)，不寻找
    相似案例、不重新推导组合、不重读 case 材料；个别参数拿不准也先完成
    其余部分进入 Compile。
  - 只有**阻塞项**才允许延迟首次 Compile。阻塞项 = 不回答就无法用 FillSpec
    表达业务结果的业务未知项 (如: 目标 sheet 未确定；块数量/输出形态未
    确定；关键业务语义无任何权威来源)。
  - 能由 Compiler 机械检出的问题**不是阻塞项** (如: 列名/列字母合法性、
    lookup/key_column、merge、clone residue、aggregate、源行覆盖率) —
    交给 Compiler 暴露后按 corrective_action 定向修，不得在 Compile 前
    手工预证明。
  - 已被本次用户明确指令或 Selected MOD 解决的**业务语义**，以及已被当前
    输入事实确定的**结构事实/前提**，不得因"想进一步确认"重开、再 ASK
    或追加调查。
  - 冲突消解 — 业务语义的裁决序: 本次用户明确指令 > Selected MOD >
    canonical pattern 默认语义。当前输入/工作簿中的客观事实用于解析结构
    与验证前提，是证据不是权威，不反向改写已确定的业务语义。结构合法性
    由 Compiler 裁决 (Compiler > pattern/example)。
  - 非阻塞的辅助证据准备 (lookup index、coverage 了解等) 不得延迟首版
    FillSpec 落盘与首次 Compile。
- **先写后编译**: 写 spec (信任 FILLSPEC「组合行为契约」+「能力映射表」,
  按问题定位答案) → `compile_fill.py` (单轮 ~0.1s) → stderr 缺陷清单
  (code + corrective_action) 即**权威反馈** → 定向修 → 重编译。**失败成本已
  量化**: 第 1 轮失败是预期路径, 定向修复通常 <2 分钟 (单轮编译 ~0.1s + 按
  corrective_action 改 spec + 重编译), 预算只约束连续失败而不约束单次失败
  —— 不要因"怕失败"而读源码。**禁止以源码阅读替代编译验证** — 读源码的成本
  是编译的数百倍, 结论还不一定对 (precision: keep 反例: 读了源码反而选错,
  见 references/KNOWN_TRAPS.md)。
- **能力求证 (Capability Resolution) — 按需加载, 正常 Run 不预读**: happy
  path 直接撰写正式 spec → formal compile; 不先 probe, 不预读 capability
  材料。只有产生**单一、可证伪的 Capability Question** (机制能力疑问: 关于
  Table Fill / Prepare / Compiler / Executor / OfficeCLI 机制行为) 时, 才按需
  读取 `references/CAPABILITY_EVIDENCE.md` (唯一详细 policy 源) 并沿其终局
  算法执行:
  1. 找与问题有直接 **Evidence Fit** 的 Standard Evidence Path (支持/拒绝/
     rollout 状态 → capability contract / `compile_fill.py --capabilities`;
     OfficeCLI 接口 → `officecli help`; 同形机械陷阱 → KNOWN_TRAPS;
     Draft 值/结构/渲染 → readback / 结构验证 / Render QA);
  2. **Known Supported** → 直接使用 — 正常 Run Verification (formal compile、
     Validated Draft、readback、结构验证、Render QA、Execution Gate) 全部执行;
  3. **Known Rejected** → 不尝试; 找满足同一业务意图与约束的 Known Equivalent
     Adaptation;
  4. **Capability Unknown** → 非 task-blocking 忽略继续; 标准流水线可回答则走
     流水线; 有等价适配则 ADAPT; 昂贵的 Compiler acceptance 架构分叉才可消耗
     本 Run 唯一 **Extra Capability Probe**; 四项资格 (unresolved +
     task-blocking + 无 Standard Evidence Path + 无 Known Equivalent
     Adaptation) 全满足的未知才可消耗本 Run 唯一 **Bounded Rescue**; Rescue 无
     Sufficient Evidence → 只有多个安全 Known Supported 路径之间的业务取舍才
     ASK, 否则 STOP (用户确认不能授权 Capability Unknown)。
- **预算 (每 Run 各一次, 不按问题重置)**: Extra Capability Probe (`--probe`,
  骨架用 `make_probe_spec.py`) 只用于未被能力权威回答、两个互斥且实质不同的
  FillSpec 骨架构成架构分叉、选错会明显重写的场景; 输出只有 ACCEPTED/REJECTED,
  具 Evidence Fit 即终结该 Capability Question, 不得转 Rescue; 环境故障允许
  原样重试。Bounded Rescue 一个问题、一个黑盒方案、一个判据、一个结论,
  获得 Sufficient Evidence 即止。**Rescue 使用前按
  `references/CAPABILITY_EVIDENCE.md` §4 合同执行: 预声明 question/plan/verdict
  → scratch 黑盒实验 → workdir 留 Run-local 记录 → Gate 披露一句 (回答了什么问题
  / PASS-FAIL 结论 / 证据 Run-local 且未制度化); 实验无结论时按 §5 边界: 仅多个
  安全 Known Supported 路径间的纯业务取舍才 ASK, 否则 STOP**。同一 Capability
  Question 只能消费其中一种路径; Probe 与 Rescue 预算独立。普通 compile defect
  走 REPAIR (见失败处置表), 不升级为 Capability Unknown。
- **TASK MODE 禁区 (硬性)**: 不读实现源码、不运行 Skill 测试套件、不修改 Skill、
  不连续设计实验; 任何实验 (含 Bounded Rescue) 一律使用独立 scratch
  文件, staged 文件只读 — 在 staged 副本上做实验会污染暂存文件, 触发重复
  flatten。
- **YAML 纪律**: 含 `: ` / 引号 / 特殊字符的字符串**统一加引号** —
  `decisions`/`gaps` 条目含 `: ` 时给**整行** (含冒号) 加双引号:
  `- "追加新历史块: 源文件 ..."`。漏写 → 裸标量被解析成 mapping 静默丢内容
  (SPEC_NON_STRING_ITEM exit 3, corrective_action 直接给正确写法 — 兜底仍在,
  但不要依赖兜底)。
- **note_phase 合规**: 关键相位至少各记录一次 — `mod_resolution` /
  `spec_authoring` / `compile_review` / `execute_review` / `gate_wait`;
  缺 Agent 相位 → run_timing 不完整, Gate 报告缺 Agent 时间栏。
### 4. Compile — `compile_fill.py` (唯一 Compiler)

```bash
python scripts/compile_fill.py --spec fill_spec.yaml --workdir <dir>
```

产出 `execution_plan.json` + `mapping.md` (均为派生)。编译前静态验证
(Compiler 内置), 失败输出结构化 defect 清单并 exit 3 — **不生成 plan**。

**静态验证全部由编译器执行** — agent 不需要手工预检或复述清单; 命中即按
stderr 的 defect 码定向修, 错误码 → 修复对照见 references/FILLSPEC.md
「常见编译错误速查」与 references/FAILURE_CLASSES.md。

**结构/层级缺陷预算 (硬性)**: Compile 返回的缺陷属**结构/层级类** —
`KEY_OUTPUT_UNWRITTEN`、`CLONE_RESIDUE_*`、`BLOCK_KEY_STRUCTURE_INVALID`、
block/继承/合并结构、静默丢弃候选 — 时, **默认第一轮先查
`combination_patterns.yaml` / FILLSPEC「组合行为契约」对应 Q, 找可复制的
模式/骨架** (改列名即用), 而不是自由改 spec 再 compile 碰运气 (Case 05 E4:
3 次编译反推 → 1 次定向修)。查到匹配模式照抄; 查不到再按 corrective_action
定向修。**命中 canonical pattern → 直接实例化** (改替换表占位列名即可),
**不再读 case 复盘 / 测试病历重推组合** (Case 07 改进 2: E3/E4 类重复推导
压缩为一次实例化)。
此规则在首版撰写阶段同样适用 (见 §3「首版收敛原则」)。

### 5. Draft Execution — `execute_batch.py` (唯一一次填充)

```bash
python scripts/execute_batch.py --plan execution_plan.json \
  --template target_baojia.xlsx --workdir <dir> [--round N]
```

1. **输入哈希核对**: staged 输入与 `plan.input_hashes` (compile 期绑定)
   比对 — 漂移 → `INPUT_HASH_DRIFT` (exit 3), 在复制模板**之前**拒绝。
2. 复制 staged target → `validated_draft.<ext>` (模板永不被修改)。
3. 按 plan 执行 (≤50 op/chunk, chunk 间坐标探针; 执行尾部显式
   `officecli close` 刷盘 — resident 延迟写被 taskkill 会丢尾部 chunk)。
4. `officecli validate` **先于** issue delta (validate 刷新编辑并强制公式求值)。
5. issue delta vs 模板基线 — 只认**新增** issue (模板自带基线 issue 是噪音)。
6. readback 全部由 Compiler 派生 — 值比较做数字归一化,**但只限真数值形态**
   (容忍 `138.00` vs `138`、`$1,234.5` vs `1234.5`、`12.5%`); 字母数字标识
   (SKU/型号/Z 码) 按文本精确比较, 写错必须被拦截。公式格断言非空,
   nulls 断言 EMPTY。**禁止手写 checks**。
   Readback 用单次范围 get 批量读取 (179 格 ≈ 1s, 而非逐格 ~90s)。
7. **结构 readback (v2.5)**: 最终行数断言 (FINAL_ROW_COUNT_MISMATCH) + group_merges
   边界断言 (GROUP_BOUNDARY_MISMATCH — validate 对合并残留视而不见)。
8. **Render QA (v2.5)**: `--render png|html|none` (默认 `html`, issue 03 /
   Case 07 改进 4 — 省略时按 html 执行, 不必自补 `view html`) — 只渲染受影响
   区域 (plan.render_qa.region), 单次终局。纯文本模型用 html 结构检查,
   **不得声称视觉验证**; 失败 → RENDER_QA_FAILED。
9. 写 `draft_receipt.json` (source/template 哈希为**执行时重算值** +
   `input_hash_check` 绑定比对, spec/plan/draft 哈希, op 计数,
   coverage, readback, structural, render_qa, issue delta, validate)。
   **Draft 保留, 不删除**。

执行细节 (刷盘顺序/失败码/修复循环) 见 `references/LAYER4_EXECUTE_LOOP.md`
与 `references/FAILURE_CLASSES.md`; 失败 (exit 3) 是**预期路径** (REPAIR 类):
读 `_draft_failure.json` 的 `defect_class`/`standard_fix` → 修 **fill_spec.yaml**
→ 重新 compile → 重新执行。**此过程不询问用户、不提供放弃选项**; 预算只
约束连续失败: 第 2 次失败才重新分类为 ASK/STOP。

### 6. Execution Gate (唯一 Human Gate, MANDATORY, fail-closed)

Present ALL of: MOD resolution 结果, 关键 mapping 与业务决策, 数据缺口与未决项,
来源覆盖, Draft 验证结果 (readback/issue/validate), Draft SHA-256, timing。
**块标题候选预生成**: 新历史块标题如需用户确认, 从源元数据预生成候选
(铜价/汇率基准如 `105000/6.7`、付款条件如 `DP AT SIGHT`、源文件名日期),
随 Gate 一并呈现——用户只需选择/确认, 不用现场想。

```bash
python scripts/execution_gate.py --set --workdir <dir>
```

`--set` 记录**被呈现的** spec/plan/draft 哈希。**End your response here.
Do NOT promote until the user confirms.**

用户明确确认后:

```bash
python scripts/execution_gate.py --confirm --workdir <dir>
```

`--confirm` 是**正向确认** (fail-closed): 无 pending marker 时报错; 确认时
重算哈希, 与呈现时不一致 → 拒绝 (需重新 --set 呈现); 一致 → 写入
`.gate3_confirmed` (绑定哈希三元组)。**确认后重建 Draft 或改 spec/plan,
promote 会被 HASH_DRIFT 拒绝** — 必须重新走一遍 Gate。

### 7. Promote — `promote_output.py` (Gate 后唯一写入入口)

```bash
python scripts/promote_output.py --workdir <dir> --final <用户要求的最终路径>
```

1. 要求存在正向确认记录 `.gate3_confirmed` — marker 缺失不是确认 (fail-closed)。
2. 三方哈希核对: 确认记录 / draft_receipt / 当前文件 (spec+plan+draft) —
   任一漂移 → 拒绝 (exit 3), 需重新生成 draft 并重新 Gate。
   **输入哈希三方核对**: plan.input_hashes / receipt 执行时值 / 当前 staged
   文件 — 任一漂移 → HASH_DRIFT (exit 3)。
3. 原子复制 draft → final, 验证 final 哈希 == draft 哈希。
4. 最小 ZIP/结构确认 (pptx 查 presentation.xml)。
5. 写 `final_receipt.json`。**Gate 后绝不再次执行填充。**

## Task Orchestration (单任务多 run 批量编排)

**何时用**: 一个业务任务包含**多条 run**（多条产品线/输出），且各 run 共享同一
源工作簿（或同 sheet）的准备事实 —— Task 层把「共享源准备」从「每 run 重复
准备」改成「任务级一次 + 逐 run 物化」，并统一批量 run 的生命周期、聚合 Gate
与计时。单 run 任务不需要 Task 层，仍走上方五个公开命令。

三个入口脚本（任务级唯一命令面，run 层五个公开命令零改动）:

```bash
python scripts/prepare_task.py --task-root <task_dir> --validate|--init|--prepare|--run
python scripts/gate_task.py      --task-root <task_dir> --set|--confirm
python scripts/resume_task.py    --task-root <task_dir> --resume [--rebuild] | --supersede --map old=new
```

- `task.yaml` 只描述编排（run 清单 + 输入输出引用 + 输出命名），**不承载业务
  映射** —— 映射/公式/校验永远在 `runs/<id>/fill_spec.yaml`（MOD 规则指导撰写）；
  业务映射确认后再写 task.yaml。
- 完整契约（task model / cache / 调度 / 生命周期与恢复 / 聚合 Gate / CLI）见
  `references/TASK_ORCHESTRATION.md` —— 本文件只给入口，机制细节不内嵌。
- Task 层**不自动确认 Gate、不自动 promote**（fail-closed 不变）；中断恢复、
  输入事实变化后的 supersede 都走 `resume_task.py`。

## PPTX 目标

- **Barrier 边界**: PPTX flatten 仍按既有行为生成自身 minimal digest（无
  premod_evidence、无 digest 延迟）— Business Reasoning Barrier (v2.5) 当前
  仅覆盖 XLSX 主路径（见 §1.4 与 ADR-0011 后果节）。首次引入需要 MOD 提名+
  完整 digest 的 PPTX 业务前，重新评估该边界。
- `platform: pptx` + `target_sheet: slide[N]/table[@id=M]` (id 来自 outline)。
- `prepare_run.py` 直接展平 pptx 表格 (每格值写入, 无克隆/合并/公式)。
- FillSpec 的 columns target 仍用列字母 (A..Z) — Compiler 自动映射为 `tc[索引]`。
- **pptx 单元格属性是 `text`, 不是 xlsx 的 `value`** — Compiler 已内置该差异。
- **支持矩阵 (issue 06 fail-closed)**: PPTX 当前能力 = 列值填充 (`columns`)
  + DOM-path `sets` (`/slide[N]/table[@id=M]/tr[X]/tc[Y]`, 值/清空均可);
  其余声明 — `formulas` (per_row/aggregates/group_aggregates)、
  `merges`/`group_merges`、`nulls`、`remove_rows`、`mode: inplace`、
  `columns[].props` — 一律编译期拒绝 (`PPTX_CAPABILITY_NOT_ROLLED_OUT`,
  corrective_action 点名声明), **不再静默丢弃**。
- `first_data_row` 声明首个数据 tr; 匹配行数必须等于可填行数 (无克隆, 行需
  预先存在); `first_data_row + 匹配行数 − 1` 越过表格实际行数 → 编译期拒绝
  (`PPTX_TARGET_ROWS_OUT_OF_BOUNDS`); 需要加行时 agent 用 python-pptx
  **一次性**创建后永久关闭 — 禁止在 officecli 操作后重新 import。
- pptx 的 key_outputs / required_empty 用完整路径
  (`/slide[N]/table[@id=M]/tr[X]/tc[Y]`)。
- 模板自身 validate 失败 (如 chart schema 扩展) 时, validate 按基线噪音记录
  (`template-baseline`), issue delta 仍是权威新缺陷检查。

## Help-first

当 officecli 属性名、参数语义、元素能力或 merge/remove 行为不确定时, 先跑
`officecli help <format> <element>` 再生成相关 ops (Standard Evidence Path);
**绝不猜测未经确认的命令语义**。已实测机械事实 (spike 四坑: 行删除残留
vMerge、unmerge 多步、`merge.down=N` 总跨度 N+1、validate 对合并残留视而不见)
见 references/KNOWN_TRAPS.md — 运行时不再重新发现。

## Exit Code Protocol

| Exit | Meaning | Action |
|------|---------|--------|
| 0 | Pass | Proceed |
| 1 | Fatal (file missing, env error) | STOP, report to user |
| 3 | Retryable | 读 stderr 的 defect/corrective_action, 定向修复后重跑 — 修复是预期步骤, 不询问用户; 第 2 次连续失败才分类为 ASK/STOP |

## 总原则: 思考按需升级

不重新推导确定性状态, 不重复评估已解决决策 — 脚本/digest/失败记录已给出的事实
不得重新推导; 验证即证据, 手动 `officecli get` 全流程 ≤2 次, 仅用于异常驱动的
定向检查; 失败优先读 `_draft_failure.json` 的 defect_class, 禁止自由实验。

**机器证据终止条件 (硬性)**: `execute_batch.py` 已返回 `issues_new` /
`validate` / readback (含结构 readback) / render 后, **禁止**再用 `officecli
issues`、读 `execution_plan.json`、`officecli get` 逐格复核, 也**禁止读 case
复盘 / 测试病历作证据** — 坐标、列宽、readback、组边界都已被机器证据证明,
人工复核是冗余探索。唯一例外 = **异常驱动的定向检查** (render 失败 / readback
意外差异): 允许 `officecli get` ≤2 次定位具体格; `get` 与 `issues` 是两回事 —
机器证据已证明时 `officecli issues` 一律禁止。

## 失败处置表 (每次失败先分类, 再行动)

| 失败类型 | 处置 |
|---|---|
| 编译缺陷清单 (compile_fill.py exit 3) | **REPAIR**: 按 stderr 的 corrective_action **一次性修完清单上全部缺陷** → 重编译。不询问用户 |
| 执行/验证失败 (execute_batch.py exit 3) | **REPAIR**: 读 `_draft_failure.json` 的 defect_class/standard_fix → 修 spec → 重编译 → 重执行。不询问用户 |
| MOD 冲突/歧义 (mod_nominate.py) | **ASK**: 单独询问用户 (降级/替换/覆盖), 不与其他问题捆绑 |
| 连续第 2 次失败 (同一任务) | 重新分类: 多个安全解释 → **ASK**; 无可证明安全计划 → **STOP** |
| 不可证明安全的操作 | **STOP**: 解释 + 推荐正确的领域能力 (如 STRUCTURAL_OP_OUT_OF_ZONE → 引导用 inplace+trim) |

**禁止**: 把 REPAIR 类问题与 ASK 类问题捆绑呈报; 提供"简化任务/手动 officecli
处理/暂停任务"等放弃选项; 以"时间/复杂度"为由绕过或忽略失败门禁。

## 不信任事件与契约漂移 (记录在案, 制度化交给 Skill Development)

对执行机制或契约产生怀疑是正常信号; 但 **TASK MODE 只在当前 Run 记录, 不在
业务热路径上做制度化改造** (补缺陷码、契约、测试、KNOWN_TRAPS)。三件套制度化
只允许在用户明确把主要目标改为诊断/修改/扩展/评测 table-fill 的 **Skill
Development** 中进行 (详见 references/CAPABILITY_EVIDENCE.md「Capability Gap
Discovery 与模式切换」)。

**触发条件 (任一, 触发即记录一条 Capability Gap Discovery)**:

| 触发条件 | 示例 |
|---|---|
| 对执行机制怀疑 > ~1 分钟 | "编译器会怎样处理 X"悬而未决 |
| 手工模拟行位移/坐标推算 | add/remove 交互、trim 后行号手算 (一次以上) |
| 读源码确认执行行为 | 无论结论对错, 都是一次未记录的不信任事件 |
| 契约结论与实测不符 | 能力矩阵/FILLSPEC 声明 vs 编译/执行结果矛盾 |

**最高优先触发条件 — 契约漂移 (issue 05 类)**: 文档/能力矩阵声称的接受性或
缺陷行为与实际编译行为不一致。契约是 Agent 决策的地图, 漂移即地图错位。
TASK MODE 下把它记录为 Contract Drift (一条 Capability Gap Discovery), 用
证据链最直接的通道继续当前 Run; 若两个权威通道冲突且无法判定哪个更直接 →
ASK/STOP (见 references/CAPABILITY_EVIDENCE.md「冲突仲裁」), 不以自造实验
仲裁。修复与一致性回归闭环 (capabilities 矩阵输出 == 实际编译结果) 属于
Skill Development。

**TASK MODE 处理 (记录, 不改造)**: 每次怀疑记录一条 Capability Gap Discovery
(问题 + 证据 + 结论/未解决), 需要跟进时在任务后形成轻量 **needs-triage** 项,
不阻塞交付。普通 defect 走 REPAIR, 机制未知按 CAPABILITY_EVIDENCE.md 的
Standard Evidence Path / Probe / Bounded Rescue 路径解决 — 二者都不构成
TASK MODE 内的 Skill 修改。

**制度化标准 (Skill Development, 用户发起)**: 三件套缺一不可 —

1. **编译器检查**: 把怀疑点变成静态缺陷码 (compile_fill 静态验证段,
   exit 3 + corrective_action); 需要时以最小变异实验 (probe 面) 查明确切
   触发条件再落码。
2. **契约 Q&A**: 结论写进 FILLSPEC「组合行为契约」/「执行顺序保证」/
   能力映射表 — Agent 按问题定位的权威答案, 不再二次勘察。
3. **contract test**: 以最小 fixture 固定该行为 (test_optimization.py 既有
   契约测试面, 不新增基建) — 未来改动使行为回归时测试变红。

**产出物 (三者同源, 缺一视为未完成)**: 缺陷码 + 契约条目 + 回归测试;
KNOWN_TRAPS 同步沉淀机械事实 (重放 oracle), 不落 KNOWN_TRAPS 的转换不算
完成。

**反例 (不转换的成本)**: 埃及 11_FRESH本土 — Agent 对 remove_rows 越界手工
模拟 30+ 次行位移才确认执行身份。事后转换产物: `REMOVE_TARGETS_APPEND_ZONE`
(编译器检查) + FILLSPEC「执行顺序保证」E1-E4 (契约 Q&A) + contract tests
(回归测试), 同域任务再运行零烧脑 — 此类转换同样在 Skill Development 中落地。

## Observability

脚本自动把机器相位追加到 `<workdir>/run_timing.json`
(`kind: machine` — prepare/compile/draft_execute/promote, 含失败轮)。LLM
思考/等待时间由 agent 自报: 在每个脚本调用前运行

```bash
python scripts/note_phase.py --workdir <dir> --phase <名称>
```

它按上一记录终点计算 `kind: agent` 条目的耗时 (建议相位名:
`mod_resolution` / `spec_authoring` / `compile_review` / `execute_review` /
`gate_wait`)。自报近似, 但能把墙钟分解成 **机器 Xs + 思考 Ys**。报告 Gate
和最终交付时引用该文件 (机器 + agent 两栏)。

**本流程没有时间限制**: run_timing 仅用于 Gate 与交付报告, 不是执行约束;
思考/等待耗时不会导致失败或降级。禁止以"时间限制/任务太复杂"为由建议放弃、
简化任务或跳过门禁。

## Output Files

```
<workdir>/                        ← ASCII workdir (C:\Temp\tablefill\<task>\)
├── prepare_manifest.json          ← Prepare 产物清单 + fingerprints
├── task_shape.json                ← Task Shape Check 分流判定 (task_shape + route + evidence)
├── *_outline.txt / *_premod_evidence.md / *_flat.csv / *_meta.json / *_candidates.yaml
├── *_digest.md                     ← 解锁后由 structure_digest.py 生成 (Post-MOD)
├── mod_resolution.json             ← 最终裁决记录（--mod 写盘）
├── fill_spec.yaml                 ← Canonical 业务语义 (LLM 撰写)
├── execution_plan.json / mapping.md   ← Compiler 派生
├── validated_draft.<ext>          ← 已验证候选交付文件 (Gate 后提升)
├── draft_receipt.json             ← 执行证据
├── final_receipt.json             ← 提升证据
├── run_timing.json
└── .gate3_pending                 ← 流程标记 (set/confirm 后消失)

<用户路径>/final.<ext>             ← Delivery (hash == draft hash)
```

## Troubleshooting

- `references/FILLSPEC.md` — FillSpec schema + 常见错误 + 示例。
- `references/KNOWN_TRAPS.md` — 已知失败模式与修复。
- `references/FAILURE_CLASSES.md` — defect_class → 标准修复映射。
- `references/TOOL_TRAPS.md` — Windows/bash/officecli 工具摩擦。
- `references/OFFICECLI_REFERENCE.md` — 路径语法、batch JSON、编码规则。
- `references/LAYER4_EXECUTE_LOOP.md` — 收敛循环与失败记录 schema。
- `references/TASK_ORCHESTRATION.md` — Task 层唯一详细契约源（单任务多 run 编排）。

> **After editing this skill or any of its scripts/references:**
> Quit and restart OpenCode for changes to take effect. OpenCode loads
> skill content once at startup and does not hot-reload edited files.
