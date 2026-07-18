# Layer 1: 展平算法详解

## 标准模式（物理合并单元格）

### 算法
1. 从 `officecli get --depth 0` 读入所有 cell，每个 cell 含 `merge` 字段
2. 解析 `merge` 字段（如 `B5:B32`），构建 `covered` 集合（所有被合并覆盖的 cell 坐标）
3. 逐行逐列 forward-fill：
   - cell 有文本 → 更新该列 register，value = text（锚点）
   - cell 在 `covered` 中 → value = 该列 register（被合并吞噬，安全填充）
   - cell 既无文本也不在 covered 中 → value = None（真空白）
4. 跳过全空行

### 关键属性
- 每列独立 register，无"维度列/指标列"预设
- `covered` 白名单区分合并空白 vs 真实空白
- Register 在合并块边界自动重置

## Pivot 模式（逻辑层级，无物理合并）

### 检测条件
```python
merge_count == 0                            # 无物理合并
col_A_blank_ratio > 0.50                    # A 列 >50% 为空（父标签只出现一次）
col_B_density > 0.80                        # B 列 >80% 有值（子行密集）
```

### 算法
1. 构建 grid，清洗 `#DIV/0!`, `#N/A`, `#VALUE!` 等错误字符串
2. 逐行判断：
   - A 列有值 → 更新 parent register，标记 `is_summary_row=True`
   - A 列无值但 B 列有值 → 从 parent register 继承，标记 `is_summary_row=False`
   - 两者都无 → 跳过
3. 父行（summary row）**不丢弃**，作为 Layer 4 填充合计行的锚点

### 与标准模式的关键差异
- 父行出现在子行之前，自身是聚合值
- 无物理 `merge` 标记，`covered` 集为空
- 设置 `SKIP_GROUP_BY=True`：每个明细行 1:1 映射到目标格
- 单位线索优先从表标题/副标题/脚注中获取，量级仅作最后手段

## 多数据块检测

当一个源文件包含多个独立数据块（如 KPI 主表 + 观察指标表），检测方式：
- 扫描展平后的行，寻找"合计"行
- 合计行之后如果出现与第一块表头文本匹配的重复表头行 → 标记为新 block
- 在元数据 YAML 中标注 `block_id` 和 `row_range`

## 特殊结构速查

| 模式 | 检测 | 处理 |
|------|------|------|
| Pivot | merge_count==0 + A blank>50% + B dense>80% | Pivot mode |
| OLE 嵌入 | officecli get /slide[N]/ole[M] | extract_ole.py |
| 多数据块 | 重复表头行 + 合计行 | 标注 block boundaries |
| 跨系列合并 | merge 范围跨多个 A 列值 | 标记为块级汇总 |
