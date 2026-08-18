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
      remove_rows: []                 # 绝对模板坐标, 自底向上; 只允许 ≤ base_last_row (add 区之外)
                                      # 越界 (> base_last_row) → REMOVE_TARGETS_APPEND_ZONE
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

### YAML 纪律 (decisions / gaps)

Q: `decisions`/`gaps` 条目含 `: ` 怎么写? A: **整行双引号包裹** —
`- "追加新历史块: 源文件 11_FRESH本土 的毛利数据"` (含冒号的裸标量整行加
双引号, 冒号也在引号内)。漏写 → 裸标量被 YAML 解析成 mapping →
SPEC_NON_STRING_ITEM (exit 3); corrective_action 直接给正确写法
(`用双引号包裹整行: - "..."`), 照抄即可 — 不要试图把 dict 包进字符串。

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
- **表头行守卫 (issue 02, Case 08 U1)**: **展平 CSV 首行（表头）是候选数据行** —
  源表首行的表头文本行 (类别/型号/…) 也是被匹配的源行。**源列含表头文本行时,
  无 selector 会把表头映射进数据区**, 必须在 selectors 用 pattern/not_pattern 排除
  (如 `{column: A, pattern: "<业务类别>*"}` 或 `{column: A, not_value: "类别"}`)。
  Compiler 在「rows 无 selector 或 selector 未排除首行、且首行是表头文本行」时给出
  编译警告 `HEADER_ROW_CONSIDERED_DATA` (不改失败语义, 记 warnings) — 机械事实:
  表头文本行 = >= 2 个非空 cell 且全部非数值的连续文本标签行。

### columns

### blocks: 多数据块（一次运行多个独立块）

```yaml
mapping:
  targets:
    - sheet: 11_FRESH本土
      base_last_row: 21
      columns: [...]            # 公共配置放 target 级 (可选)
      formulas:
        per_row: {...}          # 公共 per_row 公式链 (target 级, 可选)
      merges: [...]
      nulls: [...]
      blocks:                   # 缺省时 = 单个隐式块 (旧行为, 向后兼容)
        - clone_roles: [spacer, title←17, header←18, data←10]
          rows: {source: <家用展平名>, selectors: [...]}
          # columns/merges/group_merges/nulls 缺省继承 target 级, 只写差异
          # ⚠ formulas 例外: 块级一旦声明 formulas, 其 per_row 不继承 target 级
          #   (整体取代), 必须整段携带共享 per_row (见下方继承契约 + 反例警示)
          formulas: {aggregates: [...]}   # 块内聚合 rows "1:{n}", {n}=块内行数
          merges: [...]
        - clone_roles: [spacer, title←17, header←18, data←10]
          rows: {source: <商用展平名>, selectors: [...]}
          formulas: {aggregates: [...]}
```

- 每块独立: clone_roles 布局、rows 匹配、聚合/合并/置空、标题值; 块间行号由
  Compiler 从 base_last_row 顺序推进 (块1 数据行 → 块2 spacer/title/header/data)。
- **公共配置继承**: 块的 columns/merges/group_merges/nulls/remove_rows 缺省继承
  target 级同名配置 — 两块共享的列映射/合并/置空只写一次, 块内只写差异。
- **块级 `formulas` 是例外 (取代而非继承)**: 块级一旦声明 `formulas`，其
  `per_row` **不继承** target 级、必须整段携带；只有块级**不声明** `formulas`
  才缺省继承 target 级 formulas (含 per_row)。这正是「只写差异」措辞的误导点 —
  它只对 columns/merges/nulls/group_merges 成立, 对 `formulas` 不成立。
- {n} 在块内 = 该块数据行数; 聚合/合并范围不得越过块边界。
- PPTX 目标仅支持单块。

#### 正反对照: 块级 `formulas` 取代 vs 继承

| 形态 | 代码 | 块内结果 |
|---|---|---|
| ✅ 正确 — 块级要加自己的聚合、又要共享 target 级 per_row | 块级 `formulas:` 同时含 `per_row` (整段携带) + `aggregates` | 两块共享 per_row + 各自的 aggregates 都生效 |
| ✅ 正确 — 块级完全不用公式 | 块级**不写** `formulas` | 缺省继承 target 级 per_row/aggregates |
| ❌ 错误 — 只写差异 (只写 aggregates) | 块级 `formulas: {aggregates: [...]}` 不含 per_row | **target 级 per_row 被整体取代**、块内消失 — 共享公式链漏写, 编译能通过但缺公式 |

```yaml
# ✅ 正例: 块级要加聚合、又要共享 target 级 per_row → 整段携带 per_row
blocks:
  - rows: {source: <家用>}
    formulas:
      per_row: {O: "IFERROR(ROUND(J{r}-K{r}-L{r}-M{r}-N{r},2),0)", U: "IFERROR(IF(S{r}=0,0,T{r}/S{r}),0)"}
      aggregates: [{col: V, rows: "1:{n}", formula: "SUM(T{r1}:T{r2})/SUM(S{r1}:S{r2})", style: anchor}]

# ✅ 正例: 块级不用公式 → 不写 formulas, 自动继承 target 级 per_row
blocks:
  - rows: {source: <家用>}          # 无 formulas → 继承 target 级 per_row

# ❌ 反例: 块级只写聚合、漏写共享 per_row → target 级 per_row 被取代、块内消失
blocks:
  - rows: {source: <家用>}
    formulas:
      aggregates: [{col: V, rows: "1:{n}", formula: "SUM(T{r1}:T{r2})/SUM(S{r1}:S{r2})", style: anchor}]
      # 没有 per_row → 块的每行只有聚合, 没有 O/U 公式 (不被"缺省继承"补回)
```

#### 反例警示 (blocks 段)

1. **block 顶层 `aggregates:` / `merges:` / `per_row:` / `group_aggregates:` →
   `BLOCK_KEY_STRUCTURE_INVALID` (exit 3, 不再静默)** — 聚合/公式类声明必须写
   在 `formulas:` 之下 (`formulas.aggregates` / `formulas.per_row` /
   `formulas.group_aggregates`)。旧行为把这些错位键**静默丢弃** (编译通过、plan
   无对应操作 — 只有读 mapping.md 才发现), 现编译期拒绝并点名正确嵌套。
2. **块级 `formulas` 整体取代, 不是合并** — 见上方取代契约: 块级声明 formulas
   即整段使用, target 级 per_row 不再继承 ("只写差异" 对 formulas 不成立)。
