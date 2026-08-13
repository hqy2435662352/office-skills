# table-fill-compiler-trust — 编译器信任硬化: 把 Agent 怀疑变成编译裁决

## Comments

- 2026-08-13: 复盘基线归档 (issue 07)。埃及 FRESH 毛利表 → 报价汇总
  11_FRESH本土 单次完整运行的 run_timing 分解: **机器 63s + Agent ~650s** —
  mod_resolution 370s (读 3 digest + 3 展平 CSV + 625 行提名输出含两候选
  全量规则 + 用户裁决墙钟) / spec_authoring 166s / execute_review 55s /
  gate_wait 59s。**事实修正**: XML 勘察发生在 spec_authoring 相位, 不在
  mod_resolution。后续优化基线: mod_resolution 的 370s 由 issue 04 的 MOD
  两段加载 (提名给摘要, 裁决后加载全文) 承接; spec_authoring 的 XML 勘察
  由 issue 03 的 digest 样式粒度事实消除; gate_wait 59s 为墙钟等待, 不属
  优化对象。机器侧 63s 含 prepare 双阶段 + 编译 + 执行 + readback, 未细分。

## Problem Statement

table-fill v2.5 的 Agent 执行效率被"对 compile_fill.py 的不信任"拖累。一次真实运行
(埃及 FRESH 毛利表 → 报价汇总 11_FRESH本土) 的 run_timing 复盘: 机器 63s +
Agent 思考 ~650s。烧脑点全部发生在编译器**没有提供裁决**或**裁决与实际行为
不一致**的地方:

1. **remove_rows 弯路 (~1/3 spec 设计时间)**: Agent 手工模拟 30+ 次 add/remove
   行位移, 怀疑"remove 的目标行号会被先执行的 add 推移, 命中刚插入的新块"。
   probe 实证: **怀疑成立** — 编译器接受 append 克隆 + remove_rows 的 spec,
   产出 add(插入)→remove(裸模板坐标)的自毁 plan, 且无任何检查拦截;
   最终行数断言 (rows + adds − removes) 恒等, 抓不住该错误。
   **正确终态 (测试侧实证)**: append-only、不 remove — 占位行自然下沉保留;
   inplace 在该案例是**错误答案** (占位行是裸行, inplace 填入即无边框块,
   违反 VAL-007 格式沿用)。
2. **V/W 聚合 (3 次 probe + 2 次完整编译)**: 最终方案"单块 + 显式范围聚合"
   编译、执行、readback 627/627 **全部通过**。真正的痛点不是表达能力缺失,
   而是**契约不一致** — capabilities 矩阵声称
   `per_group_total_hardcoded_ranges` → DUPLICATE_TARGET_WRITE, 实际同形
   spec 却通过; 被拒 fixture 里存在另一个未文档化的触发因素 (大概率是聚合
   列进了 nulls 导致锚点双写)。
3. **模板结构勘察**: 最值钱的结构事实 — **占位行是否携带单元格样式**
   (带样式 → inplace 才成立; 裸行 → clone-append, 克隆携带格式) — 没有以
   决策形态出现在 digest, Agent 靠 unzip sheet XML 考古。行号空洞已有两层
   防护 (digest 行洞行 + 编译器 TEMPLATE_ROW_GAP), 本次被顺利拦截, 无需新
   机制。

根因: 现有防护 (probe/capabilities/组合契约/KNOWN_TRAPS) 覆盖"编译接受性",
但 (a) 执行语义正确性没有 oracle, (b) 契约矩阵与实际行为存在漂移 (fixture
与同形 spec 结论相反), (c) digest 缺少样式粒度的决策事实。

## Solution

三个工作流按修正后的实施顺序:

- **工作流 ③ (先行)**: 编译器静态检查 — append 块的 `remove_rows` 必须
  ≤ base_last_row, 违反报新缺陷码 `REMOVE_TARGETS_APPEND_ZONE`;
  **corrective_action 指向 append-only 合法终态** (占位行自然下沉),
  inplace 仅作为"占位行带样式时"的条件选项。配套执行顺序契约 Q&A 与
  回归测试。
- **工作流 ② (其次)**: digest 输出**样式粒度决策事实** — 占位行/克隆源行
  是否携带单元格样式 (带样式/裸行结论), 免掉 XML 考古; 布局决策树以样式
  为条件 (带样式 → inplace; 裸行 → clone-append)。
