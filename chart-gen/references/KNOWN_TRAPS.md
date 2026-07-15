# Known Traps

已知陷阱速查。遇到异常时先查此表。

| # | 陷阱 | 症状 | 原因 | 修复 | 示例命令 |
|---|------|------|------|------|---------|
| 1 | **preset 覆盖手动属性** | 传了 `legend=bottom` 但图例仍在右侧 | preset 自带 legend/colors/gridlines 等默认值，add 时同时传会被静默覆盖 | 两步法：`add` 只传 preset → `set` 覆盖需要的属性。不仅是 legend，colors/gradient/seriesoutline/marker 等同样受影响 | ```bash
# 正确
officecli add file.xlsx /Sheet1 --type chart --prop preset=corporate ...
officecli set file.xlsx /Sheet1/chart[1] --prop legend=bottom

# 错误（不要这样做）
officecli add file.xlsx /Sheet1 --type chart --prop preset=corporate --prop legend=bottom ...
``` |
| 2 | **chart-series 不可变** | 系列名错误或系列顺序不对，试图用 `set` 修改但无效 | officecli 明确不支持 chart-series remove；修改系列结构必须重建整个图表 | 确认阶段仔细检查系列配置。如需改系列，删除图表再重新 add：`officecli remove file.xlsx /Sheet1/chart[1]` 然后重新 `officecli add ...` | ```bash
# 删除错误图表
officecli remove file.xlsx /Sheet1/chart[1]

# 重新创建（确认系列配置正确）
officecli add file.xlsx /Sheet1 --type chart --prop chartType=column --prop dataRange="Sheet1!A1:C13" ...
``` |
| 3 | **多图表位置重叠** | 第二次 chart-gen 后，chart[2] 覆盖了 chart[1] 或部分遮挡 | 两次 chart-gen 调用未协调 anchor，新图表默认位置可能与已有图表重叠 | `anchor=D2:J18` 优于 cm 坐标（绑定到单元格栅格）。Step 1 pre-check 记录已有图表的 anchor，新图表放在数据区域下方空白区 | ```bash
# Step 1 pre-check：记录已有图表位置
officecli query file.xlsx chart --json

# 新图表放在数据下方空白区（假设数据到第 15 行）
officecli add file.xlsx /Sheet1 --type chart --prop anchor=D17:J33 ...
``` |
| 4 | **中文 sheet 名在 dataRange 中** | `dataRange=经营状况概览!A1:C13` 解析失败或图表数据为空 | 中文 sheet 名在 shell 环境中可能因编码解析错误而失败。Python `subprocess.run()` 直接传递字节给 OS，不受 shell 编码影响 | Python `subprocess.run()` 下中文 sheet 名可直接传递（已验证）。若通过 PowerShell/CMD 调用，用双引号包裹：`--prop dataRange="经营状况概览!A1:C13"`。优先使用 ASCII sheet 名 | ```bash
# Python subprocess.run() -- 已验证无需引号
['officecli', 'add', file, '/Sheet1', '--prop', 'dataRange=经营数据!A1:C7', ...]

# PowerShell/CMD -- 需要双引号
officecli add file.xlsx /Sheet1 --type chart --prop dataRange="经营状况概览!A1:C13" ...

# 或改用 ASCII sheet 名（最稳妥）
officecli add file.xlsx /Sheet1 --type chart --prop dataRange="Sheet1!A1:C13" ...
``` |
| 5 | **dataRange 包含空行** | 图表右侧或底部有大片空白，系列线突然中断 | LLM 推断范围过大，包含了总计行下方的空行，导致图表数据区域有空值 | Step 1 首尾采样（内联 `officecli get`），发现空行 → proposal 标记 warning，用户可在 Human Gate 发现并缩小范围 | ```bash
# Step 1 采样验证
officecli get file.xlsx /Sheet1/B2:B4 --depth 0 --json
officecli get file.xlsx /Sheet1/B11:B13 --depth 0 --json

# 若发现空行，缩小 dataRange
# 原：dataRange="Sheet1!A1:C20"
# 修正：dataRange="Sheet1!A1:C13"
``` |
| 6 | **非连续列无法用 dataRange** | 想跳过 B 列只取 A 和 C，但 `dataRange=A1:C13` 把 B 也包含进去了 | `dataRange=A1:C13` 要求连续矩形范围，无法表达"跳过 B 列" | 使用独立参数拆分：`categories=Sheet1!$A$2:$A$13` + `series1.values=Sheet1!$C$2:$C$13` + `series2.values=Sheet1!$E$2:$E$13` | ```bash
# 非连续列：拆分为独立参数
officecli add file.xlsx /Sheet1 --type chart \
  --prop chartType=column \
  --prop categories="Sheet1!$A$2:$A$13" \
  --prop series1.values="Sheet1!$C$2:$C$13" \
  --prop series1.name="销售收入" \
  --prop series2.values="Sheet1!$E$2:$E$13" \
  --prop series2.name="利润"
``` |
| 7 | **set 命令按 chart 索引定位，容易搞错对象** | 想改新图却 set 到了旧图；饼图被设置了 `outsideEnd` 报错 | `officecli set` 使用 `chart[N]` 索引，但新图索引取决于已有图表数量，不会总是 `chart[2]` | `set` 之前先 `officecli query <file> chart --json`，按 `title` 或 `anchor` 找到真实索引，再对该索引 set | ```bash
# 先 query 确认索引
officecli query file.xlsx chart --json
# 假设目标图是 chart[4]
officecli set file.xlsx /Sheet1/chart[4] --prop legend=bottom --json
``` |
| 8 | **数据标签格式随数据源变化** | 源数据从静态整数改成公式引用后，图表数据标签从小数变整数或反之 | `dataLabels` 的 number format 不随数据源自动同步；源数据类型/格式变化时标签格式可能漂移 | 修改源数据后，显式检查并重新设置 `dataLabels` 的 number format；必要时用 `officecli set ... --prop dataLabels=...` 修正 | ```bash
# 修改源后检查标签格式，必要时重新设置
officecli set file.xlsx /Sheet1/chart[1] --prop dataLabels=outsideEnd --json
``` |
| 9 | **辅助表应使用公式引用原始数据** | 辅助表里是写死的整数，客户无法追溯数据来源；原始数据更新后图表不同步 | 为了图快直接填入静态值，放弃了可审计性和自动同步 | 辅助表单元格应使用公式引用原始数据，如 `=Sheet1!B2`，而不是写死数字 | ```bash
# 在 Excel / WPS 中把辅助表单元格设为公式
# AB8: =Sheet1!B2
# AB9: =Sheet1!B3
# 让图表引用辅助表 AB8:AB13
``` |

