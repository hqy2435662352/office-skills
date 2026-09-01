# Schema 参考（clean.jsonl / resume_state.json / artifact_manifest.json）

> 运行时只需要知道每个产物是"证据 / 状态 / 资产"的哪一层；字段细节在这里。

## 1. clean.jsonl — 归一化事件流

每个事件：`seq`（1 起连续）、`timestamp`、`kind`、`source_line`、`source_type`
（原始顶层类型）、`source_ordinal`（原始序号）。事件按原始行序（chronology）。

| kind | 含义 | 注意 |
|---|---|---|
| `user` | 真实用户消息（已剥离客户端注入块） | `active:true`；全部保留含纠偏/批准 |
| `client_context` | role=user 但 100% 客户端样板 | 无任务语义，可追溯保留 |
| `assistant` | 可见助手消息；`phase`=commentary/final_answer | final_answer=该轮总结 |
| `tool_call` | 工具调用：tool/call_id/command/parse_status/input | command 仅确定性可解析时有值 |
| `tool_output` | 工具输出；call_id 与 tool_call 配对（tool 字段） | 输出=证据，不是结论 |
| `search_end` | legacy web 搜索完成标记（call_id 配对） | 查询意图证据 |
| `file_change` | 只存在于 item_completed 的 FileChange | 历史修改证据，非当前文件状态 |
| `turn_context` | turn_id/model/cwd/workspace_roots/approval_policy/sandbox/timezone | 权限模型与工作目录 |
| `lifecycle` | task_complete/turn_completed/turn_aborted | "上一轮是否正常结束" |
| `rollback` | thread_rolled_back 语义应用结果 | num_turns/invalidated_*/confidence |
| `compaction` | 压缩标记（摘要/window/replacement_history 计数） | 完整 replacement 不回放 |
| `world_state_marker` | full/keys/payload_bytes/sha256 | payload 已剥离，按需回 raw |
| `unknown` | 未识别 schema/malformed（fail-visible） | 宁多勿丢 |

截断标记：`...<TRUNCATED: N chars kept A+B; raw source line L>...`（head 60%+
tail 40%，确定性）。`file_change.changes` 按路径逐条截断。

### stats.json 数字含义

- 清洗效果：`clean_events`、`clean_bytes`、`compression_ratio`
- 移除计数：`reasoning_removed`、`token_events_removed`、`duplicates_removed`
  （item_completed 重复）、`bookkeeping_removed`（task_started/thread_settings）、
  `developer_messages_removed`、`client_blocks_stripped`、`legacy_duplicates_removed`
- 保留计数：`user_messages`、`assistant_messages`、`tool_calls/tool_outputs`、
  `file_changes`、`lifecycle_events`、`turn_contexts`、`rollbacks`、`compactions`、
  `world_state_markers`、`client_context_messages`、`search_ends`（events_by_kind）
- 健康：`unknown_records` + `unknown_by_source`（逐来源分布）、`malformed_lines`、
  `truncated_events`、`tool_inputs_parsed/unparsed`、`raw_records`

`unknown_records>0` 或 `malformed_lines>0` 时必须查看 `unknown_by_source`，
确认是预期 schema 变体还是解析缺口。

## 2. resume_state.json（schema v1.0）

```json
{
  "version": "1.0",
  "session": {"session_id","cwd","model","model_provider","originator",
              "started_at","last_timestamp","turn_count","source_rollout"},
  "task": {"identity","user_goal"},
  "execution": {"phase","status","confidence","basis","abort_count",
                "has_rollback","tail_validation_marker","note"},
  "completed": [], "in_progress": [], "pending": [], "blocked_by": [],
  "next_action": {"action","target"?,"note"?},
  "evidence_refs": [{"kind","seq","source_line"}],
  "awaiting_agent": true
}
```

### 枚举

- `execution.phase`：unknown / analysis / preparation / execution / validation /
  approval_gate / release / completed / blocked（脚本只产出
  unknown / execution / approval_gate）
- `execution.status`（脚本产出）：waiting_confirmation / interrupted /
  rolled_back / recovered_after_interruption / completed_turn /
  no_lifecycle_evidence
- `next_action.action`：continue / ask_user / verify / recover / stop
  （脚本只产出前四者）

### phase 判定规则（白盒，带 basis + confidence）

```text
最后 rollback 晚于最后 lifecycle           → execution/rolled_back（0.8）
无 lifecycle                                → unknown（0.2）
尾部 lifecycle = turn_aborted（且晚于最后用户轮）→ execution/interrupted（0.8）
尾部 lifecycle = task_complete：
  尾部 final_answer/工具输出含确认类表述（门禁/确认发布/确认输出/请回复…）→ approval_gate/waiting_confirmation（0.85）
  否则 abort_count>0                        → execution/recovered_after_interruption（0.6）
  否则                                      → execution/completed_turn（0.55）
```

