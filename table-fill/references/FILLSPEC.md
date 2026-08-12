# FILLSPEC.md — fill_spec.yaml Schema (v2.5 Canonical)

`fill_spec.yaml` is the ONLY business-semantics source in the v2.5 workflow.
It stores **rules**, not data: the Compiler (`compile_fill.py`) materializes
row values from the flattened CSVs produced by `prepare_run.py`. Every other
artifact (execution_plan.json, mapping.md, readback, receipts) is derived
from this file — if two files disagree, the spec wins.

**禁止**: 逐行复制源数据到 spec; 手写 batch JSON; 手写 checks; 在 mapping.md
中维护第二份事实。

## 完整示例 (xlsx → xlsx 迁移)

```yaml
task:
  intent: 迁移 FRESH 订家用机型毛利到报价汇总 11_FRESH本土，追加新历史块
  selected_mod: NONE              # 或 MOD 名 (来自 mod_resolution.json)
  selected_mod_revision: null

inputs:
  sources: [source_maoli.xlsx]    # staged 文件名 (prepare_manifest.json)
  target: target_baojia.xlsx
  source_sheets:                  # 哪些 sheet 被展平 (prepare 阶段决定)
    - source: source_maoli.xlsx
      sheets: [FRESH订家用机型毛利情况]
  target_sheet: 11_FRESH本土      # pptx 用 slide[N]/table[@id=M]
  # platform: xlsx               # 默认按 target 扩展名推断

fingerprints:                     # 必须 == prepare_manifest.json.fingerprints
  source_structure: fe7d...
  target_structure: a7e6...

mapping:
  targets:
    - sheet: 11_FRESH本土
      base_last_row: 21           # 目标 digest 的最后数据行 (必须 ≤ digest rows)
      clone_roles:                # 有序: 决定目标行布局
        - role: spacer            # 空行
        - role: title             # 标题行克隆
          template_row: 7
          value: "FRESH 报价 2026.07 铜价105000"   # 可选: 覆盖克隆标题文本
        - role: header            # 表头行克隆
          template_row: 8
        - role: data              # 数据行克隆源 (格式源, 必须非合并锚点!)
          template_row: 10
      rows:
        source: source_maoli_FRESH    # 展平条目 name (manifest.flattened[].name)
        selectors:                    # 哪些源行成为目标行 (AND 语义)
          - column: G
            pattern: "Z*"             # glob 匹配 (fnmatch)
          - column: A
            not_pattern: "拖多*"
      columns:                        # 源列 → 目标列
        - source: A
          target: A                   # 直接拷贝
        - source: G
          target: C
        - target: D
          lookup: {name: sku_fields, field: factory_model, missing: empty}
        - source: [P, Q]
          target: N                   # 多列求和 (缺失输入按 0)
        - target: J
          value: "0"                  # 常量
      lookups:
        - name: sku_fields
          from: inheritance.json      # build_inheritance_index.py 输出
          key_column: G
          fields: [factory_model, compressor, copper_spec]
          missing: empty              # error|empty
      formulas:                       # {r}=数据行号, {r1}:{r2}=聚合范围, {n}=数据行数
        per_row:
          O: "IFERROR(ROUND(J{r}-K{r}-L{r}-M{r}-N{r},2),0)"
          U: "IFERROR(IF(S{r}=0,0,T{r}/S{r}),0)"
        aggregates:
          - col: V
            rows: "1:{n}"
            formula: "IFERROR(SUM(T{r1}:T{r2})/SUM(S{r1}:S{r2}),0)"
            style: anchor
      merges:
        - col: A
          rows: "1:{n}"
          style: label
      nulls:                          # 克隆残留置空 (克隆行携带的未填值必须显式清空)
        - col: L
          rows: all
        - col: M
          rows: [1, 3]                # 或 "2:4" 相对行范围
      remove_rows: []                 # 绝对行号, 自底向上 (源行数 < 模板行数时)
      # styles:                       # 可选覆盖锚点/标签样式
      #   anchor: {font.size: 11}

decisions:
  - 只迁移分体单冷/欧洲冷暖/32K 机型到 11_FRESH本土
  - 其他费用 = 源其它费用 + 源运费
gaps:
  - 源目标价(J) 为空的机型: 客户目标价留空 (0-口径)
lineage:
  - source: source_maoli_FRESH_flat.csv
    role: primary
    note: 每个匹配行恰好写入一个目标数据行
  - source: inheritance.json
    role: lookup

validation:
  required_coverage:                 # 这些源行必须被消费, 否则编译失败
    - source: source_maoli_FRESH_flat.csv
      rows: [7, 14]
  required_empty: []                 # 额外断言 EMPTY 的目标格 (裸坐标或 /Sheet/A1)
  key_outputs: ["A25", "J25", "V25"] # readback 采样格 — 必须是被本 plan 写入的格
```