3. **`group_by` 对稀疏源列不建组** — 连续同值段才成组; 若目标列某组**仅组首行
   有值** (源列稀疏: 首行写值、其余行空), 物化的值序列是 `[V, "", "", ...]`,
   空不连续 → 该组**不建立**连续同值段、不建合并/不落组聚合。需让映射列每行
   物化出连续同值 (先填充组值或用列映射逐行物化) 才可分组。

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

transforms 说明: **内建数值变换仅 `round2` / `round4`** (消除 15 位成本值的
执行期溢出) — 直接写 `transform: round2` / `transform: round4` 即可用 (也支持
链式 `transforms: [round4, ...]` 组合)。`strip` / `regex_replace` **不是内建**,
必须先定义在 `mapping.transforms` 才可在列映射里按名引用; 直接写
`transform: strip` 属命名态、未定义 → `TRANSFORM_UNKNOWN` (corrective_action:
Define it in mapping.transforms)。最小定义示例:

```yaml
mapping:
  transforms:                      # 先定义命名 transform, 列映射才可按名引用
    - name: strip_sku
      function: strip
    - name: norm_model
      function: regex_replace
      pattern: "[-_ ]+"
      replacement: ""
  targets:
    - sheet: 11_FRESH本土
      base_last_row: 21
      columns:
        - source: B
          target: A
          transform: strip_sku        # 引用 mapping.transforms 里定义的 strip
        - source: F
          target: C
          transforms: [norm_model, round4]   # 链式: 先 regex 再内置 round4
```

列映射可设 `precision: keep` 显式接受长精度值 (需列宽实测背书, 见 Q7)。

### lookups

`from` 路径相对于 workdir。`build_inheritance_index.py` 的输出
(`{"index": {sku: {"field_consensus": {field: {status, value}}}}}`) 会被
Compiler 自动归一化; 非 unique 共识按缺失处理。`missing: error` → 编译失败
并列出缺失 key; `missing: empty` → 留空。

**索引输入 sheet 排除目标 sheet**: 构建 inheritance 索引时**禁止把本次填充的
目标 sheet 作为输入** — 目标 sheet 的既有数据是历史输出, 不是字段权威源;
把它喂进索引会产生自引用: 同 SKU 在不同 sheet 的多值 → 共识 conflict → 按
缺失处理 (静默留空, 常见排查线索见 KNOWN_TRAPS)。索引输入只用独立数据 sheet
(如埃及 FRESH 的 01_埃及机型、09_Fresh拖多)。

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
- **ROUND 优先序 (比 officecli-xlsx 复刻视角优先)**: 新增数据块 (含块级
  aggregates / per_row) 的**派生数值公式**一律按 table-fill ROUND 精准原则写
  (减法/乘法/除法 ROUND2、比率 ROUND4、纯加法不加), **即使模板既有公式没有
  ROUND、即使 officecli-xlsx「preserve existing templates」建议精确复刻**
  — table-fill 的 ROUND 精准原则优先 (办公套件复制旧公式不产生 ROUND, 但
  table-fill 的目标是防浮点残值 text overflow)。`text_overflow` 属 REPAIR
  预期路径 (FAILURE_CLASSES standard_fix 已覆盖): 命中时按 ROUND 精准原则
  修, 不是未知能力探测。

### validation 三件套

- `required_coverage`: 关键源行必须被消费 (编译失败而非警告)。
  `rows` 引用**展平 CSV 的原始行号** (CSV 每行最后一列携带的源表行号),
  不是目标 sheet 行号。
- **恰好一次不变量 (2026-08-17)**: 每条被消费的 `(源, 原始行)` 在整个 plan
  中**全局唯一** — 跨 block / 同一块内 `rows.sources` 多个条目的 selectors
  命中同一源行 → 编译拒绝 `SOURCE_ROW_CONSUMED_TWICE` (exit 3, 默认
  fail-closed, 无显式复用语法; 见 Q17)。
- `key_outputs`: Gate/readback 的采样格。必须是 plan 实际写入的格
  (值/公式/空皆可) — 指向未写入的格 → 编译失败 (KEY_OUTPUT_UNWRITTEN)。
  **行号不用手工重推**: 直接取上次 compile 产出的 `execution_plan.json`
  `blocks[].data_start` (每块数据首行) 及聚合/合并锚点格 (聚合元数据的
  `rows` 映射、group 锚点 = 各组首行、1:{n} 合并锚点 = 块首行) — 模板行号
  由 Compiler 顺序推进, 手工推导必然漂移 (Case 06 E4 的教训)。
- `required_empty`: 额外 EMPTY 断言 (rarely needed — nulls 已覆盖大多数)。

## 组合行为契约 (问题组织式)

> 按 Agent 提问方式组织: 一个问题一小节 + 权威答案。**本章节每条声明都有编译
> 用例背书** (`tests/test_optimization.py`: FillSpecContractTests) — 文档声称
> "能编译"必须能编译, 声称"按错误码拒绝"必须按错误码拒绝。FILLSPEC 按特性组织,
> 本章节按问题组织: 找不到答案时先查这里, **不要读 compile_fill.py 源码**。
>
> 运行时直接问编译器: `compile_fill.py --capabilities` 输出与本章节同源的契约
> 矩阵 (同一探针集驱动文档、测试与运行时报告, 三者不会漂移); 对某个具体写法
> 不确定 → 写正式 spec 走 formal compile (同管线, 编译即权威反馈)。机制能力
> 疑问 (三态 / Evidence Fit / Standard Evidence Paths / Probe / Bounded Rescue
> / ASK·STOP) 按 `references/CAPABILITY_EVIDENCE.md` 处理 — 按需加载, 正常
> Run 不预读; `--probe` 只用于该参考定义的架构分叉场景。

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

**判定标准（data-neutral，不加列名）**: 该目标列**是否被本块任一公式
（per_row / aggregates / group_aggregates 等）引用**。被引用 → 数值 0
（`value: "0"` 常量）；不被引用 → 才可留空。

