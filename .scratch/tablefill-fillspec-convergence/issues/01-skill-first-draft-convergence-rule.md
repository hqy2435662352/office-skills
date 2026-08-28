# 01 — SKILL §3 首版收敛原则 + 冲突消解小框

Status: resolved
Type: doc（`table-fill/SKILL.md`，纯文本改动）
依据: spec.md §2 事实 1（stop-rule 站错位置）；session eg_fresh_local 43m39s
pattern-命中→落盘 漂移、31m 用户催写后漂移、D/F/X 多余 ASK、FRESH本土 路由
重开。

## 背景要点

"命中 canonical pattern → 直接实例化…不再读 case 复盘"目前只写在 §4
Compile（repair 上下文）。首次撰写阶段（第一次 Compile 之前）没有任何收敛
规则适用，Agent 把 `key_column`、lookup coverage、路由复核这类 Compiler 可
机械暴露/已裁决的问题重新分类成"写首版前必须确认"。只给抽象定义不够，
必须带锚点例消除解释空间。

## 改动 1 — §3「撰写规程 (先写后编译循环)」顶部插入收敛框

在 "- **先写后编译**: …" 之前插入：

```markdown
- **首版收敛原则 (硬性)**:
  - MOD Resolution 完成后，立即以当前证据撰写首版 `fill_spec.yaml` —
    下一项主要产物就是它。
  - 命中 canonical pattern 时直接实例化其骨架 (改替换表占位即得)，不寻找
    相似案例、不重新推导组合、不重读 case 材料；个别参数拿不准也先完成
    其余部分进入 Compile。
  - 只有**阻塞项**才允许延迟首次 Compile。阻塞项 = 不回答就无法用 FillSpec
    表达业务结果的业务未知项 (如: 目标 sheet 未确定；块数量/输出形态未
    确定；关键业务语义无任何权威来源)。
  - 能由 Compiler 机械检出的问题**不是阻塞项** (如: 列名/列字母合法性、
    lookup/key_column、merge、clone residue、aggregate、源行覆盖率) —
    交给 Compiler 暴露后按 corrective_action 定向修，不得在 Compile 前
    手工预证明。
  - 已被本次用户明确指令或 Selected MOD 解决的**业务语义**，以及已被当前
    输入事实确定的**结构事实/前提**，不得因"想进一步确认"重开、再 ASK
    或追加调查。
  - 冲突消解 — 业务语义的裁决序: 本次用户明确指令 > Selected MOD >
    canonical pattern 默认语义。当前输入/工作簿中的客观事实用于解析结构
    与验证前提，是证据不是权威，不反向改写已确定的业务语义。结构合法性
    由 Compiler 裁决 (Compiler > pattern/example)。
  - 非阻塞的辅助证据准备 (lookup index、coverage 了解等) 不得延迟首版
    FillSpec 落盘与首次 Compile。
```

措辞红线（grilling 已裁决）：

- **不写** lookup index 的具体操作句（"首版落盘后补"之类时序规定属于
  ticket 03 范围/不规定）；辅助准备只保留上面这条通用句。
- **不引入 COMMIT/状态/预算词汇** — 这是行为规则，不是新机制。
- 冲突消解必须保持"事实 = 证据不是权威"的表述，不得写成"工作簿事实 >
  pattern 默认值"这类可覆盖业务语义的宽泛权威链。
- 锚点例保持 3 阻塞 + 一组非阻塞，不扩成检查表。

## 改动 2 — §4 stop-rule 加一行交叉引用

在 "…**不再读 case 复盘 / 测试病历重推组合** (Case 07 改进 2: …)" 句后追加：

```markdown
此规则在首版撰写阶段同样适用 (见 §3「首版收敛原则」)。
```

只此一行，§4 其余文本（含 Case 叙事）不动。

## Acceptance

- [x] §3 收敛框 7 条齐全，措辞符合红线（无 COMMIT 词汇、无 lookup 时序句、
      冲突消解为"证据不是权威"表述）。
- [x] §4 含交叉引用行，且未改动 §4 其他内容。
- [x] 改动只触 `table-fill/SKILL.md`；不新增状态、脚本、Gate、预算。
- [x] 正常 happy path 新增阅读量 ≤ 一个框（约 15 行），零新增工具调用。

## Comments

### 验收记录（主 Agent，2026-08-31）

子代理 (ticket 01 执行+验收) 逐字符核对 + 主 Agent 复核，4/4 PASS，零改动：

- (a) §3 收敛框 7 条 bullet 与 ticket「改动 1」逐字一致（SKILL.md L433-454，
  1 标题 + 7 子条）；措辞红线符合：无 COMMIT/状态/预算词汇、无 lookup 索引
  时序句（仅保留 ticket 允许的通用句 L453-454）、冲突消解为"证据不是权威"
  表述（L450-451）。
- (b) §4 stop-rule 句后一行交叉引用（L530），diff hunk 仅 +1 行，§4 其余
  文本（含 Case 叙事）未动。
- (c) 本票 footprint 仅 table-fill/SKILL.md 两个 hunk；无新增状态/脚本/Gate/预算。
- (d) 收敛框位于 §3 撰写规程顶部、"- **先写后编译**"之前（L431/433-454/455）。

结论：ticket 01 验收通过，Status: resolved。