## 语义要点

### clone_roles 与目标布局

`base_last_row` 之后按 clone_roles 顺序分配行号: spacer 占 1 行, title/header
各占 1 行, data 占全部匹配行。克隆语义: `add --from /Sheet/row[N]` 复制
template_row 的**格式 + 值** — 因此:

- **data 的 template_row 必须是合并区外的非锚点数据行** — 锚点行携带锚点公式
  (如 `SUM(T19:T21)`), 克隆到非锚点格即为静默公式残留 (defect:
  CLONE_SOURCE_IS_ANCHOR)。
- **克隆携带的值** 凡未被 columns/formulas/merges 覆盖的列, 必须进 `nulls`
  (defect: CLONE_RESIDUE_UNHANDLED — Compiler 自动对照 template_row 的展平值检查)。

### selectors

- 无 selectors = 全部源行。多个 selector AND 组合。
- `pattern` / `not_pattern` 用 fnmatch (支持 `Z*`、`拖多*`); `not_value` 精确排除。
- 匹配 0 行 → 编译失败 (NO_MATCHED_ROWS); required_coverage 行未消费 → 编译失败。

### columns

### blocks: 多数据块（一次运行多个独立块）

```yaml
mapping:
  targets:
    - sheet: 11_FRESH本土
      base_last_row: 21
      columns: [...]            # 公共配置放 target 级 (可选)
      formulas: [...]
      merges: [...]
      nulls: [...]
      blocks:                   # 缺省时 = 单个隐式块 (旧行为, 向后兼容)
        - clone_roles: [spacer, title←17, header←18, data←10]
          rows: {source: <家用展平名>, selectors: [...]}
          # columns/formulas/merges/nulls 缺省继承 target 级, 只写差异
          formulas: {aggregates: [...]}   # 块内聚合 rows "1:{n}", {n}=块内行数
          merges: [...]
        - clone_roles: [spacer, title←17, header←18, data←10]
          rows: {source: <商用展平名>, selectors: [...]}
          formulas: {aggregates: [...]}
```

- 每块独立: clone_roles 布局、rows 匹配、聚合/合并/置空、标题值; 块间行号由
  Compiler 从 base_last_row 顺序推进 (块1 数据行 → 块2 spacer/title/header/data)。
- **公共配置继承**: 块的 columns/formulas/merges/nulls/remove_rows 缺省继承
  target 级同名配置 — 两块共享的列映射/公式链只写一次, 块内只写差异。
- {n} 在块内 = 该块数据行数; 聚合/合并范围不得越过块边界。
- PPTX 目标仅支持单块。

### rows: 单源与多源合并

```yaml
rows:
  source: source_maoli_FRESH          # 单源 (常用)
  selectors: [...]                    # 或:
# rows:
#   sources:                          # 多源: 按列表顺序合并进同一数据块
#     - source: source_maoli_FRESH
#       selectors: [...]
#     - source: source_maoli_COMMERCIAL
#       selectors: [...]
```

多源时每个源独立做 selector 匹配, 按列表顺序拼接为目标数据行;
required_coverage 按源分别声明 (source 填展平 name 或 csv 名)。

