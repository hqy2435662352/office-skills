# 05 — 四案例演练（动作不变式验收 + 哨兵变异验证）

**What to build:** 对 Routing V2 做四案例纯文本演练（复用 V1 ticket 05 模式：只跑 prepare_run 读取 + 纯文本判定，不 compile/execute/officecli 写文件，不改 skill/scripts）：Case 1 复杂报价单（复用 V1 演练材料或既有 grid workdir）→ 预期 `grid_record/fillspec` evidence=["obvious_grid"]，**验收重点 = 进入 MOD 前的工具动作序列与 V1 基线完全一致**（routing 增量 0 的可观测证据）；Case 2 087 → `form_content/officecli_native` + ["content_composition","layout_or_object_work"]；Case 3 scratch 合成 3~5 cell 小映射 → `grid_record/officecli_native` + ["bounded_explicit_edit","no_material_grid_benefit"]；Case 4 scratch 合成或取真实 Grid+Logo/备注案例 → `mixed/combined` + 两 code + 分区边界说明。另做哨兵变异验证（删 obvious_grid / 矩阵改回 SUPPORTED / combined 改回 hybrid → 测试变红后还原）。

**Blocked by:** 01, 02, 03 — 演练对象（新路由指令/两表/哨兵）由前三票引入。

**Status:** resolved

## Agent Brief

**Category:** verification（paper rehearsal；产物只落演练 workdir 与本票 Comments）

**Authoritative context:** 以父 Spec「Table Fill — Routing V2」R2-Q8 锁定与 Acceptance Criteria 第 3/4 条为准。087 材料：源 `D:\benchmarks\OmegaUse-OfficeVal\agent-work\officeval_087\input\识字主题分层作业.xlsx`，模板原件 `C:\Temp\tablefill\officeval_087\target_fenceng.xlsx`（`\input\分层作业设计.xlsx` 已被交付覆盖不可用）。grid 案例复用 V1 演练（`C:\Temp\tablefill\route_rehearsal_grid_v2\`）或既有报价单 workdir。

### Current behavior

- V1 只演练过 087（form_content）与报价单（grid_record）两个形态；Direct 与 Combined 判据未经真实 digest 检验。

### Desired behavior

- 步骤：
  1. 跑 `pytest table-fill/tests/test_optimization.py`（含 ticket 03 哨兵）全绿；
  2. 按四案例逐案：ASCII workdir 跑 prepare_run（outline+flatten，Case 3/4 缺真实文件时在 scratch 用 officecli 建两个最小 xlsx，不触碰任何 skill/staged 文件）→ 读 digest + 任务文本 → 按新 SKILL.md §1.5 落 task_shape.json（含 evidence code）→ 记录判定；
  3. Case 1 动作不变式核对：列出进入 MOD 前的工具调用序列，与 V1 演练时的一致（0 新增）；
  4. 哨兵变异验证（ticket 03 范围内挑 2-3 个变异做一次红→还原）；
  5. 全程不调用 compile_fill 试错、不做 officecli 写文件编辑。
- 演练产物落 `C:\Temp\tablefill\route_rehearsal_v2_*\`；结论与 evidence 摘要追加本票 `## Comments`。
- 若某案例判定与预期不符：分歧写回本票 Comments 并回 spec 修订，不擅自改文档。

### Acceptance criteria

- 四案例判定与预期一致；Case 1 动作序列与 V1 基线一致（routing 零增量证据成立）；
- 变异验证记录在案且已还原；离线测试全程全绿；
- 全程零 compile 调用、零 officecli 写编辑。

### Comments

**验证结论（实施中，2026-08）— 四案例判定全数与预期一致；Case 1 动作不变式成立（routing 增量 = 0）；哨兵变异验证 3 项红→还原全部确认；离线测试全程全绿（349 passed）；全程零 compile_fill 试错、零 officecli 写编辑。**

**步骤 1 — 离线测试基线**：`python -m pytest table-fill/tests/test_optimization.py -q` → **349 passed**（首跑 8.65s；全部变异还原后终跑 11.47s，同为 349 passed）。全绿无分歧。

**步骤 2 — 四案例逐案（产物只落 `C:\Temp\tablefill\route_rehearsal_v2_*`，仓库内零写入）**：

