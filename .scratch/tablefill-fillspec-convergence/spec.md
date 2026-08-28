# Spec: table-fill 首版收敛与契约去歧（fillspec-convergence）

Status: ready-for-agent
Date: 2026-08-28 (grilling session + reviewer revision pass, `grilling` + `domain-modeling` skills)
Related: ADR-0012 (`docs/adr/0012-table-fill-first-draft-convergence.md`)

## 1. 背景：一次真实 session 的时间账

Case: `eg_fresh_local`（毛利表 → 报价汇总 `11_FRESH本土` 追加家用/商用双块，
handoff 见 `C:\Users\Administrator\AppData\Local\Temp\handoff_filltask_eg_fresh_local.md`，
任务本身已 Execution PASS、Gate 待确认）。

| 指标 | 值 |
|---|---|
| 任务总墙钟 | 89m43s（10:55:59 → 12:25:42） |
| 用户等待（2 次 ASK） | 23m09s |
| Agent 有效时间 | ≈66m34s |
| **首次写出 fill_spec.yaml 前** | **≈63m03s（≈95%）** |
| FillSpec → Compile → Execute → Gate | ≈3m31s |
| canonical pattern 命中（11:33）→ FillSpec 落盘（12:22） | ≈43m39s（扣除 ASK 等待） |
| 用户说"别想那么多了，快开始写吧"之后仍未写 | ≈31m |
| pre-spec 模型输出 tokens | ≈260k / 全程 ≈270k（≈96%） |
| 最终 Compile 3 轮总耗时 | 数十秒（R1 key_column crash → R2 clone residue → R3 SUCCESS） |

结论：性能问题不在 Compiler/Executor（它们只占 3.5 分钟且工作良好），
而在 **canonical pattern 已精确命中、MOD 已给出业务规则之后，Agent 仍持续
重新推导 mapping / routing / inheritance / grouping，迟迟不产出首版
FillSpec**。E1（manifest 污染，两次污染+恢复近 20 分钟）与第二次多余 ASK
（D/F/X inherit-vs-null，FLD-008 已裁决）都是这一根因的症状。

## 2. 根因分析：不是缺规则，是规则站错了位置 + 契约有缺口

复盘后的关键事实（已逐条对照当前仓库文件验证）：

1. **stop-rule 已存在但只覆盖 Compile repair**。"命中 canonical pattern →
   直接实例化…不再读 case 复盘 / 测试病历重推组合"写在 SKILL.md §4
   （compile 缺陷预算上下文，约 L505-507）。本 session 漂移发生在 §3
   FillSpec **首次撰写**阶段——第一次 Compile 之前，该规则从未变得适用。
2. **pattern 文件自带历史诱饵**。`combination_patterns.yaml` 三个完整
   Canonical Pattern 的 note 都含"见 docs/test-cases/case-XXX 复盘"、Case
   编号、日期、issue 号、契约测试名——告诉 Agent"以前谁这么做过"，而不是
   "怎么做"。
3. **inheritance 输入与 fill 源无契约边界**。全仓库没有任何地方写"辅助
   lookup/inheritance sheet 不得通过 `prepare_run --flatten` 进入当前 fill
   manifest"。本 session 据此把 `01_埃及机型`/`09_Fresh拖多` flatten 进
   manifest → `manifest.target` 被改写、fingerprints 重算 → 两次污染+恢复链
   ≈20 分钟。KNOWN_TRAPS 已有"索引自引用"条目但那是另一个失效模式。
4. **`key_column: sku` → 裸 IndexError**（compile_fill.py:1163）。机器能
   机械判断的非法输入被当成了 crash，而本 session 正是这个 crash 之前的
   "怕失败"心理加剧了 pre-spec 预证明行为。

同时确认：table-fill 主流程（Prepare → MOD → FillSpec → Compile → Execute →
Gate）本身没有缺失，既有"先写后编译"设计是对的——Compile 3 轮几十秒就是
证据。**不需要新的治理层，需要把已有主链路跑直。**

## 3. 目标 / 非目标

**目标**（第一批，全部低成本、零/近零 happy-path 增量）：

1. 把"首版 FillSpec 尽早落盘"写成 §3 撰写阶段的行为硬规则（含阻塞项边界
   与冲突消解小框）。