### columns

| 形态 | 含义 |
|---|---|
| `{source: B, target: A}` | 直接拷贝 |
| `{source: B, target: A, transform: strip_sku}` | 应用命名 transform |
| `{target: D, lookup: {name: t, field: f, missing: empty}}` | 纯查表 |
| `{source: G, target: C, lookup: {...}}` | 先取值再查表 (key_column 在 lookup 定义) |
| `{source: [P, Q], target: N}` | 多列求和, 缺失/`-`/空按 0 |
| `{target: J, value: "0"}` | 常量 (0-口径等) |

**已映射列无需再进 `nulls`**: 列映射逐行写值 (含空源值写空串), 已覆盖残留;
`nulls` 只用于**没有列映射但克隆携带旧值**的列 (如连接管 "/" 占位)。

transforms 支持 `regex_replace` (pattern/replacement) 与 `strip`; 另有内置数值舍入 `round2`/`round4` (消除 15 位成本值的执行期溢出)。列映射可设 `precision: keep` 显式接受长精度值。

### lookups

`from` 路径相对于 workdir。`build_inheritance_index.py` 的输出
(`{"index": {sku: {"field_consensus": {field: {status, value}}}}}`) 会被
Compiler 自动归一化; 非 unique 共识按缺失处理。`missing: error` → 编译失败
并列出缺失 key; `missing: empty` → 留空。

### formulas / merges / nulls

- 模板键: `{r}` (数据行), `{r1}`/`{r2}` (聚合起止), `{n}` (数据行数)。缺键报错。
- aggregate 的 `rows` 通常 `1:{n}` (完整覆盖); 显式范围不得越过数据块。
- merge 与 aggregate 落在同一列时, 两者都作用于数据块首行锚点格 —
  若该列同时有 per_row 公式 → DUPLICATE_TARGET_WRITE 编译失败, 换独立列。
- `nulls` rows: `all` | 相对行号列表 | `"a:b"` 范围。
- **公式约定**: 派生数值公式默认 `ROUND(...,2)` (浮点残值 → text overflow);
  **ROUND 精准原则**: 只加在减法/乘法/除法/SUM 聚合 (O/S/T 用 ROUND(...,2),
  比率 U/V/W 用 ROUND(...,4)); 纯加法 (R=P+Q) 不加 — 加法加 ROUND 会截断
  结算价精度, 放大毛利 (实测 168.7151→168.72, 毛利漂移 18.37)。

### validation 三件套

- `required_coverage`: 关键源行必须被消费 (编译失败而非警告)。
  `rows` 引用**展平 CSV 的原始行号** (CSV 每行最后一列携带的源表行号),
  不是目标 sheet 行号。
- `key_outputs`: Gate/readback 的采样格。必须是 plan 实际写入的格
  (值/公式/空皆可) — 指向未写入的格 → 编译失败 (KEY_OUTPUT_UNWRITTEN)。
- `required_empty`: 额外 EMPTY 断言 (rarely needed — nulls 已覆盖大多数)。

## 组合行为契约 (问题组织式)

> 按 Agent 提问方式组织: 一个问题一小节 + 权威答案。**本章节每条声明都有编译
> 用例背书** (`tests/test_optimization.py`: FillSpecContractTests) — 文档声称
> "能编译"必须能编译, 声称"按错误码拒绝"必须按错误码拒绝。FILLSPEC 按特性组织,
> 本章节按问题组织: 找不到答案时先查这里, **不要读 compile_fill.py 源码**。
>
> 运行时直接问编译器: `compile_fill.py --capabilities` 输出与本章节同源的契约
> 矩阵 (同一探针集驱动文档、测试与运行时报告, 三者不会漂移); 对某个具体写法
> 不确定 → `compile_fill.py --probe` (与完整编译同管线, 零副作用)。

### Q1: group_merges 列能与公式/聚合共存吗？

