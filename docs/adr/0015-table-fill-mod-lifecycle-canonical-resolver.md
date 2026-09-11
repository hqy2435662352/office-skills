<!-- 编号沿续: docs/adr/ 现有最大 0014 (Axis-neutral Grid Routing); 本 ADR 为
     上一轮 04 (Runtime 证据边界) 的 MOD 侧收口 + 本轮 Ticket 05 决议。 -->

# 0015-table-fill-mod-lifecycle-canonical-resolver

# MOD Lifecycle Decouple + Canonical MOD Resolver in Table Fill

## Status

Accepted on 2026-08-31 as part of the axis-neutral grid runtime feature
(`.scratch/table-fill-axis-neutral-grid-runtime`, Ticket 05, Phase B). This
ADR decouples the MOD lifecycle from the execution route and locks MOD
consumption to a single canonical version.

## Context

The real parameter-sheet session exposed two coupled failures:

1. **route-gated MOD disappearance**: `form_content → officecli_native →
   跳过 MOD` dropped the matched business MOD
   (`MOD_tcl_internal_parameter_to_customer_parameter_sheet`) wholesale — Z 码
   身份解析、字段白名单、受控翻译、CJK 扫描等业务治理规则全部缺失，用户
   必须人工打断才把 MOD 拉回。MOD 适用性由执行引擎选择决定，而不是由业务
   语义决定。
2. **double-source drift**: the agent globbed a same-name MOD file and read a
   `.scratch/` copy instead of the canonical `references/MOD_*.md` version —
   the runtime resolver read the canonical version, so the two sources could
   silently diverge (agent reasoning on one, runtime on another).

`form_content` 本身是合法 shape 值；问题是「shape → 跳 MOD」的耦合，不是
form_content 的判定。MOD 是业务语义层（权威源、字段政策、transform 政策、
validation 政策），与执行引擎选择正交 — FillSpec 语境的 `NOT_APPLICABLE`
只表示引擎层不用 FillSpec，不是业务规则不适用。

## Decision

1. **MOD 生命周期与 route 解耦 (hard contract in SKILL.md §2)**: 生命周期
   固定为 Prepare → Pre-MOD Evidence → Task Shape → MOD Nomination/Resolution
   → 加载 selected MOD 规则 → 业务推导 → 选择/执行 executor。「form_content
   → 跳过 MOD」不再是合法路径；命中业务 MOD 时业务治理规则照常加载并进入
   业务推导。
2. **Canonical MOD Resolver**: 新增 `scripts/_mod_resolver.py` —
   `mod_resolution.json` 每条候选/选定记录至少含 `{name, canonical_path,
   revision, sha256}`；`canonical_path` 一律由目录表派生
   （`references/` + MOD_INDEX 的 Path 列），`sha256` = 写盘时 canonical
   文件内容哈希。脚本与 Agent 只消费 resolver 返回的 canonical 版本：
   读取 canonical_path 锁定的文件并校验 sha256 / revision / 路径包含性，
   不匹配/缺失/畸形 → fail-closed（结构化 defect + corrective_action，
   CLI 经 `fail()` 以 exit 3 结束，绝不回退到同名副本）。禁止 glob 同名
   文件；scratch / history / legacy skill 副本不是合法 MOD 来源。
   `load_rules_for_selected_mod()` 经 resolver 加载两段加载第二段；
   `mod_nominate.py --check-canonical` 提供机械复核入口。
3. **向后兼容 (显式、可测试)**: 旧 mod_resolution.json（或缺新字段的记录）
   在目录表可解析时 **re-resolve from catalog**（`resolution_action:
   "re-resolved_from_catalog"` 被记录）；目录表无法解析 → fail-closed
   （`MOD_CANONICAL_FIELDS_MISSING`，corrective_action 点名缺失字段）。
   既有调用形态 `load_rules_for_selected_mod(mods_dir, path)` 保留为旧式
   直连（含路径包含性校验；目录表给出时 path 必须 == Path 列）。
4. **两段加载保留**: 提名阶段只给候选名 + 信号 + 业务逻辑摘要（不含完整
   规则集），用户裁决后才经 resolver 加载 full rules。
5. **编译期 C2/C3 语义不变**: `compile_fill.py` 的 C1–C4 仍只读
   status/selected/selected_revision；新字段（候选 canonical_path/sha256、
   记录级 selected_canonical_path/selected_sha256）是纯增量，compile 自然
   容忍。不做 resolution-hash 绑定进 spec/fingerprints（ADR-0011 C5 明确
   不在范围内）。

## Considered Options

- **Route-gated MOD applicability（shape 命中才加载 MOD）** — rejected:
  参数表事故的根因正是 shape→MOD 耦合；MOD 是业务语义层，适用性由业务
  语义（目录表信号 vs 任务/结构证据）决定，不由 executor 选择决定。
  form_content 路径同样需要 Z 码身份/白名单/受控翻译/CJK 扫描等业务规则。
- **Glob-based resolution（按文件名全局搜索 MOD）** — rejected: 同名副本
  正是双源漂移的来源；glob 结果不携带修订/完整性证明。canonical_path +
  revision + sha256 三锁把「哪个文件是权威」变成机器可验证的字面事实,
  不再依赖 Agent 的查找纪律。
- **编译期加入 resolution-hash 绑定 (C5 式)** — rejected (out of scope,
  与 ADR-0011 一致): Compiler 只校验 MOD 引用 vs 裁决一致性，不校验裁决
  过程；canonical 校验落在 resolver / `--check-canonical` 消费边界。
- **旧记录一律 fail-closed** — rejected (部分): 无条件拒绝旧记录会让
  既有工作目录/测试失去向后兼容；选择「目录表可解析 → re-resolve 并记录
  动作；不可解析 → fail-closed 点名字段」的显式二分。

## Consequences

- MOD 规则内容只有一个权威来源：canonical 文件（canonical_path + sha256
  锁定）；同名字 scratch/history/legacy 副本在机械边界被拒绝
  （MOD_CANONICAL_PATH_INVALID / MOD_CANONICAL_HASH_MISMATCH /
  MOD_PATH_NOT_CANONICAL / MOD_CANONICAL_PATH_DRIFT）。
- `form_content` 与 MOD 解耦后，shape 误判不再删除业务治理规则；后续
  Ticket 06/07（Matrix FillSpec / Semantic Gate）可以信任「命中 MOD 的规则
  已进入业务推导」作为前置。
- 双源证据由 fixture 固定：canonical 与同名 scratch 内容不同的双源
  fixture，resolver/machinery 只读 canonical（sha256 校验）。
- 旧记录行为显式化并测试化（re-resolve 记录 action / fail-closed 点名
  字段）；既有 MOD 测试保持绿色（新增字段纯增量）。
- 真实业务文件 replay 本轮明确排除；fixture 驱动流程测试 + SKILL 契约
  文字是该验收的替代（见 Ticket 05 记录）。