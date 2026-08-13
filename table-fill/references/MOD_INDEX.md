# MOD Index — Nomination Catalog

<!--
  L0.5 提名: mod_nominate.py 评估可在展平前验证的表事实信号 (任务文本/文件名/outline),
  输出提名卡, 由用户裁决 (选择或 NONE)。结构类信号 (block_layout/formula_chain/
  dimension_set) 标记"待L2复验"。L2 用全量事实复验, 冲突则重新裁决。
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
| **Scope Signals (+)** | yes | 表事实信号 (提名阶段可验证的: semantic_type/target_title/source_pattern/target_pattern/product_domain; 待L2复验的: dimension_set/measure_set/formula_chain/block_layout/unit_convention/time_granularity). 格式 `signal_kind::value`. |
| **Exclusion Signals (-)** | no | **表事实**排除信号 (agent 可判定的可观察事实)。非表事实项 (客户上下文/用户意图) 不得列入, 由用户在 Gate 裁决。 |
| **MOD File Path** | yes | Relative from `table-fill/references/`. File `MOD_<name>.md`. |
| **Revision** | yes | Monotonic integer. Incremented on every confirmed MOD update. |
| **Visibility** | yes | `public` (de-identified, ships with office-skills) or `private` (customer-owned). |

## Signal kinds

| Kind | 提名阶段可验证? | 证据来源 |
|------|----------------|---------|
| `semantic_type` | ✅ | 任务文本关键词 |
| `target_title` | ✅ | 文件名/outline 标题 |
| `source_pattern` / `target_pattern` | ✅ | 文件名 fnmatch |
| `product_domain` | ✅ | outline sheet 名/文件名关键词 |
| `sheet_marker` | ✅ (outline 已喂时) | outline sheet 名含业务标记 (如报价汇总的 `三三三`/`333` 铜管基准表) |
| `dimension_set` / `measure_set` | ⏳ 待L2复验 | flatten 表头/列画像 |
| `formula_chain` | ⏳ 待L2复验 | 公式文本抽样 (L2) |
| `block_layout` | digest 块候选/合并区 | flatten 块候选/合并区 |
| `unit_convention` / `time_granularity` | ⏳ 待L2复验 | flatten 列画像 |

## Registered MODs

| MOD Name | Aliases | Scope Signals (+) | Exclusion Signals (-) | Path | Revision | Visibility |
| tcl_quotation_summary_migration | tcl-quote-migration | semantic_type::quotation,target_title::报价汇总,source_pattern::毛利表*,target_pattern::报价汇总*,sheet_marker::三三三\|333,dimension_set::product_sku,formula_chain::net_price_to_total_margin,block_layout::repeated_24_role_history_blocks | 目标缺少24角色表头指纹(数量,报价,净价,原型机成本,结算价,净收入,毛利,单机型损益率,系列盈亏,总盈亏) | MOD_tcl_quotation_summary_migration.md | 5 | private |
| tcl_cost_reply_to_quotation_summary_block | tcl-email-quote-block | semantic_type::cost_reply_to_quotation_summary_block,source_pattern::*核价邮件*,target_pattern::*报价*,sheet_marker::三三三\|333,dimension_set::product_sku,measure_set::prototype_cost,formula_chain::net_price_to_total_margin,block_layout::repeated_quotation_history_blocks | 目标缺少客户Sheet重复批次块或Z码和原型机成本角色 | MOD_tcl_cost_reply_to_quotation_summary_block.md | 2 | private |

## 提名流程 (L0.5 — 展平前)

1. 收集证据: 任务文本 + 暂存文件名 + `officecli view outline` 输出 (flatten 本就需要的
   前置步骤, 提名零额外成本)。**禁止任何单元格级读取** (读表头/抽公式) —— 结构类信号
   一律标记"待L2复验"。
2. 运行 `python scripts/mod_nominate.py --task "<任务文本>" --files <文件名,...> --outline <outline.txt> --out 展平元数据输出/mod_nomination.md`
3. 呈现提名卡 (命中信号/待复验信号/业务逻辑摘要 + 完整规则表)。**适用性由用户裁决**:
   选择 `/mod <NAME>` 或 `/mod NONE`。agent 不做排除判定, 不猜业务对象身份。
4. **规则随提名输出**: `mod_resolution.json` 候选携带完整规则 (`rules` 字段,
   Rule ID/Group/Gate/Description/Applies to/Notes)。**候选出现即加载**
   (resolved/ambiguous/conflict 一律加载, 多候选加载全部) — 映射/公式链/
   路由/继承/校验规则进入 FillSpec 撰写上下文, 不再猜测映射关系。选中 →
   `selected_mod` 写入 fill_spec.yaml (无状态机, 无 gate_confirmed);
   NONE → 与现行无 MOD 流程一致。
   > **执行 vs 治理文档边界**: 执行任务只消费 `mod_resolution.json` 的
   > `rules`/`summary` + MOD 文件 (MOD_INDEX 指向的文件)。**不读
   > `MOD_TEMPLATE.md`** — 那是"新建/修改 MOD 文件"的治理规范, 与执行无关
   > (2026-08-12 实测: 误读全文浪费数分钟)。
5. **L2 复验**: flatten 完成后用全量事实核对被选 MOD 的结构类信号。吻合 → 继续;
   冲突 → 呈现冲突点, 用户重新裁决 (保留规则/降级 NONE/换 MOD)。

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
   Visibility is always `private`.

**State boundaries:**
- `mod_capture.py` never writes `mod_state.yaml` (已废弃)。
- On update, `MOD_<name>.md.bak` and `MOD_INDEX.md.bak` are created before mutation,
  the live revision is incremented by one, and temp-file replacement is used.
- Concurrent capture is not supported; revision is a best-effort single-user counter.
- `mod_capture.py` always writes `visibility=private`. A governed transition
  to `public` for de-identified MODs is outside the Phase 3.5 capture lifecycle
  and requires external review before any manual catalog edit.