| 组合 | 行为 | 依据 |
|---|---|---|
| group_merges 列 A + **聚合在独立列** G | ✅ 编译通过 — 锚点聚合公式写在块首行 | aggregates 是块级能力, 与组物化互不干扰 |
| group_merges 列 A + **聚合同列** A | ❌ `DUPLICATE_TARGET_WRITE` — 组锚点写与聚合锚点写都落在块首行 | 换独立列 |
| group_merges 列 A + **per_row 公式同列** A | ❌ `DUPLICATE_TARGET_WRITE` — group lowering 拥有该列每一行 (锚点写/非锚点清空) | 公式换独立列 |
| **aggregates × per_row 公式同列** | ❌ `DUPLICATE_TARGET_WRITE` — 首行锚点格双写 | 公式换独立列 |
| **merges (1:{n}) × per_row 公式同列** (列无映射) | ✅ 编译通过 — merges 只写 merge 属性, 不写值 | 与 aggregate 不同, 不注册值写入 |
| **nulls × aggregates 同列** | ❌ `DUPLICATE_TARGET_WRITE` — nulls 逐行清空 (含锚点格), 聚合再写锚点公式, 锚点双写 (特征: "first as empty") | 聚合列不要进 nulls |

规则: **同列只能有一个"值所有者"** (mapping / per_row formula / aggregate /
nulls / group lowering 五选一); merge 属性与值写入不冲突。

> 注: 早期撰写期文档假设"merges × per_row 同列 → DUPLICATE_TARGET_WRITE";
> 实测 merges 只写 merge 属性、不注册值写入, 同列共存**编译通过**
> (`test_merges_and_per_row_formula_same_column_ok` 背书) — 按"文档向行为
> 收敛"原则以实测为准。若未来要求拒绝, 需在 Compiler 侧新增检查 (另开 ticket)。

### Q2: 算术派生列 (FLD-006 减法) 的标准模式?

减法/乘法/除法派生列用 `per_row` 公式, 写在**独立的未映射列**:

```yaml
formulas:
  per_row:
    O: "IFERROR(ROUND(J{r}-K{r}-L{r}-M{r}-N{r},2),0)"   # FLD-006: 原型机成本 = 面价 − 铜管成本
    U: "IFERROR(IF(S{r}=0,0,T{r}/S{r}),0)"               # 比率
```

- 模板键 `{r}` 按数据行展开 (G 列 → G7/G8/G9...), 编译器逐行写入公式。
- **派生列必须独立**: 同列再有 columns 映射 → `DUPLICATE_TARGET_WRITE`。
- ROUND 精准原则: 减法/乘法/除法/SUM 聚合用 `ROUND(...,2)`, 比率用
  `ROUND(...,4)`; 纯加法 (R=P+Q) **不加** ROUND — 加 ROUND 截断结算价精度,
  放大毛利 (实测漂移 18.37)。

### Q3: 映射列 × 合并列 (group_merges 同列) 落值语义?

- 同一列可以有**列映射 + group_merges** (一等支持): 列映射保持值所有者,
  group_merges 只改物化策略。
- **锚点** (组内首行) 写物化值; **非锚点抑制并显式清空** (EMPTY readback) —
  "skip" 永远不等于 "留原样", 保证占位残留不变量闭合。
- 无映射列 → `label` 必填 ("" = 清空锚点), 否则 `GROUP_MERGE_ANCHOR_UNCOVERED`。

### Q4: 锚点 / label 机制?

- 锚点 = 组内首行 (consecutive equal-value runs 的第一行)。
- 映射列: 锚点写物化值 (含 numberformat props); 无映射列: 锚点写 `label`
  ("" = 清空锚点)。
- **singleton 组永不合并** (长度 1 不建 merge), 但锚点仍照常写值/清空。

### Q4b: aggregates 算残留覆盖吗?

**不算。** 残留双基线的覆盖来源只有: 逐行 fills (列映射) / nulls / per_row
公式 / merges 列 / group 锚点与 label。aggregates 只写块首行锚点格, 不是逐行
覆盖 — 依赖聚合"顺带覆盖"残留会以 `PLACEHOLDER_RESIDUE_UNHANDLED` /
`CLONE_RESIDUE_UNHANDLED` 拒绝。残留列要么逐行覆盖, 要么 `nulls: rows: all`
显式清空, 聚合列则必须**独立于 nulls** (见 Q1)。