| 情形 | 行为 |
|---|---|
| **入公式链的纯数据/非公式字段缺失**（被本块任一公式引用，如财务费用 / OA 信保 / 返点 / 其他费用） | 写出数值 0（列映射 `value: "0"` 常量，非空串）；**空串进入公式求值链会被某些引擎按非空文本判错误 → `#VALUE!` → IFERROR 兜底成 0**（Case 07 §8 教训）；readback 期待数值 0（`expect: "0"`） |
| **独立展示、不入任何公式链的字段缺失** | 才可留空 — 空串写入, readback 期待空串 (kind=value, expect="") — **不是** EMPTY 清空 |
| 数值 0 | 不是缺失 — 原样写入 "0" |
| 显式清空 | `value: null` (sets) / `nulls` → EMPTY readback |
| 多列求和 | 缺失 / 空 / "-" 输入按 0 计入 |

> 边界：`FRM-008` 报价缺失「缺失格留空」语义保留 — 报价列是独立展示（用户可见的
> 报价留空），不进入本规则；本规则只约束**被公式引用的费用等纯数据列**。

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
4. **`precision: keep` 何时被编译拒绝?** (2026-08-13 增强): prepare 已采集
   模板列宽 (`meta.column_width`, flatten 阶段从 worksheet XML `<cols>` 读取,
   不入结构指纹) 时, 编译器对 keep 列估算最宽渲染值 (数字位数 + numFmt
   格式符号, `estimate_rendered_width`) — 超出列宽 → 静态缺陷码
   `PRECISION_KEEP_NARROW_COLUMN` (exit 3, corrective_action: 改用
   `transform: round4`); 列宽未知 (旧 meta) → 保持豁免 + 
   `PRECISION_KEEP_WIDTH_UNVERIFIED` 警告。列宽实测背书是 keep 的机械
   前提 — 编译器不靠 Agent 猜"列宽够", 不再执行期才发现 text overflow。

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
| 列映射 / sets 值 / 标题 value | `value` | 数值归一化比较 — **仅限真数值形态** (`$138.00` vs `138` 容忍); 字母数字标识 (SKU/型号/Z 码) 按文本精确比较 |
| nulls / required_empty / 清空 | `empty` | 断言 EMPTY |
| per_row 公式 / aggregates / group_aggregates 组锚点 | `nonempty` | 断言非空 (公式结果编译期不可确定) |
| group 锚点 (映射值/label/清空) | `value` / `empty` | 按锚点写什么断言什么 |
| plain `merges` (1:{n}) | **无 readback** | merges 只写 merge 属性不写值, 不注册断言 |

- 数值归一化边界: 剥离 `,` / `%` / 货币符号 (`$`/`￥`/`€` 等) 与前导符号后,
  **整体必须是合法十进制数值字面量**才做归一化 (`138.00` vs `138`、
  `$1,234.5` vs `1234.5`、`12.5%` vs `12.5` 容忍); 含字母或其它字符的字符串
  (`Z001`、`SN-001`、`ABC123`) → 文本精确比较 — 字母数字标识写错必须被
  readback 拦截, 不得因归一化误判相等。

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

### Q13: 每组合计 (单块 + 显式范围聚合) 的接受边界?

单块内写多条显式范围聚合 = 每组合计行, **编译通过** (埃及案例最终方案
「单块 + 显式范围聚合」编译、执行、readback 627/627 全过; 同形脱敏模板见
`combination_patterns.yaml` → `per_group_total_explicit_ranges`, 改列名即可)。
接受边界 (违反任一 → 对应缺陷码):

| 边界 | 违反 → |
|---|---|
| **聚合列不进 `nulls`** | ❌ `DUPLICATE_TARGET_WRITE` (特征 "first as empty" — nulls 逐行清空先注册锚点格 empty, 聚合再写锚点公式注册 nonempty, 锚点双写) — 这是被拒 fixture 的真实触发因素 |
| **不与 group_merges 同列** | ❌ `DUPLICATE_TARGET_WRITE` (Q1 — 组锚点写与聚合锚点写都落块首行) |
| **范围不越块** (rows 相对块内数据行) | ❌ `AGG_RANGE_INVALID` |
| **不与 per_row 公式同列** | ❌ `DUPLICATE_TARGET_WRITE` (Q1 — 首行锚点格双写) |

- 显式范围 = 各组实际数据行范围 (如组1 `1:2`、组2 `3:5`), 范围随数据定;
  数据驱动且范围不可静态枚举时, 用 `group_aggregates` 一等能力 (Q14,
  组边界由 group_by 物化值决定) 或 `blocks[]` 每组合一块 + 块级 `1:{n}`
  (见能力映射表「每组合计 (动态边界)」行)。
- 历史纠偏 (2026-08-13): 曾误判「硬编码范围必然漂移 → 正确路径只有拆块」;
  实际拒绝 fixture 的触发因素是**聚合列进了 nulls**, 与"硬编码范围"无关 —
  最小变异实证 (被拒 fixture 只把聚合列移出 nulls 列、其余不动 → 编译通过,
  `tests/test_optimization.py` → `test_per_group_total_trigger_minimal_mutation`);
  capabilities 矩阵同时背书通过形态 (`per_group_total_explicit_ranges` = accept)。

### Q14: 每组合计 (group_aggregates) 怎么写？

**一等能力**: 组边界由 group_by 列的**物化值**决定 — 连续同值段为一组,
聚合公式自动落到各组锚点行 (组内首行), 范围 {r1}:{r2} 按组起止展开:

```yaml
formulas:
  group_aggregates:            # 列表: 每个 group_aggregate 一条
    - group_by: A              # 物化值分组列 (必须有列映射)
      col: V                   # 聚合落点列 (组锚点行)
      formula: "IFERROR(ROUND(SUM(T{r1}:T{r2})/SUM(S{r1}:S{r2}),4),0)"
      style: anchor
```

- **group_by 必须有列映射** (缺 group_by 或未映射 → `GROUP_BY_COLUMN_UNMAPPED`);
  模板键只有 `{r1}`/`{r2}`/`{n}` (未知键 → `FORMULA_TEMPLATE_INVALID`)。
- **组锚点格自动登记 nonempty readback** — 无需手写 checks (见 Q10)。
- **范围不越块 (静态守卫)**: 组范围由 group_by 物化值 (数据) 派生, 结构上恒
  在块内 — 越块 → `AGG_RANGE_INVALID` (编译器内部不变量守卫); 观测契约:
  公式展开范围恒在数据块内 (埃及等价用例逐公式断言)。
- **冲突语义 (一格一 owner, 同 Q1 规则)**: 与 group_merges 同列 / 与 per_row
  同列 → `DUPLICATE_TARGET_WRITE`; **组聚合列不进 nulls** — nulls 逐行清空
  含锚点格, 再写公式即锚点双写 (特征 "first as empty") → `DUPLICATE_TARGET_WRITE`。
