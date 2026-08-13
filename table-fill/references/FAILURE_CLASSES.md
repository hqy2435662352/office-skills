# FAILURE_CLASSES.md — 执行/验证失败缺陷分类与标准修复

`execute_batch.py` 失败时在 `_draft_failure.json` 输出
`defect_class` / `defect_classes` / `standard_fix` 字段。**拿到失败记录直接按本表修复,
不要逐格反向侦查** — 标准修复已给出, 逐格侦查是低效路径 (且每次侦查都消耗
repair 预算内的验证次数)。
修复路径: 修 **fill_spec.yaml** → `compile_fill.py` → `execute_batch.py`。
**修复是预期步骤, 不询问用户、不提供放弃选项**; 预算只约束连续失败
(第 2 次失败才重新分类为 ASK/STOP)。

## 缺陷类别 → 特征 → 标准修复

| defect_class | 识别特征 | 标准修复 | 例证 |
|--------------|---------|---------|------|
| `text_overflow` | `view issues` 报 "text overflow: N lines ... need Xpt, usable Ypt" | ①直接写入的数值溢出 → **编译期已拦截** (`NUMERIC_OVERFLOW_RISK`, 加 `transform: round4`) ②派生公式浮点残值 → `ROUND(...,2)` **且只加在减法/乘法/除法/SUM 上, 纯加法 (R=P+Q) 不加** (加在加法上会把结算价 168.7151 截成 168.72, 毛利漂移 18.37) ③公式返回空串 → **改 0-口径公式链** ④`set col width` 加宽 (末选)。⚠️ **行高根因**: 模板数据行固定 `customHeight=20pt`, 15 位成本值/残值超列宽换行即触发; **"旧块也这样"不是豁免** — issue delta 按路径计数, 新格全算新增, 必须真消除换行。| 2026-08-10: 15 位面价触发; round4+ROUND 精准修复后 issues=0, R 精确成立 |
| `empty_cell` | 关键格读回为空 / issue 标记空值 | ①克隆残留未置空 → nulls 显式声明 ②填充遗漏 → 核对 columns 映射 | 新机型行 L/M 克隆残留 0 值 (需 nulls) |
| `formula_residue` | 新块非锚点单元格残留克隆公式, validate/issues 均不报 | 数据克隆源改为**非锚点数据行** (同格式即可); spec 的 data template_row 禁用合并区锚点行 | 11_FRESH本土 曾克隆行 9 (A9:A11 锚点) 携带 `SUM(T25:T32)` 公式残留 |
| `merge_residue` | officecli 报 merge overlap/already exists/anchor 冲突 | `merge:true` 前对数据行相关列 `set merge:false` (Compiler 自动做); 新锚点补 font/alignment 样式 | 删行后残留单格合并使新合并被拒 |
| `formula_error` | 公式 not evaluated / #REF! / #VALUE! / #DIV/0! | ①引用范围与跨 sheet 前缀 (`Sheet!B13` 全名) ②浮点残值 → `ROUND(...,2)` ③IFERROR 零分母保护 | (报价−成本)×数量 浮点积溢出列宽 |
| `order_violation` | officecli batch 报排序违规 | 用 `compile_fill.py` 重新生成 plan — 脚本内置 clear→add→remove→merge→fill 全局排序 | 手写 batch 按 sheet 分段触发 |
| `prop_rejection` | unknown property / ambiguous / rejected | batch props 用完整点号名 (`font.color`/`fill`); 键名查 `officecli help xlsx set cell`; 十六进制色去 `#` | batch 内 `color:` 被拒 |
| `path_permission` | Access denied / Permission denied | ASCII workdir; 文件非只读 (`force_writable`); 中文路径先 stage | Windows 中文路径 set/batch Access denied |
| `duplicate_row` (2026-08-10) | XML 出现两个 `<row r=N>`, 部分格读写落错元素 | 根因: 在 add 操作之间穿插 cell 写入 (如标题克隆后立即写 value) 破坏 officecli 行簿记。修复: 所有 cell 写入移到 add/remove 之后 — **Compiler 已内置此排序**; 若复现, 检查 fill_spec 是否经 compile_fill.py 生成 plan | 标题 value 写在 data add 之前 → row 25 重复 |
| `unknown` | 无法自动分类 | 读失败记录原始字段 + KNOWN_TRAPS.md 人工判定 | — |
| `row_anchor_missing` | officecli 报 `Anchor row N not found` | 行号空洞: 目标 sheet row 元素 r 值不连续, `add after/from /row[N]` 锚点不存在 → 用 `scripts/repair_row_gaps.py --workdir <dir>` 物化缺失行 → 重跑 `prepare_run.py --flatten` (指纹变化) → 更新 spec 指纹 → 重编译 → 重执行 | 埃及模板 1..21, 23..52 缺 22 |

## v2.5 验证失败码

