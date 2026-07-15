# Chart Presets

7 套 officecli 内置样式预设。创建图表时通过 `--prop preset=X` 应用。

## 核心规则：两步法

**永远先 preset，后覆盖。**

```bash
# 第 1 步：用 preset 创建图表骨架
officecli add <file> /SheetName \
  --type chart \
  --prop chartType=column \
  --prop dataRange="Sheet1!A1:C13" \
  --prop title="月度销售趋势" \
  --prop preset=corporate \
  --prop anchor=D2:J18

# 第 2 步：preset 之后覆盖被改动的属性
officecli set <file> /SheetName/chart[N] --prop legend=bottom
officecli set <file> /SheetName/chart[N] --prop dataLabels=outsideEnd
```

**为什么必须两步？** `preset=corporate` 自带 legend=right、colors=商务配色等默认值。如果在 `add` 时同时传 `legend=bottom`，preset 内部可能覆盖它。两步分离确保手动属性在 preset 生效后写入。

**警告**：在 `add` 阶段同时传 `preset=X` 和 `legend=bottom` 等属性，后者可能被 preset 覆盖且静默失败。不要这样做。

---

## Preset 一览

### 1. minimal

| 属性 | 说明 |
|------|------|
| **视觉效果** | 极简：无网格线、无背景色、无图例边框、细线系列、无 3D 效果 |
| **适用场景** | 报告正文嵌入、需要与周围文字风格统一、打印友好 |
| **被覆盖的属性** | `gridlines` (none), `legend` (无框线), `chartBackground` (透明/白), `seriesOutline` (细线或无), `dataLabels` (默认不显示), `3D` (关闭) |

### 2. dark

| 属性 | 说明 |
|------|------|
| **视觉效果** | 深色背景 + 高对比度系列色。适合暗色主题演示或夜间模式报告 |
| **适用场景** | 深色 PPT 主题、Dashboard 暗色模式、投影环境 |
| **被覆盖的属性** | `chartBackground` (深灰/黑), `plotAreaBackground` (深灰), `fontColor` (白/浅灰), `colors` (高饱和对比色), `gridlines` (浅灰细线), `legend` (暗色背景) |

### 3. corporate

| 属性 | 说明 |
|------|------|
| **视觉效果** | 商务稳重：蓝灰主色调、清晰网格线、标准图例、专业字体 |
| **适用场景** | 企业年报、商务汇报、财务分析、正式提案 |
| **被覆盖的属性** | `colors` (蓝/灰/橙商务配色), `gridlines` (浅灰实线), `legend` (右侧, 有框), `font` (无衬线, 如 Calibri), `chartBackground` (白), `title` (深色, 加粗) |

### 4. magazine

| 属性 | 说明 |
|------|------|
| **视觉效果** | 杂志风：大胆配色、无网格线、大字号标题、艺术化图例位置 |
| **适用场景** | 市场宣传、品牌展示、公众号/媒体配图、需要视觉冲击力 |
| **被覆盖的属性** | `colors` (高饱和/互补色), `gridlines` (none), `legend` (底部或顶部, 无框), `font` (大字号, 可能用衬线体), `title` (大, 醒目), `chartBackground` (白或透明) |

### 5. dashboard

| 属性 | 说明 |
|------|------|
| **视觉效果** | Dashboard 专用：紧凑布局、数据标签外显、图例精简、强调可读性 |
| **适用场景** | KPI 面板、实时监控、数据大屏、需要快速扫读 |
| **被覆盖的属性** | `dataLabels` (显示值, 外显), `legend` (底部或隐藏), `gridlines` (淡色或 none), `colors` (高区分度), `chartBackground` (透明, 融入面板), `title` (小字号或隐藏) |

### 6. colorful

| 属性 | 说明 |
|------|------|
| **视觉效果** | 多彩：彩虹色系、活泼明亮、适合区分大量系列 |
| **适用场景** | 教育材料、儿童内容、需要区分 5+ 系列的复杂图表 |
| **被覆盖的属性** | `colors` (彩虹/多色轮), `chartBackground` (白), `legend` (彩色标识), `fontColor` (深灰), `gridlines` (淡色) |

### 7. monochrome

| 属性 | 说明 |
|------|------|
| **视觉效果** | 单色：同一色相的不同明度/饱和度区分系列。极简、专业、色盲友好 |
| **适用场景** | 学术论文、黑白打印、色盲无障碍、严肃数据展示 |
| **被覆盖的属性** | `colors` (单色系梯度, 如蓝 100%-20%), `chartBackground` (白), `legend` (灰度标识), `gridlines` (灰), `fontColor` (黑/深灰) |

---

## Preset 与手动属性的交互

### 安全做法

```bash
# 正确：preset 在 add 阶段，覆盖在 set 阶段
officecli add file.xlsx /Sheet1 --type chart --prop preset=corporate ...
officecli set file.xlsx /Sheet1/chart[1] --prop legend=bottom
officecli set file.xlsx /Sheet1/chart[1] --prop dataLabels=outsideEnd
```

### 危险做法（不要这样做）

```bash
# 错误：preset 和 legend 同时传，legend 可能被覆盖
officecli add file.xlsx /Sheet1 --type chart --prop preset=corporate --prop legend=bottom ...
```

### 常见被覆盖属性清单

无论选哪个 preset，以下属性都可能被覆盖。如需自定义，必须在 `add` 之后用 `set`：

- `legend`（位置、框线、背景）
- `colors`（系列配色方案）
- `gridlines`（有无、颜色、线型）
- `chartBackground` / `plotAreaBackground`（背景色）
- `font` / `fontColor` / `fontSize`（全局字体）
- `seriesOutline`（系列边框）
- `dataLabels`（显示/隐藏、位置）
- `marker`（折线图的点标记样式）

> **规则**：`add` 阶段只传 `preset=X`，不传任何可能被 preset 覆盖的属性。所有样式微调在 `set` 阶段完成。
>
> **注意**：当源数据从静态值改为公式引用时，`dataLabels` 的 number format 可能漂移。修改源数据后应检查数据标签格式，必要时用 `officecli set ... --prop dataLabels=...` 重新固定。