- **Case 1 复杂报价单（纸面演练，复用 V1 材料）** → `grid_record` / `fillspec` / `["obvious_grid"]` ✓（与预期一致）
  - workdir：`C:\Temp\tablefill\route_rehearsal_v2_case1\`（`task_shape.json` + `verdict.md`）。
  - 材料：只读复用既有报价单 workdir `C:\Temp\tablefill\eg_fresh_0818\`（prepare_manifest + 家用/商用两个源 digest + 目标 `11_FRESH本土` digest + fill_spec.yaml task.intent APP-001），未新跑 prepare_run。
  - digest 关键信号：源 33×29（家用，16~17 条 record 行，139 公式）/ 23×28（商用，12~13 条 record 行）+ 目标 21×24 **三个可克隆数据块** B1/B2/B3（score 1.0，clone 行模板 title/header/data）+ 24 列表头与公式链模板 → 三信号齐备一眼可判。
  - **Case 1 动作不变式验收（进入 MOD 前动作序列 vs V1 基线）**：见下「动作序列对比表」——4 步序列与 V1 完全一致，**0 新增动作**（无新 LLM 调用 / officecli / inspect / render / 脚本 / scoring / decomposition / feature extraction），evidence 由 V1 长句改为固定单 code `["obvious_grid"]`（spec R2-Q2 设计落地，非新增动作）；判定后按 stop-rule 立即进 MOD，未跑 compile/execute。
- **Case 2 087（真实 prepare_run）** → `form_content` / `officecli_native` / `["content_composition","layout_or_object_work"]` ✓（与预期一致）
  - workdir：`C:\Temp\tablefill\route_rehearsal_v2_case2\`（含完整 prepare 产物 + `task_shape.json` + `verdict.md`）。
  - 材料：真实源 `D:\benchmarks\OmegaUse-OfficeVal\agent-work\officeval_087\input\识字主题分层作业.xlsx` + 模板原件 `C:\Temp\tablefill\officeval_087\target_fenceng.xlsx`；跑 `--outline` + `--flatten --sheets "source_087.xlsx:Sheet1;target_087.xlsx:Sheet1"` → FLATTEN_STAGE_DONE。
  - digest 关键信号：源 27×11、**36 合并区**、文字跨行跨格散落、全列 METADATA、无数据块候选；目标 9×6、**8 合并区**（标题/三层内容格/反馈区）、无可克隆数据行模板 → 搬运单位是"多格拼内容塞固定格"。
  - **指纹与 V1 完全一致**（source `8a02f5ad…` / target `dbb7e3ac…`）——源/模板未变，digest 可复现。
- **Case 3 3~5 cell 小映射（scratch 合成）** → `grid_record` / `officecli_native` / `["bounded_explicit_edit","no_material_grid_benefit"]` ✓（与预期一致）
  - workdir：`C:\Temp\tablefill\route_rehearsal_v2_case3\`（合成 fixture `source_small.xlsx` 2行×3列表头+1 record、`target_small.xlsx` 固定报价小单，写集合 = B3/D3/F3 三格；+ prepare 产物 + `task_shape.json` + `verdict.md`）。
  - 材料：officecli **新建**两个最小 xlsx（仅 scratch 演练目录，未编辑任何既有文件）；跑 prepare_run。
  - **Direct 双必要条件逐条核查**：① 自检句"OfficeCLI batch 本身能不能成为完整执行计划"→ **能**（3 个固定 set 即完整计划，无动态推导）；② 无需 Grid 专业能力（无 record-driven iteration / clone / placeholder / inplace / lookup / formula / aggregate / group merge / 结构治理）→ 全过。
  - 备注：源确为"表头+record"形态（grid 语义），但因写集合 bounded/explicit 且无 Grid 实质收益 → Direct，与「Applicability ≠ Justification」反例定义一致；触发层 single-cell 排除不适用（3 格 > 1 格）。
- **Case 4 Grid + Logo/备注（scratch 合成）** → `mixed` / `combined` / `["substantial_grid_workload","separable_non_grid_workload"]` ✓（与预期一致）
  - workdir：`C:\Temp\tablefill\route_rehearsal_v2_case4\`（合成 fixture `source_products.xlsx` 41×5、40 条 record + 40 公式；`target_quote.xlsx` 报价单模板：A1 Logo 区（行高60）/ A3:B3 客户名称 / A5:E45 明细区（表头+3 样例行+37 空行）/ A47:B47 备注区（行高40）；+ prepare 产物 + `task_shape.json` + `verdict.md`）。
  - digest 关键信号：源 41×5 稳定表头 + 40 记录 + 公式链模板 `D{r}*C{r}`（substantial grid）；目标同时含固定内容位（LOGO/客户名称/备注）与明细表区（两类 workload 并存）。
  - **分区边界（Combined 契约"清晰可分离"核验）**：Grid 段归 Grid 执行器 = 明细区 A5:E45（表头行 5 固定、数据行 6..45 由源 40 记录驱动克隆、E 列公式链模板复制）；non-grid finishing 段归 officecli = 上区 A1:B3（Logo/客户名称固定格）+ 下区 A47:B47（备注）+ 行高（row1/row5/row47）。两段坐标区间互不相交、无共享 side effect、finishing 不触碰 Grid-owned region / structural invariants → **缠绕度为零，清晰可分离** → 判 combined（高度缠绕才进 uncertain，本案例不适用）。执行契约（Grid first → finishing → 统一 QA → 单一 Final Gate）纸面确认。
  - 备注：V2 首个 mixed/combined 判定样本（§0.2 第 4 行 Direct/Combined 行的「待第一条真实案例 evidence」标注——本案例为 scratch 合成，仍属 canonical 起步；真实案例 evidence 待 benchmark 补充）。

**步骤 3 — 哨兵变异验证（3 项，红→逐字节还原→绿）**：

| # | 变异点 | 临时改动 | 目标哨兵 | 红（FAILED） | 还原确认 |
|---|---|---|---|---|---|
| (a) | SKILL.md §1.5 删 `obvious_grid`（4 处全替换为 `FASTPATH_GRID_ONLY`） | `test_skill_md_obvious_grid_fast_path` + `test_skill_md_task_shape_routing_terms` | **2 failed**（缺 obvious_grid evidence code / 缺路由词） | 备份还原后 **2 passed** |
| (b) | CAPABILITY_EVIDENCE §0.1 能力表 grid 行 `APPLICABLE` 改回 `SUPPORTED` | `test_capability_evidence_task_shape_matrix_terms` | **1 failed**（grid 行 FillSpec Model 列必须是 APPLICABLE 不是 SUPPORTED） | 备份还原后 **1 passed** |
| (c) | SKILL.md §1.5 路由值域表 mixed 行 `combined` 改 `hybrid` | `test_skill_md_routing_route_domain_three_values_no_hybrid`（另 `test_skill_md_routing_legal_shape_route_combinations` 未红——其守的是 ASCII 分流块，非值域表行，符合预期） | **1 failed**（mixed 合法 route 必须是 combined；hybrid 不得出现） | 备份还原后 **1 passed** |

- 还原方式：逐项 Copy-Item 还原开工前备份副本；结束时 SHA256 三文件（SKILL.md / CAPABILITY_EVIDENCE.md / test_optimization.py）与开工前快照**逐字节一致**（AAC311EB… / 297D9518… / F1FAC8E9…），且 `git diff`（工作树 vs HEAD）对 SKILL.md + CAPABILITY_EVIDENCE.md 的字节哈希与开工前快照一致（E2F0024B…，identical-to-start=True）——**变异零残留**。
- 纠偏记录：变异 (a) 首轮误用 `obvious_grid_mut`（含 `obvious_grid` 子串）未触发红——证实哨兵为子串匹配；改用 `FASTPATH_GRID_ONLY` 后按预期变红。

**步骤 5 — 全程纪律确认**：未调用 compile_fill 试错；officecli 仅用于 Case 3/4 scratch 合成 fixture **新建**（src 9/210 ops、tgt 7/33 ops），未编辑任何既有文件；仓库内零写入（唯一写入 = 本票 `## Comments` 追加）；未 commit、未改 git 配置。

