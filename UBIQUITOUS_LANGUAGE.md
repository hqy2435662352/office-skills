# Ubiquitous Language

## Table-fill MOD lifecycle

| Term | Definition | Aliases to avoid |
| --- | --- | --- |
| **Table Fill** | The controlled workflow that maps source-table data into a formatted Office-table target. | Table copy, table migration |
| **MOD** | A declarative, versioned business-rule and acceptance-criteria profile used by Table Fill. | Plugin, script, executor |
| **Primary MOD** | The single MOD selected for one Table Fill run in phase one. | Combined MOD, default MOD |
| **MOD Gate** | The approval point between classification and mapping where the user selects or confirms the business interpretation. | Mapping gate |
| **Execution Gate** | The approval point after mapping where the user authorizes the concrete mapping and batch plan. | MOD Gate |
| **Direct Migration** | A traceable 1:1 source-to-target transfer that does not alter business meaning. | Simple transformation |
| **Business Transformation** | Aggregation, unit conversion, period derivation, cross-table joining, or formula logic that changes business interpretation. | Direct migration |
| **MOD Capture** | A single-confirmation action that registers or updates a private MOD after verified delivery, using a prepared run-local MOD Markdown file. | Auto-learning, MOD Update Proposal |
| **MOD State** | The run-scoped record of MOD candidates, selection, rule approvals, overrides, and revision. Revision is local history for single-user recovery, not concurrency control. | Shared MOD, runtime guess |
| **MOD Acceptance Item** | A short business-specific check added to the existing Table Fill gates after a MOD is selected. | Validation engine |
| **Private MOD** | A customer-owned MOD containing customer-specific rules, data sources, or sensitive context. Capture is private-only in Phase 3.5. | Public MOD |

## Relationships

- A **Table Fill** run selects zero or one **Primary MOD** in phase one.
- A **Primary MOD** may influence a mapping proposal only after the **MOD Gate** confirms it.
- An **Execution Gate** authorizes a concrete mapping proposal, not the business profile itself.
- A **MOD Capture** is performed only after a verified run produces reusable rules and the user chooses to preserve them in a private MOD.
- A **MOD State** is the only MOD selection source that mapping and verification may consume during a run.
- A **MOD Acceptance Item** supplements, rather than replaces, Table Fill's existing output verification.
- A **Private MOD** is self-contained; there is no public-base inheritance or shadowing.
- Without a **Primary MOD**, only **Direct Migration** may execute automatically; every **Business Transformation** needs explicit approval.

## Table Fill V3 governance runtime