---

## 快速诊断索引

| 症状 | 对应陷阱 # |
|------|-----------|
| 图例位置不对 | 1 |
| 系列名/顺序错误且无法修复 | 2 |
| 新图表遮挡旧图表 | 3 |
| 中文 sheet 名图表无数据 | 4 |
| 图表有大片空白区域 | 5 |
| 非连续列被错误包含 | 6 |
| set 到了错误的 chart / 饼图被设 outsideEnd 报错 | 7 |
| 改数据源后数据标签格式变了 | 8 |
| 辅助表数字无法追溯来源 | 9 |

## 预防性检查清单

每次创建图表前确认：

1. **preset 和手动属性分开**：`add` 阶段只传 preset，样式微调在 `set` 阶段
2. **系列配置在 Human Gate 仔细核对**：一旦创建，系列结构不可变
3. **anchor 不与已有图表重叠**：Step 1 pre-check 查询已有图表位置
4. **中文 sheet 名加双引号**：`--prop dataRange="中文名!A1:C13"`
5. **dataRange 首尾采样验证**：空行警告 → 缩小范围
6. **非连续列提前识别**：改用 categories + seriesN.values 拆分
7. **set 前必须 query**：按 title/anchor 确认真实 chart 索引，禁止假设固定索引
8. **改数据源后检查数据标签格式**：必要时重新设置 dataLabels number format
9. **辅助表使用公式引用原始数据**：禁止写死数字，确保可审计和同步