- **工作流 ① (最后)**: 先修契约文档 — 查清被拒 fixture 的确切触发条件、
  把"每组合计"复制即用模式 (含 V/W 同块显式范围写法) 写进
  combination_patterns.yaml (零编译器改动风险, 3 probe → 0); 之后再做
  group_aggregates 一等化 (正确终态, 但引入新的锚点/合并交互面, 风险等级
  更高, 放最后)。

## User Stories

### 工作流 ③: remove_rows 编译期拦截

1. As a 写 fill_spec 的 Agent, I want append 块声明 remove_rows > base_last_row 时编译器立刻报 REMOVE_TARGETS_APPEND_ZONE, so that 我不必手工模拟行位移来判断安全性。
2. As a 写 fill_spec 的 Agent, I want corrective_action 首选指向「append-only 是合法终态」(占位行自然下沉, 不需删除), so that 我不被误导去用 inplace。
3. As a 写 fill_spec 的 Agent, I want corrective_action 仅在「占位行携带单元格样式」时提示 inplace 为条件选项, so that 裸行占位场景不会得到无边框块。
4. As a 写 fill_spec 的 Agent, I want remove_rows ≤ base_last_row 的经典场景 (源行数 < 模板行数) 保持编译通过, so that 既有运行不受影响。
5. As a 写 fill_spec 的 Agent, I want 该检查覆盖每个 append 块 (含 blocks[] 多块), so that 任何块的 remove_rows 都不会与 add 区交互。
6. As a 读 FILLSPEC 的 Agent, I want 一章「执行顺序保证」说明 op 顺序不变量, so that 我不读源码也能回答执行机制问题。
7. As a 读 mapping.md 的 Agent, I want plan 附带机械事实栏 (removes 全部 ≤ base_last_row 与 add 区无交互), so that 执行前疑问当场蒸发。
8. As a 维护者, I want 该检查有 contract test 背书 (含埃及案例等价 fixture), so that 未来改动不会重新引入自毁 plan。

### 工作流 ②: digest 样式粒度决策事实

9. As a 写 fill_spec 的 Agent, I want digest 报告候选占位行/克隆源行是否携带单元格样式 (带样式/裸行结论), so that 我不必 unzip sheet XML 考古。
10. As a 写 fill_spec 的 Agent, I want 该事实区分「裸行占位」与「带样式占位」, so that append vs inplace 决策有事实依据而非猜测。
11. As a 写 fill_spec 的 Agent, I want 布局决策树以样式为条件 (带样式 → inplace 成立; 裸行 → clone-append 克隆携带格式), so that 决策树不会把我引入错误路径。
12. As a 写 fill_spec 的 Agent, I want 决策树保留「模板既有块收缩 → append + remove_rows (≤ base)」分支, so that 经典场景不被新机制影响。
13. As a 维护者, I want 行号空洞保持既有两层防护不变, so that 本次已生效的机制不被重复建设。
14. As a 维护者, I want MOD 规则全量加载的输出形态优化 (提名阶段给摘要+命中证据, 裁决后才加载选中 MOD 全文), so that mod_resolution 相位的信息过载可控。

### 工作流 ①: 契约一致性先行, 一等能力殿后

15. As a 写 fill_spec 的 Agent, I want capabilities 矩阵对 `per_group_total_hardcoded_ranges` 的结论与实际行为一致, so that 我不被错误结论误导去探索替代路径。
16. As a 写 fill_spec 的 Agent, I want 被拒 fixture 的确切触发条件被查明并写入契约 Q&A (如"聚合列进 nulls → 锚点双写"), so that 同形 spec 的通过与拒绝边界清晰。
17. As a 写 fill_spec 的 Agent, I want combination_patterns.yaml 提供「每组合计」复制即用模式 (含 V/W 同块显式范围写法), so that 新场景零 probe 起步。
18. As a 维护者, I want 契约修正以文档+fixture 注释为主、编译器行为不动, so that 零编译器改动风险。
19. As a 写 fill_spec 的 Agent, I want `group_aggregates` 一等能力 (per-group 聚合写组锚点行 + 跨块总计落点 spike 后解锁) 作为终态存在, so that 报价域每运行必现的组合有声明式表达。
20. As a 写 fill_spec 的 Agent, I want 组聚合与 group_merges 同列按 DUPLICATE_TARGET_WRITE 拒绝, so that 一格一 owner 不变量保持。
21. As a 维护者, I want group_aggregates 的每个契约声明有编译用例背书, so that 文档声称与编译器行为 lockstep。
22. As a 执行 readback 的 Agent, I want 组聚合锚点格自动登记 nonempty 断言, so that 无需手写 checks。