**分歧**：无。四案例判定与 spec/票预期全部一致；判据（SKILL.md §1.5 + CAPABILITY_EVIDENCE §0.2）足以支撑全部判定。两点观察留档：① Case 3/4 为 scratch 合成（无真实 benchmark 案例可复用），digest 无自动数据块候选（候选检测依赖更强的行样式重复，非路由缺陷——判定输入三要素本就含任务文本 + LLM 读 digest 信号型判据）；② 087 指纹与 V1 完全一致，确认源/模板未漂移、digest 可复现。

**验收记录（resolved，主 agent 逐票验收）— 验收通过。**

- **测试（主 agent 独立复跑）**：`pytest table-fill/tests/test_optimization.py -q` → **349 passed**（含 ticket 03 全部哨兵），与实施者记录一致。
- **四案例产物核实**：`C:\Temp\tablefill\route_rehearsal_v2_case{1..4}\` 均存在 `task_shape.json` + `verdict.md`（Case 2/3/4 含完整 prepare 产物——digest/outline/candidates/flat/meta；Case 1 为纸面演练复用 eg_fresh_0818，与 V1 方式一致）。
- **Case 1 动作不变式**：进入 MOD 前序列（prepare 产物复用 → 3 digest 读取 → 三输入判定 → 落 task_shape.json）与 V1 基线逐项一致，无新增 inspect/render/评分/分解/feature extraction/脚本/LLM 调用；evidence 为固定单 code `["obvious_grid"]`——routing 增量 = 0 的可观测证据成立。
- **哨兵变异验证**：3 项（删 obvious_grid / APPLICABLE→SUPPORTED / combined→hybrid）红→逐字节还原→绿，SHA256 与 git diff 双重证明零残留；加上 ticket 03 的 4 项，spec AC4 五类变异全部覆盖。
- **仓库范围**：git status 与开工基线比对——M 文件集合完全一致，无任何越界写；唯一仓库内写入 = 本票 Comments；演练产物全部在 C:\Temp。
- **结论**：验收通过，Status 置 resolved。spec 头部注记已同步为 resolved（实施授权已给出、五票按序执行完毕、349 passed、零触碰 FILLSPEC/scripts）。
