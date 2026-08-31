---
name: codex-session-resume
description: >
  Resume a previous Codex session's task state from its local rollout-*.jsonl
  archive and CONTINUE the work — restore the current goal, verified progress and
  the exact resume point via deterministic preprocessing + agent reading, then
  rebind the current workspace and keep executing. Use this skill whenever the
  user asks to continue an earlier Codex conversation or task, e.g. "继续刚才那个
  任务", "继续昨天的 table-fill 开发", "继续这个 session", "继续 session
  01a051c7-...", "读取这个 rollout jsonl 继续做", "接着上一个 Codex 对话做",
  "resume the codex session", "pick up where we left off", or hands you a
  rollout-*.jsonl / codex session id and expects ongoing work. Also trigger on
  vague continuation phrases ("接着做", "继续上一次", "上次做到哪了，继续") when
  a Codex session archive is the likely source. Do NOT use for: summarizing a
  past conversation, migrating a thread to a new provider, or restarting a task
  from scratch — this skill continues tasks, it does not replay conversations
  or treat the old rollout as current truth.
license: MIT
compatibility: >
  Python 3.10+ (stdlib only; no third-party dependencies), PowerShell or POSIX
  shell. Primary scenario is Windows Codex Desktop. The old rollout is read-only.
---

# Codex Session Resume (V1)

## 1. 心智模型

旧 rollout 是 **Historical Truth**：它只告诉 Agent *上一个 Session 发生过什么*。

当前 workspace 是 **Current Truth**：filesystem / git 告诉 Agent *现在实际是什么状态*。

冲突时：

> Current workspace facts win.

因此本 Skill 的产物是：

```text
restore historical understanding      ← 从 rollout 恢复
+  rebind current workspace           ← 用当前文件系统校准
=  resume execution                   ← 从真实断点继续干活
```

Session Resume 是 **Task Continuity**，不是 Conversation Summary，也不是
Same-thread Provider Migration。不要修改 Codex session/provider 元数据，不要
尝试让旧 thread 在新 provider 直接 resume，不要自动创建新 thread。

## 2. 默认用户体验

```text
用户："继续昨天那个 session 的任务。"
Agent: [定位 session] → [preprocess] → [恢复状态] → [rebind workspace] → [从断点继续]
```

**不要**默认输出一份摘要然后询问"是否继续"。如果下一动作明确且不涉及特殊
审批 gate，直接执行。恢复后的内部状态（§10）通常不需要整份汇报给用户。

## 3. 三步流水线 + 硬规则

```text
raw rollout
    ↓  ① scripts/preprocess_session.py（必须是第一步，永远）
meta.json + stats.json + clean.jsonl
    ↓  ② Agent 按 §7 Reading Strategy 阅读
Agent 内部 resume state
    ↓  ③ Workspace Rebind（§11）→ continue
```

**硬规则：在处理之前，绝不直接读 raw rollout JSONL 全文。**
`*.jsonl` 含加密 reasoning、token 账目、运行时重复事件；直接读会污染判断。
只有 clean representation 无法回答某个具体问题时，才允许 **targeted forensic
backfill**：按 `source_line` 回 raw 文件中读那 1 行的内容（或极小范围），
搞清后立即回到 clean 流。不要重新全文加载 raw rollout。

## 4. Session 定位（含自然语言发现）

选择优先级：

```text
1. Explicit rollout path       用户给了路径 → 直接用
2. Exact session ID            例："继续 session 01a051c7-..." → 文件名/索引精确匹配
3. 用户指定时间                 "昨天/上周/8月30日" → 按 started_at 过滤
4. cwd / 当前项目              索引条目 cwd 与当前 workspace 的匹配度
5. User Messages 语义匹配       主要的语义判断依据（见下）
```

### 先建索引（一次扫描，支持自然语言）

```bash
python scripts/index_sessions.py \
    --root %USERPROFILE%\.codex\sessions \          # Windows
    --root ~/.codex/sessions \                       # POSIX
    --root ~/.codex/archived_sessions \              # 归档线程（若存在）
    --out session-index.json                          # 只写这一个文件，.codex 只读
```

真实 Codex 布局：`~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl`（session id 内嵌在
文件名中）。索引每个 session 只保留：session_id / rollout_path / started_at /
last_timestamp / cwd / model_provider / originator / history_mode /
**user_messages（全部真实用户消息，完整保留，无 recent-N 截断）**。
legacy 双写（response_item message + event_msg/user_message 同一文本）已在索引中
去重。

