# 03 — FILLSPEC.md：layout composition non-goal 一句

**What to build:** 在 `table-fill/references/FILLSPEC.md` 增加一句 non-goal，钉住 FillSpec 执行模型边界：layout/content composition into fixed form regions is outside the FillSpec execution model。目的：防止未来 Agent 看到 `sets` 后产生"再扩两个 props 不就支持 wrapText/行高了吗"的扩张念头。不新增 schema、不新增错误码、不新增 compiler branch。

**Blocked by:** None — can start immediately.

**Status:** resolved

## Agent Brief

**Category:** documentation（边界钉住；零代码变化）

**Authoritative context:** 以父 Spec「Table Fill — Task Shape Routing」D1/D5 为准。本票是 FILLSPEC 的边界声明，不改变任何 schema 语义；现有编译期拒绝行为（SPEC_SOURCE_CSV 等）保持原样。

### Current behavior

- FILLSPEC 定义 `sets` 为"目标级绝对写"、props 白名单 V1=numberformat，但没有一句总纲说明"哪些任务形态不属于 FillSpec 执行模型"。
- 087 讨论中出现过"最小增强 = 纯 sets + props 扩 alignment.wrapText"的诱惑路径（已裁决不做）；缺 non-goal 表述时，未来 Agent 会重新推导同一诱惑。

### Desired behavior

- 在 FILLSPEC 开头或 scope 区增加一句（中英对照即可）：

  > FillSpec models structured grid/record transformations; layout/content composition into fixed form regions is outside the FillSpec execution model. 表单版式组合（多格→一格内容块、图片、行高、wrapText 版面适配）走 officecli native 路径（见 CAPABILITY_EVIDENCE.md 任务形态矩阵）。

- 明确写一句"不为上述形态扩展 props 白名单/schema"，但不新增任何缺陷码与验证逻辑（现有拒绝码已足够表达）。
- 篇幅克制：一句 non-goal + 一行指引，不写 087 案例分析长文。

### Acceptance criteria

- FILLSPEC.md 可 grep 到 non-goal 句与"officecli native"指引；
- FILLSPEC schema 章节零改动（diff 仅新增该 non-goal 段）；
- ticket 04 断言通过（如断言含 FILLSPEC 检查）。

### Comments

- 交付：在 `table-fill/references/FILLSPEC.md` 文件头之后、「## 完整示例」之前新增 `## Non-goal（执行模型边界）` 段（中英对照）。
  内容含：non-goal 原句 + officecli native 指引（见 CAPABILITY_EVIDENCE.md 任务形态矩阵）+「本 spec 不为上述形态扩展 props 白名单 / schema」一句；未新增错误码/schema 字段/compiler branch。
  验证：`git diff` 仅新增该段，schema 章节零改动；`pytest table-fill/tests/test_optimization.py -k DocCoverageGuard -q` 92 passed。
