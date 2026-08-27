# 05 — 双演练验证（087 form_content / 报价单 grid_record）

**What to build:** 对 ticket 01/02 落定的路由指令与判据做纯文本演练验证，不重跑 officecli 编辑：(1) 跑离线文档一致性测试；(2) 用 087 的源文件 + 模板原件跑一次真实 `prepare_run`（outline + flatten）生成 digest，按 SKILL.md 新工作流纯文本执行 Task Shape Check，期望判 `form_content / officecli_native`；(3) 取一个既有 grid workdir（报价单类）的 digest 材料做同样演练，期望判 `grid_record / fillspec` 且运行路径零变化。演练中不得调用 FillSpec 编译器来"试错"。

**Blocked by:** 01, 02, 04 — 演练对象（路由指令/判据/断言）由前三票引入。

**Status:** resolved

## Agent Brief

**Category:** verification（paper rehearsal，不产生交付物、不修改 skill/scripts）

**Authoritative context:** 以父 Spec「Table Fill — Task Shape Routing」Acceptance Criteria 第 3 条为准。087 材料：源 `D:\benchmarks\OmegaUse-OfficeVal\agent-work\officeval_087\input\识字主题分层作业.xlsx`；模板原件 `C:\Temp\tablefill\officeval_087\target_fenceng.xlsx`（注意 `\input\分层作业设计.xlsx` 已被交付覆盖，不可用作模板）。grid 案例从仓库既有 报价单 workdir（如 核价邮件填充毛利汇总测试0812 / 汇总测试0807 等目录下的 prepare 产物）选取，或临时用既有报价源/目标文件跑 prepare。

### Current behavior

- 无验证：路由指令只有文案，未验证"现有 digest 信号是否足以支撑判定"（D3 暂缓加机械信号的前提）。

### Desired behavior

- 步骤：
  1. `pytest table-fill/tests/test_optimization.py`（含 ticket 04 新增断言）全绿；
  2. 087 演练：ASCII workdir（如 `C:\Temp\tablefill\route_rehearsal_087\`）跑 `prepare_run.py --outline` + `--flatten`（源+模板原件），读 digest + 任务文本，按新 SKILL.md 步骤写 Task Shape Check 判定与 evidence 行；期望 `form_content`；
  3. grid 演练：取一个报价单类既有 staged 源/目标（或临时 prepare），同法判定；期望 `grid_record`；
  4. 确认两次演练全程未调用 compile_fill 试错、未用 officecli 编辑任何文件。
- 演练产物只落在演练 workdir 与票内 Comments；不改 skill 文件、不改 scripts。
- 若某演练判定与预期不符：把分歧写回本票 Comments 并回 spec 修订判据（不回编译器），不擅自改文档。

### Acceptance criteria

- 两演练判定与预期一致，evidence 行能支撑判定；
- 离线测试全绿；全程零 officecli 编辑、零编译器调用；
- 结论（含 evidence 摘要）追加到本票 `## Comments`。

### Comments

**验证结论（resolved，2025-07）— 双演练与预期一致，全程零编译试错、零 officecli 编辑。**

**步骤 1 — 离线文档一致性测试**：`python -m pytest table-fill/tests/test_optimization.py -q` → **342 passed in 6.93s**（全绿，含 ticket 04 路由术语一致性断言）。无分歧。

**步骤 2 — 087 form_content 演练（真实 prepare_run）**：
- workdir（新建）：`C:\Temp\tablefill\route_rehearsal_087_v2\`，ASCII；事前无 officecli resident 进程，模板/源文件未被占用。
- `--outline`：源 `source_087.xlsx` 单 sheet `Sheet1`（27行×11列）；目标 `target_087.xlsx` 三 sheet `Sheet1`（9行×6列）+ `Sheet2`/`Sheet3`（空）。
- `--flatten --sheets "source_087.xlsx:Sheet1;target_087.xlsx:Sheet1" --target target_087.xlsx` → FLATTEN_STAGE_DONE，指纹 source `8a02f5ad…` / target `dbb7e3ac…`。
- 源 digest 关键信号：27×11 二维排版内容，**36 合并区**，文字跨行跨格散落（题面/拼音/田字格/插图），**无稳定 header + 重复 record 行**，列全部 METADATA/UNCERTAIN；无自动数据块候选。
- 目标 digest 关键信号：9×6 固定合并表单页，**8 合并区**（A1:F1 标题 / C4:F4、C5:F5、C6:F6 三层内容格 / B7:F9 反馈），**无可克隆数据行模板**，列全 METADATA；无自动数据块候选。
- 任务指令：把源《四季小景》内容（文字 + 25 图）填入固定表单单页，保留 8 合并区与模板格式。
- **判定：`form_content` / `officecli_native`**（Skip MOD/FillSpec）。evidence 摘要：源为跨格跨行组合的版式内容、目标为无可克隆数据行的固定合并区、搬运单位是"多格拼成文本/图片塞固定格"而非行记录→行记录。
- 落盘 `C:\Temp\tablefill\route_rehearsal_087_v2\task_shape.json`。

**步骤 3 — 报价单 grid_record 演练（纸面，复用既有 prepare 产物）**：
- 选用 workdir：`C:\Temp\tablefill\eg_fresh_0818\`（埃及毛利汇总→报价单类；含 prepare_manifest + 3 digest + mod_resolution + fill_spec）。只读，未写其任何文件。
- 任务指令（fill_spec.yaml `task.intent`）：把毛利表两个源 sheet（家用/商用）作为两个新历史块追加进 `11_FRESH本土` sheet，保留既有历史块。
- 源 digest 关键信号（`source_maoli_FRESH_digest.md`）：33×29 稳定多层 header（订单明细/类别/数量/报价/净价/面价/毛利/损益率…）+ **16~17 条重复 record 行**（Z 码机型 Z2U20101081941 等，DIMENSION/MEASURE 列，139 公式）。
- 目标 digest 关键信号（`target_baojia_11_FRESH_digest.md`）：21×24 含**三个可克隆重复数据块** B1/B2/B3（title/header/data 模板行，score 1.0）+ 24 列 header + 公式链模板。
- **判定：`grid_record` / `fillspec`**。evidence：源稳定 header+重复 record、目标有可克隆数据区、映射列↔列、输出行数由源记录数驱动（每块 16 数据行）。运行路径 = 原 MOD → FillSpec → compile → execute → Gate → promote，**零变化**（纸面确认，未实际跑 compile/execute）。
- 演练产物 `C:\Temp\tablefill\route_rehearsal_grid_v2\task_shape.json`（判定 + evidence 摘要；未写既有 workdir）。

**步骤 4 — 零编译 / 零编辑确认**：两次演练全程**未调用 compile_fill.py 试错**、**未用 officecli 编辑任何文件**。087 演练中 prepare_run 的 flatten/digest 属演练的 Prepare 步骤本身（演练对象），非重新编辑；officecli 子进程调用封装在 prepare_run 内部，仅用于机械暂存/outline/digest，非写目标文件。

**分歧**：无。两演练判定与 spec/票预期一致，判据（SKILL.md §1.5 + CAPABILITY_EVIDENCE §0.1）足以支撑判定，现有 digest 信号（合并区、无数据块候选、可克隆数据块、record 行重复度）无需追加 picture/shape 计数即可一眼可判（D3 暂缓成立，未出现需补机械信号的误判样本）。