### Q5: 空值 / 0-口径?

| 情形 | 行为 |
|---|---|
| 空源值 | 缺失格留空 — 空串写入, readback 期待空串 (kind=value, expect="") — **不是** EMPTY 清空 |
| 数值 0 | 不是缺失 — 原样写入 "0" |
| 显式清空 | `value: null` (sets) / `nulls` → EMPTY readback |
| 多列求和 | 缺失 / 空 / "-" 输入按 0 计入 |

### Q6: lookup missing 语义?

- `missing: error` → 编译失败 `LOOKUP_KEY_MISSING` (列出缺失 key)。
- `missing: empty` → 缺失 key 的格留空 (空串 readback); 命中行正常取值。
- 非 unique 共识按缺失处理; 字段不在索引 schema → `LOOKUP_FIELD_MISSING`。

### Q7: precision: keep vs round4 推荐次序?

1. **首选 `transform: round4`** (或 round2) — 编译期消除 15 位成本值的
   text overflow; 漏写时编译器自动 round4 并给 AUTO_ROUND4 警告。
2. **`precision: keep` 只在列宽确有余量时用** — 它只豁免编译期检查, 不解决
   执行期 text overflow。
3. 反例教训 (2026-08-12): Agent 读了 `apply_precision_policy` 源码后自选
   `precision: keep` 绕过文档推荐的 round4 → 第一轮 text_overflow 失败。
   **源码阅读给出错误安全感 — 按文档推荐序写, 用编译验证。**

### Q8: CLONE_SOURCE_IS_ANCHOR 检查作用域?

- **只检查 data role 的 template_row** — 锚点行携带锚点公式 (如
  `SUM(T19:T21)`), 克隆到数据行即静默公式残留, 编译拒绝。
- **title/header 的 template_row 选锚点行无编译检查** (实测: 锚点行作
  title/header 克隆源编译通过) — 锚点公式会被克隆进单行标题, 若未给
  `value` 覆盖则残留。**任何克隆源的 template_row 都避免选锚点行**;
  title/header 给 `value` 可覆盖文本, 但公式残留不在此机制覆盖内。
- 混合 inplace 的 overflow 克隆 (template_row) 同样适用 data 检查。

### Q9: title/header 的 value 何时写入?

- **延迟到 adds 之后、fills 阶段写入** (deferred_values) — 所有 add/remove
  先完成, 值写入不与行结构操作穿插。这是 `duplicate_row` 坑的防御排序:
  add 之间穿插 cell 写入会破坏 officecli 行簿记。
- 推论: plan 的 op 顺序恒为 clear → add → remove → merge → fill; 标题值
  属于 fill 阶段, 不会出现在 add 序列中。

### Q10: readback 断言种类 (register 语义)?

| 写入来源 | readback kind | 断言 |
|---|---|---|
| 列映射 / sets 值 / 标题 value | `value` | 数字归一化比较 (`$138.00` vs `138`) |
| nulls / required_empty / 清空 | `empty` | 断言 EMPTY |
| per_row 公式 / aggregates / 合并锚点 | `nonempty` | 断言非空 (公式结果编译期不可确定) |

- 每格恰好一种 kind (一格一 owner); 同一格被两种写入来源命中 →
  `DUPLICATE_TARGET_WRITE`。

### Q11: 克隆会携带 template_row 的合并区吗?

- **会** — `add --from` 复制 template_row 的格式 + 值 + mergeCell
  (2026-08-12 spike 实测: 克隆合并标题行 A1:F1 → 克隆行自动带 A41:F41 合并)。
- 推论: **标题行/表头行克隆源选合并行, 无需额外合并 op** (合并随克隆携带);
  其携带的旧文本由 title/header 的 `value` 覆盖, 否则残留。
