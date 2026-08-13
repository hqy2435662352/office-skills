# Layer 1: OLE 嵌入 Excel 处理

## 检测

**任何时候扫描目标 slide 结构时，必须先查 OLE：**

```bash
officecli get file.pptx "/slide[N]/ole[M]" --depth 0 --json
```

如果返回的 `progId` 包含 `Excel.Sheet`，则该 slide 存在 OLE 嵌入的 Excel 表格。

**禁止在未执行 OLE 探测的情况下报告"此 slide 没有表格"。**

## 提取

使用 `scripts/extract_ole.py` 自动提取：

```bash
python scripts/extract_ole.py --input file.pptx --slide 14 --output-dir 展平元数据输出/
```

内部流程：
1. 通过 slide rels（`ppt/slides/_rels/slide{N}.xml.rels`）查找 `oleObject{M}.bin` 映射
2. 从 PPTX ZIP 中读取 `ppt/embeddings/oleObject{M}.bin`
3. 在二进制流中搜索 `PK\x03\x04`（ZIP 文件头标记）
4. 提取首个包含有效 sheet 数据的 ZIP 段为 xlsx
5. 输出到 `展平元数据输出/oleObject{M}_slide_extracted.xlsx`

## 填充

提取后的 xlsx 视为普通源文件：
1. 用 `flatten_source.py` 展平
2. MOD Resolution → FillSpec 映射 → 用 officecli 填充

## 输出

**不要尝试将填充后的 xlsx 重新打包回 PPTX。** OLE 复合文档格式无法可靠地重写。

将填充后的 xlsx 作为最终产物输出，与 filled PPTX 放在同一级目录。并提醒用户：
> "OLE 均价分析数据已填入 `oleObject2_filled.xlsx` 和 `oleObject3_filled.xlsx`，与 PPTX 在同一目录。请在 Excel 中打开 → 选中表格区域 → Ctrl+C → 回到 PPTX 对应 slide → 右键粘贴为'保留源格式'。粘贴后即变为原生 PPTX 表格。"

## 已知陷阱

- OLE 二进制中可能包含多个 `PK\x03\x04` 标记（OLE 容器本身也是类 ZIP 结构）。提取脚本会自动尝试每个偏移，选择包含有效 sheet XML 数据的第一个。
- 提取的 xlsx 中的 Sheet1 可能数据不在 A1 起始位置，需要先做全量扫描确定实际数据范围。