- **声明形态校验**: `group_aggregates` 必须是条目列表 (或
  `{per_group: [...], whole_run: {...}}` dict); 条目非 mapping / per_group 非
  列表 → `GROUP_AGGREGATES_INVALID` (结构化拒绝, 不静默吞掉)。
- **whole_run (跨块总计)**: 落点语义 (末块尾部 vs 独立行) 待 spike 锁定 —
  **spike 前声明 → `CAPABILITY_NOT_ROLLED_OUT`** (结构化拒绝, 不询问用户)。
- pptx 的 group_aggregates lowering 未 rollout → `PPTX_CAPABILITY_NOT_ROLLED_OUT`。

### Q15: lookup 索引坏了 / 整列未命中怎么办?

| 情形 | 行为 |
|---|---|
| 索引文件归一化后为空 (0 entries — 清洗脚本重写 inheritance.json 丢了 `field_consensus`, 或文件本身为空) | ❌ 编译拒绝 `LOOKUP_TABLE_EMPTY` (exit 3) — **不再静默全空** (埃及 FRESH 坑 1: 曾全部静默留空, 计划照常产出, 唯一暴露点是 Agent 审 mapping.md 发现 Written values 全空) |
| 索引非空, 但某声明 lookup 列**所有行**都未命中 | ⚠️ 编译警告 `LOOKUP_COLUMN_ALL_MISSING` (不阻断, 记 warnings, Gate 呈现) — 拦截整列静默空, 同时允许合法缺失 (如某 SKU 确实不在索引里, 记 gaps) |

- 修复 `LOOKUP_TABLE_EMPTY`: 检查索引结构 (`field_consensus` 是否存在 / 是否被
  手工改写), 用 `build_inheritance_index.py` **重建索引** — 禁止手改 JSON。
- `LOOKUP_COLUMN_ALL_MISSING` 出现时判断: 真缺失 (keys 不在索引 → 记 gaps) 还是
  索引坏了 (结构问题 → 重建索引) — 重建前先检查**索引输入是否误含目标 sheet**
  (自引用: 目标 sheet 既有块与独立数据 sheet 同 SKU 多值 → 共识 conflict →
  按缺失处理, 表现与"整列未命中"相同, 见 KNOWN_TRAPS)。

### Q16: 模板行号空洞修复后指纹怎么办?

| 情形 | 行为 |
|---|---|
| 编译报 `TEMPLATE_ROW_GAP` (exit 3) — 行洞命中 add 锚点/克隆源 | 跑 `scripts/repair_row_gaps.py --workdir <dir>` — 物化缺失行元素, 并**自动重跑 flatten (仅目标 sheet) 同步 prepare_manifest.json 指纹** |
| 修复后要更新 spec 指纹 | **Agent 不再手工同步** — 脚本输出 JSON 带新 `fingerprints.target_structure`, 抄进 fill_spec.yaml, 或一步完成: `repair_row_gaps.py --workdir <dir> --patch-spec fill_spec.yaml` (外科手术式改写该键, 保留其余内容) |
| 修复后重编译 | `compile_fill.py --spec fill_spec.yaml --workdir <dir>` — 指纹匹配后正常出 plan |

- 流程保证: **行洞修复 = staged 文件修改 = 指纹必然变化** (机械事实); 指纹
  同步由 repair 脚本自动完成, 唯一手工动作 = 更新 spec 指纹 + 重编译。
- 注意: `repair_row_gaps.py` 修复后若仍报空洞 (复见), 先查 `officecli close`
  刷盘 (脚本已内置), 再查 staged 文件是否被后续 flatten 覆盖过。

### Q17: 多个块 (或多源条目) 的 selectors 选中同一源行怎么办?

| 情形 | 行为 |
|---|---|
| 两个 block 的 selectors 命中同一源行 (或同一块内 `rows.sources` 两个条目选中同一行) | ❌ `SOURCE_ROW_CONSUMED_TWICE` (exit 3) — 数据行/数量/金额/聚合翻倍但 coverage 仍显示通过, 编译器现做 `(source, original_row)` 全局唯一性检查并拒绝 |
| 各块 (各条目) 的 selectors 不相交 — 每个源行至多被一个块消费 | ✅ 编译通过 |

- 规则: **默认 fail-closed** — 每条被消费的 `(源, 原始行)` 全 plan 全局唯一,
  且目前**没有** `reuse`/`repeat`/`multi` 显式复用语法。selectors 重叠必须
  通过收窄 `pattern` / `not_pattern` / `not_value` 消除 (或把行拆到不同源)。
  若未来出现"同一源行要进入两个目标位置"的真实业务需求, 需先新增显式复用
  声明语法 (另开 ticket), 不允许静默重复消费。

### Q18: PPTX 目标能声明什么？不支持的声明会怎样？

> issue 06: pptx 未 rollout 声明曾**静默丢弃** (编译通过、plan 无对应操作)。
> 现 fail-closed — 编译期拒绝 + corrective_action, 不再静默。

| 声明 | 行为 |
|---|---|
| `columns` 列值填充 / DOM-path `sets` | ✅ 编译通过 — pptx 当前唯一支持能力 |
| `formulas.per_row` / `formulas.aggregates` / `formulas.group_aggregates` | ❌ `PPTX_CAPABILITY_NOT_ROLLED_OUT` — 公式格是 xlsx 能力 |
| `merges` / `group_merges` | ❌ `PPTX_CAPABILITY_NOT_ROLLED_OUT` — pptx 合并 lowering (vMerge/rowspan) 未 rollout |
| `nulls` | ❌ `PPTX_CAPABILITY_NOT_ROLLED_OUT` — pptx 无克隆, 无残留可清 |
| `remove_rows` | ❌ `PPTX_CAPABILITY_NOT_ROLLED_OUT` — pptx 无结构行操作 |
| `mode: inplace` | ❌ `PPTX_CAPABILITY_NOT_ROLLED_OUT` — 表行本就预建 |
| `columns[].props` (numberformat) | ❌ `PPTX_CAPABILITY_NOT_ROLLED_OUT` — pptx 文本格无数字格式 |
| `first_data_row + 匹配行数 − 1` > 表格实际行数 | ❌ `PPTX_TARGET_ROWS_OUT_OF_BOUNDS` — pptx 行不能克隆, 越界必须在编译期拒绝 |

