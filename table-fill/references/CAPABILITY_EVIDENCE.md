# Capability Evidence

Table-Fill Task Mode 的能力求证详细政策源。**按需读取** — 正常 Run 不加载本文件;
只有产生单一、可证伪的 **Capability Question** (机制能力疑问) 时才读取, 并沿本
参考的终局算法执行。主 Skill 只保留短算法与预算 (见 SKILL.md「撰写规程」);
本文件是 三态 / Capability Question / Evidence Fit / Standard Evidence Paths /
Extra Capability Probe / Bounded Rescue / continue·ADAPT·ASK·STOP / Run-local
evidence / 模式切换 的唯一详细规则源。

## 1. 能力知识三态与 Capability Question

### 三态 (仅此三态, 无第四态)

| 状态 | 含义 | 动作 |
|---|---|---|
| **Known Supported** | 适用且有权威通道正面支持 | 直接使用; 正常 Run Verification 全部执行 (formal compile、execute、Validated Draft、readback、结构验证、Render QA、Execution Gate) |
| **Known Rejected** | 适用且有权威通道明确拒绝 (含 not-rolled-out 门) | 不尝试该行为; 寻找满足同一业务意图的 Known Equivalent Adaptation |
| **Capability Unknown** | 无适用权威通道决定该问题 | 按第 5 节终局动作处理; 不因复杂、首次出现、特性数量或"想再确认"而进入 |

"Known" 只是前两个状态的口语简称, **不是第四个状态**。Adaptability
(可适配性) 是独立的任务路径判断, 不是能力状态。

### Capability Question

一个 Capability Question 必须是**单一、可证伪**、关于机制行为的疑问 — 涉及
Table Fill / Prepare / Compiler / Executor / OfficeCLI 的机制行为 (接受性、
拒绝、rollout 状态、坐标/顺序/副作用语义)。一次一个, 不得在求证中途扩题。

**不属于 Capability Question** (走各自既有路径, 不进入本参考):

- 业务语义与业务歧义 → MOD Resolution / `gaps` / Execution Gate
- 源数据事实 → 展平 CSV / digest / 源文件
- 普通 spec/编译/执行缺陷 → REPAIR (失败处置表) / `_draft_failure.json`
- 人的视觉判断 → Render QA

### Evidence Fit 与证据作用域

**Evidence Fit** = 证据通道是否**直接决定**该 Capability Question。没有 Evidence
Fit 的证据 (答了另一个问题的有效答案) 是 non-dispositive 证据, 不能制造信心。

四个维度保持分离: 工具输出 (ACCEPTED/REJECTED) ≠ 证据强度 (dispositive /
non-dispositive) ≠ 能力状态 (三态) ≠ 工作流动作 (使用/适配/ASK/STOP)。`--probe`
的输出只有 ACCEPTED 或 REJECTED 两种结果; 无 Fit 的结果不构成第三个状态。

**证据作用域 (scope)**: 结论不得超过证据直接声明或证明的范围 —

- capability contract / `--capabilities` → 契约声明范围, 可跨 Run;
- formal compile → 只决定**当前具体 FillSpec** 是否被接受, 不推断全局支持;
- Compiler-derived readback / 结构验证 / Render QA → 只解决**当前 Run**;
- `officecli help` → 只解决 help 直接声明的公开接口事实;
- KNOWN_TRAPS → 只解决**直接同形**的已实测机械事实, 不泛化。

## 2. Standard Evidence Paths (封闭小表)

| Capability Question 域 | Standard Evidence Path | 结论作用域 |
|---|---|---|
| 正式支持、明确拒绝、rollout 状态 | capability contract (FILLSPEC 能力映射表等) / `compile_fill.py --capabilities` | 契约声明范围, 可跨 Run |
| 当前具体 FillSpec 是否被 Compiler 接受 | **formal compile**; 仅合格架构分叉可用一次 `--probe` (第 3 节) | 当前具体 spec |
| OfficeCLI 命令、属性、参数、公开元素接口 | `officecli help <format> <element>` | help 直接声明范围 |
| 已实测 OfficeCLI/机械陷阱 | 直接同形的 KNOWN_TRAPS 条目 | 条目同形事实, 不泛化 |
| 当前 Draft 值、公式结果、EMPTY | Compiler-derived readback | 当前 Run |
| 当前最终行数、merge boundary、结构结果 | 结构验证 (final row / group boundary) | 当前 Run |
| 当前受影响区域渲染结果 | Render QA (png/html) | 当前 Run |

