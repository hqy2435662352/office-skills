# LAYER4_EXECUTE_LOOP.md — Draft 执行、验证与修复循环 (v3)

v3 只有**一次填充执行**。冒烟测试已删除: `execute_batch.py` 在模板副本上执行
plan 并**保留**结果为 `validated_draft.<ext>`; Spec Review 确认（唯一人工点）
后、verify 全绿即 `promote_output.py` 做哈希验证复制自动交付。deliver 后绝不
再次执行填充。

本文件承载执行期的**过程性知识** (SKILL.md 只留一行指针 — 渐进式披露原则):
刷盘顺序、结构 readback、Render QA 分支、失败码与修复预算。

## 执行 (execute_batch.py)

1. **前置**: `execution_plan.json` (compile_fill.py 产物) + staged target。
2. **复制模板** → `<workdir>/validated_draft.<ext>` (模板永不被修改;
   `_officecli.copy_template` 强制可写, 处理只读属性)。
3. **分块执行**: ≤50 op/chunk, chunk 间坐标探针 (`get` 首个 cell path)。
   首个失败 chunk 即停 (坐标系统可能已损坏)。transient 错误 (Windows
   resident/文件锁竞态) 清理后重试一次。
4. **resident 刷盘 (2026-08-12 实测)**: 坐标探针会启动 resident, 其后 chunk
   在**内存中**应用、磁盘写延迟到 save/close/idle。结尾 `clean_residents()`
   (taskkill) 会丢掉未刷盘的尾部 chunk (实测 E15–E19 随机缺失)。因此:
   chunk 循环尾 + 主流程结尾各显式 `officecli close` 刷盘 (无 resident 时
   close 是 no-op)。
5. **机器验证顺序重要**: `officecli validate` **先于** issue delta —
   validate 刷新待处理编辑并强制公式求值, 先查 issues 会把新写公式误报为
   formula_not_evaluated。issue delta 只认**新增** issue (模板自带基线 issue
   是噪音 — 埃及运行模板基线 235 条)。
6. **Readback**: 全部由 Compiler 从 plan 派生, 禁止手写 checks。
   - `value`: 数字归一化比较 (容忍 `$138.00` vs `138`、千分位、% 后缀),
     非数字精确比较。
   - `nonempty`: 公式格断言非空 (公式结果无法在编译期确定性计算)。
   - `empty`: nulls/required_empty 断言 EMPTY。
   - 批量读取: 单次范围 get (179 格 ≈ 1s, 而非逐格 ~90s)。
7. **结构 readback**:
   - **最终行数断言**: `plan.expected_final_row_count` (base 行数 + 全部结构
     delta: Σappend add − remove − trim + overflow) vs `view outline` 实际
     sheet 行数 → 不匹配 = FINAL_ROW_COUNT_MISMATCH。
   - **组边界断言**: 每个 group_merges 列查询数据区内 `format.merge` 集合,
     必须 == Compiler 推导的 expected_merges (singleton 永不合并)。残留
     单格合并 (A19:A19) 与陈旧合并都在集合差里显形 — `officecli validate`
     对合并残留视而不见, 组边界是唯一闭环检查 → 不匹配 =
     GROUP_BOUNDARY_MISMATCH。
8. **Render QA**: `--render png|html|none`, **默认 `html`** (issue 03 / Case 07
   改进 4 — 省略 `--render` 时按 html 执行, Agent 无需再自补 `view html`)。
   - png (多模态模型): `view screenshot --range <region>` → 视觉检查。
   - html (纯文本模型, 默认): `view html --range <region>` → 结构渲染检查,
     **不得声称视觉验证**。
   - 只渲染受影响区域 (plan.render_qa.region), 单次终局, 消耗 repair budget;
     产物生成失败 → RENDER_QA_FAILED (png 失败可降级 html — 属 budget 内
     一次 ADAPT)。`--render none` 仍显式可选。
9. **Receipt** (`draft_receipt.json`): source/template 哈希是**执行时重算值**
   (不再抄 manifest 的 outline 期 files[].sha256), 另记 `input_hash_check`
   = {bound, actual, drifted} (与 plan.input_hashes 编译期绑定的比对结果);
   另有 spec/plan/draft 哈希、op 计数、source coverage、readback 通过数、
   structural、`render_qa` (含默认 html 的执行结果, 计入机器证据)、issue delta、
   validate 结果、key_outputs。

失败 → exit 3 + `_draft_failure.json` (defect_class/standard_fix)。