## Implementation Decisions

### 工作流 ③

- 新增缺陷码 `REMOVE_TARGETS_APPEND_ZONE`: append 块 (含隐式单块与 blocks[]
  每个非 inplace 块) 的 `remove_rows` 条目若 > base_last_row 即缺陷; 缺陷
  携带行号、块标签与拦截理由。
- 检查位置: 编译期静态验证段 (布局之后、ops 生成之前), 与既有 anchor/gap/
  out-of-zone 检查同段。
- 语义依据 (锁定): append 块的 add 全部插在 base_last_row 之下, remove_rows
  声明的是模板坐标; 若 remove > base_last_row, 其执行时身份被先行的 add
  推移, plan 自毁。remove_rows ≤ base_last_row 的经典场景与 add 区无交互,
  保持合法。
- **corrective_action 语义 (测试侧修正)**: 首选「append-only 是合法终态 —
  占位行自然下沉保留, 无需删除」; 仅当占位行携带单元格样式时才提示
  mode: inplace 为条件选项 (样式事实来自工作流 ②)。禁止无条件指向 inplace。
- inplace 块的 remove_rows 不在本检查范围 (inplace 消费编译器推导的 Trim,
  不消费 remove_rows)。
- FILLSPEC 新增「执行顺序保证」Q&A 章节: op 顺序不变量 (clear→add→remove→
  merge→fill; add 后 remove 的目标身份; 底上序理由), 每条件配 contract test。
- mapping.md/execution_plan 增加机械事实栏, 从契约派生 (非自由文本)。

### 工作流 ②

- digest/manifest 增加样式粒度决策事实: 对 base_last_row 以下的候选占位行段
  与各 clone_roles 候选源行, 检测单元格级样式 (边框/填充/字体/对齐/数字
  格式) 存在性, 输出结论 — `占位行样式: 裸行` 或 `带样式 (样例坐标)`。
- 检测逻辑放 prepare 阶段 B, 指纹计算不变 (决策事实不入指纹, 旧 spec 不
  因 digest 增强失效)。
- 行号空洞保持既有两层防护 (digest 行洞行 + TEMPLATE_ROW_GAP), 不新增机制。
- FILLSPEC 新增「布局决策树」小节, **以样式为第一判定条件**:
  - 占位行带样式 → inplace 成立 (占位区消费);
  - 占位行裸行 → clone-append (克隆携带格式, VAL-007 格式沿用), 占位行
    自然下沉保留 (append-only 合法终态);
  - 模板既有块收缩 (源行数 < 模板行数) → append + remove_rows (≤ base)。
- MOD 规则加载输出形态优化 (流程纪律): 提名阶段输出摘要 + 命中证据,
  用户裁决后才加载选中 MOD 完整规则 — 缓解 mod_resolution 相位信息过载
  (真实构成: 读 3 digest + 3 展平 CSV + 625 行提名输出含两候选全量规则 +
  用户裁决墙钟)。候选规则全量加载后才允许呈现裁决的硬性要求**不变**,
  改变的是呈现前的加载时机与呈现后的加载粒度。

### 工作流 ①

- **契约修正 (先行, 零编译器改动)**: 查明 capabilities 矩阵
  `per_group_total_hardcoded_ranges` fixture 被拒的确切触发条件 (假设:
  聚合列进 nulls → 锚点双写 "first as empty"), 把触发条件写进 FILLSPEC
  组合行为契约 Q&A 与 fixture 注释; 同形通过 spec 的边界 (单块多显式范围
  聚合 + 聚合列不进 nulls + 不与 group_merges 同列) 文档化。
- combination_patterns.yaml 增加「每组合计 (单块 + 显式范围, 含 V/W 同块
  写法)」复制即用片段, probe 验证"改列名即可编译"。
- **group_aggregates (终态, 最后实施)**: schema 扩展
  `mapping.targets[].formulas.group_aggregates`:
  ```yaml
  formulas:
    group_aggregates:
      - group_by: A            # 物化值分组列 (必须有列映射)
        col: V                 # 聚合落点列 (组锚点行)
        formula: "IFERROR(ROUND(SUM(T{r1}:T{r2})/SUM(S{r1}:S{r2}),4),0)"
        style: anchor
      whole_run:               # 跨块总计落点 (spike 后解锁)
        col: V
        formula: "..."
        rows: last_block_tail  # 落点语义 spike 前锁定为拒绝
  ```