- 反向推论: data 行克隆源若为合并区行, 克隆行携带的旧合并正是
  group_merges 重建时 unmerge 的对象 (含单格残留 A19:A19 的源头)。

### Q12: merges × aggregates 共存与多组聚合写法?

| 组合 | 结果 |
|---|---|
| `merges 1:{n}` + `aggregates 1:{n}` 同列 | 编译通过 — 聚合锚点 = 块首行 = 合并锚点 (既有一行声明) |
| 同列多条 `aggregates` + 显式范围 (如 `2:2`、`3:3`) | 编译通过 — 每条聚合落在各自的显式行 (实测: 数据行 8/9 落点正确), 用于**块内多组小计行** |
| `merges 1:{n}` + 多条显式范围聚合同列 | 编译通过, 但聚合锚点会落在合并区**非锚点格** — 执行期行为未验证, **不建议** (整块合并与分组小计语义互斥, 用上一行写法) |

- 与它们冲突的只有 per_row 公式 (同列 → DUPLICATE_TARGET_WRITE, Q1)。

## 能力映射表: MOD 规则类型 → FillSpec 表达模式

> 新增 MOD 规则入库时对照此表: 判断"这条业务规则能否表达、用什么模式表达"。
> 支持状态以 compile_fill.py 实际行为为准, 每条表达模式都有编译用例背书
> (`tests/test_optimization.py`: CapabilityMappingContractTests)。

| MOD 规则类型 | 标准表达模式 | 支持状态 |
|---|---|---|
| 算术派生 (减法/乘法/除法/比率) | `formulas.per_row` + ROUND 精准 (如 `IFERROR(ROUND(X{r}-Y{r},2),0)`) | 一等 |
| 每组合计 (系列盈亏按产品组合计) | 块级 `aggregates rows: 1:{n}` 只做整块聚合; 组合边界由数据决定, spec 无法表达动态组内范围 | **暂无一等**; 变通: `blocks[]` 每组合一块 + 各自块级 aggregates; 强行声明越块组内范围 → `AGG_RANGE_INVALID` |
| 每组合计 — 负面表达 (勿用) | 块内硬编码多个显式范围聚合 (如 `1:4`, `5:7`...) — 组边界由数据决定, 硬编码必然漂移; 且与 nulls 同列时锚点双写 | ❌ `DUPLICATE_TARGET_WRITE` (特征 "first as empty") — 正确路径只有拆块 |
| 字段继承 | `columns.lookup` + `mapping.lookups` (`missing: empty`) | 一等 |
| 路由 (条件取列) | selectors 行过滤 + `fallback` 列回退 | 一等 |
| 0-口径 | 常量 `value: "0"` / 空源留空 / 多列求和缺失按 0 | 一等 |
| 常量 | `columns.value` | 一等 |
| 查表 | `mapping.lookups` + `columns.lookup` | 一等 |
| pptx 分组合并 / 占位区 | — | **暂无**: `PPTX_CAPABILITY_NOT_ROLLED_OUT` (spike 夹具验证后 rollout) |

## 多目标与 PPTX

- **每次运行恰好一个目标**: `mapping.targets` 超过 1 个 → 编译拒绝
  (SPEC_TARGETS_TOO_MANY)。多 sheet 交付请拆成多次运行, 或把多个 sheet 合并进
  一个目标条目。
- **PPTX 目标**: `platform: pptx` + `target_sheet: slide[N]/table[@id=M]`;
  columns target 用列字母, Compiler 自动映射 `tc[索引]`; 单元格属性为 `text`;
  `first_data_row` 声明首个数据 tr; 无克隆/合并/公式; key_outputs /
  required_empty 用完整 tr/tc 路径。
- **PPTX 能力边界 (v2.5)**: `sets` 支持 (完整 DOM 路径
  `/slide[N]/table[@id=M]/tr[X]/tc[Y]`, 值/清空均可); `group_merges` 的
  pptx lowering (vMerge/rowspan) 按 spike 夹具验证后再 rollout — 当前声明 →
  编译拒绝 (PPTX_CAPABILITY_NOT_ROLLED_OUT); `mode: inplace` 对 pptx 无意义
  (表行本就预建) → 同样拒绝。

