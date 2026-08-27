# 02 — CAPABILITY_EVIDENCE.md：任务形态×执行路由两层矩阵

**What to build:** 在 `table-fill/references/CAPABILITY_EVIDENCE.md` 固化两层能力语义矩阵与判据：`grid_record: table-fill=SUPPORTED / FillSpec=SUPPORTED / executor=fillspec`；`form_content: table-fill=SUPPORTED / FillSpec=NOT_APPLICABLE / executor=officecli_native`。附 form_content 判据清单（任务指令 × 源结构 × 目标结构），并记录 087 作为矩阵第一条 evidence（含编译器拒绝码实测事实）。措辞锁死：`NOT_APPLICABLE`，不得写成 `UNSUPPORTED` 或 `Known Rejected`。

**Blocked by:** None — can start immediately.

**Status:** resolved

## Agent Brief

**Category:** enhancement（能力语义文档；无代码行为变化）

**Authoritative context:** 以父 Spec「Table Fill — Task Shape Routing」D1/D5 锁定决策为准。本票维护 CAPABILITY_EVIDENCE.md 作为能力求证的唯一详细 policy 源这一既有契约——矩阵新增章节，不拆散既有三态/Evidence Fit/Standard Evidence Paths 结构。

### Current behavior

- CAPABILITY_EVIDENCE.md 只有三态（Known Supported / Known Rejected / Capability Unknown），没有"任务形态 × 执行路由"这一层产品语义。
- 087 讨论中曾出现把 form_content 写成 Known Rejected 的表述——但 D1 已裁决：form_content 是产品支持的路径，只是不适用 FillSpec 执行模型。
- 缺少防漂移表述：未来 Agent 可能把 `NOT_APPLICABLE` 解读为"capability gap 待补"（→ 诱发向 FillSpec 塞 wrapText/行高/图片的扩张），或把 087 当作定义本身而非证据。

### Desired behavior

- 新增矩阵章节（权威表述）：

  ```text
  Task Shape      table-fill Product   FillSpec Engine     Executor
  -----------------------------------------------------------------------
  grid_record     SUPPORTED            SUPPORTED           fillspec
  form_content    SUPPORTED            NOT_APPLICABLE      officecli_native
  ```

- `form_content` 判据清单（信号，非打分）：目标主体是固定内容区（merged form regions）且无可克隆数据行模板；源内容需跨格/跨行组合后才能写入；主要操作是文本/图片/版面内容填充而非重复 record 映射；判定始终结合**任务指令**（同一文件不同任务可走不同路径）。
- 087 作为第一条 evidence 记录：拒绝码实测（`SPEC_SOURCE_CSV` compile_fill.py:2375、`CLONE_SOURCE_IS_ANCHOR`/`CLONE_RESIDUE_UNHANDLED`、inplace 1:1 约束、`PROPS_WHITELIST=("numberformat",)` compile_fill.py:1314）+ 一句结论（这些拒绝码是引擎正确表达"非 record/grid 世界"，不是缺陷）。标注：证据 ≠ 定义。
- 措辞纪律明文化：`NOT_APPLICABLE` ≠ `UNSUPPORTED`（后者暗示"欠 feature"）≠ `Known Rejected`（后者指"契约已拒绝的行为"）。Form_content 是另一条一等执行路径。

### Acceptance criteria

- 矩阵与判据在 CAPABILITY_EVIDENCE.md 中可被 grep 到；术语与 SKILL.md（ticket 01）一致；
- 全文中 form_content 未与 Known Rejected/UNSUPPORTED 绑定表述；
- ticket 04 新增断言通过；
- 未改动 `--capabilities`、三态终局算法、Probe/Rescue 合同等既有章节语义。

### Comments

- **交付 (ticket 02 完成)**: 在 `table-fill/references/CAPABILITY_EVIDENCE.md`
  新增开篇章节「## 0. 任务形态 × 执行路由（两层能力语义）」, 落地四部分:
  (1) 两层能力矩阵 (原文含 Task Shape / SUPPORTED / NOT_APPLICABLE / Executor /
  fillspec / officecli_native); (2) §0.1 form_content 信号型判据清单 (任务指令 ×
  源结构 × 目标结构); (3) §0.2 第一条 evidence 087 (SPEC_SOURCE_CSV@2375、
  CLONE_SOURCE_IS_ANCHOR / CLONE_RESIDUE_UNHANDLED、inplace 1:1、PROPS_WHITELIST
  =("numberformat",)@1314 + "非 record/grid 世界"结论 + 证据≠定义标注);
  (4) §0.3 措辞纪律 (NOT_APPLICABLE ≠ UNSUPPORTED ≠ Known Rejected, form_content
  是另一条一等路径)。既有三态/Evidence Fit/Standard Evidence Paths/Probe/Rescue/
  终局动作/词句章节零改动, 仅新增章节与段落。DocCoverageGuard 测试子集 92 项
  全绿。遗留 `## Comments` 前的空行在状态行后已按 Markdown 标准补一行, 无语义影响。
