# Known Traps

已知陷阱速查。遇到异常时先查此表。

| 陷阱 | 症状 | 根因 | 修复 |
|------|------|------|------|
| **python-pptx 覆盖** | 输出文件有表结构但数据全空 | `pptx.save()` 将整个 PPTX 重写为 ZIP，覆盖 officecli 增量写入 | 先 python-pptx（建表）→ 保存关闭 → 再 officecli 填充。**禁止在 officecli 之后再调用 python-pptx save()** |
| **验证跑在错误文件** | 验证通过但输出文件为空 | 验证脚本读取了临时文件而不是最终文件 | 验证/readback 一律指向 `validated_draft.<ext>`（执行后）与 final（提升后）路径 |
| **add 之间穿插 cell 写入 → 重复行 (2026-08-10)** | XML 出现两个 `<row r=N>`；部分格的值/公式/合并落到错误元素，readback 与 validate 表现不一致 | 在 `add` 操作之间对已克隆行写 value 破坏 officecli 行簿记（如标题克隆后立即写标题文本，随后继续 add 表头/数据行） | 所有 cell 写入必须排在全部 add/remove 之后 — `compile_fill.py` 内置此全局排序；标题克隆文本在 fills 阶段写入。**禁止手写 batch 绕过** |
| **`precision: keep` 对抗 overflow 无效 (2026-08-10)** | 保留 15 位面价后新格全触发 overflow | "旧块也存全精度"不构成豁免 — issue delta 按路径计数, 旧块同款问题是基线噪音, 新格全算新增 | 真消除换行: 直接值 `round4` + 公式 ROUND 精准 (见 FAILURE_CLASSES); `precision: keep` 只在列宽确有余量时用 |
| **ROUND 扫射放大毛利 (2026-08-10)** | 给 R 结算价=P+Q 加 ROUND → 168.7151 截成 168.72 → 毛利 (O−R)×数量 从 −115181.63 漂到 −115200 | ROUND 加在了无残值的加法上 | ROUND 只加在减法/乘法/除法/SUM; P+Q 保持原式 |
| **模板行高 20pt + 长值 → text overflow (2026-08-10)** | 数据行固定 customHeight=20pt, 长值换行 2 行 24pt > 20pt 判溢出 | 模板行高窄 + 超长渲染文本 | round4/ROUND 消除换行; 行高调整属模板层, 不动 |
| **克隆源=合并锚点行 → 公式残留** | 新块非锚点单元格残留克隆公式（如 V28 残留 `SUM(T28:T30)`），validate/issues/readback 均不报 | `add --from` 把锚点行公式随行复制到新行；合并后非锚点单元格残留公式 | data 的 template_row 一律选**非锚点数据行**（同格式即可）。Compiler 静态检查 CLONE_SOURCE_IS_ANCHOR |
| **克隆残留值 (empty_cell)** | 新行出现模板行旧值（如 J=134 带入） | `add --from` 复制值 + 格式 | 未填列必须进 `nulls`；Compiler 自动对照 template_row 展平值检查 CLONE_RESIDUE_UNHANDLED |
| **浮点残值 → text overflow** | 派生公式渲染 `7747.50000000001`，issues 报 overflow | 浮点运算长尾 + 列宽不足 | 派生公式 `ROUND(...,2)`；长精度成本 4 位小数 |
| **空串公式 → text overflow 误报** | issues 报 overflow 在**故意留空**的单元格（缺失输入行） | 检测器对"公式返回空串 + wrapText + 行高不足"误报 | 缺失输入行**禁用空渲染公式**，统一 0-口径公式链（缺失格留空、净价=0、毛利=(0−结算价)×数量、损益率零分母保护） |
| **`set value:""` 破坏公式格** | 公式变成纯文本数值 | `value:""` 将单元格标记为 literal，后续 `set formula` 失效 | 公式格直接 `set formula` 覆盖，不先清空（Compiler 的 formula 列不进 clear 阶段） |
| **`from` 不等于插入位置** | 克隆行全跑到 sheet 末尾 | `add` 的 `from` 只决定格式源，不指定位置时默认追加到末尾 | 必须同时给 `after`；Compiler 自动生成 `after: /Sheet/row[N-1]` |
| **删行重建导致格式丢失** | 扩表后新行边框/填充/字体丢失 | 先 remove 旧数据行再 add 新行 | 正确顺序：① 克隆格式行 ② 只有源行数 < 模板行数时才 remove（自底向上）③ 填充 |
| **`text` 属性漂移** | batch 用 `text` 部分生效、与 `value` 混用时值丢失 | `text` 不是 officecli 官方属性（help 无此键），仅未文档化兼容别名 | 一律写 `value`；清空用 `{"value": ""}`，置空用 `{"value": null}` |
| **`issues` 不是独立命令** | `officecli issues` 报 Unrecognized command | `issues` 是 `view` 的 mode（`officecli view <file> issues`） | 结构检查用 `officecli view <file> issues --json`（响应在 `data.issues`）。脚本已按此实现 |
| **`numFmt` 大小写歧义** | 部分版本读回格式丢失 | help 官方键是 `numberformat`（别名 `format`/`numfmt`） | 统一写 `numberformat` |
| **Access denied on set** | officecli set 报 PermissionError | 模板/副本只读（copy2 保留只读属性） | `_officecli.force_writable` 在每次复制后执行 |
| **中文路径乱码/拒绝** | 乱码或 Access denied | PowerShell GBK 编码；officecli set/batch 不支持中文路径 | Python `subprocess.run()` UTF-8；一律 stage 到 ASCII workdir |
| **验证对象=模板基线 issue 噪音** | 输出仍有几百条 issues，误判失败 | 模板自带基线 issue（埃及模板 235 条） | 只认 issue **delta**（输出 − 模板）；`issues_delta` 内置 |
| **flatten 标题行虚假数据** | 展平 CSV 中横向合并标题行显示上一数据行值 | 旧前向填充把合并区覆盖当纵向合并 | flatten 已修复（仅纵向合并传播锚点值）；重新 flatten |
| **全表继承查询重复扫描** | 逐码 get D/F/X，耗时和上下文暴涨 | SKU 命中不携带相邻角色字段 | 用 `build_inheritance_index.py` 一次 `officecli view text`；索引生成后封闭取数 |
| **stale spec 静默执行** | 结构变了还在用旧 spec 填充 | 无 fingerprint 校验 | Compiler 强制 fingerprint 匹配 (FILLSPEC_FINGERPRINT_MISMATCH)；结构变化 → 重跑 prepare_run |
| **resident 延迟写被 taskkill 丢尾部 chunk (2026-08-12)** | readback 全过但最终文件缺最后一批值 (如 13 行填了前 8 行) | 坐标探针 `officecli get` 启动 resident 后, 后续 batch 在内存中应用、磁盘写延迟到 save/close/idle；结尾 `clean_residents()` taskkill 把未刷盘的 chunk 丢了 | `execute_batch.py` 已在 chunk 循环尾部与主流程结尾显式 `officecli close` 刷盘 (无 resident 时 close 是 no-op)；遇此症状先重跑执行并核对 `_draft_failure.json` |
| **selectors 用目标列数校验 (2026-08-12)** | 27 列源表进 6 列目标时报 `selector column 'L' out of range` | selector 校验误用目标 dims.cols | Compiler 已改按**源表实际宽度**校验 selector 列 |