| Term | Definition | Aliases to avoid |
| --- | --- | --- |
| **Workflow Runtime** | The trusted, host-independent state authority for Governed Runs, Gate lifecycle, protected paths, MOD snapshots, and authorization decisions. | Skill script, broker service |
| **Host Adapter** | A thin host-specific integration that binds one Host Session to one Governed Run, classifies host tool calls, blocks unauthorized protected writes, and submits Human Decisions to the Workflow Runtime. | Runtime, generic plugin framework |
| **Governance Mode** | The runtime capability classification for a run: `HOST_GOVERNED`, `SKILL_ONLY`, or `UNSUPPORTED`. | Security level |
| **HOST_GOVERNED** | The only Governance Mode that guarantees fail-closed enforcement of protected writes and trusted Human Gate confirmation. | Plugin enabled |
| **SKILL_ONLY** | A compatibility mode in which the Skill may guide the workflow but no enforcement guarantee is claimed. | Soft governance, partially governed |
| **UNSUPPORTED** | A mode in which required host capabilities are unavailable and the requested governance contract cannot be provided. | Broken mode |
| **Governed Run** | A registered Table Fill execution whose phase, artifacts, protected paths, Gate state, and Host Session binding are owned by the Workflow Runtime. | Chat, task, process |
| **Host Session** | The host's conversation or execution session identity. A Governed Run is bound to exactly one current Host Session. | Run ID |
| **Run Resume** | The explicit transition that binds a Governed Run to a new Host Session and immediately invalidates the old binding. | Reopen chat, implicit continuation |
| **Protected Path** | An exact normalized absolute path registered by a Governed Run as subject to write governance. | Workspace glob, output folder pattern |
| **Protected Write** | A host tool call classified as capable of mutating one or more Protected Paths. | Office write, execution step |
| **Protected Write Guard** | The Host Adapter mechanism that permits or rejects a Protected Write by consulting current Workflow Runtime state. | Broker, daemon, action service |
| **Write Classification** | The host-specific result assigned to a tool call: `READ_ONLY`, `WRITE(paths)`, or `UNKNOWN`. | Command allowlist |
| **UNKNOWN Write** | A tool call whose mutation behavior or target paths cannot be positively established; it is rejected in a Governed Run when it may reach protected state. | Probably safe call |
| **Human Decision** | A response collected through the host's native human interaction surface. It is evidence submitted by the Host Adapter, not authority asserted by the Agent. | Agent confirmation |
| **Gate Confirmation** | A Workflow Runtime transition that accepts a Human Decision for a pending Gate only when the Host Session, run binding, Gate identity, and basis digest all match. | Question response, approval message |
| **Gate Basis** | The canonical facts, choices, proposed mapping, rules, or effects presented for a Human Gate decision. | Prompt, summary text |
| **Basis Digest** | A deterministic digest of the Gate Basis used to reject stale or mismatched confirmations. | File hash, prompt hash |
| **Gate Invalidation** | The transition that makes downstream Gate confirmations unusable after an upstream governed artifact or decision changes. | Warning, recheck suggestion |
| **Run-Local MOD Snapshot** | The immutable MOD revision captured for one Governed Run after selection; later catalog revisions do not alter that run. | Live MOD, cached lookup |
| **Fail-Closed** | The rule that a protected operation is rejected when classification, authority, binding, or state is unknown, stale, or invalid. | Warn and continue |
| **Clean Cutover** | The V3 state policy that starts new Governed Runs in the V3 format without migrating V2 run state while preserving reusable MOD assets. | Backward-compatible state migration |
| **Active Skill Baseline** | The immutable installed Skill tree at `C:\Users\Administrator\.config\opencode\skills\table-fill` used as the read-only source for candidate cloning. | Live skill, current skill |
| **Candidate Skill** | The repository-local Skill tree at `table-fill-v3/` produced by cloning the Active Skill Baseline and changing only the frontmatter `name` to `table-fill-v3`. | New skill, replacement skill |
| **Candidate Clone Manifest** | A canonical relative-POSIX-path inventory: files record type, size, and SHA-256; directories record zero size and null SHA-256; the tree digest hashes the UTF-8 canonical JSON. It proves the Candidate matches the Baseline except for the parsed name change. | File list, copy log |

## V3 governance relationships

- A **Workflow Skill** explains the Table Fill procedure; the **Workflow Runtime** owns trusted run and Gate state; the **Host Adapter** enforces host-specific boundaries.
- A **Host Adapter** may request a **Human Decision**, but only the **Workflow Runtime** may commit a **Gate Confirmation**.
- A **Gate Confirmation** is valid only for the matching **Governed Run**, **Host Session**, Gate identity, and **Basis Digest**.
- A Layer 1 or Layer 2 change invalidates dependent downstream Gates. A Layer 3 change is governed by the **MOD Gate**, and delivery remains governed by the **Execution Gate**.
- A **Protected Write Guard** governs only exact registered **Protected Paths**; it is not a workspace-wide policy engine.
- `SKILL_ONLY` and `UNSUPPORTED` never claim the guarantees of `HOST_GOVERNED`.
- A **Run Resume** replaces rather than supplements the previous **Host Session** binding.
- A **Run Resume** retains prior Gate records for audit but makes their confirmations non-consumable under the new binding.
- A **Run-Local MOD Snapshot** remains stable even when the shared MOD Catalog advances to a newer revision.

## Table Fill routing (Task Shape Check — Routing V2)