### 用索引做选择

先硬过滤（时间 → cwd/项目），再让 Agent 读 user_messages 做语义判断：

```text
用户："继续昨天那个参数表任务"

索引候选（8月30日，5 个 session）：
  session A  cwd=北非客户参数表整理
    user: 摩洛哥和欧洲参数表都要更新外发……
    user: 发布8份Excel……
  session B  cwd=报价单
    user: 修改报价模板列……
    …
→ A 明显匹配，直接继续
```

不需要 embedding、不需要 semantic index：User Message 是最高信息密度的
选择依据。多个候选确实无法可靠区分时才问用户（一句话列出 2-3 个及依据）。
一个候选明显占优 → 直接继续。不要因为"省事"强迫用户提供 session id。
（一句话给出 2-3 个候选及依据即可）。不要因为"省事"而强迫用户给 session id。

## 5. Preprocess（结构化事实提取，不做语义判断）

```bash
python scripts/preprocess_session.py --input <rollout.jsonl> --output <out-dir>
```

输出 `<out-dir>/{clean.jsonl, meta.json, stats.json}`。脚本只做结构去噪与
规范化：**它不判断什么完成了、什么 pending、用户意图、下一步做什么**。
这些都归 Agent。可选截断参数 `--cap-tool-input/--cap-tool-output/
--cap-file-change/--cap-unknown` 通常不需要动。

先读 `meta.json` 与 `stats.json`，再用它们理解 clean.jsonl。

### clean.jsonl 事件类型（都有 `seq/source_line/source_type/source_ordinal`）

| kind | 含义 | 阅读时注意 |
|---|---|---|
| `user` | 真实用户消息（客户端注入的样板块已被剥离并计数） | 全部保留，含纠偏/批准 |
| `client_context` | role=user 但内容是纯客户端注入样板（plugins/AGENTS.md/env） | 无任务语义；为可追溯而保留 |
| `assistant` | 可见助手消息；`phase` = commentary/final_answer/None | final_answer = 该轮面向用户的总结 |
| `tool_call` | 工具调用；`tool`,`call_id`,`command`,`parse_status`,原始`input` | `command` 只在能确定性解析出 exec JSON 时有值；`parse_status=unparsed` 表示保留原始 JS 包装 |
| `tool_output` | 工具输出；用 `call_id` 与对应 `tool_call` 配对（`tool` 字段） | 输出是证据，不是结论 |
| `search_end` | legacy web 搜索完成标记（与 `web_search_call` 按 call_id 配对） | 查询意图证据，小体积保留 |
| `file_change` | 来自 `item_completed` 的 FileChange（只存在于那里！） | 历史修改证据；**不是**当前文件状态 |
| `turn_context` | 每轮上下文：turn_id/model/cwd/workspace_roots/approval_policy/... | 判断当时工作目录与权限模型 |
| `lifecycle` | `event` = task_complete / turn_completed / turn_aborted | **回答上一轮是否正常结束**（§7 Pass 4） |
| `rollback` | `thread_rolled_back`：最近 N 个用户轮已被回滚 | 见 §6 |
| `compaction` | 上下文压缩标记（含摘要、window、replacement_history 计数） | 见 §6 |
| `world_state_marker` | world_state 的轻量标记（payload 已剥离，仅 full/keys/bytes/sha256） | 说明存在快照；内容按需回 raw 核验 |
| `unknown` | 未识别 schema / 无法解析的行（fail-visible，统计可见） | 宁多勿丢 |

`truncated` 标记（`...<TRUNCATED: N chars kept A+B; raw source line L>...`）表示
超大 payload 被确定性截头尾；需要全文时按 source_line 回 raw。

### stats.json 关键数字
`reasoning_removed` / `token_events_removed` / `duplicates_removed` /
`bookkeeping_removed` / `client_blocks_stripped` / `unknown_records` /
`malformed_lines` / `tool_inputs_parsed` vs `unparsed` /
`legacy_duplicates_removed` / `events_by_kind`。清点确认：清洗生效、无异常
丢失、未识别 schema 可见（含 `item_completed` 内未知 item type —— 一律
fail-visible，绝不静默丢弃）。

## 6. 两个必须理解的失效语义（V1.1 已显式支持）