规则:

- 表是**封闭**的: 不发明更精致的证据来源, 不做证据评分, 不做 fallback 框架。
- **Canonical Pattern 不是 support/rejection 证据** — 它是首选构造路径
  (缺同形 Pattern 不产生 Capability Unknown; 见下节「默认可组合」)。
- 源码、Skill 测试套件、fixture 和自行发明的实验**不是 TASK MODE Standard
  Evidence Paths**。
- **默认可组合**: Known Supported 能力默认可组合, 除非权威契约明确声明冲突
  或约束; 具体组合的 ownership、geometry、range、duplicate-write 由
  **formal compile** 验证, 不手动模拟 Compiler。
- **冲突仲裁**: 显式能力拒绝 (含 not-rolled-out 门) 优先于未针对其约束的
  泛化接受证据 (fail-closed); 其他直接证据冲突 → 用对当前问题最直接的通道,
  同时记录 **Contract Drift**; 无法判定哪个更直接 → **ASK/STOP**, 不以自造
  实验仲裁 (见第 5 节)。

## 3. Extra Capability Probe (每 Run ≤ 1)

**用途唯一**: 只在下列**全部**成立时, 允许在撰写正式 FillSpec 之前消耗本 Run
唯一一次 Extra Capability Probe (`compile_fill.py --probe` 或
`make_probe_spec.py` 生成骨架):

1. 问题属于 Compiler acceptance 且能力权威 (contract / `--capabilities`) 未回答;
2. 存在**两个互斥、实质不同**的 FillSpec 骨架形成架构分叉;
3. 选错分支会造成**明显重写** (昂贵返工)。

**明确不构成资格** (直接走 formal authoring → formal compile): 普通 compile
defect、复杂组合、首次出现、特性数量多、缺少同形 Canonical Pattern、想增加
信心、谨慎心理、普通可修错误。

**结果与预算**:

- 预算属于 Run 而非问题: 每 Run 至多一次; 不被问题的数量重置。
- 输出只有 **ACCEPTED / REJECTED**; 对问题具有 Evidence Fit 的结果
  **立即终结该 Capability Question** — 不能把 dispositive 结果包装成
  "不够放心" 后再进入 Rescue。
- 明确**环境故障** (工具崩溃、文件锁、编码问题) 允许**原样重试**, 不算第二次
  概念性 probe; 改变问题、spec shape 或验证目标 = 新的探索, 不允许。
- 结果只解决当前具体 spec 的接受性 (formal compile 同管线); 不升级跨 Run
  knowledge, 不写入 KNOWN_TRAPS (制度化见第 5 节模式切换)。

## 4. Bounded Rescue (每 Run ≤ 1)

**四项资格 (同时成立才可进入)**:

1. **Unresolved** — 该 Capability Question 仍未解决 (没有被权威通道或
   dispositive probe 回答, 也不能用已有答案重新开启);
2. **Task-blocking** — 不解决就阻塞当前任务交付;
3. **No Standard Evidence Path** — 第 2 节小表中没有任何适用通道;
4. **No Known Equivalent Adaptation** — 没有满足同一业务意图与约束的
   Known Supported 等价路径。**适配必须保留业务意图与约束**: 弱化结构、
   格式、可追溯性或请求行为的"简化"不是适配。

**一次 Rescue 的边界 (一个/一个/一个/一个)**:

- 一个**预先声明**的 Capability Question;
- 一个**预先声明**的 Black-Box Rescue Experiment;
- 一个**预先声明**的 pass/fail 判据;
- 一个 verdict。

改变问题或证据方案 = 耗尽该 Rescue。**Sufficient Evidence 一旦出现立即结束** —
不追求完整机制理解, 不测试无关变体。

**黑盒实验方式**:

