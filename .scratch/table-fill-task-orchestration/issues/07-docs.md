# 07 — Docs（SKILL.md 入口 + TASK_ORCHESTRATION.md + KNOWN_TRAPS + CONTEXT + ADR-0009）

Status: resolved
Type: task
Blocked by: 01, 02, 03, 04, 05, 06

## Comments

- 2026-08-24: 落地于本仓库（docs-only 变更，零脚本改动）。
  - `table-fill/references/TASK_ORCHESTRATION.md`（新）：Task Layer 唯一详细
    契约源 —— task model（三层 schema + 约束 + 禁止键）、cache contract
    （键四分量语义、eager 预展平、物化、Identity 二分、白名单）、scheduler
    contract（阶段表 + 并发默认值 2/2/4/2/1/2、单一写者、失败传播）、
    lifecycle/resume（状态机 + 断点判定矩阵 + crash window + 失败二分 +
    supersede）、gate aggregation、timing 双栏、CLI（三个脚本 + timing_task
    只读报告 + 退出码 0/1/3）、典型流程。无 Python 类名/函数名/内部目录。
  - `table-fill/SKILL.md`：新增「Task Orchestration」入口小节（何时用 + 三
    脚本 + 指向 reference + fail-closed 提示），**不含** cache key / barrier
    / status schema / resume matrix 机制细节（全文 grep 验证零命中）；顺带
    修复 issue 01–06 挂账的 pre-existing failure
    `test_skill_md_failure_cost_quantified`（撰写规程补「失败成本已量化：第
    1 轮失败是预期路径，定向修复通常 <2 分钟」）；Troubleshooting 补
    TASK_ORCHESTRATION.md 指针。
  - `table-fill/references/KNOWN_TRAPS.md`：新增「Task 层陷阱」小节五条 ——
    execute crash window（receipt 缺失不得直接 gate）、status ≠ truth
    （断点以 artifact 证据判定）、Office 并发 = 2（环境经验非架构契约）、
    supersede 后继续修旧 run、引用式路径（../../cache）破坏自包含。
  - `CONTEXT.md`：新增 9 个术语（Task / Task Definition / Task Prepare
    Snapshot / Task Runtime State / Cache Key / Materialize / Superseded /
    Checkpoint / Active·Superseded Cost），append-only，既有 run 定义未改。
  - `docs/adr/0009-table-fill-task-orchestration-layer.md`：Decision（引入
    Task Orchestration Layer 全要素）+ Non-goals（不替代 Run Layer / 不改
    Compiler·Executor / 无 workflow engine / 非第二业务语义层 / 非 cache
    系统 / 非时钟）+ Consequences（正/负）+ Alternatives Rejected；沿用
    0008 的 ADR 结构（docs/adr/ 现存 0001–0008 均未纳入 VCS，本 ADR 同
    约定落盘不追踪）。
  - FAILURE_CLASSES.md 未修改（git 无该文件 diff）。
  - 测试：`tests/test_task_orchestration.py` 187 passed；SKILL.md 内容断言
    组（test_optimization.py -k skill_md）20 passed —— 原 1 条 pre-existing
    failure 已转绿；全量回归见下（挂账补记）。
  - 验收核对：① SKILL.md 新增小节 grep「cache key|barrier|resume matrix|
    status schema|断点判定矩阵」零命中；② TASK_ORCHESTRATION.md 与 spec
    Implementation Decisions 一致（阶段表/并发默认值/键语义/物化/单一写者/
    矩阵与 24–29、31 决策对齐），全文无 Python 类名/函数名（script 名与
    artifact 文件名为契约面，允许）；③ KNOWN_TRAPS 五条齐；④ CONTEXT.md
    术语 9 条齐 + 既有定义逐条未动（append-only 编辑）；⑤ ADR-0009 含
    Decision / Non-goals / Consequences。