- lowering: 物化 group_by 值 → 连续同值段分组 (复用 compute_groups) → 聚合
  公式展开到组锚点行 → register nonempty readback。
- 冲突语义: 与 group_merges 同列 / 与 per_row 同列 → DUPLICATE_TARGET_WRITE;
  组聚合列不进 nulls; 范围越块 → AGG_RANGE_INVALID。
- whole_run 跨块总计落点语义 (末块尾部 vs 独立行) 需一次 spike 锁定; spike
  前以 `CAPABILITY_NOT_ROLLED_OUT` 拒绝 (与 pptx group_merges 同级门),
  spike 后解锁。per-group 聚合 (不跨块) 不做门。
- 能力映射表「每组合计」行从"暂无一等/变通"改为"一等"; capabilities 矩阵
  同步 (同 _probe_fixtures 驱动)。

### 通用

- 实施目标: 当前 active 的 table-fill skill 安装 (含其 scripts/tests/references),
  变更走其自身 git 仓库提交。
- 所有新缺陷码/契约声明遵循既有模式: 编译期结构化缺陷清单 (code + corrective_action),
  exit 3, 不询问用户。
- 不信任事件转换纪律 (制度): 任何 Agent 对执行机制怀疑超过 ~1 分钟的实例,
  事后强制转换三件套 (编译器检查 + 契约 Q&A + contract test) — 本 spec 三个
  工作流即该纪律的首批产物。

## Testing Decisions

- **好测试的定义**: 只测外部行为 — 编译接受/拒绝、缺陷码、plan 结构、digest
  字段; 不测内部函数细节。
- **模块**: compile_fill (静态检查 + group_aggregates lowering)、prepare/
  structure digest (样式粒度事实)、契约矩阵 (_probe_fixtures + capabilities)。
- **缝合点 (单一化)**: 全部新测试走既有 probe/契约测试面 —
  FillSpecContractTests / CapabilityMappingContractTests / Prepare 系列测试
  的模式, 不新增测试框架或 fixture 基建 (埃及案例 fixture 是既有 fixture 的
  参数变体: 裸行占位与带样式占位两个形态)。
- **Prior art**: row-gap 修复 (`.scratch/table-fill-row-gap/`) 的验收模式;
  test_optimization.py 的契约测试 (文档声称与编译器行为同源断言);
  test_mxp_e2e.py 的 E2E 快照模式 (工作流 ① 的 E2E 验证可选)。
- 回归测试必须含: 越界 remove_rows 拒绝 (③)、裸行 vs 带样式占位行的 digest
  事实差异 (②)、per_group_total_hardcoded_ranges 被拒 fixture 的触发条件
  断言与其同形通过形态 (①)。
- 契约一致性回归: capabilities 矩阵输出的每个条目必须与实际编译结果一致 —
  fixture 漂移类问题 (本 spec 的核心触发事件) 以"矩阵输出 == 实际编译"断言
  闭环。

## Out of Scope

- pptx 的 group_merges / group_aggregates lowering (已按 spike 门 staged)。
- 完整 op 序列 shift 模拟器 (通用执行模拟) — 本 spec 只做静态检查, 不建
  执行器影子模型。
- MOD 捕获/治理变更、V3 runtime governance、trusted host。
- 移除"禁止读源码"纪律 — 保持现状, 由 oracle 覆盖度自然减少违反。
- 每组合计的"组尾小计行"新行插入形态 — 若 spike 显示需要新行, 另行立项。
- 修改 officecli 行为 (remove 的 r 属性定位语义不改)。
- 行号空洞机制的任何扩展 (既有两层防护已足够)。

## Further Notes

- 复盘数据基线: 埃及 FRESH 案例 机器 63s + Agent ~650s; mod_resolution 370s
  构成 = 读 3 digest + 3 展平 CSV + 625 行提名输出 (两候选全量规则) + 用户
  裁决墙钟 — XML 勘察发生在 spec_authoring 相位, 不是 mod_resolution
  (测试侧事实修正)。
- 实施顺序即依赖顺序: ③ (独立) → ② (独立) → ① 契约修正 (独立, 先行) →
  ① group_aggregates (殿后, 依赖契约面)。
- 每个工作流落地后把对应实例写进 KNOWN_TRAPS (权威事实化), 形成下一次
  运行的 oracle。
- 验收标准参考 docs/table-fill-v3-acceptance-standard.md 的精神 (外部行为
  可验证), 但本变更停留在 v2.5 skill 范围。
