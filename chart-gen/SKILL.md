---
name: chart-gen
description: |
  为已有数据的 Excel 文件生成图表。当用户提到"画图"、"图表"、"可视化"、"柱状图"、"折线图"、"饼图"、"条形图"、"给 xlsx 加图"、"生成图表"或任何与 Excel 图表相关的请求时，必须使用此 skill。即使请求中没有明确出现"chart"或"图表"字样，只要涉及对 xlsx 数据的可视化展示，就应当触发。

  不适用于：从零创建 xlsx（无数据源）、修改已有图表的样式（应直接 officecli set）、PPT 图表（不在范围内）。
license: MIT
compatibility: |
  Required: officecli on PATH, Python 3.8+, Windows
  Optional: references/CHART_TYPES.md, references/CHART_PRESETS.md
---

# Chart Gen

为已有数据的 xlsx 文件创建图表。3 步工作流：分析 → 确认 → 生成。单次调用只创建一个图表。

**Why this structure matters**: 图表创建后 series 结构不可变，如果数据范围推断错误，只能删除重建。Human Gate 让用户在生成前验证数据范围，避免昂贵返工。

---

## ⚠️ 依赖加载（必须先执行）

本 skill 操作 xlsx 文件中的图表，依赖基础 skill 提供 Excel 格式规范、QA 门禁和可视化交付标准。

在调用本 skill 之前，**必须先加载以下依赖**：

```
skill(name="officecli-xlsx")
```

如果未加载，agent 缺少 Excel 文件的核心操作规则（公式、单元格格式、列宽、数字格式、`view html` 验证等），可能生成格式不合格的文件。

**验证方式**：加载后检查 `officecli-xlsx/SKILL.md` 的 Shell & Execution Discipline 和 Requirements for Outputs 节是否可用。

---

## Hard Constraints

- **数据只读**: Step 1 只使用 `officecli get` 读取，不做任何 `set`
- **数据不动**: Step 3 只 `add chart`，不修改数据区域的任何 cell
- **原地修改**: 输出就是输入文件本身，不生成副本
- **officecli only**: 图表创建只用 `officecli add chart`，禁止 openpyxl chart API
- **Python subprocess**: 所有 officecli 调用使用 Python `subprocess.run()`，禁止 PowerShell 管道
- **preset 后 override**: 先 `add --prop preset=X`，再用 `set` 覆盖属性。禁止在 add 时同时传 preset 和冲突属性
- **单次单图**: 每次调用只创建一个图表。多图需求通过多次独立调用满足
- **chart-series 不可变**: 如果创建了错误系列，只能删除整个图表重建。禁止企图用 `set` 修复系列结构
- **拒绝 = 重新推理**: 若用户在 Human Gate 拒绝推荐，必须回到 Step 1 重新分析。禁止重复相同的推荐
- **start 时 close**: 每次开始操作前执行 `officecli close <file>`（释放可能存在的驻留锁）
- **end 时 close**: 操作完毕后执行 `officecli close <file>`（释放给下游）
- **set 前必须 query**: 对任何 chart 执行 `officecli set` 前，必须先 `officecli query <file> chart --json`，按 `title`/`anchor` 确认目标索引。禁止假设新图位于 `chart[2]` 等固定索引

---

## Exit Code Protocol

| Exit | Meaning | Action |
|------|---------|--------|
| 0 | 通过 | 继续 |
| 1 | 致命错误（文件缺失、环境问题、文件被锁） | STOP，报告用户 |
| 3 | 可重试（前置条件未满足、数据验证失败） | 应用 corrective_action，重试 |

错误输出格式（四段式，不可省略）：
```
[CHART_GEN_ERROR] ROOT_CAUSE
[CHART_GEN_ERROR] CORRECTIVE_ACTION
[CHART_GEN_ERROR] CONTEXT
```

---

## Step 1: 分析

**PRECONDITION**: `officecli close <file>` 执行完毕，文件无驻留锁。

1. 记录已有图表：`officecli query <file> chart --json`
2. 读取 xlsx 结构和数据内容（`officecli get`，`--depth 0`）
3. 推断推荐的图表类型、数据范围、系列配置、样式预设、位置
4. 对推断的 dataRange 首尾采样（前 3 行 + 末 3 行，内联 `officecli get`），验证非空
5. 输出 `展平元数据输出/{name}_chart_proposal.yaml`（基于 `assets/chart_proposal_template.yaml`）

**状态锚点**（完成后必须输出）：
```
[CHART-GEN: S1=DONE, Proposal=展平元数据输出/{name}_chart_proposal.yaml, Gate=pending, Next=S2]
```

---

## Step 2: 确认 ⛔ HUMAN GATE

**PRECONDITION**: `python scripts/layer_gate.py --target 2 --workdir 展平元数据输出/` exits 0.

读取 proposal.yaml，向用户展示推荐配置 + 追溯表：

### 追溯表：图表推荐验证

