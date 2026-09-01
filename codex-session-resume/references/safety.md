# Safety（安全边界全量说明）

> Runtime 版在 SKILL.md Safety boundaries；这里是展开条款与理由。

## 1. 只读承诺（任何脚本、任何模式）

| 禁止 | 原因 |
|---|---|
| 修改/删除/移动原 rollout | 原始档案是取证基线 |
| 修改 `~/.codex` 任何状态 | 不碰 session/thread/provider 元数据 |
| 触碰 `~/.codex/*.sqlite` | 不在本 Skill 范围 |
| 自动创建/迁移 Codex thread | 不是同线程续跑工具 |
| 在 `.codex` 内写入索引/缓存（`--out` 必须在外部） | 保持用户环境洁净 |

脚本输出只写入显式 `--out` 目录；`extract_artifacts` 对 manifest 路径只做
stat/read 验证，从不写回。

## 2. Fail-visible（宁多勿丢）

- 未知顶层类型 / payload 类型 / role / item_completed item 类型 / malformed
  行 → `kind=unknown` + `unknown_by_source` 计数 + source_line。
- `--merge` 的 evidence_ref 不在 clean.jsonl → exit 2，拒绝生成状态。
- 截断永远带显式标记（原长度 + source_line），从不断尾丢失。
- 客户端注入块只有完整边界才剥离；不确定时保留原文。

## 3. Approval / Execution Gate（Resume 不能自动跨越）

publish / promote / deploy / release / 外发 / 破坏性 / 不可逆操作，必须在本
对话中获得用户的新确认。具体：

- 旧 session 停在"等待确认发布"且新用户只说"继续任务" → 恢复到等待确认并
  呈现，不自动发布。
- "继续任务"可以授权普通开发步骤，但不能替代流程显式要求的特殊批准。
- 旧 session 的批准 ≠ 本轮许可（跨轮授权不传递）。
- 哈希绑定门禁（重跑后哈希变化）→ 必须重新验证并重新走 gate。
- `resume_state` 把 phase 判为 approval_gate 是基于尾部证据的规则推断
  （confidence 0.85）；Agent 复核时应确认"确认类表述"确实存在。

## 4. 中断与丢失处理

- 尾部 `turn_aborted` → 先定位中断点再继续（verify 优先）。
- 中途中断但尾部正常完成 → `abort_count` 可见，恢复后验证关键产物。
- 资产 missing → 不是失败；先 rebind（branch/cwd/移动/匹配错），再决定
  重建；重建后 fresh verification + fresh gate。

## 5. 终止条件

- 用户请求的只是状态查询 → 只报告内部 resume state，不写外部变更。
- 普通问题 / 无明确继续指令 → 不自动扩大动作范围（normal-action 纪律）。