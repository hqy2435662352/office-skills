# Layer 1: OLE 嵌入 Excel 处理

## 检测

**任何时候扫描目标 slide 结构时，必须先查 OLE：**

```bash
officecli get file.pptx "/slide[N]/ole[M]" --depth 0 --json
```

`ole[M]` 是 slide 内 OLE 对象的**位置序号**（1 起，按 slide rels 中 oleObject 关系
顺序，不是 embedding 文件编号）。返回 `success: true` 且对象的 `progId` 包含
`Excel.Sheet`，则该 slide 存在 OLE 嵌入的 Excel 表格；返回 `success: false` 说明
该序号没有 OLE 对象。

**禁止在未执行 OLE 探测的情况下报告"此 slide 没有表格"。**

## 提取

使用 `scripts/extract_ole.py` 自动提取：

```bash
python scripts/extract_ole.py --input file.pptx --slide 14 --output-dir 展平元数据输出/
```

内部流程（选择逻辑：关系类型 + progId，不取"第一个文件名"）：
1. 解析 slide rels（`ppt/slides/_rels/slide{N}.xml.rels`），只保留**关系 Type 以
   `/oleObject` 结尾**的 embedding（image / notesSlide / vbaProject 等一律排除），
   得到 `oleObject{M}.bin` 映射列表（按 rels 文档顺序）。
2. 按映射顺序探测 `officecli get file.pptx "/slide[N]/ole[K]"`（K = 1..slide 内
   OLE 个数），读取返回对象的 `progId` 与 `relId`。
3. 选中第一个 `progId` 含 `Excel.Sheet` 的 embedding；探测槽位用返回的 `relId`
   回映射到 rels（relId 缺失时按位置对应）。
4. 无任何 OLE，或有 OLE 但无 `Excel.Sheet` → 失败，缺陷码 `OLE_NO_EXCEL_EMBEDDING`
   （含 corrective_action），**不静默产出错误对象**。
5. 从 PPTX ZIP 中读取 `ppt/embeddings/oleObject{M}.bin`，在二进制流中搜索
   `PK\x03\x04`（ZIP 文件头标记）。
6. 提取首个包含有效 sheet 数据的 ZIP 段为 xlsx，输出到
   `展平元数据输出/oleObject{M}_slide_extracted.xlsx`。

## 填充

提取后的 xlsx 视为普通源文件：
1. 单 sheet 用 `scripts/flatten_table.py` 展平：

   ```bash
   python scripts/flatten_table.py --input <提取的.xlsx> --target <Sheet名> --output <out.csv> [--meta <out_meta.json>]
   ```

2. 多 sheet（一个 workbook 多个表）用 `scripts/flatten_workbook.py`（共享 outline
   探测，避免每个 sheet 重复启动进程）：

   ```bash
   python scripts/flatten_workbook.py --input <提取的.xlsx> --plan plan.json --out-dir <输出目录/>
   # plan.json 格式: {"targets": [{"sheet": "Sheet1", "name": "home"}, ...]}
   ```

3. MOD Resolution → FillSpec 映射 → 用 officecli 填充

## 输出

**不要尝试将填充后的 xlsx 重新打包回 PPTX。** OLE 复合文档格式无法可靠地重写。

将填充后的 xlsx 作为最终产物输出，与 filled PPTX 放在同一级目录。并提醒用户：
> "OLE 均价分析数据已填入 `oleObject2_filled.xlsx` 和 `oleObject3_filled.xlsx`，与 PPTX 在同一目录。请在 Excel 中打开 → 选中表格区域 → Ctrl+C → 回到 PPTX 对应 slide → 右键粘贴为'保留源格式'。粘贴后即变为原生 PPTX 表格。"

## 已知陷阱

- OLE 二进制中可能包含多个 `PK\x03\x04` 标记（OLE 容器本身也是类 ZIP 结构）。提取脚本会自动尝试每个偏移，选择包含有效 sheet XML 数据的第一个。
- 提取的 xlsx 中的 Sheet1 可能数据不在 A1 起始位置，需要先做全量扫描确定实际数据范围。
- `officecli` 的 `/ole[M]` 序号是 slide 内 OLE 对象的**位置序号**，与 embedding
  文件名编号（`oleObject{N}.bin` 的 N）不一定相同；`extract_ole.py` 已通过 rels
  顺序 + relId 回映射处理，手工探测时不要按文件名编号猜序号。
