# Parser 设计（结构性规则全集）

> `preprocess_session.py` 是**结构事实提取器，不是工作状态裁判**。所有规则
> 都可追溯（source_line/source_ordinal），所有未知都 fail-visible。

## 1. 顶层记录分类

| 顶层类型 | 处理 |
|---|---|
| `session_meta` | → meta.json（仅此一处读取） |
| `event_msg` | 按 payload.type 分流（见 §2） |
| `response_item` | 按 payload.type 分流（见 §3） |
| `turn_context` | → turn_context 事件（裁剪字段）+ rollback 分段边界 |
| `world_state` | → world_state_marker（payload 剥离：full/keys/bytes/sha256） |
| `compacted` | → compaction 标记（message 截断 2KB；replacement_history 只计数不注入） |
| 其它 | → unknown（fail-visible） |

## 2. event_msg payload.type 策略

| type | 策略 |
|---|---|
| `item_completed`：item.type=FileChange | **KEEP→file_change**（仅此存在处） |
| item_completed：Reasoning/AgentMessage/UserMessage/CommandExecution/McpToolCall | DROP+count（duplicates_removed；McpToolCall 与 function_call 流 1:1 镜像为证据化结论） |
| item_completed：其它/缺失 | → unknown（raw_type=`item_completed:<type|missing-item|<cls>>`） |
| `token_count` | DROP+count |
| `reasoning` / `agent_reasoning` | DROP+count（chain-of-thought 永不恢复/重写/推断） |
| `task_started` / `thread_settings_applied` | DROP+count（bookkeeping） |
| `task_complete` / `turn_completed` / `turn_aborted` | KEEP→lifecycle（turn_id/durations/reason/error/message≤1KB） |
| `thread_rolled_back` | 应用 rollback 语义（§6） |
| `compacted` | 同顶层 compacted |
| `user_message`/`agent_message`/`assistant_message` | legacy 消息（§5） |
| `web_search_end` | KEEP→search_end（call_id/query≤500） |
| 其它 | → unknown |

## 3. response_item payload.type 策略

| type | 策略 |
|---|---|
| `message` role=user | 内容块剥离后保留；纯样板 → client_context（id 保留） |
| `message` role=assistant | KEEP（phase 保留；文本≤40KB 截断） |
| `message` role=developer | DROP+count（环境样板） |
| `message` 其它 role | → unknown |
| `reasoning` | DROP+count |
| `custom_tool_call` | → tool_call（exec JS wrapper 确定性提取 command；parse_status 显式） |
| `custom_tool_call_output` | → tool_output（call_id 两遍配对） |
| `function_call` | → tool_call（arguments 按 JSON 解析，含在 parsed/unparsed 统计） |
| `function_call_output` | → tool_output |
| `web_search_call` | → tool_call（call_id 兼容 `id` 形态） |
| 其它 | → unknown |

## 4. 客户端注入块剥离（内容块级，非位置级）

只删除**完整边界**已知块；不完整/未知 → 原文保留（宁保留不误删）：

```text
<recommended_plugins>…</recommended_plugins>
<environment_context>…</environment_context>
<permissions…</permissions>
<permission_profile…</permission_profile>
# AGENTS.md instructions\n\n<INSTRUCTIONS>…</INSTRUCTIONS>
```

- 块可被空白/换行分隔（`\n\n` 前缀跳过后再匹配）
- 闭合标签之后的用户正文必须保留
- 未闭合块 → 整块保留；统计只计实际剥离数（client_blocks_stripped）

## 5. legacy 双写去重

codex-tui 旧格式对同一用户输入写两条：response_item message + event_msg
/user_message。同一 turn segment 内文本一致的后者视为标记重复 →
DROP+count（legacy_duplicates_removed）。依赖"内容先于标记"的出现顺序
（真实样本实测）；反序时重复可见于 stats，不静默。

## 6. Rollback（turn-aware）

- 分段：以 turn_context.turn_id 为界；无 turn_id 时退化为 user 消息边界
  （rollback confidence=approximate，诚实标注）
- `thread_rolled_back(num_turns=N)` → 最新 N 个**仍有效**的用户轮整段失效
  （用户消息+其助手/工具事件标记 `active:false, invalidated_by`）；已失效段
  不消耗预算；无用户事件的段（纯上下文）不消耗预算
- 输出 rollback 事件：num_turns / invalidated_users（段数）/ invalidated_events
  / confidence（exact|approximate）

## 7. Tool call 处理

- **两遍配对**：第一遍按事件顺序记 call_id→tool；第二遍（输出前）回填仍
  未配对的 output（顺序异常也稳）
- exec JS wrapper：`exec_command(` 后扫描平衡 JSON，json.loads 提取 cmd；
  失败 → parse_status=unparsed，原始 input 保留（≤16KB 截断）。禁 eval。
- function_call：arguments 按 JSON 解析（parsed/unparsed 计入统一统计）

## 8. 健壮性

- streaming read（逐行），超大 rollout 无整读；`stat().st_size` 计 raw bytes
- `payload_bytes`/`replacement_history_bytes` 按 UTF-8 字节数（非字符数）
- malformed / 非对象行 → unknown + malformed_lines 计数
- 输入严格 UTF-8（BOM 兼容）；Windows 路径与中文逐字保留

## 9. index_sessions.py

- 递归找 `rollout-*.jsonl`（多 --root：sessions + archived_sessions）
- 条目：session_id/rollout_path/started_at/last_timestamp/cwd/model_provider/
  originator/history_mode/**user_messages（全部，含 legacy 双写相邻去重）**
- 确定性：started_at 降序（不可解析垫底），无时钟字段，输出仅 --out 文件
- 不可读文件跳过计数；根缺失警告；无可读 rollout → exit 1

## 10. extract_resume_state.py / extract_artifacts.py

见 docs/schema.md 的判定规则与契约（脚本只消费 clean.jsonl/meta.json，
永不读 raw）。