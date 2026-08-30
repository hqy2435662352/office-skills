# MOD Index — Nomination Catalog

<!--
  L0.5 提名: mod_nominate.py 评估可在展平前验证的表事实信号 (任务文本/文件名/outline),
  输出提名卡, 由用户裁决 (选择或 NONE)。结构类信号中 dimension_set 按 premod_evidence
  表头角色事实核对 (premod_evidence 已喂时给出真 hit/miss, 未喂时 pending);
  block_layout/formula_chain/measure_set 等其余结构信号标记"待L2复验"。
  L2 用全量事实复验, 冲突则重新裁决。
  非表事实项 (客户/组织/用户意图) 不作为 agent 排除依据 —— 一律在提名/执行 Gate
  由用户裁决。
-->

## Schema

Each row registers one MOD. 提名脚本 (`mod_nominate.py`) 只读本表 + MOD 文件的
`Applicability` 与 `业务逻辑摘要` 段; 不读取规则明细 (规则在用户裁决后加载)。

| Column | Required | Description |
|--------|----------|-------------|
| **MOD Name** | yes | Unique, stable identifier. |
| **Aliases** | no | Comma-separated shorthand names a user may supply. Case-insensitive match. |
| **Scope Signals (+)** | yes | 表事实信号 (提名阶段可验证的: semantic_type/target_title/source_pattern/target_pattern/product_domain/sheet_marker/dimension_set(premod_evidence 已喂时); 待L2复验的: measure_set/formula_chain/block_layout/unit_convention/time_granularity). 格式 `signal_kind::value`. |
| **Exclusion Signals (-)** | no | **表事实**排除信号 (agent 可判定的可观察事实)。非表事实项 (客户上下文/用户意图) 不得列入, 由用户在 Gate 裁决。无 evaluator 的未知排除条件不默认放行 — 记入 `pending_exclusions`, 阻断自动 resolved (fail-closed, 询问用户)。 |
| **MOD File Path** | yes | Relative from `table-fill/references/`. File `MOD_<name>.md`. |
| **Revision** | yes | Monotonic integer. Incremented on every confirmed MOD update. |
| **Visibility** | yes | `public` (de-identified, ships with office-skills) or `private` (customer-owned). |

## Signal kinds

| Kind | 提名阶段可验证? | 证据来源 |
|------|----------------|---------|
| `semantic_type` | ✅ | 任务文本关键词 |
| `target_title` | ✅ | 文件名/outline 标题 |
| `source_pattern` / `target_pattern` | ✅ | manifest 原始文件名 + 暂存名 fnmatch（原始业务文件名优先）|
| `product_domain` | ✅ | outline sheet 名/文件名关键词 |
| `sheet_marker` | ✅ (outline 已喂时) | outline sheet 名含业务标记 (如报价汇总的 `三三三`/`333` 铜管基准表) |
| `dimension_set` | ✅ (premod_evidence 已喂时, 按表头角色事实核对; 未喂 → pending) | premod_evidence 表头角色 (如 `product_sku` → Z码/SKU/型号/货号) |
| `measure_set` | ⏳ 待L2复验 | flatten 列画像 |
| `formula_chain` | ⏳ 待L2复验 | 公式文本抽样 (L2) |
| `block_layout` | premod_evidence 块位置/合并区 | flatten 块位置/合并区 |
| `unit_convention` / `time_granularity` | ⏳ 待L2复验 | flatten 列画像 |

> 说明 (2026-08-27 走查修复): `source_pattern` / `target_pattern` 中文模式
> (如 `source_pattern::毛利表*`) 现在可命中 prepare_manifest.json 里的原始
> 业务文件名（`files[].source` basename），不再因 stage_files.py 强制的
> ASCII 暂存名而 miss。

## Registered MODs

| MOD Name | Aliases | Scope Signals (+) | Exclusion Signals (-) | Path | Revision | Visibility |
|---|---|---|---|---|---|---|
| _(no MOD registered — 暂无注册 MOD: 私有客户 MOD 不随发布推送, 见「Adding a MOD」捕获流程)_ | | | | | | |



## 提名流程 (L0.5 — 展平前)

1. 收集证据: 任务文本 + 暂存文件名 + `officecli view outline` 输出 + 各 sheet 的
   `*_premod_evidence.md` (flatten 机械产出; outline 本就需要的
   前置步骤, 提名零额外成本)。**禁止任何单元格级读取** (读表头/抽公式) —— 结构类信号
   只消费 flatten 机械产出的 premod_evidence: dimension_set 按 premod_evidence
   表头角色核对 (可验证 → 真 hit/miss), 其余结构信号标记"待L2复验"。
