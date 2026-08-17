---
name: table-fill
description: >
  Use this skill whenever the user asks to fill, populate, map, or transfer data
  between spreadsheet tables — in any direction (xlsx→pptx, xlsx→xlsx, pptx→pptx,
  pptx→xlsx). Activate immediately on phrases like "fill", "populate", "map", "展平",
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
  Required: officecli (on PATH), Python 3.8+ (PyYAML).
  All read/write goes through officecli. No openpyxl, no pandas, no python-pptx in the pipeline.
  Must co-load: officecli-xlsx (path syntax, open/save lifecycle, batch patterns, QA gates)
  Recommended: officecli-win (Windows subprocess encoding workaround)
metadata:
  drift-risk: high
  gate-count: 1
  mod-nomination: true
---

# Table Fill (v2.5)

Fill, migrate, or aggregate data between Office tables (xlsx/pptx, any direction)
with one canonical business-semantics file, one execution, one human gate, and
full traceability. V2 replaced the old four-layer flow: it merged the batch
builder / mapping renderer / validator / smoke test into a single Compiler, made
MOD resolution a conditional interrupt, and made the user-approved draft the
final file — promotion is a hash-verified copy, never a second execution.

**Why this structure matters**: mapping errors caught after filling are expensive.
V2 catches them in the Compiler (static validation, Section 9 below) before any
file is touched, then proves executability exactly once on the draft, then lets
you approve that validated draft — so what you approve is what gets delivered.

**Why officecli over openpyxl**: All operations use officecli exclusively. Three
reasons: (1) `add --type row` auto-updates dependent structures (formulas,
conditional formatting sqref, data validation, named ranges); (2) python-pptx's
`save()` silently overwrites all officecli-written changes; (3) openpyxl's
dimension metadata is unreliable. If data cleaning or aggregation is needed, use
pandas separately before entering the pipeline — never inside it.

## ⚠️ 依赖加载（必须先执行）

```python
skill(name="officecli-xlsx")    # 路径语法、open/save 生命周期、batch 模式、QA 门禁
# 目标为 PPTX 时追加: skill(name="officecli-pptx")
# Windows/中文路径时追加: skill(name="officecli-win")
```

## v2.5 Skill-only 边界

v2.5 is a skill-level workflow. It does NOT simulate V3 runtime governance:

- No Governed Run, Runtime Guard, Host Adapter, protected paths, or Gate Basis binding.
- The `.gate3_pending` marker is a flow hint, not a trusted authorization state.
- Scripts raise the bar for following the workflow, but cannot enforce it —
  the agent's job is to follow the workflow because it is correct, not because
  a guard watches.

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
   resident cleanup, retry helpers (all in `scripts/_officecli.py`).
7. 同一业务事实只能有一个权威来源 — business semantics live ONLY in fill_spec.yaml;
   everything else (plan, mapping.md, readback, receipt) is derived.

## 权威模型

| 对象 | 类型 | 权威性 |
|---|---|---|
| staged source/target | 输入快照 | 本次运行输入事实 (prepare_manifest.json 记录哈希) |
| `fill_spec.yaml` | Canonical | 唯一业务语义、映射、转换和追溯事实源 |
| `execution_plan.json` | Derived | Compiler 从 FillSpec 物化的操作和验证预期 |
| `mapping.md` | Derived | Compiler 生成的人类审查视图 (编辑 spec, 从不编辑它) |
| `validated_draft.*` | Derived Result | 已执行并通过验证的候选交付文件 |
| `draft_receipt.json` | Evidence | 输入/Spec/Plan/Draft 哈希 + 验证结果 |
| `.gate3_pending` | Skill-only Marker | 流程提示, 不是可信授权状态 |
| final output | Delivery | Validated Draft 的提升副本 (哈希一致) |

**禁止**: 在 Markdown 和 YAML 中分别维护业务事实; 手写 batch JSON; 手写 checks;
冒烟后删除并重新执行; MOD 拥有独立运行状态。

## 工作流 (五个公开命令)

```
prepare_run.py ──► MOD Resolution ──► fill_spec.yaml ──► compile_fill.py
      (outline)    (条件中断)          (LLM 撰写)          (plan+mapping+验证)
                                                            │
                                              execute_batch.py (唯一一次填充)
                                                            │
                                                        Validated Draft + receipt
                                                            │
                                              Execution Gate (唯一 Human Gate)
                                                            │
                                              promote_output.py (hash 验证复制)
```