### 6.1 Rollback —— `"active": false` 的用户轮已失效
`thread_rolled_back` 表示最近 N 个用户 turn 被回滚（Codex 原生语义：
`drop_last_n_user_turns`）。脚本按 **turn segment**（以 turn_context.turn_id
为界；同一 turn 内多条 user 消息 = 一个 rollback 单位）把对应轮次的用户消息
+ 助手消息 + 工具事件标记为 `"active": false, "invalidated_by":
"thread_rolled_back"`，并输出 `rollback` 事件（num_turns /
invalidated_users / invalidated_events / confidence）。

`confidence`：
- `exact` —— 相关 segment 都有 turn_id，轮次边界可信；
- `approximate` —— 无 turn_context（fallback 到 user 消息边界），不要把
  轮次计数当成精确值。

**阅读规则：Current Goal 的构建只能使用 `active: true` 的用户轮。**
把已回滚的"后来改为方案 B"当成当前要求，是 Current Goal 级别的错误。
回滚前的旧轮次重新成为有效要求。`rollback` 事件本身保留在流中以备追溯。

### 6.2 Compaction —— 历史已被蒸馏，但标记仍在
`compaction` 事件保留：摘要 message（已截断）、window 元数据、
`replacement_history_present/items/bytes`。**完整 replacement_history 不会
重复注入**（否则三重重复）。若需要被压缩掉的细节，按 source_line 做
targeted backfill。compaction 之前的 user/tool 证据仍然有效，只是更早。

## 7. Session Reading Strategy（按顺序，恢复 CURRENT EXECUTABLE STATE）

不要"从第一行总结到最后一行"。按下面五遍读：

### Pass 1 — Identity
读 `meta.json` + `stats.json`：session 是什么、cwd、model/provider、时间、
turn 数、大小、清洗统计。确认这是用户要的那个 session。

### Pass 2 — User Intent（用户权威线）
读全部 `user` 事件（含 `active:false` 的，便于理解纠偏轨迹）：

```text
Initial Goal → Scope Changes → Corrections → Current Goal
```

原则：
- **后来的明确用户决策覆盖早期用户决策**（`active:true` 轮次中最新优先）。
- 防止把临时指令误读成最终范围：例如"先看摩洛哥部分"是过程指令，
  "摩洛哥能效和欧洲能效都需要更新外发参数表"才是范围裁决。
- `client_context` 事件无任务语义，跳过。

### Pass 3 — Execution Evidence（工具实证线）
读 tool_call / tool_output / file_change，回答：
**实际做到了哪里？**（不是"助手说自己做到了哪里"）
- exit code、stdout/stderr、验证/测试输出、git 输出、文件操作结果。
- file_change 是历史证据；**当前磁盘/git 状态才是真相**（§11）。

### Pass 4 — Tail（最后有效事件）
重点分析会话尾部：
- 最后一个 `user`：批准/拒绝/授权？
- 最后一个 `tool_call/tool_output`：最后完成的动作与结果？
- 最后一个 `assistant`：`phase=final_answer` 的公开声明？
- 最后一个 `lifecycle` 事件：

```text
task_complete / turn_completed  → 该轮已正常结束，无工具卡在半途
turn_aborted (+ reason)          → 轮次被中断，断点很可能就在中断前一刻
```

这决定 Resume Point 的性质：正常收尾后停在 gate，与工具调用中途被掐断，
是完全不同的断点。

### Pass 5 — Targeted Backfill
只在有具体缺口时向前查：技术决策、失败测试、用户批准、某字段为何被改等。
按 source_line 回 raw 定点核验，不要无限回溯。

## 8. Evidence Hierarchy

```text
Level A — Workspace / Execution Evidence   （最高）
    filesystem、git status/diff、tool output、test output、exit code、
    successful write、validation result
Level B — User Authority
    用户要求、纠偏、批准、拒绝、scope / acceptance 决策
Level C — Assistant Claims
    助手公开声明"完成/实现/修复"
Level D — Assistant Plans                   （最低）
    "接下来我会……"、"准备……"、"下一步……"
```

规则：
- **Level D 永远不能证明工作完成。**
- **Level C 不能覆盖相反的 Level A。**
- 后来的明确 Level B 可覆盖早期 Level B。
- 但 Level B 在 *rebind 之后* 仍须与 Level A 交叉验证：用户当年批准的是一份
  已经不存在/已变化的文件时，以当前 workspace 为准。

## 9. 任务状态分类

