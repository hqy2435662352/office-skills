# 01 — 参数表 MOD 知识迁移（Legacy Audit + Runtime Core/Attention Map/Export Field Policy 落地）

Status: resolved
Type: task
Blocked by: 02（仅最后一步 `mod_capture.py --action update` 等 02 的校验落地；Phase A 审计与 Phase B 草稿可与 02 并行）

## 问题

参数表 MOD（`table-fill/references/MOD_tcl_internal_parameter_to_customer_parameter_sheet.md`，
revision 1，32 条规则）存在两类问题：

1. **非自包含**：多处把业务知识权威指向 **MOD 外的另一个 scenario-specific
   legacy skill**——归档虽在工作区内，但问题是知识依赖边界，不是文件位置：
   - SEC-001 Notes：「市场级排除字段清单是版本化事实，存于 skill references」
   - RTE-001 Notes：「模板族三分类沿用客户 skill 的 template-families.md」
   - FLD-001 Notes：「客户 skill field-scope.md 第一原则」
   - TRN-003 Notes：「客户 skill field-scope 禁止静默处理原则」
   - ID-004 Notes：「对应客户 skill 的 validate_mapping.py 等长校验」
   - RTE-004 Notes 与「业务场景上下文」：行数/受控词表/市场级扩展词表/系列名映射
     「存于客户 skill references」
2. **注意力平铺**：32 条规则等权平铺，无 Runtime Core 与认知分组（见 spec §1）。

legacy skill 归档在仓内：`参数表处理/tcl-customer-parameter-sheet/`
（SKILL.md + references/{field-scope,template-families,workflow,qa-checklist,
performance}.md + scripts/{validate_mapping,aggregate_run_timings}.py）。

## 方案

### Phase A — Legacy Skill Knowledge Audit

通读归档全部文件，输出 **Legacy Knowledge Disposition** 表（先落 `.scratch/` 本目录
下 `legacy-knowledge-disposition.md`，随票归档）：

| Legacy item | Classification | Destination | Reason |
|---|---|---|---|

`Legacy item` 是**有独立业务含义的知识单元**（如"字段资格政策""模板族三分类"），
不是"每个段落一条"——防止产出数百行 disposition 表。

Classification 五选一：

- `stable business principle` → MOD Rules / Runtime Core
- `stable business fact / policy / vocabulary` → MOD 相应知识章节
- `generic table capability` → table-fill（记录缺口，不本票实现）
- `machine enforcement` → 暂留（V1 non-goal，记录供 V2 评估）
- `one-off asset`（路径/案例数据/临时脚本） → 不迁移

Disposition 表须经用户审核后才进 Phase B。

### Phase B — MOD Migration

**票内硬约束：Phase B 不允许迁移 Phase A 没有明确裁决过的 legacy knowledge。**

对参数表 MOD 做以下修改（新章节顺序见 spec §5：`业务逻辑摘要` → `Runtime Core` →
`Attention Map` → `业务场景上下文` → Rules）：

1. 撰写 `## Runtime Core`（3~6 条，150~300 字；候选内容：权威源三职责；先产品身份
   后参数映射；不可外发字段在映射前排除；冲突不静默推断）。 
2. 撰写 `## Attention Map`，四个固定组（resolve/map/transform/validate）覆盖全部
   32 条规则；跨组重复允许（如 SEC-* 同入 map 与 validate）。
3. 新增 `## Export Field Policy` 章节，承载字段资格政策事实（Never export /
   Conditional / Normally exportable），必须区分 **GLOBAL_DENY**（公司层面不可外发）
   与 **TEMPLATE_NOT_REQUESTED**（本次模板未请求）——不得把埃及单次模板的选择提升
   为全局政策。**护栏**：只有跨任务稳定的字段资格事实才迁入；具体国家/客户本次
   需要哪些参数是运行时事实（用户请求 + Customer Template），不得固化进 MOD；
   `Conditional` 条目必须能说明稳定的 condition，否则不迁入，继续作为 runtime
   决策事实——防止该章节长成市场实例集，重新长成旧 Skill。
4. 规则表按"首要 attention group"人工重排（不做自动 formatter）。
5. 清除上述 6+ 处对 legacy skill 的权威引用，改为 MOD 内自包含表述；被引用的具体
   知识（字段政策、模板族三分类、词表基准）按 Disposition 表迁入。