`DRAFT_VERIFY_FAILED` 记录里的 `structural_failures` 与 `RENDER_QA_FAILED`
记录携带以下机器码 — 按此表定向修复, 不要自由实验:

| code | 识别特征 | 标准修复 | 例证 |
|---|---|---|---|
| `FINAL_ROW_COUNT_MISMATCH` | structural_failures 报 expected != actual 行数 | ①trim/overflow 方向与数据量不符 → 核对源匹配行数与 capacity ②selectors 误配/漏配行 ③重编译重执行 (budget 1 轮, 再失败 ASK/STOP) | MXP 占位区 capacity 18 vs 13 产品 → trim 5, 期望 35 行 |
| `GROUP_BOUNDARY_MISMATCH` | 列上实际合并区集合 != 推导集合 (validate 对合并残留视而不见) | ①group_by 物化值出现意外同值段/杂散值 → 修数据或 selectors ②merges 与 group_merges 同列混用 ③残留合并未拆 → 确认 lowering 逐行 unmerge (含单格残留 A19:A19) | 删行后单格残留卡住重合并 |
| `RENDER_QA_FAILED` | `--render` 产物生成失败 (exit 3) | ①png 失败 → 降级 `--render html` (纯文本模型只做结构渲染检查, 不得声称视觉验证 — 属 budget 内一次 ADAPT) ②html 也失败 → 核对 region (plan.render_qa.region) 与文件路径 ③修复后重跑 | 无渲染后端 / 路径含中文 |

## 修复后验证循环

1. 修 fill_spec.yaml (含 decisions/gaps 更新)
2. `compile_fill.py` → exit 0 (静态验证先行, 不生成 plan 则继续修)
3. `execute_batch.py --round N+1` → exit 0
4. **预算**: 首次修复是预期路径, 不询问用户; 第 2 次连续失败才重新分类 —
   多个安全解释 → ASK, 无可证明安全计划 → STOP (不得静默再修, 也不得以
   "时间/复杂度"为由放弃、简化任务或绕过门禁)。

## 编译缺陷速修 (compile_fill.py exit 3 — stderr 聚合列出, 一次修完)

编译缺陷是 **REPAIR 类**: 按 stderr 的 corrective_action **一次性修完清单上
全部缺陷** → 重编译, **不询问用户**。

| 错误码 | 标准修复 |
|---|---|
| `SUM_NON_NUMERIC` | 多列求和遇到非数值/空 → 改用单列映射, 或先确认该列是数值列 (含 `-`/空按 0 是既有语义) |
| `CLONE_SOURCE_IS_ANCHOR` | data template_row 是合并区锚点 → 换同格式的非锚点数据行 (锚点行携带锚点公式) |
| `DUPLICATE_TARGET_WRITE` | 同一格被写两次 → 检查 columns/nulls/formulas/merges/sets/group_merges 重叠, 每格只留一个 owner |
| `SELECTOR_INVALID` | selector 列越界 → 对照 digest 列宽修正列字母 |
| `NO_MATCHED_ROWS` | selector 匹配 0 行 → 修 pattern 或检查源展平/来源名 |
| `REQUIRED_COVERAGE_UNMATCHED` | 必需源行未消费 → 修 selectors 或记入 gaps |
| `NUMERIC_OVERFLOW_RISK` | 直接值 >4 位小数/>12 位有效数字 → 加 `transform: round4` 或 `precision: keep` (编译期拦截, 不等执行期溢出) |
| `PRECISION_KEEP_NARROW_COLUMN` | `precision: keep` 列最宽渲染值超出 prepare 实测列宽 → 改用 `transform: round4` (keep 需要列宽实测背书, 不再执行期才发现溢出) |
| `TRANSFORM_UNKNOWN` / `LOOKUP_*` / `FILLSPEC_FINGERPRINT_MISMATCH` 等 | 按 stderr corrective_action 定向修 |

## 防再犯 (写 spec 时就避免)

| 缺陷 | 预防动作 |
|------|---------|
| text_overflow | 派生公式默认 `ROUND(...,2)`; 长精度值直接写 4 位小数 |
| formula_error 浮点 | per_row/aggregate 公式模板带 ROUND + IFERROR |
| merge_residue | merge/aggregate 列由 Compiler 自动全行 `merge:false` |
| order_violation / duplicate_row | 一律走 compile_fill.py, 禁止手写 batch.json |
| empty_cell / formula_residue | data template_row 选非锚点行; 未填列进 `nulls` |
| FINAL_ROW_COUNT_MISMATCH | capacity/start_row 照 digest 声明; selectors 精确表达业务过滤 |
| GROUP_BOUNDARY_MISMATCH | group_by 用目标列映射; 每列只用一种合并模式 |
| RENDER_QA_FAILED | 纯文本模型默认 `--render html`; region 走 plan.render_qa.region |
