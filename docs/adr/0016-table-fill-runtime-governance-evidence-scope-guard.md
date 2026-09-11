<!-- 编号沿续: docs/adr/ 现有最大 0015 (MOD Lifecycle Decouple + Canonical
     Resolver); 本 ADR 为上一轮 04 (Runtime 证据边界) 的治理收口 + 本轮
     Ticket 09 决议 (四条治理契约 + 合法业务事实来源枚举)。 -->

# 0016-table-fill-runtime-governance-evidence-scope-guard

# Runtime Governance / Evidence Scope Guard in Table Fill

## Status

Accepted on 2026-08-31 as part of the axis-neutral grid runtime feature
(`.scratch/table-fill-axis-neutral-grid-runtime`, Ticket 09, Phase D). This
ADR locks the four runtime governance contracts plus the legal-business-source
enumeration as stable SKILL contract text, and records why contract text (not
a file-read firewall) is the enforcement mechanism.

## Context

The real parameter-sheet session turned the user into a Runtime Supervisor:
the agent recursively searched the whole repository for a legacy skill
(MOD 设计上应自包含)、在 Prepare/MOD 之前用 Python/openpyxl 直接建输出、
PDF 抽取/管道探测等自由探索 — 直到用户四次打断（"按流程用 officecli"、
"这是 grid 加载 MOD"、"用 task 机制"、"别看其它文件夹"）。上一轮
04 (Runtime Evidence Boundary) 只立了「tests 非业务 SoT」一条边界；本轮
把治理扩展为四条互不扩大的契约 + 合法来源枚举，并明确第一版不建文件读取
防火墙（spec Out of Scope）。

## Decision

1. **Runtime / Development Asset Boundary + 合法业务事实来源枚举**
   (SKILL.md §1.6 第一块): tests/fixtures/benchmark expected/historical
   snapshots 默认只属于 Skill Development / Regression Oracle —
   **Tests prove HOW, not WHAT**；「test 夹具里这么写」不作来源解释。
   Runtime 业务事实只来自六类合法来源: 用户指令 / Primary（源文件）/
   Template（目标模板）/ selected MOD / MOD 受控规则与 lookup / 用户显式
   指定资产；唯一例外 = Skill Development 语境（用户明确把主要目标改为
   开发/评测本 skill）。
2. **Source Scope Guard** (§1.6 第二块): 用户声明来源边界时,
   semantic/filesystem search 限制在 declared scope + table-fill runtime
   code/docs + selected MOD；禁止无授权 `Get-ChildItem -Recurse` 级全盘
   递归搜索 legacy skill / 历史输出作为业务答案；越界搜索被记录/阻止
   （诊断 + 视严重度 fail-closed）。
3. **Runtime Tool Contract** (§1.6 第三块): 禁止无授权 openpyxl 直写业务
   执行 — Python 仅限 Skill Development / diagnostics / tests；officecli
   调用必须经 `_officecli.officecli()` 适配器（不变量 6 保持, 与
   ADR-0006 trusted-host 精神一致）。
4. **Exploration Stop Rule** (§1.6 第四块): obvious_grid → routing probes
   = 0（既有 Fast Path stop-rule 保留）；非 obvious 有限预算（受限补观察 +
   结构补充 probe ≤ 1 次）；预算耗尽仍不确定 → ask / ambiguous；禁止无限
   view/render/script/glob/legacy search 直到「感觉理解」。
5. **Barrier Enforcement** (§1.6 第五块): Task Shape / MOD Resolution
   完成前不允许 field mapping / business selector / output generation —
   与 ADR-0011 (Business Reasoning Barrier, §1.4) 呼应，不重复改写其
   「禁止做」清单。
6. **机制选择: 契约文字, 不是防火墙** — 第一版不建文件读取防火墙（spec
   Out of Scope 显式禁止）；落实 = SKILL 硬契约 + contract test（pin
   措辞，改动变红）+ session replay（行为最终裁判）。MOD 侧双源由
   ADR-0015 的 canonical resolver（canonical_path + revision + sha256）
   机械代偿。每个契约块给稳定 `#### ` anchor，供 contract test 钉住。

## Considered Options

- **文件读取防火墙 / 新访问控制子系统** — rejected: spec Out of Scope 显式
  禁止；且防火墙拦得住「读不到」，拦不住「读进来再当作业务答案」——证据
  权威是行为契约问题，不是 IO 权限问题；防火墙还违反既有 phases
  （staged 只读 + ZIP/XML 直读 + officecli 适配器）的分层。
- **机械探索计数器（runtime 探测预算执行器）** — rejected: 探索预算的消费
  方是 LLM 路由判定（Fast Path 判定输入只有 evidence/manifest，0 探测），
  机械计数器既捕获不了「读同一文件三遍」式的隐性探索，又变成新子系统；
  预算以契约形式表述并由 contract test + replay 验收。
- **只依赖 replay，不写 contract test** — rejected (部分): replay 是行为
  的最终裁判，但改动时不自动变红；contract-text pin 给回归红绿灯，
  replay 负责真实会话行为验收，两者互补。
- **把 tests 值排除做成编译期校验（spec 级黑名单）** — rejected: 编译器
  不应知道「tests 是什么」；依赖链本就没有通向 tests 的路径（值只从
  manifest 绑定的 staged flattened CSV 物化，见 Ticket 09 记录），把
  不存在的问题机械化只会制造伪规则。

## Consequences

- SKILL.md §1.6 五块契约文字落盘，每块独立 `#### ` anchor；contract test
  （`tests/test_runtime_governance.py`）与既有 Layer-3 风格 test 一起 pin
  措辞 — 措辞/段落被改或删除时变红。
- ADR-0011 仍是 §1.4 Business Reasoning Barrier 的权威；§1.6 只呼应用
  「Task Shape / MOD Resolution 完成前不得映射」的闸门顺序，不重写清单。
- 「tests 值不回灌」由 compile-flow mini fixture 证明（machine-proven:
  值只从 staged flattened CSV 物化、input_hashes 绑定 staged 二进制、
  指纹漂移 fail-closed）；「tests 不可能是 SoT」的禁止本身是契约固定
  （runtime 代码没有 tests 概念）。机器可证 vs 契约固定的完整边界见
  Ticket 09 记录。
- scripts/（runtime 层）无 openpyxl 导入是机器可证的（静态审计），openpyxl
  仅存在于 Skill Development 的测试夹具生成器 — 与「Python 仅限 Skill
  Development / diagnostics / tests」一致。
- 真实业务文件 replay（evidence authority 检查）本轮明确排除（用户禁令）；
  替代 = 契约文字 pin + tests 值不回灌 mini fixture（已记录在 Ticket 09）。