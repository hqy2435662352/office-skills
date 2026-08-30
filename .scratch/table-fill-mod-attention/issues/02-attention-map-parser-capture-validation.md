# 02 — Attention Map 解析器 + capture 写盘前硬校验

Status: resolved
Type: task
Blocked by:

## 问题

Attention Map（spec §5.2）需要机器可解析与防漂移校验，否则 MOD 演进后 Map 会与
规则表脱节（dangling ID、漏分组）。本票是干净的机制票：只做解析与校验，不做
renderer / loader / staged delivery / priority / dependency / 自动重排。

## 方案

### 1. `table-fill/scripts/_mod_catalog.py` 新增 `parse_attention_map()`

- 输入 MOD Markdown 全文，抽取 `## Attention Map` 段（段界正则与
  `mod_nominate.py parse_mod_file` 同款：`## Attention Map[^\n]*\n(.*?)(?=\n## |\Z)`）。
- 段内解析 `- <group>: <ID>, <ID>, ...` 行（沿用 Applicability 的
  `- ([\w_]+): (.+)` 风格）。
- 返回 `dict[str, list[str]]`（保持文件内组的出现顺序）；**段不存在返回 `None`**
  （区分"无 Map"与"空 Map"——无 Map = 旧 MOD，不校验）。
- 笨 parser：不解析 priority/dependency/enforcement/condition。
- **不得 silent-ignore**：段内除空行外的每个非空内容行都必须匹配
  `- <group>: <ID>, ...` 语法；不匹配的行必须作为结构化结果抛出（供校验层拒收），
  不得悄悄丢弃——校验器无法拒绝它看不见的行，silent-ignore 会使
  dangling/coverage 校验失去意义。

### 2. `table-fill/scripts/mod_capture.py` 写盘前校验

在 `_validate_source()` 之后、任何文件写入之前调用（create/update 同路径；
校验对象 = 完整 source 文本，即最终 candidate body）：

仅当 `parse_attention_map()` 返回非 None 时启用，逐条 fail（`CaptureError`，
exit 3，错误信息带 corrective 提示）：

1. **malformed line**：段内存在不匹配 `- <group>: <ID>, ...` 语法的非空行
   → 拒收，列出行号与内容；
2. **dangling**：Map 引用的 Rule ID 不在规则表中 → 拒收，列出 ID；
3. **coverage**：规则表中每条 Rule 至少出现在一个 group → 漏分组拒收，列出 ID；
4. **closed set**：group 名 ∈ {resolve, map, transform, validate} → 其他组名拒收；
5. **group 唯一**：同一 group 出现两行（如两行 `- resolve: ...`）→ 拒收
   （不引入 append/override 语义，最多四行，保持笨 parser）；
6. **顺序**：出现的 group 必须遵循 resolve → map → transform → validate 的相对
   顺序（允许子集，如某 MOD 无 transform 可省略；不允许 validate 排在 resolve
   之前）→ 乱序拒收；
7. **组内重复**：同一 group 内同一 Rule ID 出现两次 → 拒收；
8. **跨组重复**：合法，不报错。

另加一条最小 Runtime Core 校验：`## Runtime Core` 段存在但正文为空/纯空白 →
拒收（exit 3）。**不做**条数/字数/关键词/重复度等重型校验。

### 3. 测试（pytest，`table-fill/tests/`，参照 test_mod_decontamination.py 模式）

- 正常 Map：解析结果与期望 dict 一致（含组序、多 ID、跨组重复保留）；
- 无 Map 的旧 MOD：create/update 行为不变（兼容性回归——硬验收）；
- 拒收用例各自覆盖：malformed 行（如段内混入一句散文）/ dangling / 漏覆盖 /
  非法组名 / 同一 group 两行 / 组序颠倒（validate 在 resolve 前）/ 组内重复 /
  Runtime Core 空段；
- 正例：跨组重复不拒收；group 子集（如只有 resolve+map+validate，省 transform）
  且顺序正确 → 通过；
- Runtime Core 有内容 → 通过。

## 验收

- 新增测试全绿；现有 `test_mod_roundtrip.py` / `test_mod_decontamination.py` 及
  纯编译器测试子集不回归；
- 手工冒烟：对无 Map 的既有 MOD 源文件跑 `mod_capture.py --action update` 校验路径，
  行为与现状一致（不报新错误）；
- `mod_nominate.py` 零改动（本票不触碰提名路径）。

## Answer

Status: resolved（2026-08-28，主导 Agent 验收通过）

### 交付
- `table-fill/scripts/_mod_catalog.py`：新增 `AttentionMapParseError`（带 1-based 行号与原文）、`parse_attention_map_lines()`（行级有序视图）、`parse_attention_map()`（dict 视图，段缺失返回 None）；段界正则与 mod_nominate.parse_mod_file 同款；笨 parser，无 silent-ignore。
- `table-fill/scripts/mod_capture.py`：新增 `_validate_attention_metadata(text, rules)`，create/update 双路径在 `_validate_source()` 之后、任何写盘之前对最终 candidate body 执行；8 项 spec 校验 + Runtime Core 非空最小校验；全部失败 CaptureError exit 3 + corrective 提示（malformed 由 parser 先行拒收，其余聚合为一条消息）。
- `table-fill/tests/test_mod_attention_map.py`：新增 30 用例（parser 单测、全部拒收用例、正例、无 Map 旧 MOD create/update 兼容回归、ticket 01 启用用例）；capture 用例全部走 temp-root mock（HYGIENE，不碰 live references/）。

### 验收证据（主导 Agent 复核）
- 新测试 + 既有 mod 测试：`113 passed`（30 新 + 83 既有）；子代理全量套件 `735 passed`（exit 0）。
- `git status`：table-fill/references/ 无新增改动、无 .bak 残留；`mod_nominate.py` 零改动（mtime 早于本票）。
- 兼容性：无 Map 旧 MOD create/update 行为不变（测试 + temp-copy CLI 冒烟均验证）。

### 设计决策（票内未闭合项，已定）
- 空 ID 元素 / 冒号后无内容 → malformed（拒收，绝不静默丢弃）。
- 空段 `{}` → 启用校验 → 因 coverage 失败而拒收（区分"无 Map"与"空 Map"）。
- 未知组名不参与顺序检查（由 closed-set 报错），避免位置查找崩溃。

### 既有问题（不在本票范围，报告不修）
1. `--action update` 用**无 MOD_ 前缀**的名字时，`replace_index_row` 仍按裸名匹配索引行 → exit 1 找不到行（工作区既有 `_strip_prefix` 改动未覆盖该函数；ticket 01 的捕获命令因此需用完整注册名）。2. `replace_index_row` 失败时 .bak 已提前生成（非原子，既有）。3. e2e 偶发 GBK 控制台编码失败（本机环境，与 MOD 代码无关）。