| # | 系列名称 | 数据范围 | 采样值（前3） | 采样值（末3） | 推荐图表类型 | 推荐理由 |
|---|---------|---------|-------------|-------------|-------------|---------|
| 1 | （从 proposal 读取） | （从 proposal 读取） | （Step 1 采样） | （Step 1 采样） | （从 proposal 读取） | （从 proposal 读取） |
| — | 分类轴 | （从 proposal 读取） | （Step 1 采样） | （Step 1 采样） | — | 作为 X 轴分类标签 |

> 验证方式：打开源 xlsx，对照每个系列的"采样值"是否与源数据一致。如果不一致，说明数据范围推断有误——请手动指定正确范围。

用户可确认、修改或拒绝：
- **确认**: 继续 Step 3
- **修改**: 更新 proposal.yaml 中的对应字段，标记 `confirmed: true`
- **拒绝**: 回到 Step 1 重新推理（禁止重复相同推荐）

确认后运行：`python scripts/layer_gate.py --confirm-gate 1 --workdir 展平元数据输出/`

**状态锚点**（完成后必须输出）：
```
[CHART-GEN: S2=DONE, Gate=CONFIRMED, Next=S3]
```

**End your response here. Do NOT continue to Step 3 until the user replies.**

---

## Step 3: 生成 + 验证

**PRECONDITION**: `python scripts/layer_gate.py --target 3 --workdir 展平元数据输出/` exits 0，且 proposal 包含 `confirmed: true`。

### 创建图表（两步法）

```python
import subprocess, json

# 第 1 步：用 preset 创建图表骨架
subprocess.run([
    'officecli', 'add', filepath, '/SheetName',
    '--type', 'chart',
    '--prop', 'chartType=column',
    '--prop', 'dataRange=Sheet1!A1:C13',
    '--prop', 'title=月度销售趋势',
    '--prop', 'preset=corporate',
    '--prop', 'anchor=D2:J18',
    '--json'
], capture_output=True)

# 第 2 步：preset 之后覆盖被改动的属性
subprocess.run([
    'officecli', 'set', filepath, '/SheetName/chart[N]',
    '--prop', 'legend=bottom',
    '--json'
], capture_output=True)
```

> **为什么两步分离？** `preset=corporate` 自带 legend=right，如果 add 时同时传 `legend=bottom`，preset 内部可能覆盖它。两步分离确保手动属性在 preset 生效后写入。

### 修改图表属性前（必须）

**必须先 query，再 set。** 新创建的 chart 不一定落在你预期的索引上（例如已有 3 张图时，新图可能是 `chart[4]` 而非 `chart[2]`）。直接对 `chart[N]` 执行 `set` 可能改到错误的图表。

```python
# 第 0 步：query 列出所有图表，按 title/anchor 找到真实索引
result = subprocess.run(
    ['officecli', 'query', filepath, 'chart', '--json'],
    capture_output=True
)
charts = json.loads(result.stdout.decode('utf-8'))
# 从响应中找到目标 chart 的索引，例如 chart[4]

# 第 2 步：对该真实索引执行 set
subprocess.run([
    'officecli', 'set', filepath, '/SheetName/chart[4]',
    '--prop', 'legend=bottom',
    '--json'
], capture_output=True)
```

### 读取图表（验证用）

```python
# 列出所有图表
subprocess.run(['officecli', 'query', filepath, 'chart', '--json'], capture_output=True)

# 读特定图表
subprocess.run(['officecli', 'get', filepath, '/Sheet1/chart[1]', '--json'], capture_output=True)

# 读系列（验证数据绑定）
subprocess.run(['officecli', 'get', filepath, '/Sheet1/chart[1]/series[1]', '--json'], capture_output=True)
```

### EXIT GATE

```bash
python scripts/verify_output.py --output <file> --workdir 展平元数据输出/
```

验证内容：
- 图表存在性：`officecli query chart` 确认图表已创建
- 数据绑定：`officecli get /sheet/chart[N]/series[K]` 确认 valuesRef 非空且指向正确列

若 exit non-zero：修复问题后重跑。Do NOT 报告完成。

**状态锚点**（完成后必须输出）：
```
[CHART-GEN: S3=DONE, Chart=/SheetName/chart[N], Gate=EXIT_PASSED, Next=COMPLETE]
```

---

## Output Files

```
展平元数据输出/              ← Intermediate
└── {name}_chart_proposal.yaml   # 图表推荐草案（含 traceability 段）

{workspace}/                 ← Final（原地修改）
└── <input>.xlsx               # 同一文件，新增图表
```

---

## References

- `references/CHART_TYPES.md` — 18 种图表类型速查（含何时选用 + 数据形状匹配规则）
- `references/CHART_PRESETS.md` — 7 套 preset 样式效果说明 + 被覆盖属性清单
- `references/KNOWN_TRAPS.md` — 已知陷阱（preset 交互 / 系列不可变 / 布局碰撞 / 编码）
- `assets/chart_proposal_template.yaml` — proposal.yaml 的 schema 模板