判定都基于事件结构（谁在最后、含什么关键词），不是语义概述。

### evidence_refs 构成

latest_user / final_answer / last_lifecycle / last_tool_output /
last_file_change / last_rollback / last_abort（各至多一条，最近者）。

### --merge 契约（V1.3 两层分离）

```text
resume_state.json  = MACHINE layer（确定性、纯净、可复现；永不因 merge 改变）
agent_state.json   = AGENT layer（阅读 Agent 的解读）
```

`--merge <src>`：读取 Agent 填写的解读文件（completed/in_progress/pending/
blocked_by + 可选 next_action/notes），校验每个 evidence_ref 必须存在于
clean.jsonl（否则 exit 2），输出 `agent_state.json`（默认 --out 同目录；
`--out-agent-state` 可改）。机器层保持 `awaiting_agent:true` 不动。

agent_state.json：
```json
{"version":"1.0","layer":"agent","session_id":"...",
 "completed":[...],"in_progress":[...],"pending":[...],"blocked_by":[...],
 "next_action":{...},"notes":"...","evidence_validated":true}
```

### resume_brief.md（V1.3 P0，启动页）

由 `generate_resume_brief.py` 确定性渲染（无 LLM、无时钟）：

- 任务 identity + 原始目标（≤180 字截断）
- 当前状态：phase/status 中文解释 + 事实（turn 数/abort/rollback）+ 下一步
- 已完成/待办：来自 agent_state（≤8 项）；无 agent 层时给出证据入口指引
- **不要（Do not）**：白盒规则（gate 不自动发布、不无据重新生成、中断先
  定位、active:false 不使用、required 缺失先 rebind）
- 资产摘要：importance 计数 + required 缺失清单（≤6 条）
- 证据入口：resume_state → agent_state → clean.jsonl（seq/source_line）

长度目标：<1000 tokens（中文 brief 实测 ~530-680 tokens）

## 3. artifact_manifest.json（schema v1）

```json
{
  "schema_version": 1,
  "max_hash_bytes": 1000000,
  "stats": {"file_change_artifacts","tool_command_artifacts","env_reads_filtered",
            "total","produced","input","evidence",
            "exists_on_disk","missing_on_disk","hashed",
            "importance": {"required","optional","historical","ephemeral"},
            "required_exists","required_missing"},
  "artifacts": [
    {"path","category","role","role_basis","source","source_ref",
     "importance","importance_basis",
     "verification": {"exists","size","mtime","sha256"?,"hash_skipped"?}}
  ]
}
```

- `category`：produced / input / evidence
- `role`（白盒）：generator（脚本等）/ deliverable（xlsx/docx/pptx/pdf/csv/
  png/html 等后缀）/ evidence（basename 含 receipt/manifest/validation/
  report/summary/hash）/ input
- `importance`（V1.3，白盒，importance_basis 可见）：
  - **required**：deliverable/evidence 角色，或 staging 之外的源输入
    （source.xlsx 等）—— 继续前必须存在
  - **optional**：file_change 产出的脚本/配置（可重建）
  - **historical**：staging（Temp/tmp）内的读取/衍生路径 —— 缺失无害
  - **ephemeral**：无扩展名路径（目录/暂存）
- `source`：file_change（主源）或 tool_command（parsed exec 中的字面量路径）
- 路径归一化：normcase（Windows 大小写折叠）去重，保留最新 source_ref
- 验证：exists/size/mtime(UTC)；`sha256` 仅当 `size <= max_hash_bytes`
  （默认 1MB）；大文件 → `hash_skipped:"large_file"`
- tool_command 路径提取上限：150 条/类（确定性 cap）；`.codex` 内部路径
  （skills/memories 等环境读入）→ 过滤并计入 `env_reads_filtered`
- 路径 token 规则：必须 drive-letter 或 dot-relative 开头（`C:\...`、`.\...`）；
  以 `/` 根开头的 POSIX 路径与 officecli sheet 查询路径（`/R32 摩洛哥能效/A1:G5`）
  不提取 —— Windows 主场景的已知取舍

### 缺失语义

`exists:false` ≠ 任务失败。旧机器路径在当前机器缺失正是 Rebind 需要的信号：
先确认 branch/cwd/是否移动，再决定重建；重建后哈希变化必须重新走验证与 gate。