## spike 四坑 (pptx 合并 lowering)

来自能力 spike (officecli 1.0.143, fixture.pptx) — **先于实现验证的机械事实**:

| 陷阱 | 症状 | 修复 |
|------|------|------|
| **删合并锚点行 → 悬挂 vMerge** | 删除行后 `validate` 不报, 但 XML 里留下悬空 vMerge 延续 | lowering 必须清理; 逐格 `vmerge=false` 重置 |
| **unmerge 不是单动词** | pptx 没有一步 unmerge | 锚点格 `rowspan=1` + 每个延续格 `vmerge=false` |
| **`merge.down=N` 总跨度 N+1** | 以为合并 2 行写 `merge.down=2` → 实际 3 行, 吞掉下一组 label 格 | 跨度语义: down=N = 含锚点共 N+1 行 (镜像 `merge.right=N`); 文档化, 别在运行时重猜 |
| **validate 对合并残留视而不见** | xlsx 单格残留 A19:A19 / pptx 悬挂 vMerge 都不报 | 组边界断言由 Compiler 推导 + 执行器 readback (get format.merge 集合比对) 闭环 |

## inplace 位置模型认知陷阱

| 陷阱 | 症状 | 正确心智 |
|------|------|------|
| **把 inplace 行号当可移位坐标** | spec 里写 trim 后的行号, 或 set 写到最终坐标 | `start_row` / `sets.path` 一律**模板坐标**; 移位由 Excel 自然发生, Compiler 只翻译 readback |
| **占位区行号凭空推导** | 容量/起始行与 digest 不符 → INPLACE_REGION_OUT_OF_BOUNDS | 显式声明 + 编译期校验; 不猜 |
| **trim 不是尾部** | 想把多余占位行从中间删 | trim 恒为尾部 (编译器推导, 不写 remove_rows) |
| **inplace 块后面跟块** | 插入点歧义 | inplace 恒为末块 (INPLACE_NOT_LAST_BLOCK) |
| **删行后重合并被单格残留卡住** | A19:A19 残留使 `merge:true` 被拒 | 组重建 lowering 先逐行 unmerge (含单格残留) 再建合并; singleton 永不合并 |
| **`merge:false` 逐格即可拆合并** | 手动脚本用 range 级 `A11:A14 merge:false` | 编译器按数据行逐格 `merge:false`, 同样生效且无需翻译 digest 范围 |
| **set 写最终坐标** | A36 (埃及条款行) 写成 A31 | spec 写模板坐标 A36, trim 后自然到 A31; readback 已翻译 |

