# Architecture（设计理念与历史）

> Runtime instructions live in `SKILL.md`. This file explains WHY and records
> what the real rollouts taught us. 中文为主，Agent 按需阅读。

## 1. 产品定位（V1 → V1.2 演进）

| 版本 | 定位 | 产出 |
|---|---|---|
| V1.0 | Codex Session 清洗器 | clean.jsonl / meta.json / stats.json |
| V1.1 | Session Discovery + Hardening | index_sessions.py、turn-aware rollback、fail-visible、保守剥离 |
| V1.2 | **Agent Continuation Package 生成器** | + resume_state.json（状态层）+ artifact_manifest.json（资产层） |

核心不是保存更多历史，而是在最小恢复成本下让新 Agent 安全接管旧任务：

```text
Intent + State + Evidence + Artifact + Permission Boundary
```

五个齐了，Agent 才真正具备"接手别人做到一半工作的能力"。

明确冻结（不做）：长期记忆、embedding/vector search、semantic memory、
LLM summary、自动执行恢复（auto-resume）、Codex native replay、全仓库扫描。

## 2. 心智模型

```text
Historical Truth（旧 rollout）= 上一个 Session 发生过什么
Current Truth（workspace/git）= 现在实际是什么状态
冲突时 → Current workspace facts win
```

Session Resume 是 Task Continuity，不是 Conversation Summary，也不是
Same-thread Provider Migration。不做线程/session/provider 元数据迁移。

## 3. 分层信任（证据 → 状态 → 资产）

```text
clean.jsonl        = 事实/证据层（历史事实、调查依据、debug corpus）
resume_state.json  = 状态层（Execution State Snapshot，规则推断 + 待 Agent 补全）
artifact_manifest  = 资产层（恢复执行环境所需文件 + 当前磁盘验证）
raw rollout        = 极端取证层（仅 source_line 定点回读）
```

读法：正常恢复走 resume_state → manifest → workspace 校验；状态有疑问查
clean.jsonl；clean 答不了才回 raw（targeted backfill，严禁全文加载）。

## 4. 真实样本发现史（设计依据）

来自两份真实 Codex Desktop rollout + 9 份本机 legacy sessions
（codex-tui/deepseek/history_mode=legacy）的审计：

1. **`FileChange` 只存在于 `event_msg.item_completed`** —— response_item 流
   没有对应物。所以 item_completed 不能整体丢弃；其余 item 类型
   （Reasoning/AgentMessage/UserMessage/CommandExecution/McpToolCall）才是
   response 流的重复。
2. **首条 `role=user` 常是客户端注入样板**（`<recommended_plugins>` +
   AGENTS.md instructions + `<environment_context>`），且可与真实文本混在同一
   content item —— 必须按内容块剥离（仅完整边界可删，宁保留不误删），禁用
   "第一条 user 忽略"式位置启发。
3. **exec input 是 JS 包装串** —— `const r = await tools.exec_command({...})`；
   只做确定性平衡-JSON 提取 `cmd`（parsed/unparsed 显式标注），禁止 eval。
4. **legacy 双写** —— 同一用户消息会同时出现 response_item message 和
   event_msg/user_message 标记（同 turn 内文本一致 → 去重计数）。
5. **legacy web 搜索** —— `web_search_end` 与 `web_search_call` 通过
   call_id↔id 配对（call 记录用 `id` 而非 `call_id`）。
6. **rollback/compaction 语义来自 openai/codex 源码**
   （`rollout_reconstruction.rs`）：`thread_rolled_back(num_turns)` =
   drop_last_n_user_turns；`compacted` 携带 replacement_history/window 元数据；
   world_state full=基线、非 full=merge patch。两份 Desktop 样本未含此类记录，
   实现以官方语义 + synthetic fixtures 校准为准（V2 待真实样本复核）。

## 5. Session Discovery（V1.1）

选择优先级：explicit path → exact session id → 用户指定时间 → cwd/项目 →
user-messages 语义。索引只提取高信号内容（session 元数据 + 全部真实 user
messages，无 recent-N 截断）。真实布局 `~/.codex/sessions/YYYY/MM/DD/`，
session id 内嵌文件名；archived_sessions 缺失时索引优雅警告跳过。

## 6. 状态推断为什么是"规则 + 置信度"（V1.2）

`resume_state.execution` 的 phase/status 由白盒规则产出（尾部 lifecycle、
gate 关键词、abort/rollback 计数），每条带 `basis`（event seq）与
`confidence`。**completed/in_progress/pending 脚本不猜** —— 语义属于阅读
Agent；Agent 填充后 `--merge` 回写，脚本校验每个 evidence_ref 必须指向
clean.jsonl 真实存在的 event（否则 exit 2）。这样状态层永远可审计：
state → evidence_ref → clean event → (必要时) raw line。

## 7. Gate / Permission 模型

approval_gate 判定需要尾部证据（final_answer/工具输出含确认类表述）。一旦
判定，resume 不得自动跨越：publish/promote/deploy/release/外发/破坏性/不可逆
操作必须在本对话获得新的用户确认。旧 session 的批准 ≠ 本轮许可。哈希绑定类
gate（如 table-fill 的 execution gate）在重跑后必须重新走 gate。