2. canonical pattern 成为自包含、可直接实例化的骨架（只保留规范性指针）。
3. 建立 inheritance/lookup 辅助输入与 fill 源的角色边界契约（3 触点，纯
   doc）。
4. `LOOKUP_KEY_COLUMN_INVALID` 结构化缺陷，封死整个 key_column crash 类。

**非目标（明确移出本次 scope）**：

- TTFFS/CM2F 等 benchmark 指标、eval/测试集体系——**完全不纳入，连
  Deferred 都不写**，日后单独讨论测试与评估体系。
- pattern K=0 / clone residue 0-口径指引补充（前版 7.4）——本轮 cut，可
  日后另行开票。
- Compile 失败轮数机制——3 轮/几十秒是健康路径，不动。

## 4. 设计原则（本 effort 的裁决准则）

- **复杂度往 Compiler 移，不往 Agent 流程移**：能由 Compiler 低成本机械
  判断的，放 Compiler，不补 Agent SOP。
- **权威规则只有一份**：正式契约落在唯一权威文档（FILLSPEC.md / 脚本
  help），其他位置最多一句就地提醒，不做第四份副本。
- **事实是证据，不是权威**：业务语义裁决序 = 本次用户明确指令 > Selected
  MOD > canonical pattern 默认语义；当前输入/工作簿事实只用于解析结构与
  验证前提，不反向改写已确定的业务语义。
- **不加状态、不加 Gate、不加预算**：不新增 COMMIT 状态机、探索预算、
  ASK preemption runtime。
- **Deferred does not imply planned work**：Deferred 节只记录"暂否决的
  升级选项"及其再触发条件，不是路线图。

## 5. Tickets（第一批，全部 ready-for-agent）

| # | Ticket | 文件 | 类型 |
|---|---|---|---|
| 01 | SKILL §3 首版收敛原则 + 冲突消解小框 | `issues/01-skill-first-draft-convergence-rule.md` | doc（SKILL.md） |
| 02 | canonical pattern 三串全洗（自包含） | `issues/02-canonical-pattern-self-contained.md` | doc（combination_patterns.yaml） |
| 03 | inheritance 输入角色解耦契约 | `issues/03-inheritance-input-contract.md` | doc（FILLSPEC.md + SKILL.md + 脚本 help） |
| 04 | `LOOKUP_KEY_COLUMN_INVALID` 缺陷码 | `issues/04-lookup-key-column-invalid-defect.md` | code（compile_fill.py + FILLSPEC.md + tests） |

验收基线：01–03 为纯文本改动，验收 = 逐条对照本 spec 与各 ticket 的
acceptance 清单 + 下一个真实任务观察；04 附带回行测试（malformed +
out-of-range 两用例）。

## 6. Deferred（仅记录暂否决的升级选项，不是计划内工作）

**统一再触发原则**：完成本轮轻量 contract/Compiler 修复后，同一类问题仍在
**多个独立真实任务**中重复出现，且无法通过更小的契约澄清解决，才重新评估
Runtime 化。

| 选项 | 当时的动机 | 暂否决理由 |
|---|---|---|
| FillSpec COMMIT 状态 / commit barrier | 强制 pattern 命中后立即产物 | 现有阶段划分足够；新增状态本身成为新的理解负担 |
| Exploration Budget / tool-call 限额 | 限制 pattern 命中后的探索 | 复杂任务易误伤；管理成本 > 收益 |
| KNOWN→STOP 状态机 | 打断重复确认 | 转为一句行为原则（ticket 01）已覆盖 |
| ASK Preemption runtime | 防 D/F/X 式多余 ASK | ticket 01 冲突消解框先行；"已有权威答案则不得 ASK"是文本规则 |
| prepare_run 拒绝 target 工作簿非 target sheet 进 `--sheets` | 防 manifest 污染复发 | ticket 03 契约澄清先行；角色边界按 sheet 职责判断，不是工作簿位置 |
| Core manifest 冻结 + `lookup_sources`/`inheritance_inputs` namespace | 物理解耦继承输入 | 接口歧义先修；重复犯错再升级 Runtime（V3_RUNTIME_CANDIDATE） |