- 2026-08-24: code-review（Standards + Spec 双轴）处置——
  - 采纳（Spec 轴）：① compile 并发标注 spec 区间（表内写明「4（spec 区间
    4–8，实现常量取 4）」）；② gate 呈现措辞修正 —— `--run` 的 gate 阶段 =
    逐 run 建立 pending 证据（不呈现），聚合呈现只发生在 `gate_task --set`
    一次（§3 阶段 5 / §7.1 / §8 同步改写，「Gate 只呈现一次」的人机交互语义
    不再有双呈现歧义）；③ 缓存键删除「长度前缀编码」实现细节措辞（D34），
    改为「确定性编码消除边界歧义」；④ 共享事实声明语义固化 —— task.yaml
    无独立共享声明节，共享集 = run 清单 (file,sheet) 需求去重（派生语义）；
    ⑤ timing_task.py 定位为 spec S7 授权的「独立脚本输出」补充报告入口。
  - 采纳（Standards 轴）：⑥ §7.5 退出码双通道契约精确化 —— 守卫级错误走
    stderr（与单 run 套件 fail() 一致），阶段失败清单走 stdout，进度行只走
    stderr（与 _officecli.fail / _fail_json 实测一致）；⑦ §6 栏名对齐
    CONTEXT 术语 Active Cost / Superseded Cost；⑧ SKILL.md 小节标题改半角
    括号（与全文/KNOWN_TRAPS 新节一致）；⑨ §9 与 spec 关系措辞收敛
    （S1–S7 契约面 + S8 证据面，S8 测试分层归属 spec）；⑩ ADR-0009
    Consequences 改用名词避免 reserved-term "batch" 命名新行为。
  - 保留（有意设计）：`gate_summary` 的 task_timing 块键名（artifact 数据
    契约，非 Python 符号）；§4.2「直接进 gate」措辞（与 issue 04 原矩阵
    一致，§4.5 已声明不自动跳过）；§6 kind+phase 分组统计（issue 06 契约
    本体）；CONTEXT.md 未纳入 VCS（与 docs/adr/0001–0008 同为磁盘管理，
    append-only 由编辑锚点保证，git 无基线可验证 —— 已在验收核对中注明）。

## 问题

Task Orchestration 是新概念层，若把机制细节散落到 SKILL.md 会使其变成所有机制的
百科，Agent 阅读成本上升。需保持职责分离：SKILL.md = 行为流程、reference =
契约、KNOWN_TRAPS = 失败模式、CONTEXT = 术语、ADR = 架构决策。

## 设计

1. **SKILL.md**：仅加「Task Orchestration」入口小节——何时用（单任务多 run、
   run 共享源准备事实 → prepare_task.py / gate_task.py / resume_task.py）、指向
   references/TASK_ORCHESTRATION.md。**不内嵌** cache key / barrier / status
   schema / resume matrix 等机制细节。
2. **references/TASK_ORCHESTRATION.md**（新文件，Task Layer 唯一详细契约源）：
   - Task model（task.yaml / manifest / status 三层，schema + 示例）；
   - Cache contract（键、eager 预展平、物化、Cache Identity vs Artifact
     Identity）；
   - Scheduler contract（阶段、barrier、并发默认值、单一写者）；
   - Lifecycle / Resume（状态机、断点判定矩阵、supersede、失败二分）；
   - Gate aggregation（gate_summary、逐 run 确认展开）；
   - Timing 双栏；
   - CLI 用法（三个脚本参数与退出码）。
   - 只描述 contract / invariants / allowed behavior，**不写实现细节**
     （Python 类 / 函数名 / 内部目录结构），避免重构成本。
3. **KNOWN_TRAPS.md**：新增 task 层失败模式——
   - execute crash window（draft 存在但 receipt 缺失 → 不得直接 gate，断点判定
     重跑 execute）；
   - status ≠ truth（禁止只信 task_status 判定断点，必须校验 artifact）；
   - Office 并发 = 2（Windows resident 进程持文件锁；环境经验，非架构契约）；
   - supersede 后继续修旧 run 的陷阱（输入事实已变，旧产物链无意义）；
   - 引用式路径（../../cache）破坏 run 自包含的教训。
4. **CONTEXT.md**：新增术语——task（多 run 容器）、task.yaml（canonical Task
   Definition）、task_manifest（derived Prepare Snapshot）、task_status（derived
   Lifecycle State）、cache_key（task-local cache identity）、materialize（复制
   task artifact 进 run-owned workspace）、superseded（保留证据的终止 run
   版本）、checkpoint（artifact 校验的恢复边界）、active/superseded cost。
   **不改既有 run 定义**（task 是新增容器关系）。
5. **docs/adr/ADR-0009**（Task Orchestration Layer，必须写，不后补）：
   - Decision：引入 Task Orchestration Layer；
   - Non-goals：不替代 Run Layer、不改 Compiler/Executor、不引入 workflow
     engine、不成为第二个业务语义层；
   - Consequences：正向（多 run 效率、共享准备、生命周期管理）/ 负向（新增
     task 层 artifact 面）。
6. **FAILURE_CLASSES.md 不修改**（run 失败分类不被 task 层污染）。

## 验收

- SKILL.md 新增小节不含机制细节（cache key / barrier / status schema / resume
  matrix 均不在 SKILL.md 出现）；
- TASK_ORCHESTRATION.md 与 spec 的 Implementation Decisions 一致，且不含实现
  细节（无 Python 类名/函数名/内部目录）；
- KNOWN_TRAPS 五条 task 层失败模式已写入；
- CONTEXT.md 新增术语齐全，既有 run 定义未改（git diff 验证）；
- ADR-0009 落盘，内容含 Decision / Non-goals / Consequences。
