# 04 — Compiler 缺陷码 `LOOKUP_KEY_COLUMN_INVALID`

Status: resolved
Type: code（`table-fill/scripts/compile_fill.py` + `table-fill/references/FILLSPEC.md` + `table-fill/tests/test_optimization.py`）
依据: spec.md §2 事实 4。session eg_fresh_local 首版 spec 写
`lookup.key_column: sku`（索引逻辑键名）→ compile_fill.py:1163
`values[col_letter_to_idx(kcol)]` 裸 `IndexError`。

## 契约

`key_column` 必须是**当前展平源数据中真实存在的 Excel 列字母**（如 `G`），
不是索引逻辑键名/字段名。两种非法输入违反同一契约，一个缺陷码封死整个
crash 类：

| 输入 | reason | 原 |
|---|---|---|
| `sku`（非列字母） | `invalid_format` | 裸 IndexError |
| `AB`（合法字母但超出源列数，如源仅 A:J） | `out_of_range` | 仍会裸 IndexError（同类 crash，一并封死） |

## 实现要求

1. **静态验证阶段拦截**（生成 plan 之前），exit 3 结构化缺陷，单码：
   `LOOKUP_KEY_COLUMN_INVALID`，defect detail 内区分
   `invalid_format` / `out_of_range`，不扩张 defect taxonomy。
2. **两个入口都堵**：列级 `columns[].lookup.key_column` 与表级
   `lookups[].key_column`。
3. **corrective_action 给人话**（按 reason 区分）：
   - invalid_format: `key_column='sku' is invalid: expected an Excel
     column letter present in the flattened source (e.g. G), not a
     logical field/key name.`
   - out_of_range: `key_column='AB' is out of range: flattened source has
     columns A:J. Choose an existing source column.`
4. **range 检验基准 = 该 lookup 的实际 consumer source(s)**，不是"所有
   source"。编码前先核实表级 `lookups[]` 的真实绑定语义（全局作用于所有
   block/source，还是仅被部分 column/block 引用）：Compiler 已有 consumer
   解析就直接复用；没有就**不要为本票顺带重构 consumer graph** — 在使用
   点做安全 guard（同样产出结构化缺陷、消灭裸 IndexError）优先于错误的
   全局校验（对未被消费的 source 校验宽度会误拒合法 FillSpec）。
5. **明确不做**（grilling 裁决）：不接受逻辑名兼容、不猜 header 映射、
   不做别名/模糊匹配/自动修复 — 只负责拒绝非法值。那会重新引入重复列名/
   中文别名（类别 vs 类别Ⅱ）类不确定性，与"机械可判断的交给 Compiler"
   原则相逆。
6. compile_fill.py 现有 resolve 路径保留兜底，但静态拦截命中后不应再能
   走到 1163 的裸 IndexError。
7. **normalization 行为冻结**: 现有**合法**输入的接受行为不变（大小写、
   空白容忍等一切照旧，`key_column: g`、`" G "` 等现状行为保持原样）；
   非法输入除"裸 crash → 本缺陷码"外不做其他收紧或放宽。guard 的接受集
   = 现有列字母解析（`col_letter_to_idx` 一线语义）的接受集，禁止另发明
   `^[A-Z]{1,3}$` 类新 regex 造成兼容性漂移。

## 制度化三件套（按 SKILL.md 制度化标准，缺一视为未完成）

1. **Compiler 检查**：上述缺陷码（静态验证段，exit 3 + corrective_action）。
2. **契约条目**：FILLSPEC.md — `key_column` 定义处（约 L78 lookup 示例注释
   与 lookup 节）明确一句 "Excel source column letter，如 `G`，不是逻辑
   字段名"；「常见编译错误速查」表（约 L927 区域）新增
   `LOOKUP_KEY_COLUMN_INVALID` 行。
3. **回归测试**：`tests/test_optimization.py` 既有契约测试面，两个用例：
   - malformed：`key_column: sku` → exit 3 + `LOOKUP_KEY_COLUMN_INVALID`
     (invalid_format)，不产生 plan、无裸 traceback；
   - out-of-range：`key_column: AB` + 10 列源 → exit 3 +
     `LOOKUP_KEY_COLUMN_INVALID` (out_of_range)。

** deliberately 不新增 KNOWN_TRAPS 条目** — 本票确立准入边界：KNOWN_TRAPS
收"看起来合理但实际危险的机制陷阱"（自引用、行位移、merge 语义）；非法
schema 参数属普通静态校验输入错误，不沉淀为领域陷阱。此决定是对制度化
标准中"不落 KNOWN_TRAPS 不算完成"的有意收窄，理由记录于本票与 ADR-0012。

## Acceptance

- [x] 两个非法输入均 exit 3 + 单缺陷码 + 按 reason 区分的 corrective_action，
      stderr 无裸 traceback。
- [x] 合法列字母（含双字母如 `AB` 且源列数足够）不受影响，compile 通过。
- [x] 列级与表级入口均被拦截（各一断言）。
- [x] range 校验只覆盖该 lookup 的实际 consumer source(s)；表级 lookup
      绑定语义的核实结论记录在实现说明中，无误拒合法 spec 的新增路径。
- [x] 合法输入行为零漂移：现有通过编译的 spec 用例全部仍通过。
- [x] FILLSPEC.md 定义句 + 速查行就位；KNOWN_TRAPS 无新增条目。
- [x] 既有测试套件无回归（`pytest table-fill/tests/test_optimization.py`）。


## Comments

### 验收记录（主 Agent，2026-08-31）

子代理实现 + 主 Agent 独立复核（读 diff + 独立跑全量回归），7/7 PASS：

1. 两个非法输入均 exit 3 + 单缺陷码 LOOKUP_KEY_COLUMN_INVALID + reason 区分的
   corrective_action（'sku'→invalid_format / 'AB'+8 列源→out_of_range），stderr
   无裸 traceback（测试断言 =Traceback= 不存在），编译在返回 plan 前失败
   （静态 gate 位于 compile_spec 的 validate_inplace_declaration 之后、
   match_block_sources 之前）。
2. 合法列字母不受影响：小写 'c'、双字母 'AB'+28 列源均 compile 通过
   （接受集 = CELL_RE(^[A-Z]{1,2}$) + col_letter_to_idx 大小写容忍，
   未发明新 regex）。
3. 列级与表级入口均被拦截：列级 malformed（invalid_format）与表级
   out_of_range 各一断言，另有 resolve_lookup 直呼兜底 2 条。
4. range 校验只覆盖实际 consumer source(s)：表级 lookups[] 绑定语义已核实
   —— 仅被 columns[].lookup 引用的 lookup 才被消费（materialize_values 内
   两处调用点逐行消费该列所在 block 的 rows source 行）；未引用 lookup
   不检查（不消费不 crash），结论记录于 validate_lookup_key_columns docstring
   与实现说明。
5. 合法输入零漂移：全量回归 370 passed / 0 failed（基线 362 + 新增 8），
   主 Agent 独立复跑确认（370 passed in 8.02s）。
6. FILLSPEC.md 定义句（L79 示例注释 + L307-311 lookups 节）+ 速查表新行
   （L952）就位；KNOWN_TRAPS 无新增条目（diff 仅 ticket 03 可选尾句）。
7. 裸 IndexError 封死：resolve_lookup 兜底（L1294-1312）越界/负 index/
   非字符串 → 同码缺陷 + None；旧 L1163 裸 values[col_letter_to_idx(kcol)]
   已不存在。

结论：ticket 04 验收通过，Status: resolved。