2. 运行 `python scripts/mod_nominate.py --task "<任务文本>" --files <文件名,...> --outline <outline.txt> --digest <…_premod_evidence.md,…> --out 展平元数据输出/mod_nomination.md`
3. 呈现提名卡 (候选名 + 命中/待复验信号 + 业务逻辑摘要 + 裁决选项;
   ambiguous 时附各候选的**规则证据摘要** id+description, 足够裁决判断)。
   **不含完整规则集** — 适用性由用户裁决: 选择 `/mod <NAME>` 或 `/mod NONE`。
   agent 不做排除判定, 不猜业务对象身份。
4. **裁决落盘**: 用户裁决后必须带 `--mod <NAME|NONE>` 重跑 `mod_nominate.py`
   把选择写入 `mod_resolution.json`（NAME 须属本轮候选集，越界 fail-closed；冲突
   覆盖记 `overridden_exclusions`；`--mod NONE` 写 `resolved` + `selected: NONE`）—
   `mod_resolution.json` 从此是**最终裁决记录**（Barrier 解锁的字面文件检查 +
   编译器 C2/C3/C4 依据）。
5. **规则裁决后加载 (两段加载)**: 用户选定后, 才从**选中** MOD 文件全文
   (或 `mod_resolution.json` 的 `rules` 字段) 加载完整规则, 注入 FillSpec
   撰写上下文 — 映射/公式链/路由/继承/校验规则进入 spec 撰写上下文, 不再
   猜测映射关系。选中 → `selected_mod` 写入 fill_spec.yaml (无状态机,
   无 gate_confirmed); NONE → 与现行无 MOD 流程一致。**硬性要求不变**:
   候选规则进入 spec 撰写上下文前必须已加载 — 改变的是加载时机与粒度,
   不是是否加载。**加载顺序挂在 Barrier 解锁程序上**: `resolved + 某 MOD` →
   先 `load_rules_for_selected_mod()` 加载全文规则, 再生成全量 digest
   (`structure_digest.py`, 目标 sheet 加 `--target`); `NONE`/`status=none` →
   直接生成 digest (见 SKILL.md §1.4/§2)。
   > **执行 vs 治理文档边界**: 执行任务只消费 `mod_resolution.json` 的
   > `summary`/信号 + **选中 MOD 文件全文** (两段加载第二段: 完整规则从
   > MOD 文件全文解析, 由 `mod_nominate.load_rules_for_selected_mod()` 提供)。
   > **不读 `MOD_TEMPLATE.md`** — 那是"新建/修改 MOD 文件"的治理规范, 与执行无关
   > (2026-08-12 实测: 误读全文浪费数分钟)。
6. **L2 复验**: 结构类信号在 premod_evidence 上给出真 hit/miss 或 pending（见步骤 1）;
   裁决落盘 → 规则加载 → 全量 digest 生成后, 按需要复核被选 MOD 声明的结构与
   digest 事实。吻合 → 继续; 冲突 → 呈现冲突点, 用户重新裁决 (保留规则/降级
   NONE/换 MOD) → 更新裁决 (`--mod` 重跑)。

## Adding a MOD

After a verified table-fill run (all four layers passed), the user may capture the
business logic as a private MOD through a single-confirmation post-delivery lifecycle:

1. **One user confirmation** — after verified non-trivial delivery, the agent asks
   whether to capture the run as a MOD. A verified `MOD=NONE` run is eligible because
   capture consumes the prepared MOD Markdown file.
2. **Prepare the MOD Markdown file** — the agent drafts the file following
   `references/MOD_TEMPLATE.md`: `Applicability` 业务逻辑指纹头 + `业务逻辑摘要` 段 +
   六列规则表 (Rule ID, Group, Gate, Description, Applies to, Notes). The draft is
   presented to the user for review.
3. **One private capture command** — after confirmation, invoke:
   `python scripts/mod_capture.py --source <MOD.md> --mod-name <NAME> --action create`
   (or `--action update` for an existing MOD). The command writes
   `references/MOD_<name>.md` and appends one row to the Registered MODs table above.
   Visibility defaults to `private`; `--visibility public` is explicit and
   requires the decontamination check to pass (see MOD_TEMPLATE 「捕获去污染原则」).

**State boundaries:**
- `mod_capture.py` never writes `mod_state.yaml` (已废弃)。
- On update, `MOD_<name>.md.bak` and `MOD_INDEX.md.bak` are created before mutation,
  the live revision is incremented by one, and temp-file replacement is used.
- Concurrent capture is not supported; revision is a best-effort single-user counter.
- `mod_capture.py` defaults to `visibility=private`; `--visibility public` is
  mechanically gated by the decontamination check, but capture alone does not
  constitute external review. A governed transition to `public` for
  de-identified MODs stays outside the Phase 3.5 capture lifecycle and
  requires external review before any manual catalog edit.