| Term | Definition | Aliases to avoid |
| --- | --- | --- |
| **task_shape** | workload 本质，值域 `grid_record` / `form_content` / `mixed` / `uncertain`，**不代表执行方式**；与 route 正交（归属：`table-fill/SKILL.md` §1.5；`table-fill/references/CAPABILITY_EVIDENCE.md` §0.2）。 | route, execution path |
| **route** | 当前 run 的执行选择，值域仅 `fillspec` / `officecli_native` / `combined`；与 task_shape 正交，同一 shape 可有不同 route（归属：`table-fill/SKILL.md` §1.5；`table-fill/references/CAPABILITY_EVIDENCE.md` §0.2）。 | task_shape, mode |
| **grid_record** | 稳定 header + 重复 record 行 + 可克隆数据区的 workload；映射以列↔列为主、输出行数由源记录数驱动；默认 Fast Path 走 `fillspec`，Direct 例外走 `officecli_native`（归属：`table-fill/SKILL.md` §1.5；`table-fill/references/CAPABILITY_EVIDENCE.md` §0.1）。 | （误用作）文件本身 |
| **form_content** | 固定内容区（merged form regions）、无可克隆数据行模板、源内容需跨格/跨行组合的 workload；一等路径 `officecli_native`，FillSpec 侧 `NOT_APPLICABLE`（归属：`table-fill/SKILL.md` §1.5；`table-fill/references/CAPABILITY_EVIDENCE.md` §0.1）。 | layout 填充 |
| **mixed** | Grid + Non-grid 的**组合 workload**，需两套执行模型协作，仅走 `combined`（归属：`table-fill/SKILL.md` §1.5；`table-fill/references/CAPABILITY_EVIDENCE.md` §0.1-0.2）。 | hybrid |
| **uncertain** | 无明确信号时的**临时判定态**，不是稳定类型；不落执行 route，受限补观察后必须重判（归属：`table-fill/SKILL.md` §1.5；`table-fill/references/CAPABILITY_EVIDENCE.md` §0.2）。 | fallback 默认项 |
| **fillspec** | route 值之一：本次经 FillSpec 执行（MOD → FillSpec → Compile → readback → 结构验证 → QA → Gate 全链），是 `grid_record` 的默认/Fast Path 路由（归属：`table-fill/SKILL.md` §1.5；`table-fill/references/CAPABILITY_EVIDENCE.md` §0.2）。 | 第三引擎（误） |
| **officecli_native** | route 值之一：本次经 OfficeCLI（inspect → atomic edit → adjust）执行、不经 FillSpec；是 `form_content` 与 `grid_record` Direct 的路由（归属：`table-fill/SKILL.md` §1.5；`table-fill/references/CAPABILITY_EVIDENCE.md` §0.2）。 | fallback, script |
| **combined** | route 值之一：FillSpec + OfficeCLI 的**组合执行**（Grid 段 + OfficeCLI finishing，单一 Final Gate 锁最终 draft），**不是第三引擎**（归属：`table-fill/SKILL.md` §1.5「Combined 最小契约」；`table-fill/references/CAPABILITY_EVIDENCE.md` §0.2）。 | hybrid（消歧见 Flagged ambiguities） |
| **applicability** | 适用性：执行模型能否**自然表达**该 workload（`APPLICABLE` / `NOT_APPLICABLE`），能力语义表维度（归属：`table-fill/references/CAPABILITY_EVIDENCE.md` §0.3；`table-fill/SKILL.md` §1.5）。 | justification |
| **justification** | 启动理由：即使适用，本次是否**值得启用完整 pipeline**，路由决策表维度（归属：`table-fill/references/CAPABILITY_EVIDENCE.md` §0.3；`table-fill/SKILL.md` §1.5）。 | applicability |
| **obvious_grid_fast_path** | 明显 Grid（稳定 header + 重复 record + 可克隆数据区）读毕 digest 立即 `grid_record` + `fillspec`、evidence 固定 `["obvious_grid"]`、直进 MOD 的默认**主路径**（不是 fallback）；**禁止继续 routing 分析**、0 新增动作（归属：`table-fill/SKILL.md` §1.5「Level 0」）。 | default-to-grid fallback |

**边界：evidence code 不进 glossary** — `obvious_grid` / `bounded_explicit_edit` / `no_material_grid_benefit` / `content_composition` / `layout_or_object_work` / `substantial_grid_workload` / `separable_non_grid_workload` 及 uncertain 三码（`insufficient_routing_evidence` / `conflicting_workload_signals` / `task_intent_ambiguous`）是**判定标签**，归 `table-fill/references/CAPABILITY_EVIDENCE.md` §0.4 词表，一律不在本 glossary 注册；词表不封闭，仅在真实 benchmark 反复出现时才晋升。

## Flagged ambiguities

- “MOD” means a declarative business profile; it must not imply executable custom code in phase one.
- “Human Gate” has two distinct purposes: the **MOD Gate** selects business semantics, while the **Execution Gate** approves concrete file changes.
- “Approval” is too broad for V3 state transitions. Use **Human Decision** for the host-collected answer and **Gate Confirmation** for the runtime-accepted transition.
- “Protected output” must name exact **Protected Paths**; it must not imply an entire workspace, directory glob, or every file mentioned in chat.
- “Resume” must mean **Run Resume** with binding replacement; reopening or continuing a chat is not sufficient.
- “Safe tool” must not replace **Write Classification**. Only positively proven `READ_ONLY` calls bypass protected-write authorization.
- “**combined**”（table-fill routing）≠ FILLSPEC “**hybrid overflow**”（`references/FILLSPEC.md:853`）：后者是 inplace 位置模型的克隆溢出（占位区填满后从 `template_row` 克隆 N−capacity 行接在占位区之后），与本路由概念无关；`hybrid` 不作为 route 名出现。