## execute 失败自动复核 (T03 Execute Stability)

Task 编排 (`task_prepare.execute_worker`) 里 execute 子进程失败（非零退出或
超时/被杀窗口）**不直接进入失败恢复流程**，先走执行器边界的自动复核（复用
`_officecli` 适配器与 execute_batch 读回逻辑；`execute_batch.py` 本体与产物
契约零改动）：

1. **force flush / close**：`officecli close <draft>` 刷盘 resident 延迟写，
   再 `clean_residents()` 释放文件锁（顺序不能反——taskkill 会丢未刷盘的
   尾部 chunk，KNOWN_TRAPS「resident 延迟写被 taskkill 丢尾部 chunk」）。
2. **读回复核**：目标格 = 本次 plan 的**写入目标**（`plan.operations` 中可
   定位、可文本验证的 value/text op，非全文件扫描；复用
   `execute_batch.batch_read_cells` 批量范围 get，非 A1 坐标逐格 fallback）。
3. **决策**（纯函数 `task_prepare.review_execute_failure`，可 import 单测）：
   - `recovered` —— ≥1 目标格已含计划写值（readback 假失败场景：文件实际
     有值、读回为空）→ 恢复路径；
   - `retry` —— 无目标格含写值且未重试 → 一次自动重跑；
   - `confirmed` —— 无写值且已重试 → 确认失败（保留既有失败收敛路径：exit 3
     + failure record → 修 spec 重跑 / 失败二分重新初始化；不伪造 receipt、
     不跳过验证、不与输入事实变化的重新初始化混淆）。
4. **自动重跑一次**：recovered/retry 均自动重跑 execute_batch（单跑 19s，
   复核 ~1s → 失败重跑 <30s 目标）；重跑成功 → run 正常推进到 deliver，
   artifacts 带 `recovery` 注记（verdict/recovered_cells/retried）进阶段报告，
   重跑仍失败 → confirmed。

写阶段未到达（无 `validated_draft.*`：plan 缺失/输入漂移/复制失败）→ 无值
可复核，直接按既有失败收敛路径。

## 修复循环 (首次修复是预期路径)

```
_draft_failure.json → 修 fill_spec.yaml → compile_fill.py → execute_batch.py --round N+1
```

- **修复是预期步骤**: 编译/执行失败时按失败记录直接修 — **不询问用户、不提供
  放弃选项** (简化任务/手动 officecli/暂停都不是选项)。
- **预算只约束连续失败**: 独立静态检查一次性全部报告 (聚合诊断), 只有阻断性
  失败才 fail-fast; 第 2 次连续失败必须重新分类为 **ASK** (多个安全解释) 或
  **STOP** (无可证明安全计划) — 不得静默再修, 也不得以"时间/复杂度"为由放弃。
- 禁止自由实验 (逐格 get 侦查、机制试探); 定向验证 ≤2 次, 超限按
  standard_fix 执行。
- 错误码判定标准: 为什么这个错误不能在前一层被消灭 (capacity 超限是 ADAPT
  不是错误; INPLACE_REGION_OUT_OF_BOUNDS 只表达模型事实矛盾)。
- 失败码 → 标准修复见 `references/FAILURE_CLASSES.md` (含 v2.5 三码)。

## 交付 (promote_output.py)

| 检查 | 行为 |
|---|---|
| `draft_receipt.json` 缺失 | RECEIPT_NOT_FOUND 拒绝 — 无已验证 draft 存在的证据 (fail-closed) |
| receipt 损坏 | RECEIPT_INVALID 拒绝 |
| receipt / 当前 spec+plan+draft 三方哈希任一漂移 | HASH_DRIFT 拒绝 → 重新生成 draft + 重新验证 |
| **输入哈希三方核对 (2026-08-13)** | plan.input_hashes (编译期绑定) / receipt 执行时值 (source_hashes + template_sha256) / 当前 staged 文件 — 任一漂移或证据缺失 → HASH_DRIFT 拒绝 (fail-closed) |
| 原子复制 (同目录 tmp + 校验哈希 + os.replace) | 先删旧 final 是禁止的 — 复制或 replace 失败时旧交付文件必须保留 |
| final 哈希 == draft 哈希 | 不匹配 → FINAL_HASH_MISMATCH 拒绝 |
| ZIP 结构 (pptx 查 presentation.xml) | 损坏 → ZIP_STRUCTURE_INVALID 拒绝 |
| 通过 | 写 final_receipt.json |