### 1. Prepare — `prepare_run.py` (两个阶段)

```bash
# 阶段 A: 环境预检 + 暂存 + outline (MOD Resolution 的证据 + 选 sheet 依据)
python scripts/prepare_run.py --workdir <ascii_dir> \
  --files "C:\...\毛利表.xlsx|source_maoli.xlsx,C:\...\报价汇总.xlsx|target_baojia.xlsx" \
  --outline

# 阶段 B: flatten + classify + digest + 结构 fingerprint (机械, 不等 MOD)
python scripts/prepare_run.py --workdir <dir> --flatten \
  --sheets "source_maoli.xlsx:FRESH订家用机型毛利情况;target_baojia.xlsx:11_FRESH本土" \
  --target target_baojia.xlsx
```

- workdir 必须 ASCII (`C:\Temp\tablefill\<task>\`)。所有 artifact 名称 ASCII 化。
- 输出 `prepare_manifest.json` (文件哈希、flattened sheets、digest、fingerprints)。
- 每个 sheet 一个 `{name}_digest.md` — **LLM 只读 digest, 不读 meta.json**。
- 阶段 B 之前先读 outline 确认 sheet 名; 一次 outline 只跑一次, 不重复。
- **`--sheets` 按任务文本一次列出全部源 sheet** (文件间用 `;` 分隔,
  `file.xlsx:S1,S2;file2.xlsx:S3`) — 漏列 sheet 会触发 TARGET_NOT_FLATTENED
  报错后补跑; 增量展平 (flatten 可多次调用) 是**兜底**不是常态。
- flatten 可**多次调用增量展平** (如先源后目标、或分 sheet 批次): manifest
  按条目 name 合并, 新覆盖旧, 不互相覆盖。
- **行号空洞修复 (TEMPLATE_ROW_GAP)**: `scripts/repair_row_gaps.py --workdir <dir>`
  物化缺失行元素后**自动重跑 flatten (仅目标 sheet) 同步 manifest 指纹** —
  flatten 不需手工重跑; 唯一动作 = 更新 spec 的 target_structure 指纹
  (抄 repair 输出 JSON 的 `fingerprints.target_structure`, 或 `--patch-spec`
  一步完成) + 重编译 (见 FILLSPEC Q16)。

### 2. MOD Resolution — `mod_nominate.py` (条件中断)

```bash
python scripts/mod_nominate.py --workdir <dir> --task "<任务文本>" \
  --files "source_maoli.xlsx,target_baojia.xlsx" \
  --outline "source_maoli_outline.txt,target_baojia_outline.txt" \
  --digest "source_maoli_FRESH_digest.md,target_baojia_11_FRESH_digest.md" \
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

`selected_mod` 只写入 fill_spec.yaml — 没有 mod_state, 没有独立 Gate。
MOD 文件格式与捕获流程见 `references/MOD_TEMPLATE.md` / `mod_capture.py`。

**规则注入时机 (硬性)**: 候选 MOD 的规则**必须加载后才可写 spec** — 此硬性
要求不变, 改变的是加载时机与粒度, 不是是否加载。为控制 mod_resolution 相位
信息过载 (真实构成: 读 3 digest + 3 展平 CSV + 用户裁决墙钟 — 提名输出曾含
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

**MOD 规则变更必须经用户审核 (硬性)**: 任何向 MOD 添加、修改、删除规则
(规则本体/Notes/Applicability/摘要/场景上下文) 的行为, agent 必须先呈现
「拟变更规则 + 理由 + 逐条 diff」, 用户明确确认后才允许写入。MOD 内容禁止
承载单次运行事实数字与裁决方式 (见 MOD_TEMPLATE.md「规则变更治理」)。

### 3. FillSpec — `fill_spec.yaml` (LLM 撰写)

**规则优先于数据**: 不逐行复制源数据; Compiler 从 flattened CSV 物化行值。
Schema 见 `references/FILLSPEC.md`, 可复制模板见 `assets/fill_spec_template.yaml`。
要点:

- `task.intent` + `task.selected_mod` (来源信息, 不是状态)
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
- `decisions` / `gaps` / `lineage` 必填 — 追溯事实
- `validation`: `required_coverage` (源行必须被消费) + `key_outputs`
  (readback 采样格, 必须是被写入的格) + `required_empty`

**公式约定**: 派生数值公式默认带 `ROUND(...,2)` 防浮点残值 (如
7747.50000000001 → 溢出列宽触发 text overflow); 0-口径行 (报价缺失) 保留
公式链, 缺失格留空并记入 gaps — 禁止 `IF(J="","",...)` 空白渲染公式。
**ROUND 要精准, 不要扫射**: 只加在**真产生残值**的运算上 — 减法/乘法/除法/
SUM 聚合 (如 O/S/T 用 ROUND(...,2), 比率 U/V/W 用 ROUND(...,4)); 无残值的
纯加法 (如 R 结算价=P+Q) 保持原式 — 给加法加 ROUND 会把 168.7151 截成
168.72, 毛利 (O−R)×数量 被放大 (实测漂移 18.37)。

**精度约定**: 直接写入的数值若 >4 位小数或 >12 位有效数字 (如源面价
`168.715100569657`), Compiler 在**编译期**报 `NUMERIC_OVERFLOW_RISK` —
加内置 `transform: round4` (或 round2) 即可, 不必等到执行期溢出再修一轮;
`precision: keep` 显式接受长精度, 但**需要列宽实测背书** — prepare 已采集
模板列宽 (`meta.column_width`) 时, 编译器估算 keep 列最宽渲染值并与列宽
比较: 超出 → `PRECISION_KEEP_NARROW_COLUMN` (exit 3, corrective_action 改用
round4); 列宽未知 → 豁免 + `PRECISION_KEEP_WIDTH_UNVERIFIED` 警告。

**撰写规程 (先写后编译循环)**:

- **先写后编译**: 写 spec (信任 FILLSPEC「组合行为契约」+「能力映射表」,
  按问题定位答案) → `compile_fill.py` (单轮 ~0.1s) → stderr 缺陷清单
  (code + corrective_action) 即**权威反馈** → 定向修 → 重编译。**禁止以源码
  阅读替代编译验证** — 读源码的成本是编译的数百倍, 结论还不一定对
  (precision: keep 反例: 读了源码反而选错, 见 references/KNOWN_TRAPS.md)。
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
- **效率提示**: 把每轮验证成本从数分钟降到秒级 — compile 一轮 0.1s,
  读源码是它的数百倍。不确定"编译器会怎样处理 X"时, 写正式 spec 走
  formal compile 让编译器回答 (probe 只留给架构分叉)。
- **失败成本量化 (为什么不怕第 1 轮失败)**: 第 1 轮执行失败是**预期路径** —
  修复成本 ≈ 编译 0.1s + 重跑 ~20s + 一次 spec 改动, 合计 <2 分钟; 为防失败
  而读源码是负收益 (一次源码阅读 ≈ 数分钟到十几分钟)。repair 预算 1 轮
  约束的是**连续失败** (防无限循环), 不是单次失败 — 单次失败照常走
  "失败处置表 → 修 → 重跑"。

### 4. Compile — `compile_fill.py` (唯一 Compiler)

```bash
python scripts/compile_fill.py --spec fill_spec.yaml --workdir <dir>
```

产出 `execution_plan.json` + `mapping.md` (均为派生)。编译前静态验证
(Section 9), 失败输出结构化 defect 清单并 exit 3 — **不生成 plan**。

**Section 9 静态验证清单** (Compiler 内置):

- 每条必需源记录进入且仅进入一个目标位置
- 不存在重复目标写入 (同一格只能被一个 mapping/null/formula/group/set 写入)
- 所有写入都有 source、transform 或 explicit decision
- clone source 不是合并锚点 (锚点克隆携带锚点公式到非锚点格)
- clone 残留空值政策完整 (template 行的非空列必须被 fill/null/formula/merge 覆盖)
- **inplace 不变量 (12 码)**: 每目标至多一个 inplace 块且必须末位;
  占位区必须存在且坐标稳定 (append 区合法性 + 重叠检查); 前置 remove_rows 只在
  append 区; sets 禁止进占位区; 保留行残留按**每行自身原值**双基线检查
  (PLACEHOLDER_RESIDUE_* 逐保留行 / 克隆残留沿用 template_row)
- 公式引用范围不越过语义块 (aggregate rows 必须在数据块内)
- aggregate 范围覆盖完整 (通常 `1:{n}`)
- required gaps 已显式记录 (spec.gaps)
- 目标路径来自结构摘要 — base_last_row / 列字母不得超出 digest 维度
- FillSpec fingerprint 与当前输入匹配 (stale spec → 拒绝)

### 5. Draft Execution — `execute_batch.py` (唯一一次填充)

```bash
python scripts/execute_batch.py --plan execution_plan.json \
  --template target_baojia.xlsx --workdir <dir> [--round N]
```

1. 复制 staged target → `validated_draft.<ext>` (模板永不被修改)。
2. 按 plan 执行 (≤50 op/chunk, chunk 间坐标探针; 执行尾部显式
   `officecli close` 刷盘 — resident 延迟写被 taskkill 会丢尾部 chunk)。
3. `officecli validate` **先于** issue delta (validate 刷新编辑并强制公式求值)。
4. issue delta vs 模板基线 — 只认**新增** issue (模板自带基线 issue 是噪音)。
5. readback 全部由 Compiler 派生 — 值比较做数字归一化 (容忍
   `138.00` vs `138`), 公式格断言非空, nulls 断言 EMPTY。**禁止手写 checks**。
   Readback 用单次范围 get 批量读取 (179 格 ≈ 1s, 而非逐格 ~90s)。
6. **结构 readback (v2.5)**: 最终行数断言 (FINAL_ROW_COUNT_MISMATCH) + group_merges
   边界断言 (GROUP_BOUNDARY_MISMATCH — validate 对合并残留视而不见)。
7. **Render QA (v2.5)**: `--render png|html|none` — 只渲染受影响区域
   (plan.render_qa.region), 单次终局。纯文本模型用 html 结构检查,
   **不得声称视觉验证**; 失败 → RENDER_QA_FAILED。
8. 写 `draft_receipt.json` (source/template/spec/plan/draft 哈希, op 计数,
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
3. 原子复制 draft → final, 验证 final 哈希 == draft 哈希。
4. 最小 ZIP/结构确认 (pptx 查 presentation.xml)。
5. 写 `final_receipt.json`。**Gate 后绝不再次执行填充。**

## PPTX 目标

- `platform: pptx` + `target_sheet: slide[N]/table[@id=M]` (id 来自 outline)。
- `prepare_run.py` 直接展平 pptx 表格 (每格值写入, 无克隆/合并/公式)。
- FillSpec 的 columns target 仍用列字母 (A..Z) — Compiler 自动映射为 `tc[索引]`。
- **pptx 单元格属性是 `text`, 不是 xlsx 的 `value`** — Compiler 已内置该差异。
- `first_data_row` 声明首个数据 tr; 匹配行数必须等于可填行数 (无克隆, 行需预先存在;
  需要加行时 agent 用 python-pptx **一次性**创建后永久关闭 — 禁止在 officecli
  操作后重新 import)。
- v2.5: `sets` 支持完整 DOM 路径 (`/slide[N]/table[@id=M]/tr[X]/tc[Y]`);
  `group_merges`/`group_aggregates`/`mode: inplace` 的 pptx 侧在 spike 夹具
  验证前拒绝 (PPTX_CAPABILITY_NOT_ROLLED_OUT)。
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

不重新推导确定性状态, 不重复评估已解决决策。脚本/digest/失败记录已给出的事实
不得重新推导。验证即证据: plan 派生 readback 通过即视为已验证 — 手动
`officecli get` 全流程 ≤2 次, 仅用于异常驱动的定向检查。失败优先读
`_draft_failure.json` 的 defect_class, 禁止自由实验。

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
├── *_outline.txt / *_digest.md / *_flat.csv / *_meta.json / *_candidates.yaml
├── mod_resolution.json            ← MOD 裁决 (结构化)
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

> **After editing this skill or any of its scripts/references:**
> Quit and restart OpenCode for changes to take effect. OpenCode loads
> skill content once at startup and does not hot-reload edited files.
