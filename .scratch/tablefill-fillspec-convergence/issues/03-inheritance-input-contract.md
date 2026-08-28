# 03 — inheritance/lookup 输入使用角色契约（3 触点，纯 doc）

Status: resolved
Type: doc（`table-fill/references/FILLSPEC.md` + `table-fill/SKILL.md` 一句 +
`table-fill/scripts/build_inheritance_index.py` docstring/--help）
依据: spec.md §2 事实 3。session eg_fresh_local 把 `01_埃及机型`/
`09_Fresh拖多`（staged target 工作簿的 sheet）喂进 `prepare_run --flatten`
→ manifest 条目合并、`manifest.target` 被改写为 `09_Fresh拖多`、fingerprints
重算 → 两次污染 + 恢复链 ≈20 分钟。全仓库此前没有任何地方写 lookup 用途
与 fill manifest 的边界。

## 契约本体（权威定义，落在 FILLSPEC.md）

按**使用角色（usage role）**划界，不是按 sheet 身份或工作簿位置划界。核心
规则一句：

> **Do not flatten a sheet solely because you need it for lookup.**
> manifest membership 由 fill-source 用途决定，不由"某 sheet 是否也被用于
> lookup"决定。

```markdown
### Fill source use vs lookup-only use (硬性角色边界)

- 某 sheet 作为 **fill source** 被消费（本次要展平并写进目标）→ 属于 fill
  manifest，经 `prepare_run --flatten` 进入。
- 某 sheet **仅为 lookup/inheritance** 被读取 → 不得为了构建索引把它
  flatten 进当前 fill manifest；用 `build_inheritance_index.py --input
  <staged.xlsx> --sheet <name>` 直接从 staged workbook 构建索引，不经
  flatten。
- 同一 sheet 同时承担 fill source 与 lookup 两种用途是**合法**的 — lookup
  用途不改变、不重复其 manifest 身份；禁止把本契约读成"某类 sheet 天生
  不能做 fill source"。
- 违反后果（机械事实）: 对仅为 lookup/inheritance 读取的 sheet 执行
  `--flatten` 会合并 manifest 条目并可改写 `manifest.target`、重算
  source/target fingerprints → `fill_spec.fingerprints` 失配 → 编译拒绝，
  且需手工恢复 manifest。
```

落点：FILLSPEC.md lookups 节（约 L302 "`from` 路径相对于 workdir…
`build_inheritance_index.py` 的输出"处）+ Q15（索引清洗/重建条目）附近
交叉引用一句。**FILLSPEC.md 是此契约的唯一权威副本。**

## 触点 2 — SKILL.md §1 flatten 说明处一句就地提醒

在 "flatten 可**多次调用增量展平**…" 条目后追加一行：

```markdown
- **不得仅为构建 lookup/inheritance 索引而把 sheet flatten 进当前
  manifest** — 索引直接用 `build_inheritance_index.py` 读 staged
  workbook (契约见 FILLSPEC「Fill source use vs lookup-only use」)。
```

一句为止，不复制契约正文。

## 触点 3 — `build_inheritance_index.py` docstring + `--help`

docstring 首段补一句，argparse 加 `epilog=`：

```text
When a sheet is used only as a lookup/inheritance input, build the index
directly from the staged workbook (--input/--sheet). Do not add a sheet to
the fill manifest solely for lookup/index construction: manifest membership
is decided by fill-source use, not by lookup need.
```

## 明确不做

- **不新增 KNOWN_TRAPS 独立条目**（避免第四份契约副本）。可选：在现有
  "索引自引用"条目（约 L32）尾部并一句指向 FILLSPEC 契约，不改其权威
  定义。
- **不规定索引构建时序**（首版前/后均可，ticket 01 已有通用句）。
- **不把契约写成 sheet 身份二选一** — 本票修订核心：互斥的是"一次使用的
  角色"，不是 sheet 的身份；合法的双用途组合不被封死。
- **不加 prepare_run runtime 护栏** — 记入 spec Deferred（角色边界的
  runtime 化），本轮纯 doc。

## Acceptance

- [x] FILLSPEC.md 含唯一权威契约段，按使用角色表述，且含三要素：①
      "manifest membership 由 fill-source 用途决定"总则；② "同一 sheet
      双用途合法、manifest 身份不变"反过度泛化句；③ 违反后果（机械
      事实）。
- [x] SKILL.md §1 仅一句就地提醒并指向 FILLSPEC 契约，措辞为"仅为索引
      不得 flatten"而非"lookup sheet 不得为 fill source"。
- [x] `build_inheritance_index.py --help` 输出含 happy path 与
      solely-for-lookup 禁止句；脚本行为零改动（docstring/argparse help
      除外）。
- [x] KNOWN_TRAPS 无新增独立条目。


## Comments

### 验收记录（主 Agent，2026-08-31）

子代理执行 + 主 Agent 独立复核，4/4 PASS（纯插入；脚本行为零改动；无 commit）：

1. FILLSPEC.md L313-327 唯一权威契约段「Fill source use vs lookup-only use
   (硬性角色边界)」，按使用角色表述，三要素齐：① fill-source 用途决定
   manifest membership 总则；② "同一 sheet 双用途合法、manifest 身份不变"
   反过度泛化句；③ 违反后果机械事实（合并条目/改写 manifest.target/重算
   fingerprints → fill_spec.fingerprints 失配 → 编译拒绝 + 手工恢复）。
   Q15 交叉引用一句在 L607（仅指向，不复制正文）。
2. SKILL.md §1 L111-113 仅一句就地提醒：「不得仅为构建 lookup/inheritance
   索引而把 sheet flatten 进当前 manifest」，措辞为"仅为索引不得 flatten"
   而非"lookup sheet 不得为 fill source"，指向 FILLSPEC 契约。
3. build_inheritance_index.py：docstring 首段补票中英文句；
   argparse 增 epilog= 含 happy path（--input/--sheet）与 solely-for-lookup
   禁止句；--help 实测输出两段齐全；AST 解析通过；脚本行为零改动
   （formatter_class 仅影响 help 排版）。
4. KNOWN_TRAPS 无新增独立条目：仅「索引自引用」行（L32）尾部一句指向
   FILLSPEC 契约（可选条款，指针非副本）。主 Agent grep 复核：契约引用
   出现于 FILLSPEC L313/L607、SKILL L113、KNOWN_TRAPS L32，均按票落点。

结论：ticket 03 验收通过，Status: resolved。
