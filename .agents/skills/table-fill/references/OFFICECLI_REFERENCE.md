# officecli 调用规范

## 基本用法（必读）

**officecli 不需要显式 `open` / `save`。** 每个命令直接读写文件，写操作立即生效：

```python
# ✅ 直接操作——无需 open
subprocess.run(["officecli", "get", "file.xlsx", "/Sheet/A1", "--json"], ...)
subprocess.run(["officecli", "set", "file.xlsx", "/Sheet/A1", "--prop", "text=hello"], ...)
subprocess.run(["officecli", "batch", "file.xlsx", "--input", "batch.json"], ...)
```

`officecli open` 是可选的性能优化——它会启动一个常驻进程，后续命令通过内存通信而非每次打开文件。**对于 batch 操作不需要**，batch 本身已经是一次性批量执行。

**所有 officecli 写操作直接修改文件**，不需要额外保存步骤。这与 openpyxl 的 `wb.save()` 模式完全不同——如果用 openpyxl 的思维去理解 officecli，会误以为需要 `open` 显式打开文件。

## batch JSON 格式（执行机制）

V2 中 batch JSON 由 `compile_fill.py` 生成 (execution_plan.json 的 operations 段),
LLM 不直接生成。`execute_batch.py` 用 `officecli batch` 执行。格式：

```json
[
  {"command": "remove", "path": "/SheetName/row[28]"},
  {"command": "remove", "path": "/SheetName/row[27]"},
  {"command": "add", "parent": "/SheetName", "type": "row", "props": {"cols": 10}},
  {"command": "set", "path": "/SheetName/B2", "props": {"merge": "B2:B7"}},
  {"command": "set", "path": "/SheetName/C5", "props": {"text": "空调", "font.color": "000000"}},
  {"command": "set", "path": "/SheetName/D5", "props": {"text": "37.65", "numFmt": "#,##0.00"}},
  {"command": "set", "path": "/SheetName/col[D]", "props": {"width": 18}},
  {"command": "set", "path": "/SheetName/row[1]", "props": {"height": 24}}
]
```

### batch JSON 命令速查

| command | 用途 | 必需字段 | 常用 props |
|---------|------|---------|-----------|
| `add` (row) | 插入空行 | `parent`, `type` | `cols`, `height` |
| `add` (row clone) | 插入带格式行 | `parent`, `type`, `from` | `cols` |
| `remove` | 删除行 | `path` | — |
| `set` (clear cell) | 清空值保留格式 | `path` | `{"text": ""}` |
| `set` (cell) | 写值/格式 | `path` | `text`, `formula`, `numFmt`, `font.color`, `fill`, `bold`, `merge` |
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

### 严格排序规则

batch.json 中的操作必须按此顺序排列（SKILL.md `[REQUIREMENT]`）：
1. **remove**（删行）——从底向上
2. **add**（插行）——从底向上
3. **set merge**（合并单元格）
4. **set value/format**（填值、设格式）
5. **set structural**（列宽、行高、冻结）

前两步改变坐标系统，后续 set 必须基于新坐标。

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

十六进制颜色不要加 `#`：`FF0000`，不是 `#FF0000`。
公式不要加 `=`：`"formula": "SUM(B2:B4)"`，不是 `"=SUM(B2:B4)"`。

### PPTX 特殊要求

PPTX 目标的文本 cell 必须显式设置字体：

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

# ✅ 正确：Python subprocess
subprocess.run(["officecli", "get", filepath, path, "--json"], capture_output=True)
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
