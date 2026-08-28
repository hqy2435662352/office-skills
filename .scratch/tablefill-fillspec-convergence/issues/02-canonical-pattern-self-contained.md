# 02 — canonical pattern 三串全洗（运行时文本自包含）

Status: resolved
Type: doc（`table-fill/assets/combination_patterns.yaml`，纯文本改动）
依据: spec.md §2 事实 2。本 session 11:33 命中 `multiproduct_block_append`
后，pattern note 里的 "真实执行证据…见 docs/test-cases/case-005 与
case-006 复盘" 直接诱导了 11:34 起的 case 重读链（stop-rule 违规的第一
步）。

## 清洗标准（grilling 已裁决）

> canonical pattern 应该告诉 Agent **"怎么做"**，而不是"以前谁这么做过"。
> Pattern 自包含其结构语义，只依赖正式 contract，不依赖历史成功案例证明
> 自己。

**保留（规范性指针 — 回答"正式规则在哪里"）**：

- FILLSPEC 章节 / 「组合行为契约」Q# 引用（如 Q5/Q8/Q12/Q19/U4）；
- KNOWN_TRAPS 条目引用（U1/U4/U7 等机械事实编号）；
- 本文件内兄弟 pattern 互引（如 `per_group_total_explicit_ranges`、
  `zero_policy pattern`）与"见本文件头部说明"；
- 替换表注释、结构锁定段、fragment 本体 — 全部功能性，不动。

**删除（历史性/证明性指针 — 回答"为什么相信它"）**：

- Case 编号叙事（"真实任务 Case 05/06"、"Case 08 (核价邮件 → MXP
  17_MXP…2026-08-18)" 等）；
- `docs/test-cases/*`、`tests/*` 文件路径指针；
- issue 号引用（"issue 02/03"）；
- 入库日期标注（"2026-08-16/19 入库"）；
- **契约测试名**（`MultiproductBlockPatternContractTests` 等）— 测试覆盖
  属开发维护信息，由测试代码自身维护，不进 Agent 运行时阅读面；
- 执行数据叙事（"compile 一次过 → execute readback 627/627"）。

**不做替换句**：不要写"已由真实任务 Validated Draft E2E 背书"之类的资历
句 — pattern 的权威来自它作为仓库契约的地位，不来自出身。清洗后 note 只
保留：适用条件、结构行为、必要的规范性引用。

## 范围

三个完整 Canonical Pattern 全洗（同类诱饵不留存活实例）：

1. `multiproduct_block_append`（本 session 实际咬人的一个）；
2. `preformatted_quotation_inplace`；
3. `single_quotation_block_append`。

局部片段（feature snippets，如 `zero_policy` 内联的 "Case 07 §8 教训" 标签）
不在本票范围 — 那是教训标签而非阅读指针，且无路径可点。文件头部注释对
Agent 同样是可见阅读面：其中若含具体历史指针（tests/ 路径、issue 编号、
Case 编号、日期），**同步去除这些具体指针**，但保留抽象准入标准语句本身
（如 "Canonical Pattern 入库须有编译用例/Validated Draft 背书"）— 仍是
一次文本 scrub，不重新设计维护者文档。

实现时逐行判断的试金石：**这个引用被删除后，Agent 实例化骨架会缺信息吗？**
会 → 是规范性指针，保留；不会 → 删。

## Acceptance

- [x] 三个 canonical pattern 的 question/answer/note 及其内部注释中无
      Case 编号、无 `docs/`/`tests/` 路径、无 issue 号、无入库日期、无
      测试类名、无执行数据叙事、无资历替换句。
- [x] 文件头部注释中无具体历史指针（tests/ 路径、issue 编号、Case 编号、
      日期），抽象准入标准语句保留。
- [x] 所有保留下来的引用只指向：FILLSPEC 章节/Q#、KNOWN_TRAPS 条目、本
      文件内兄弟 pattern/头部说明。
- [x] fragment/替换表注释：先 grep 确认 — 不含历史性文本则逐字未动；
      若个别含历史指针，仅删该指针、结构语义零改动。
- [x] 结构锁定段的事实内容（U4、ROUND 精准、selectors 守卫等）逐条仍在，
      只去了历史标签。
- [x] YAML 仍可解析（`python -c "import yaml;yaml.safe_load(open('table-fill/assets/combination_patterns.yaml',encoding='utf-8'))"`)。

## Comments

### 验收记录（主 Agent，2026-08-31）

子代理执行 + 主 Agent 独立复核，6/6 PASS（13 处编辑，仅触碰
`table-fill/assets/combination_patterns.yaml`，diff 15+/25-，无 commit）：

1. 三串 canonical pattern（preformatted_quotation_inplace /
   multiproduct_block_append / single_quotation_block_append）的
   question/answer/note 与内部注释：Case 编号（Case 05/06/08）、
   docs/test-cases/、tests/ 路径与契约测试类名（Multiproduct*/Single*、
   FillSpec/CapabilityMapping ContractTests）、issue 号（issue 02/03）、
   入库日期（2026-08-16/19/18）、执行数据叙事（"compile 一次过 → execute
   readback 627/627"）全部清除；无新增资历替换句。主 Agent grep 复核：
   仅 zero_policy 内 "Case 07 §8"（票明示范围外）与头部抽象准入语存留。
2. 头部注释：删 tests/ 路径 + 2 测试类名 + "(issue 02)"，抽象准入标准
   （①②③ 与"并有编译用例背书"）保留。
3. 保留引用清点（主 Agent 复核）：FILLSPEC「组合行为契约」/「blocks: 多
   数据块」、Q5/Q8/Q12/Q19、U1/U4/U7、兄弟 pattern 互引
   （per_group_total_explicit_ranges / zero_policy / multiproduct_block_append）、
   "见本文件头部说明"×3 — 全部在白名单内。两难裁决合理：⑥"见 SKILL「公式
   约定」"不在白名单且规则已内联 → 删；(Case 07 F1) 的 F1 是 Case 体系悬空
   标签 → 整体删。
4. fragment：preformatted 与 multiproduct 逐字未动；single 替换表注释
   (U7, 见 issue 03) → (U7)。
5. 结构锁定段 ①-⑧（multiproduct）/①-⑦（single）事实全部保留（template_row
   非合并锚点、label-only、nulls、V 显式范围+Q12、U4、块级 formulas 取代、
   ROUND 精准、data_start 取行号、selectors 守卫）。
6. YAML 解析验证：python yaml.safe_load 通过。

结论：ticket 02 验收通过，Status: resolved。
