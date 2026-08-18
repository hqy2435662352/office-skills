# officecli 调用规范

## 基本用法（必读）

**officecli 是命令式调用，没有 openpyxl 式显式 `open` / `save` 步骤**，但写入
**不是立即落盘**的 —— 这是 **resident 延迟写**模型（2026-08-12 实测）：

- 任何调用都可能启动 resident 常驻进程（例如坐标探针 `get`）。resident 启动后，
  后续操作在**内存中**应用，磁盘写延迟到 save/close/idle。
- 收尾必须显式 `officecli close <file>` 刷盘（无 resident 时 close 是 no-op）。
  **用 taskkill 强杀 resident（`clean_residents()`）会丢掉未刷盘的尾部操作**
  （实测 E15–E19 随机缺失）。
- table-fill 流程中 `execute_batch.py` 已在 chunk 循环尾 + 主流程尾显式
  `officecli close` 刷盘；手写/一次性命令时**自己负责 close**。

```python
# ✅ 直接调用——无需 open；写操作收尾显式 close 刷盘。
# 一律经共享适配器 `_officecli.officecli()`（UTF-8 subprocess, errors=replace）,
# 禁止裸 subprocess.run(["officecli", ...]) 绕过 (issue 09)。
from _officecli import officecli
officecli("get", "file.xlsx", "/Sheet/A1", "--json")
officecli("set", "file.xlsx", "/Sheet/A1", "--prop", "value=hello")
officecli("batch", "file.xlsx", "--input", "batch.json")
officecli("close", "file.xlsx")   # 刷盘; 无 resident 时 no-op
```

> 权威依据: `references/LAYER4_EXECUTE_LOOP.md`「resident 刷盘 (2026-08-12 实测)」
> 与 `references/KNOWN_TRAPS.md`「resident 延迟写被 taskkill 丢尾部 chunk」。

## batch JSON 格式（执行机制）

V2 中 batch JSON 由 `compile_fill.py` 生成 (execution_plan.json 的 operations 段),
LLM 不直接生成。`execute_batch.py` 用 `officecli batch` 执行。格式：

```json
[
  {"command": "add", "parent": "/SheetName", "type": "row", "props": {"cols": 10}},
  {"command": "add", "parent": "/SheetName", "type": "row", "from": "/SheetName/row[3]", "after": "/SheetName/row[7]"},
  {"command": "remove", "path": "/SheetName/row[28]"},
  {"command": "remove", "path": "/SheetName/row[27]"},
  {"command": "set", "path": "/SheetName/B2", "props": {"merge": "B2:B7"}},
  {"command": "set", "path": "/SheetName/C5", "props": {"value": "空调", "font.color": "000000"}},
  {"command": "set", "path": "/SheetName/D5", "props": {"value": "37.65", "numberformat": "#,##0.00"}},
  {"command": "set", "path": "/SheetName/col[D]", "props": {"width": 18}},
  {"command": "set", "path": "/SheetName/row[1]", "props": {"height": 24}}
]
```

(顺序 = op 恒序 `clear → add → remove → merge → fill`, 见下「全局排序不变量」;
示例省略 clear 段 —— 清空旧值/置空用 `{"value": ""}` / `{"value": null}`。)

### batch JSON 命令速查

| command | 用途 | 必需字段 | 常用 props |
|---------|------|---------|-----------|
| `add` (row) | 插入空行 | `parent`, `type` | `cols`, `height` |
| `add` (row clone) | 插入带格式行 | `parent`, `type`, `from` | `cols` |
| `remove` | 删除行 | `path` | — |
| `set` (clear cell) | 清空值保留格式 | `path` | `{"value": ""}` |
| `set` (cell, xlsx) | 写值/格式 | `path` | `value`, `formula`, `numberformat`, `font.color`, `fill`, `bold`, `merge` |
| `set` (cell, pptx) | 写文本 | `path` | `text`, `font`, `size` |
| `set` (col) | 列宽 | `path` | `width` |
| `set` (row) | 行高/隐藏 | `path` | `height`, `hidden` |
| `set` (sheet) | 冻结/标签色 | `path` | `freeze`, `tabColor` |

### 行操作最佳实践

**扩表（源行数 > 模板行数）**：清空模板旧值 → 用 `add --from` 克隆格式行 → 填新数据
**缩表（源行数 < 模板行数）**：清空模板旧值 → 填数据 → 删除底部多余行
**等表（源行数 = 模板行数）**：只清空旧值 + 填新数据，不增删行

**关键**：`from` 和插入位置是两个独立参数。`from` 只决定格式源（从哪一行克隆 cell 样式和合并单元格），不决定插入位置。如果不指定位置参数，新行默认插入到 sheet 末尾。

行克隆示例（在 row[5] 后插入 3 个带格式行）：
```json
{"command": "add", "parent": "/Sheet", "type": "row", "from": "/Sheet/row[5]", "after": "/Sheet/row[5]"},
{"command": "add", "parent": "/Sheet", "type": "row", "from": "/Sheet/row[5]", "after": "/Sheet/row[5]"},
{"command": "add", "parent": "/Sheet", "type": "row", "from": "/Sheet/row[5]", "after": "/Sheet/row[5]"}
```
⚠️ 若省略 `after`，三行全插入到 sheet 末尾而非 row[5] 之后。
`from` 会克隆源行的 cell 内容、样式和单行合并单元格；相对公式引用自动偏移。

