# LAYER4_EXECUTE_LOOP.md — Draft 执行、验证与修复循环 (v2.5)

v2.5 只有**一次填充执行**。冒烟测试已删除: `execute_batch.py` 在模板副本上执行
plan 并**保留**结果为 `validated_draft.<ext>`; Execution Gate 批准后
`promote_output.py` 做哈希验证复制。Gate 后绝不再次执行填充。

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
8. **Render QA**: `--render png|html|none`。
   - png (多模态模型): `view screenshot --range <region>` → 视觉检查。
   - html (纯文本模型): `view html --range <region>` → 结构渲染检查,
     **不得声称视觉验证**。
   - 只渲染受影响区域 (plan.render_qa.region), 单次终局, 消耗 repair budget;
     产物生成失败 → RENDER_QA_FAILED (png 失败可降级 html — 属 budget 内
     一次 ADAPT)。
9. **Receipt** (`draft_receipt.json`): source 哈希 (来自 manifest)、
   template/spec/plan/draft 哈希、op 计数、source coverage、readback 通过数、
   structural、render_qa、issue delta、validate 结果、key_outputs。

失败 → exit 3 + `_draft_failure.json` (defect_class/standard_fix)。

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

## 提升 (promote_output.py)

| 检查 | 行为 |
|---|---|
| `.gate3_confirmed` 缺失 | GATE_NOT_CONFIRMED 拒绝 — marker 缺失不是确认 (fail-closed) |
| `.gate3_pending` 仍存在 | GATE_PENDING 拒绝 |
| 确认记录 / receipt / 当前 spec+plan+draft 三方哈希任一漂移 | HASH_DRIFT 拒绝 → 重新生成 draft + 重新 Gate |
| 原子复制 (同目录 tmp + 校验哈希 + os.replace) | 先删旧 final 是禁止的 — 复制或 replace 失败时旧交付文件必须保留 |
| final 哈希 == draft 哈希 | 不匹配 → FINAL_HASH_MISMATCH 拒绝 |
| ZIP 结构 (pptx 查 presentation.xml) | 损坏 → ZIP_STRUCTURE_INVALID 拒绝 |
| 通过 | 写 final_receipt.json |

## Timing

每个脚本自动追加 `{phase, started_at, duration_ms}` 到 `run_timing.json`:
prepare_outline / prepare_flatten / compile / draft_execute / promote。
Gate 展示和最终报告引用该文件, 不依赖聊天时间戳。