- 修复路径: 值类需求预计算进 `columns`; 公式/合并类需求用 xlsx 平台表达;
  行数不足用 python-pptx **一次性**加行 (禁止在 officecli 操作后重新 import)。
- 背书: `tests/test_optimization.py` (CapabilityMappingContractTests) +
  探针矩阵 `pptx_*` 行 + 真实 PPTX E2E (`tests/test_pptx_e2e.py`, 缺模板/
  officecli 时显式 skip)。

### Q19: `aggregates` / `group_aggregates` 会自动创建合并区吗? 聚合列非锚点残留怎么覆盖?

**不自动创建合并区** (issue 03 固化, 组合空缺 U4)。`aggregates` 只把公式写到
锚点行 (显式范围的首行 / 组首行), `group_aggregates` 只把公式写到各组的组首行 —
两者都**不**为聚合列建合并区; 克隆 data 行携带到聚合列**非锚点格**的旧值/公式会
残留 (聚合只写锚点, 不逐行覆盖, 见 Q4b)。

- **非锚点残留必须显式 `merges` 覆盖**: 聚合列的同范围显式 `merges`
  (`{col: V, rows: "1:{n}", style: label}`) 把聚合锚点=合并锚点, 非锚点格并入
  合并区 → 残留被覆盖。聚合锚点恰好落在合并锚点格的 `merges 1:{n} + aggregates
  1:{n}` 同列组合是合法形态 (Q12), 本契约保证非锚点残留由此闭合。
- 聚合列**不进 `nulls`** (进了 → 锚点双写 DUPLICATE_TARGET_WRITE, "first as empty", Q1/Q13)。
- **每源分组一条 V 的显式范围 merges+aggregates + 总盈亏 W 一条 1:{n}** 的完整
  可复制骨架见 `combination_patterns.yaml` → `multiproduct_block_append` (家用/
  商用双数据块, 克隆残留 + 分组系列盈亏 + 块总盈亏 + 合并, key_outputs 取
  blocks[].data_start 与聚合/合并锚点格); **单块报价/核价块 (一个源一块, 无分组)
  用同一机制的 `single_quotation_block_append`** (净价公式链 + 总盈亏一条 1:{n}
  merges+aggregates + 0-口径费用列 + 克隆残留/外部引用 nulls, rows.selectors 必须
  排除表头行)。

## 执行顺序保证 (Execution Order Contract)

> 执行机制疑问 (add 之后 remove 的目标是谁? 执行器会不会重排/翻译?) 的权威
> 答案 — 答案在本契约, **不要读 compile_fill.py 源码**。每条声明有编译用例
> 背书 (`tests/test_optimization.py`: ExecutionOrderContractTests)。本契约同时
> 派生进每个 plan: execution_plan.json `mechanical_facts` + mapping.md「执行
> 机械事实」栏由编译器机械计算 (removes 与 add 区关系 / 锚点链依赖 / shift
> 结论), 非自由文本 — 读 plan 即读契约结论, 不用手工模拟。

### E1: op 全局顺序不变量?

- **append-only 形态** (无 inplace): 恒为 **clear → add → remove → merge → fill**
  — 值写入全部排在结构操作之后。
- **inplace 混合形态**: 全局序列 = **append 块全部操作 → sets → 终末 inplace
  块结构操作 (overflow 克隆 add → trim remove) → inplace 值操作** — 与「v2.5:
  Row Layout Mode」的执行阶段不变量同一事实。注意 **sets 是值写, 却先于
  inplace 结构操作执行** — 位置由 Excel 行移位搬移, spec 永远不算最终坐标。
- **两形态共同的不可破坏约束** (防 duplicate_row, KNOWN_TRAPS 2026-08-10
  实证): 值写入永不穿插 **append 区** add — append 块 adds 连续生成; 结构性
  op 恒先于其相位内的值写; 标题克隆文本属 fill 阶段 (deferred_values)。
- 约束: 任意两个 **append 区** add 之间零 cell 写入; append 块 remove 全部在
  其块 adds 之后; inplace trim removes 在 sets 之后、inplace 值写之前。

### E2: add 之后 remove 的目标是谁?

- `remove_rows` 是**模板坐标**, 不随 add 推移: append 块的 add 全部插在
  base_last_row 之下 (append 区), remove_rows 只能声明 ≤ base_last_row 的模板
  既有行 — REMOVE_TARGETS_APPEND_ZONE 静态检查保证两者无交集, remove 命中的
  永远不是刚插入的新行。
- 推论: remove op 的行号 == spec 声明的行号, 无需手工模拟行位移
  (埃及 11_FRESH本土 曾为此模拟 30+ 次, ~1/3 spec 时间)。

### E3: remove 为什么自底向上?

- 每个 append 块的 remove_rows 按行号**降序** (自底向上) 生成 op; 块间按块序。
- 理由: 删除行元素不重排其余行的 r 值 (留下行号空洞, 与模板 row-gap 同源),
  但若执行器按位置解析, 先删上行会让下行坐标失效 — 自底向上保证任何解析方式
  下其余 remove 目标保持有效。
- inplace Trim 同理由: 尾部裁剪 (编译器推导), 自底向上。

### E4: 坐标翻译边界?

- **ops 用模板坐标**: add/remove/set 的 path 全部是模板坐标 (spec 写什么就是
  什么), 执行期 Excel 行移位自然发生。
- **readback 用最终坐标**: inplace 场景 (trim/overflow) 下, readback /
  row_map / writes / group_boundaries / sets 记录全部翻译为最终坐标
  (Compiler 的 final_row / spec_final_row_of)。
- 无 inplace 区 → 无行移位 → readback 坐标 == 模板坐标。
- 结论: Agent 永远不用计算移位后的行号。

### E5: 输入哈希在哪些边界重算绑定?