### 全局排序不变量（op 恒序）

batch.json 的操作顺序由 `compile_fill.py` 生成，**全局恒为
`clear → add → remove → merge → fill`**（append-only 形态；inplace 混合形态的
精确序列见 `references/FILLSPEC.md`「执行顺序保证」E1）。细则：

1. **clear** — 清空旧值/置空（`{"value": ""}` 清空、`{"value": null}` 置空）
2. **add**（插行）— 自顶向下（配合 `after` 定位；值写入**不穿插** add，防
   duplicate_row）
3. **remove**（删行）— 自底向上；remove_rows 是模板坐标，与 add 区无交集
   （`REMOVE_TARGETS_APPEND_ZONE` 编译期保证）
4. **merge**（合并单元格）
5. **fill**（填值/公式/格式）

add/remove 改变坐标系统，后续 set 必须基于新坐标。**手写 batch 即违规** —— batch
一律由 `compile_fill.py` 生成（见 `references/TOOL_TRAPS.md`「手写 checks /
batch」）。

### 路径格式

| 格式 | 目标 | 示例 |
|------|------|------|
| xlsx cell | `/SheetName/XY` | `/分公司经营状况一览表/D5` |
| xlsx row | `/SheetName/row[N]` | `/Sheet1/row[5]` |
| xlsx col | `/SheetName/col[X]` | `/Sheet1/col[D]` |
| xlsx range | `/SheetName/X1:Y99` | `/Sheet1/A1:AX200` |
| PPTX cell | `/slide[N]/table[@id=M]/tr[R]/tc[C]` | `/slide[5]/table[@id=2]/tr[3]/tc[3]` |
| PPTX table | `/slide[N]/table[@id=M]` | `/slide[5]/table[@id=2]` |

**注意**：`[N]` 是 1-based（XPath 风格），`--index` 是 0-based。路径中 sheet 名或 slide 索引包含中文时，必须用引号包裹完整路径。

### props 命名规则

在 batch JSON 的 props 中，**必须使用完整的属性名**：

- ✅ `"font.color": "FF0000"` — 字体颜色
- ✅ `"fill": "FFFF00"` — 背景颜色
- ❌ `"color": "FF0000"` — 歧义，会被拒绝

**xlsx 单元格属性是 `value` / `numberformat`；pptx 文本格属性是 `text`。**
`text`/`numFmt` 不是 xlsx 官方属性（`text` 仅是未文档化兼容别名，与 `value`
混用时值会丢失；`numFmt` 大小写歧义会导致格式读回丢失）—— 一律写 `value` /
`numberformat`（见 `references/KNOWN_TRAPS.md`「`text` 属性漂移」「`numFmt`
大小写歧义」）。

十六进制颜色不要加 `#`：`FF0000`，不是 `#FF0000`。
公式不要加 `=`：`"formula": "SUM(B2:B4)"`，不是 `"=SUM(B2:B4)"`。

### PPTX 特殊要求

PPTX 目标的文本 cell 必须显式设置字体，属性用 `text`（不是 xlsx 的 `value`）：

```json
{"command": "set", "path": "/slide[5]/table[@id=2]/tr[3]/tc[3]", "props": {"text": "19.07", "font": "微软雅黑", "size": "9pt"}}
```

---

## depth 控制

| depth | 用途 | 每个 cell 输出量 |
|-------|------|-----------------|
| 0 | 快速扫描结构、读 xlsx 值 | ~5 行 |
| 1 | 读 PPTX cell（含 rowspan/colspan/fill/bold/align） | ~30 行 |
| 2 | 完整表格 dump（含原始 XML） | ~100 行 |

**规则**：xlsx 用 depth 0，PPTX 单个 cell 用 depth 1，PPTX 全表用 depth 2。

## 中文路径编码陷阱

**禁止**直接通过 PowerShell 管道调用 officecli——GBK 编码会损坏中文：

```powershell
# ❌ 错误：PowerShell 管道
officecli get "C:\含中文\file.xlsx" "/A1" --json

# ✅ 正确：Python 走共享适配器 `_officecli.officecli()`（UTF-8 subprocess）
from _officecli import officecli
officecli("get", filepath, path, "--json")
```

**临时文件路径**：使用 ASCII 纯英文路径（`C:\Temp\oc_work\`）。

## 权限问题

```python
import stat, shutil
shutil.copy2(template, output)
os.chmod(output, stat.S_IWRITE | stat.S_IREAD)
```

## 返回码与错误处理

- officecli `set` / `batch` 成功返回纯文本 "Updated ..."，不是 JSON
- officecli `get` 成功返回 JSON（`{"success": true, "data": {...}}`）
- 检查 `success` 字段判断是否成功
- batch 中失败的 cell 可用 `officecli set` 逐条重试