## v2.5: Row Layout Mode — inplace 占位区

预设格式的占位行 (无公式、客户模板场景, 如报价单 18 行产品占位) 用 `mode: inplace`
直接消费, 不再克隆追加:

```yaml
mapping:
  targets:
    - sheet: ATLAS Quotation
      base_last_row: 40          # 目标 digest 最后一行 (必须 ≥ 占位区末端)
      clone_roles:
        - role: data
          mode: inplace          # 缺省 = append (向后兼容)
          start_row: 7           # 模板坐标 (占位区首行)
          capacity: 18           # 占位区行数 (显式声明, 编译期校验)
          template_row: 8        # overflow 克隆的格式源 (必须非合并锚点)
      group_merges:              # 数据驱动分组合并 (append/inplace 通用)
        - col: A
          group_by: A            # 目标列逻辑物化值, 连续同值段为一组
          style: label           # 默认 label
        - col: F
          group_by: A
          label: ""              # 无映射列的锚点文本 ("" = 清空锚点)
      sets:                      # 目标级绝对写 (模板坐标)
        - path: A4
          value: "To Messrs: MXP"
        - path: F13
          value: null            # 显式清空 (EMPTY readback)
        - path: A36              # 模板坐标; trim 后 Excel 自然移位
          value: "* ship to Algeria"
          props: {numberformat: ...}   # 可选白名单属性
      columns:
        - source: L
          target: E
          props: {numberformat: "$#,##0.00"}   # 应用到该列每个物化格
```

语义要点 (锁定语义, Compiler 强制执行):

- **坐标约定**: `start_row` / `sets.path` 一律是**模板坐标**, spec 永远不计算
  移位后的行号。坐标稳定由两条结构性约束联合保证: append 块的行的结构操作
  只能在 append 区 (base_last_row 以下) 合法; 任何前置操作不得触碰占位区。
- **N > capacity → hybrid overflow**: 占位区填满, 从 `template_row` 克隆
  N−capacity 行接在占位区之后 (Total 行自然下移)。
- **N < capacity → Trim**: 恒为**尾部**裁剪 (编译器推导, 不写 `remove_rows`)。
- **执行阶段不变量**: append 块全部操作 → sets → 终末 inplace 块的结构操作
  (overflow 克隆 add → trim remove) → inplace 值操作。sets 的 readback 路径由
  Compiler 翻译为最终坐标 (如 A36 → 最终 A31)。
- **group_merges 重建**: ①物化行值 ②按 group_by 连续同值段算组 ③unmerge
  列上占位区内的全部既有合并 (含单格残留如 A19:A19) ④非锚点显式清空 +
  锚点写值/label ⑤长度 > 1 才建合并 (singleton 永不合并)。映射列 → 锚点写
  物化值、非锚点清空; 无映射列 → `label` 必填 ("" = 清空锚点)。
- **残留双基线**: inplace 块保留行按**每行自身原值**查
  PLACEHOLDER_RESIDUE_UNHANDLED / _PARTIAL_NULLS; overflow 克隆行沿用
  template_row 基线 (CLONE_RESIDUE_*)。覆盖来源: 逐行 fills / nulls /
  逐行公式 / group 锚点与 label — **不含 sets** (sets 禁止进入占位区)。
- **props 白名单**: V1 仅 `numberformat`; 值语义与展示语义正交
  (`value: null` + `props.numberformat` 合法)。白名单外 → PROPS_WHITELIST_VIOLATION。

### 映射增强 (MXP 场景需求)

- **`fallback`**: `{source: D, target: B, fallback: B}` — 主源为空时取回退列
  值 (Model 优先工厂型号、空则产品描述)。