> 审计 issue 03 (2026-08-13): Draft 执行与提升必须重新绑定 source/template
> 内容哈希, 杜绝 "哈希齐全但输入漂移" 的 Draft。绑定边界:
>
> | 边界 | 动作 |
> |---|---|
> | **compile** | `compile_fill.py` 重算 staged 输入 (sources + target) 的内容 sha256, 写入 `plan.input_hashes` (按 staged 文件名) — **不是** 抄 prepare_manifest.json 的 `files[].sha256` (那是 outline 期快照, repair_row_gaps 修改 staged 文件后**过期** — repair 只重算指纹, 不刷新 files[].sha256; 重编译即重绑定, repair 流程天然兼容) |
> | **execute** | `execute_batch.py` 在 `copy_template` **之前**重算 staged 输入哈希, 与 `plan.input_hashes` 比对; 漂移/缺失绑定 → `INPUT_HASH_DRIFT` / `INPUT_HASH_BINDING_MISSING` (exit 3, corrective_action: 恢复未漂移输入或重 prepare+重编译) — 绝不带着漂移输入开始填充 |
> | **receipt** | `draft_receipt.json` 的 `source_hashes` / `template_sha256` 是**执行时重算值** (不再无条件抄 manifest); 另记 `input_hash_check` = {bound, actual, drifted}, 一致/漂移可查 |
> | **promote** | `promote_output.py` 的 HASH_DRIFT 核对范围含 source/template: plan 绑定 / receipt / 当前 staged 文件三方一致, 任一漂移 → exit 3 (fail-closed: 缺绑定或缺 receipt 证据也拒绝) |

- staged 输入 = 本次填充的输入快照; compile 后手工改动 staged 文件 (非
  repair 流程) 会先被 execute 拒绝, 不会再出现"读旧 manifest 哈希"的盲区。
- `.gate3_confirmed` 绑定三元组结构不变 (spec/plan/draft) — 输入哈希由
  plan 背书 (plan 本身在门禁三元组内)。

## 能力映射表: MOD 规则类型 → FillSpec 表达模式

> 新增 MOD 规则入库时对照此表: 判断"这条业务规则能否表达、用什么模式表达"。
> 支持状态以 compile_fill.py 实际行为为准, 每条表达模式都有编译用例背书
> (`tests/test_optimization.py`: CapabilityMappingContractTests)。

| MOD 规则类型 | 标准表达模式 | 支持状态 |
|---|---|---|
| 算术派生 (减法/乘法/除法/比率) | `formulas.per_row` + ROUND 精准 (如 `IFERROR(ROUND(X{r}-Y{r},2),0)`) | 一等 |
| 每组合计 (系列盈亏按产品组合计, 静态范围) | 单块 + 多条显式范围聚合 (每组合计一行, 见 Q13); 聚合列不进 nulls | 一等 (复制即用: `combination_patterns.yaml` → `per_group_total_explicit_ranges`, 改列名即可) |
| 每组合计 (动态组边界, 范围不可静态枚举) | `formulas.group_aggregates` — group_by + col + formula, 组锚点行落公式, 组边界由数据决定 (见 Q14) | **一等** (组范围静态校验留块内; 聚合列独立于 nulls; 同列冲突 → `DUPLICATE_TARGET_WRITE`); 变通: `blocks[]` 每组合一块 + 各自块级 `aggregates 1:{n}` |
| 每组合计 — 负面表达 (勿用) | 聚合列进 `nulls` → 锚点格先被清空 (empty) 又被聚合写 (nonempty) | ❌ `DUPLICATE_TARGET_WRITE` (特征 "first as empty") — 聚合列不进 nulls; 正确路径: group_aggregates 一等能力 (Q14) 或单块显式范围 (Q13) |
| 跨块总计 (whole_run) | `formulas.group_aggregates.whole_run` — 落点语义 (末块尾部 vs 独立行) 待 spike 锁定 | **暂无**: `CAPABILITY_NOT_ROLLED_OUT` (spike 结论落地后解锁) |
| 字段继承 | `columns.lookup` + `mapping.lookups` (`missing: empty`) | 一等 |
| 路由 (条件取列) | selectors 行过滤 + `fallback` 列回退 | 一等 |
| 0-口径 | 常量 `value: "0"` / 空源留空 / 多列求和缺失按 0 | 一等 |
| 常量 | `columns.value` | 一等 |
| 查表 | `mapping.lookups` + `columns.lookup` | 一等 |
| pptx 分组合并 / 占位区 / 公式 / 合并 / 置空 / 删行 | — | **暂无**: `PPTX_CAPABILITY_NOT_ROLLED_OUT` (spike 夹具验证后 rollout; 曾静默丢弃, issue 06 起编译期拒绝); pptx 当前支持 = 列值填充 + DOM-path sets; 行越界 → `PPTX_TARGET_ROWS_OUT_OF_BOUNDS` |

## 多目标与 PPTX

- **每次运行恰好一个目标**: `mapping.targets` 超过 1 个 → 编译拒绝
  (SPEC_TARGETS_TOO_MANY)。多 sheet 交付请拆成多次运行, 或把多个 sheet 合并进
  一个目标条目。
- **PPTX 目标**: `platform: pptx` + `target_sheet: slide[N]/table[@id=M]`;
  columns target 用列字母, Compiler 自动映射 `tc[索引]`; 单元格属性为 `text`;
  `first_data_row` 声明首个数据 tr; 无克隆/合并/公式; key_outputs /
  required_empty 用完整 tr/tc 路径。
- **PPTX 能力边界 (v2.5, issue 06 fail-closed)**: PPTX 当前能力 = **列值填充**
  (`columns` → 每格 `text` 写入) + **DOM-path `sets`**
  (`/slide[N]/table[@id=M]/tr[X]/tc[Y]`, 值/清空均可)。其余声明一律编译期拒绝
  (`PPTX_CAPABILITY_NOT_ROLLED_OUT`, **不再静默丢弃**):
  `formulas.per_row` / `formulas.aggregates` / `formulas.group_aggregates`
  (公式格是 xlsx 能力)、`merges` / `group_merges` (pptx 合并 lowering
  vMerge/rowspan 未 rollout)、`nulls` (无克隆残留可清)、`remove_rows` (无结构
  行操作)、`mode: inplace` (表行本就预建)、`columns[].props` (pptx 文本格无
  数字格式)。
- **PPTX 行边界 (issue 06)**: `first_data_row + 匹配行数 − 1` 越过表格实际行数
  → 编译期拒绝 `PPTX_TARGET_ROWS_OUT_OF_BOUNDS` — pptx 行不能克隆, 越界必须
  在编译期暴露, 而非执行期才失败。

## v2.5: Row Layout Mode — inplace 占位区

