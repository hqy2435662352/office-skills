# Layer 2: 列分类规则

## 四类列定义

| 分类 | 定义 | 判断信号 | 聚合方式 |
|------|------|---------|---------|
| **DIMENSION** | GROUP BY 键 | 文本重复出现, InlineString type, 常有合并格 | GROUP BY |
| **MEASURE_AGGREGABLE** | SUM 安全 | 数值变化, Number type, `#,##0.00` format | SUM() |
| **MEASURE_DERIVED** | 比率/单价/利润率 | `#0.00%` format, 文本含"率/价/占比" | SUM(分子)/SUM(分母) |
| **METADATA** | 序号/单位/备注 | 序号连续, 单位文本固定, 不参与运算 | 跳过 |

## 分类信号优先级

1. **列名语义**（"数量"→MEASURE, "类别"→DIMENSION）
2. **数据模式**（少量重复值→DIMENSION, 多样数值→MEASURE）
3. **officecli type**（InlineString→强 DIMENSION 信号, Number→弱信号）
4. **officecli numberformat**（`#0.00%`→DERIVED, `#,##0.00`→AGGREGABLE）
5. **业务上下文**（报价表? KPI 考核? 销售分析?）

## DERIVED 指标公式链追踪

对每个 DERIVED 指标，必须写出公式链。示例：

```
净价(O) = 报价(J) - 财务费用(K) - OA信保(L) - 返点(M) - 其他费用(N)
结算价(R) = 原型机成本(P) + 铜管成本(Q)
净收入(S) = 净价(O) × 数量(G)
毛利(T) = (净价(O) - 结算价(R)) × 数量(G)
```

## 多数据块标注

如果源 CSV 包含多个独立的数据块：

```yaml
blocks:
  - block_id: 1
    block_type: "main_kpi"
    row_range: [8, 18]
    description: "主营业务 KPI 指标（总销量/收入/利润等）"
    boundary_marker: "合计行(第 19 行)"
  - block_id: 2
    block_type: "observation"
    row_range: [22, 31]
    description: "观察指标（结构占比/毛利率等）"
    boundary_marker: "重复表头行(第 20-21 行)"
```

## YAML 输出格式

参见 `assets/元数据_template.yaml`。必须包含：
- 源文件路径
- 数据块列表（含行范围和类型）
- 每列的 index、名称、分类、单位、衍生公式（如适用）
- 数据质量说明（缺失值、错误值、单位线索）