- **`transforms` 列表**: `transforms: [rename_drive5, add_voltage]` — 按序链式
  应用; 单值 `transform` 仍是单变换简写。正则替换的 replacement 含反斜杠转义
  (如 `'\1\n(220-240V,1N,50Hz)'`) 时 YAML 用**单引号**包裹。

## 常见编译错误速查

| 错误码 | 含义 | 修复 |
|---|---|---|
| FILLSPEC_FINGERPRINT_MISMATCH | 结构变了, spec 过期 | 重跑 prepare_run, 读新 digest, 更新 spec |
| CLONE_SOURCE_IS_ANCHOR | 数据克隆源是合并锚点 | 换非锚点数据行 |
| CLONE_RESIDUE_UNHANDLED | template_row 携带某列值但未覆盖 | 加 columns mapping 或 nulls |
| DUPLICATE_TARGET_WRITE | 同一格被写两次 | 检查 columns/nulls/formulas/group/sets 重叠 |
| MERGE_RANGE_INVALID / AGG_RANGE_INVALID | 范围越过数据块 | 用 `1:{n}` |
| KEY_OUTPUT_UNWRITTEN | key_outputs 指向未写格 | 换成被写的格 |
| NUMERIC_OVERFLOW_RISK | 直接值 >4 位小数 / >12 位有效数字 (成本 15 位精度) | 加 `transform: round4` 或 `precision: keep` |
| LOOKUP_KEY_MISSING / FIELD_MISSING | 查表失败 | missing: empty 或修 key/索引 |
| REQUIRED_COVERAGE_UNMATCHED | 必需源行未消费 | 修 selectors 或记入 gaps |
| SPEC_TARGETS_TOO_MANY | 声明了多个目标 | 每次运行只编译一个目标 |
| CLONE_RESIDUE_PARTIAL_NULLS | nulls 只覆盖部分行, 其余行残留克隆值 | 用 rows: all 或加列映射 |
| PPTX_TARGET_OUT_OF_BOUNDS | 列字母超出表宽 | 检查表列数 |
| INPLACE_MULTIPLE_BLOCKS | 一个目标声明了多个 inplace 块 | 每目标至多一个 inplace 块 |
| INPLACE_NOT_LAST_BLOCK | inplace 块不是末块 / 块内 inplace role 不是最后 | 移到 blocks[] 末尾 / clone_roles 末尾 |
| INPLACE_REGION_OUT_OF_BOUNDS | start_row+capacity−1 超出 digest 行数 (模型事实矛盾) | 重读 digest 修正声明 |
| INPLACE_NO_CLONE_SOURCE | inplace data role 缺 template_row | 声明非锚点占位行 |
| INPLACE_REGION_OVERLAP | 前置 add/remove 或 sets 触碰占位区 | base_last_row ≥ 占位区末端; 移除/写入移到区外 |
| STRUCTURAL_OP_OUT_OF_ZONE | 非终末 inplace 块的结构行操作 ≤ base_last_row | remove_rows 只声明在 append 区 |
| PLACEHOLDER_RESIDUE_UNHANDLED | 保留占位行携带未覆盖值 | 加 columns/null/group label |
| PLACEHOLDER_RESIDUE_PARTIAL_NULLS | nulls 只覆盖部分保留行 | rows: all 或列映射 |
| GROUP_MERGE_ANCHOR_UNCOVERED | 无映射 group 列缺 label | 声明 label ("" = 清空) |
| GROUP_BY_COLUMN_UNMAPPED | group_by 列无列映射 | 加 columns 映射 |
| MERGE_MODE_CONFLICT | 同列混用 merges + group_merges | 每列只用一种合并模式 |
| SET_OUT_OF_BOUNDS | sets.path 超出 digest 维度 / 格式非法 | 用模板坐标裸格或完整 DOM 路径 |
| PROPS_WHITELIST_VIOLATION | props 超出 {numberformat} | 只用白名单键 |
| PPTX_CAPABILITY_NOT_ROLLED_OUT | pptx 声明 inplace/group_merges | 用 xlsx, 或等 pptx lowering 验证后 rollout |