预设格式的占位行 (无公式、客户模板场景, 如报价单 18 行产品占位) 用 `mode: inplace`
直接消费, 不再克隆追加。**完整可实例化骨架 (含分组合并 + 绝对写 + numberformat) 见
`combination_patterns.yaml` → `preformatted_quotation_inplace`** — 真实
Validated Draft E2E 同形、数据中立, 按 fragment 内参数替换表一次实例化即编译
(机械契约测试: `tests/test_optimization.py` → PreformattedQuotationPatternContractTests;
完整 Canonical Pattern 准入边界见 catalog 头部说明):

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
  移位后的行号。坐标稳定由两条结构性约束联合保证: append 块的 add 全部插在
  base_last_row 之下 (append 区); `remove_rows` 只能声明 ≤ base_last_row 的
  模板既有行 (add 区之外, 不被 add 推移) — 越界 (> base_last_row) 的 remove
  会被先行的 add 推移行号, 命中刚插入的新数据行 (自毁 plan), 编译器以
  REMOVE_TARGETS_APPEND_ZONE 拒绝; 任何前置操作不得触碰占位区。
- **N > capacity → hybrid overflow**: 占位区填满, 从 `template_row` 克隆
  N−capacity 行接在占位区之后 (Total 行自然下移)。
- **N < capacity → Trim**: 恒为**尾部**裁剪 (编译器推导, 不写 `remove_rows`)。
- **append-only 合法终态**: 源行数 < 模板行数时, 首选让占位行**自然下沉保留**
  (append-only), 不需要 remove_rows; inplace (占位区消费) 仅在占位行携带单元格
  样式时成立 — 裸行占位 inplace 填入即无边框块, 违反 VAL-007 格式沿用。
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

## 布局决策树 (以样式为第一判定条件)

> **布局决策必须以 digest 的样式粒度事实为第一判定条件** (`占位行样式: 带样式
> (样例: A23)` / `占位行样式: 裸行 (23-52)` + 各 clone_roles 克隆源行样式
> 结论 — prepare 阶段 B 自动检测), 而不是"占位块存在与否"。按占位块存在性
> 推荐 inplace 会把 Agent 引入错误路径 (埃及案例实证: 23-52 占位行是裸行,
> inplace 填入即无边框块, 违反 VAL-007 格式沿用)。三分支的违规形态由编译器
> 缺陷码裁决 — 决策树与编译裁决同源, 不许凭猜。

| 分支 | 判定条件 (样式优先) | 决策 | 违规形态 → 缺陷码 |
|---|---|---|---|
| ① 占位区消费 | digest 报**占位行带样式** | `mode: inplace` 成立 — start_row/capacity 消费占位区; N>capacity → overflow 克隆; N<capacity → 尾部 Trim (编译器推导, **不写 remove_rows**) | 区域声明矛盾 → INPLACE_REGION_OUT_OF_BOUNDS; 前置块结构行操作触碰占位区 → INPLACE_REGION_OVERLAP / STRUCTURAL_OP_OUT_OF_ZONE |
| ② 裸行占位 | digest 报**占位行裸行** | **clone-append** — 克隆携带格式 (满足 VAL-007 格式沿用), 占位行**自然下沉保留** (append-only 是合法终态, **不写 remove_rows**) | 误用 inplace → 无边框块 (VAL-007 违反, 编译不拦, 靠本决策树拦截); 误写越界 remove_rows → REMOVE_TARGETS_APPEND_ZONE |
| ③ 既有块收缩 | 模板既有块行数 > 源行数 (源行数 < 模板行数) | append + `remove_rows` (**≤ base_last_row**, 自底向上) — 经典收缩场景 | remove_rows > base_last_row → REMOVE_TARGETS_APPEND_ZONE (先行的 add 推移行号, remove 命中刚插入的新数据行 — 自毁 plan); 模板行号空洞 → TEMPLATE_ROW_GAP / digest 行洞行 (既有两层防护, 不新增机制) |

判定次序: **先看样式** (分支 ①/② 二选一), 再看行数差 (分支 ③ 只作用于
append 形态 — inplace 的收缩由尾部 Trim 消费, 不写 remove_rows); 样式事实读
digest, 不要 unzip sheet XML 考古。

## 常见编译错误速查