- 只通过公开的 Table Fill / Office 接口观察行为; **不读实现源码**。
- 优先使用当前 staged 输入的**独立 scratch 副本**, 并裁剪到该问题需要的最小
  对象/范围; **staged 输入保持只读**。
- 仅当当前对象无法安全隔离或复制、且**原因在实验前记录**时, 允许最小
  synthetic artifact; 从任务副本切换到 synthetic 属于同一 evidence plan,
  不产生第二次 Rescue。

**TASK MODE 禁区 (硬性)**: 不读实现源码、不运行/检视 Skill 测试套件、
不创建 fixture 链、不修改 Skill、不追无关变体。

**预算关系**:

- **Probe 与 Rescue 预算独立** (问题域不同), 但**同一 Capability Question
  只能消费一种额外求证路径** — 不允许 probe 后再包装同一问题进 Rescue,
  也不允许改名/拆分/扩题各消费一次。
- **Rescue 与 REPAIR 正交**: Rescue 不消耗、不补充、不重置 formal
  compile/execute 尝试预算; 失败不自动触发 Rescue; 成功最多让唯一允许的
  REPAIR 更有依据。第二次连续 formal 失败仍按既有失败处置表 ASK/STOP。

## 5. 终局动作: continue / ADAPT / ASK / STOP

在 Capability Question 出现时, 沿以下终局算法执行 (每步结论都以证据作用域
为限):

1. 找到与问题具有直接 **Evidence Fit** 的 Standard Evidence Path (第 2 节);
2. **Known Supported** → 直接使用; 正常 Run Verification 仍全部执行;
3. **Known Rejected** → 不尝试; 寻找 Known Equivalent Adaptation (约束保持);
4. **Capability Unknown**:
   - a. **非 task-blocking** → 忽略未知, 继续任务;
   - b. **标准流水线可回答** → 继续正常流水线 (compile / draft / readback /
     结构验证 / Render QA) 取得证据;
   - c. **有 Known Equivalent Adaptation** → **ADAPT**;
   - d. **Compiler acceptance 的昂贵架构分叉** → 可消耗本 Run 唯一
     Extra Capability Probe (第 3 节);
   - e. **unresolved + task-blocking + 无 Standard Evidence Path +
     无 Known Equivalent Adaptation** → 可消耗本 Run 唯一 Bounded Rescue
     (第 4 节);
   - f. **Rescue 无 Sufficient Evidence** → 只有多个安全 Known Supported 路径
     之间存在**纯业务取舍**时才 **ASK**; 没有可证明安全的路径 → **STOP**。

**ASK/STOP 铁律**: 用户确认**不能把 Capability Unknown 变成技术许可** —
ASK 选项只能是业务取舍, 不能包含"按未验证机制执行"或"也许安全"。

### Run-Local Capability Evidence 与 Gate 披露

Rescue 成功只产生 **Run-Local Capability Evidence**, 不升级为跨 Run 的
Known Supported。Agent 在 workdir 留下简短人类可读记录, 至少包含:

- Capability Question
- black-box evidence plan
- predeclared pass/fail criterion
- actual result
- evidence artifact identity、path 与 hash (适用时)

Execution Gate 增加**一句**精简披露, 语义必须包含: 回答了什么问题、PASS/FAIL
结论、证据只适用于当前 Run、尚未制度化。内部四项资格判断不展开呈现。

### Capability Gap Discovery 与模式切换

- **Capability Gap Discovery** 只在当前 Run 记录 (问题 + 证据 + 结论/未解决);
  任务后如需跟进 → 轻量 **needs-triage** 项, 不阻塞交付。
- **Capability Institutionalization** (补缺陷码 + 契约 Q&A + contract test,
  KNOWN_TRAPS 同步 — 即既有三件套质量标准的制度化形态) 只在用户明确把主要
  目标改为诊断/修改/扩展/评测 table-fill 的 **Skill Development** 中进行。
- **只有用户**可以把主要目标从 Table-Fill Task Mode 切换为 Skill Development
  (随时可暂停或结束当前 Run 发起); Agent 不能因为 Capability Unknown、Rescue、
  Contract Drift 或感知的复用价值自行切换模式。