把关键事项恢复为四类，防止最常见错误（把"准备做"恢复成"已经做"）：

| 状态 | 判据 |
|---|---|
| VERIFIED COMPLETED | 有明确工具/文件/测试/git/validation 证据（Level A） |
| COMPLETED BUT NOT VERIFIED | 只有助手声明（Level C），无充分执行证据 |
| IN PROGRESS | 已开始，无完成证据 |
| PENDING | 仅讨论或计划（Level D） |

## 10. Internal Resume State

继续干活前，Agent 内部应能回答（通常无需整份展示给用户）：

```text
CURRENT GOAL                          ← 最新 active user 决策
AUTHORITATIVE USER CONSTRAINTS        ← 纠偏/批准/范围裁决
VERIFIED COMPLETED WORK               ← Level A 证据
UNVERIFIED / IN-PROGRESS WORK
PENDING WORK
KNOWN RISKS
LAST VERIFIED EXECUTION POINT         ← Pass 3/4 结论
EXPECTED NEXT ACTION
APPROVAL GATE STATUS                  ← §12
```

## 11. Workspace Rebind（必做，任何一次 resume 都不跳过）

恢复历史后，**不允许假设 workspace 没变**。对 git 仓库至少：

```text
cwd
git branch
git status
git diff --stat
```

再检查与 Resume Point 直接相关的文件（脚本生成物、被修改的 xlsx/pptx/
docs、关键配置文件）是否真实存在。Session 保存的是过去认知，当前 workspace
才是最终现实。

### 四种情况
1. **Workspace 一致** → 直接继续 Pending Work。
2. **Workspace 比旧 session 新**（文件已被后来修改 / 工作已 commit /
   另一个 Agent 已继续）→ 不要覆盖，重新计算 Resume Point。
3. **文件缺失** → 不立即重建。先确认：branch 对不对、cwd 对不对、
   是否被移动/重命名、是否 session 匹配错。
4. **git 状态变化** → Current git state wins。

## 12. 测试恢复规则

不要无脑重跑旧 session 的全部测试。顺序：
1. rebind workspace（§11）
2. 确认关键 artifact（8 份草稿/产物存在性、内容哈希可对照）
3. 看当前 diff
4. 再判断是否需要重跑

以下情况*建议*重新验证：session 在测试前中断；workspace 后来发生变化；
下一步强依赖之前测试有效性；acceptance 要求 fresh verification。
已强验证且 workspace 未变时，不为流程形式主义重跑昂贵测试。

## 13. Approval / Execution Gate（Resume 不能自动跨越）

旧 session 中明确的审批边界（publish / deploy / **promote** / release /
外发 / 破坏性操作 / 不可逆操作），resume 时**原样恢复，不得自动跨越**。

例：旧 session 最后状态是"8 份草稿已验证、停在发布门禁、等待用户确认"，
用户现在只说"继续这个任务"：

```text
恢复为：等待发布确认  →  向用户展示"万事俱备，是否发布？"
而不是：自动 publish
```

一般的"继续任务"可授权继续普通开发步骤，但不能替代流程中显式要求的特殊
批准。**用户当前的许可（在本次对话中）永远不能假设为旧 session 的跨轮许可。**

## 14. Handoff 不是 V1 主流程

正常路径：rollout → clean → reconstruct → rebind → continue。
**不要默认生成 Handoff 文档。** 仅当用户明确要求 / debug / benchmark /
跨 Agent 交接时才考虑生成。

## 15. Safety 承诺

- 原 rollout **只读**：不修改、不删除、不移动。
- 不修改 `~/.codex` 任何状态、不碰 SQLite、不写 thread/session 元数据。
- preprocess 输出写入独立目录。

## 16. V1.1 已知限制（如实看待）

- rollback 的 `approximate` 置信度场景（无 turn_context）：轮次边界以 user
  消息为准，多消息单 turn 可能被拆成多个单位。
- legacy 双写去重依赖"内容消息先于 event_msg 标记"的出现顺序（真实
  codex-tui rollout 实测如此）；反序时可能出现重复 user 事件（stats 可见）。
- 客户端注入样板只剥离有完整边界的已知块；未知变体保留为文本（宁多勿丢）。
- `compacted` 的完整 replacement_history 不回放；深层细节走 forensic backfill。
- 脚本不做任何语义判断（这是特性，不是缺陷）：工作状态由 Agent 恢复。