| 错误码 | 含义 | 修复 |
|---|---|---|
| FILLSPEC_FINGERPRINT_MISMATCH | 结构变了, spec 过期 | 重跑 prepare_run, 读新 digest, 更新 spec |
| INPUT_HASH_DRIFT | staged 输入 (source/template) 在 compile 后被修改/缺失 — execute 在 copy_template 前拒绝 (exit 3), 或 promote 的 HASH_DRIFT 三方核对拒绝 | 恢复未漂移的 staged 输入, 或重跑 prepare_run + compile_fill.py 重绑定 (见 E5) |
| INPUT_HASH_BINDING_MISSING | plan 无 input_hashes 绑定 (旧版 compile 产物), execute 无法核对输入 | 重跑 compile_fill.py 重绑定后重执行 (见 E5) |
| CLONE_SOURCE_IS_ANCHOR | 数据克隆源是合并锚点 | 换非锚点数据行 |
| CLONE_RESIDUE_UNHANDLED | template_row 携带某列值但未覆盖 | 加 columns mapping 或 nulls |
| DUPLICATE_TARGET_WRITE | 同一格被写两次 | 检查 columns/nulls/formulas/group/sets 重叠 |
| MERGE_RANGE_INVALID / AGG_RANGE_INVALID | 范围越过数据块 | 用 `1:{n}` (聚合); group_aggregates 的组范围由数据派生, 越块是编译器内部不变量守卫 (观测契约: 公式范围恒在块内, 埃及等价用例断言) |
| KEY_OUTPUT_UNWRITTEN | key_outputs 指向未写格 | 换成被写的格 — 行号直接取 last compile 的 `execution_plan.json` `blocks[].data_start` 及聚合/合并锚点格 (模板行号由 Compiler 推进, 不手工重推; 见 validation 三件套 / combination_patterns 骨架的 key_output 落点) |
| NUMERIC_OVERFLOW_RISK | 直接值 >4 位小数 / >12 位有效数字 (成本 15 位精度) | 加 `transform: round4` 或 `precision: keep` |
| PRECISION_KEEP_NARROW_COLUMN | `precision: keep` 列最宽渲染值超出 prepare 实测列宽 (meta.column_width) | 改用 `transform: round4` (或加宽列) — keep 需要列宽实测背书 |
| PRECISION_KEEP_WIDTH_UNVERIFIED | `precision: keep` 但 meta 无列宽 (旧 prepare 产物, 警告不阻断) | 重跑 prepare_run --flatten 采集列宽, 或改用 round4 |
| LOOKUP_KEY_MISSING / FIELD_MISSING | 查表失败 | missing: empty 或修 key/索引 |
| LOOKUP_TABLE_EMPTY | 索引归一化后为空 (field_consensus 丢失 / 文件被手工改写) | 检查索引结构, 用 build_inheritance_index.py 重建 — 禁止手改 JSON |
| LOOKUP_COLUMN_ALL_MISSING | 索引非空但声明 lookup 列全部未命中 (警告, 不阻断) | 判断真缺失 (记 gaps) 还是索引损坏 (重建); 重建前检查索引输入是否误含目标 sheet (自引用 → 共识 conflict → 缺失) |
| HEADER_ROW_CONSIDERED_DATA | 展平 CSV 首行（表头文本行）被当作候选数据行 — rows 无 selector（或 selector 未排除首行）会把表头映射进数据区 (警告, 不阻断; issue 02 / Case 08 U1) | 在 rows.selectors 加 pattern/not_pattern 排除表头行 (如 `column A pattern 业务类别*` 或 `column A not_value 类别`) |
| REQUIRED_COVERAGE_UNMATCHED | 必需源行未消费 | 修 selectors 或记入 gaps |
| SPEC_TARGETS_TOO_MANY | 声明了多个目标 | 每次运行只编译一个目标 |
| SPEC_NON_STRING_ITEM | `decisions`/`gaps` 条目被 YAML 解析成 mapping (裸标量含 `: `) | **整行双引号包裹** (含冒号): `- "追加新历史块: 源文件 ..."` — corrective_action 给正确写法, 照抄即可 |
| CLONE_RESIDUE_PARTIAL_NULLS | nulls 只覆盖部分行, 其余行残留克隆值 | 用 rows: all 或加列映射 |
| PPTX_TARGET_OUT_OF_BOUNDS | 列字母超出表宽 | 检查表列数 |
| INPLACE_MULTIPLE_BLOCKS | 一个目标声明了多个 inplace 块 | 每目标至多一个 inplace 块 |
| INPLACE_NOT_LAST_BLOCK | inplace 块不是末块 / 块内 inplace role 不是最后 | 移到 blocks[] 末尾 / clone_roles 末尾 |
| INPLACE_REGION_OUT_OF_BOUNDS | start_row+capacity−1 超出 digest 行数 (模型事实矛盾) | 重读 digest 修正声明 |
| INPLACE_NO_CLONE_SOURCE | inplace data role 缺 template_row | 声明非锚点占位行 |
| INPLACE_REGION_OVERLAP | 前置 add/remove 或 sets 触碰占位区 | base_last_row ≥ 占位区末端; 移除/写入移到区外 |
| STRUCTURAL_OP_OUT_OF_ZONE | 非终末 inplace 块声明 remove_rows (结构行操作只属于终末 inplace 块的 Trim) | 前置 append 块不声明 remove_rows; 收缩由终末 inplace 块 Trim (编译器推导) |
| TEMPLATE_ROW_GAP | 目标 sheet 行号空洞命中 add 锚点/克隆源行 | `scripts/repair_row_gaps.py --workdir <dir>` — 指纹自动重算; 更新 spec 指纹 (或 `--patch-spec`) → 重编译 (见 Q16) |
| REMOVE_TARGETS_APPEND_ZONE | append 块 remove_rows > base_last_row — add 推移行号后 remove 命中新数据行 (自毁 plan) | 首选 **append-only 合法终态**: 占位行自然下沉保留, 无需删除; remove_rows 只能声明 ≤ base_last_row 的模板既有行; 仅当占位行带样式时 inplace 才是条件选项 |
| PLACEHOLDER_RESIDUE_UNHANDLED | 保留占位行携带未覆盖值 | 加 columns/null/group label |
| PLACEHOLDER_RESIDUE_PARTIAL_NULLS | nulls 只覆盖部分保留行 | rows: all 或列映射 |
| GROUP_MERGE_ANCHOR_UNCOVERED | 无映射 group 列缺 label | 声明 label ("" = 清空) |
| GROUP_BY_COLUMN_UNMAPPED | group_by 列无列映射 (group_merges / group_aggregates) | 加 columns 映射 |
| GROUP_AGGREGATES_INVALID | group_aggregates 声明形态非法 (条目非 mapping / per_group 非列表) | 按 Q14 schema 写条目列表 (或 {per_group, whole_run} dict) |
| BLOCK_KEY_STRUCTURE_INVALID | block 顶层错位键/未知键 — `aggregates`/`per_row`/`group_aggregates` 应写于 `formulas:` 之下, typo (如单数 `formula`) 或任何非合法顶层键; 曾静默丢弃 (不再静默), 现编译期拒绝 (exit 3) | 块级只在 `formulas` 下写聚合类声明 (`formulas: {aggregates: [...]}` / `formulas: {per_row: ...}`); 合法顶层键 = clone_roles/rows/columns/formulas/merges/group_merges/nulls/remove_rows/styles |
| MERGE_MODE_CONFLICT | 同列混用 merges + group_merges | 每列只用一种合并模式 |
| SET_OUT_OF_BOUNDS | sets.path 超出 digest 维度 / 格式非法 | 用模板坐标裸格或完整 DOM 路径 |
| PROPS_WHITELIST_VIOLATION | props 超出 {numberformat} | 只用白名单键 |
| CAPABILITY_NOT_ROLLED_OUT | 声明 spike 未解锁的能力 (如 group_aggregates.whole_run 跨块总计, 落点语义待 spike) | 用已解锁表达 (每组合一块 + 块级 aggregates), 或等 spike 结论落地 |
| PPTX_CAPABILITY_NOT_ROLLED_OUT | pptx 声明未 rollout 能力: inplace / group_merges / group_aggregates / formulas (per_row·aggregates) / merges / nulls / remove_rows / columns[].props — 曾静默丢弃, 现编译期拒绝 (issue 06) | 用 xlsx, 或等 pptx lowering 验证后 rollout; 值类需求预计算进 columns; 删除多余声明 |
| PPTX_TARGET_ROWS_OUT_OF_BOUNDS | first_data_row + 匹配行数 − 1 越过表格实际行数 (pptx 行不能克隆) | 重读 digest 修 first_data_row / 收窄 selectors / python-pptx 一次性加行 (禁在 officecli 之后重新 import) |