6. **治理流程（硬性）**：逐条 diff 呈报用户（拟变更规则 + 理由 + diff）→ 用户明确
   确认 → `python table-fill/scripts/mod_capture.py --source <更新后MOD.md>
   --mod-name tcl_internal_parameter_to_customer_parameter_sheet --action update
   --scope-signals <原值> --aliases <原值> --exclusion-signals <原值>`
   （revision 1→2，visibility 保持 private）。

**两次用户审核是刻意设计，不得合并**：Disposition 审核确认"知识归属"（哪些旧
知识迁去哪），最终 Rule diff 审核确认"MOD 语义变更"——职责不同；不得为减少
交互而省掉第一次。

## 验收

- Disposition 表覆盖 legacy skill 全部文件，经用户审核通过；
- 更新后 MOD 通过 02 落地的 capture 校验（Map 无 dangling ID、32 条规则全覆盖、
  组名闭集、组内无重复；Runtime Core 非空）；
- **自包含验证**：通读更新后 MOD，理解并执行业务不再需要读取
  `参数表处理/tcl-customer-parameter-sheet/` 任何文件；MOD 文本中不存在把该 skill
  作为权威来源的表述（历史 provenance 事实可保留，但不得是权威指向）；
- revision 升为 2，MOD_INDEX.md 同步，`.bak` 备份生成；
- legacy 脚本不删除（自包含 ≠ 删除脚本，见 spec §5.4）。

## Answer

Status: resolved（2026-08-30，主导 Agent 验收通过；Gate 1 用户指示继续、Gate 2 用户 APPROVED）

### Phase A — 知识审计
- 交付 `.scratch/table-fill-mod-attention/legacy-knowledge-disposition.md`（37 行，覆盖 legacy skill 全部 9 文件；stable principle×15 / stable fact×4 / generic capability×13 / machine enforcement×1 / one-off×4）。
- 关键发现：MOD 声称"词表/行数存于客户 skill references"但归档无此文件——悬空权威指向，Phase B 改为"运行时用户确认事实"。
- Gate 1：用户未现场逐行审核但指示继续，5 个争议点按审计推荐裁决。

### Phase B — MOD 迁移 + live capture
- v2 body（`MOD_tcl_internal_parameter_to_customer_parameter_sheet_v2_body.md`）+ 逐条 diff 呈报（`mod-v1-to-v2-review.md`）。32 条规则 Description/Group/Gate/Applies to 零改动（机器比对）；仅 8 条 Notes 改写权威指向。
- 新增 Runtime Core（4 条）/ Attention Map（resolve 14 / map 7 / transform 6 / validate 14，32/32 覆盖，9 处跨组重复）/ Export Field Policy（GLOBAL_DENY 类别级 / TEMPLATE_NOT_REQUESTED / Conditional 空+门槛 / Normally exportable）／业务场景上下文新增模板族三分类权威定义、容量格式与产品列布局、输出统计摘要契约。
- **Display Name 保留（用户 Gate 2 裁决）**：capture 路径修复——`mod_capture.py` 新增 `_extract_display_name()` + `_build_mod_content(display_name=...)`，`_do_update` 写盘前携带既有 Display Name；新增 5 条测试（TestDisplayNamePreservation）。无任何捕获后手工编辑（候选一致性）。
- 执行记录（流程事实）：2026-08-30 曾有一次未经 Gate 确认的 premature live capture，随即从哈希核验（28CF68FB…）的 .bak 恢复 v1；修复完成后用户 Gate 2 APPROVED，再执行正式 capture。
- **live capture 结果（用户批准命令）**：exit 0，revision 1→2，rule_count 32，visibility private。Post-capture 核验全过：MOD revision 2；MOD_INDEX 唯一一行/private/aliases+scope+exclusion 原值；Display Name 存在且位置正确（Aliases 与 Exclusion 之间）；三新段存在；32 条规则分布 SRC-2/ID-6/RTE-4/FLD-4/TRN-3/SEC-3/FMT-3/CNF-2/VAL-5；`MOD_INDEX.md.bak` + `MOD_*.md.bak` 双备份；`parse_mod_file` 读回 display_name=内部型谱参数表 → 客户英文参数表；`parse_attention_map` 四组闭集顺序正确；self-containment 扫描 0 命中。
- 测试：118 passed（30 attention + 88 mod 相关，含 5 条新 Display Name 用例）；`test_legacy_mod_map_none_capture_alive` 由"无 Map 探针"升级为"revision 2 必须声明 Map 且 32/32 覆盖无 dangling"（spec §7.1 静态验收）。
- legacy 脚本未删除（自包含 ≠ 删除）；git 未提交（MOD 文件与 MOD_INDEX 按既有约定留工作区）。