## 源码阅读与实验纪律 (2026-08-12)

组合行为知识 (特性交互的接受性) 已前移到 FILLSPEC「组合行为契约」+「能力映射
表」; 编译输出是权威缺陷清单。以下来自 2026-08-12 实测复盘 (8 次源码阅读中
3 次不必要、2 次半必要、1 次有害):

| 陷阱 | 症状 | 正确路径 |
|------|------|------|
| **读了源码反而做错决策 (precision: keep 反例)** | Agent 读 `apply_precision_policy` 后自选 `precision: keep` 绕过文档推荐的 `round4` → 第一轮 text_overflow 失败 | 信任 FILLSPEC 推荐次序: `round4` 优先, `keep` 仅列宽足够时; 用编译验证, 不用源码预读 |
| **staged 副本污染** | spike 实验直接在 staged 副本上做 → 文件被改 → 重新 flatten (+10s 机器 + 心智负担) | spike 永远用独立 **scratch** 文件; staged 文件只读 |
| **源码阅读代替编译验证** | 为确认一个行为读源码 25 次交互 (机器 63s 的工作拖成 15–25 分钟) | **先写后编译**: compile 一轮 ~0.1s, stderr 缺陷清单 (code + corrective_action) 即权威反馈; 仅「文档未覆盖 且 报错无法解释」才读源码 |
| **YAML 引号漏写** | `decisions`/`gaps` 含 `: ` 的裸标量被解析成 mapping, 内容静默丢失 | 含 `: `/引号/特殊字符的字符串统一加引号; SPEC_NON_STRING_ITEM 是兜底不是常态 |
| **note_phase 缺失** | run_timing.json 只有 machine 条目, Gate 报告缺 Agent 时间栏 | 关键相位 (mod_resolution/spec_authoring/compile_review/execute_review/gate_wait) 至少各记一次 |

## 组合行为陷阱 (2026-08-12 实测)

| 陷阱 | 症状 | 正确路径 |
|------|------|------|
| **nulls × aggregates 同列** | 锚点格 `DUPLICATE_TARGET_WRITE` (特征 "first as empty" — nulls 先清空, 聚合再写公式) | 聚合列不进 nulls; 值所有者五选一 (mapping/per_row/aggregate/nulls/group) |
| **聚合列进 nulls 表达"每组合计"** | 聚合锚点 `DUPLICATE_TARGET_WRITE` (特征 "first as empty" — nulls 先清空锚点格, 聚合再写公式); 曾误判为「硬编码范围必然漂移 → 只有拆块」(2026-08-13 契约修正: 最小 spec 实证触发因素就是聚合列进 nulls) | 聚合列不进 nulls — 同形 spec (单块 + 显式范围聚合) 编译通过 (埃及最终方案); 复制即用见 combination_patterns.yaml `per_group_total_explicit_ranges` |
| **`nulls rows` 用 `["1:2","3:4"]` 混合列表** | probe 抛 Python traceback 而非缺陷清单 (int("1:2") 崩溃) | 用 `rows: all` / int 列表 / `"a:b"` 字符串; 编译器现以 NULLS_ROWS_INVALID 结构化拒绝 |
| **`officecli get --depth 0` 查不到 mergeCell** | 合并验证漏报 | 用 `officecli query merge` (或 execute readback 的组边界断言), 别靠 get 的单元格属性 |
| **重复验证已覆盖事实** | 为确认 aggregates 锚点/克隆残留行为重复读 tests 与源码 (FILLSPEC Q1/Q10 已声明) | 契约章节未写的问题才查; 机械事实 (如克隆是否携带合并) 先在 KNOWN_TRAPS 找答案 |

## 已 spike 确认的机械事实 (免重复 spike)

| 事实 | 结论 |
|------|------|
| **克隆携带合并区** | `add --from` 复制 template_row 的格式+值+**mergeCell** (实测: 克隆合并标题行 A1:F1 → 克隆行 A41:F41 带合并) — 标题/表头克隆源选合并行无需额外合并 op; data 行克隆携带的旧合并是 group_merges unmerge 的对象 |
| **merges × aggregates 同列** | `merges 1:{n}` + `aggregates 1:{n}` 同列编译通过 (聚合锚点=合并锚点=块首行); 同列多条 aggregates 用显式范围 (2:2、3:3) 做块内多组小计; merges+多组显式范围同列**不建议** (聚合锚点落合并区非锚点格, 执行期未验证) |
