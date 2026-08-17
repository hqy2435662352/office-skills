# TOOL_TRAPS.md — 工具摩擦清单 (Windows/bash/officecli)

本清单汇总 table-fill 运行中反复出现的工具层陷阱。**执行前扫一眼本文件,
可避免每轮 1-3 次无效调用。** 与 officecli-win 技能互补: 本文件聚焦调用习惯,
officecli-win 聚焦编码与 subprocess。

## 1. Windows/bash 环境陷阱

| 陷阱 | 现象 | 正确做法 |
|------|------|---------|
| bash for 循环 + 中文路径变量 | `for n in a b; do ... ${n}_meta.json ...` 展开失败, 报 META_NOT_FOUND | 避免在路径中做变量拼接; 显式写全路径或改用 Python 脚本 |
| `grep -P` (Perl 正则) | 报 "grep: -P supports only unibyte and UTF-8 locales" | 用基本 grep / `grep -E`, 或 Python re |
| raw PowerShell 管道 | GBK 编码损坏中文输出 (乱码) | 一律 Python `subprocess.run(..., encoding="utf-8")` |
| 中文路径 `set`/`batch` | Access denied (get 可能正常) | 所有输入先 stage 到 ASCII workdir (stage_files.py) |
| 输出文件只读属性 | copy2 保留源只读位 → officecli 写失败 | execute 已有 force_writable; 手工复制后用 `chmod +w` |

## 2. officecli 调用习惯

| 陷阱 | 现象 | 正确做法 |
|------|------|---------|
| `query 'Sheet!col[P]'` 语法 | "unknown key 'P'. Available: width..." | 列信息用 `get "<file>" "/Sheet/col[P]"` |
| `get` 多路径一次传入 | 输出混杂难解析 | 一次一个路径; 或用 `--json` + Python 解析 |
| 直接读 `view text` 全 sheet | 输出含所有 sheet 噪声, 撑爆上下文 | 用 `--start/--end` 限定; 或 grep sheet 段 |
| `view <file> issues` 与 `--json` 差异 | 文本视图与 JSON 字段不同; subtype 空时显示 `[]` | 需要结构化判断时用 `--json` + Python |
| 公式链 dump 巨大 | `query 'cell:has(formula)'` 输出数百行单元格 | 先 grep 出 `formula=` 片段, 或 Python 提取 (cell, formula) 对 |
| `view html` 流式输出 | 无落盘 HTML 文件 (仅 stdout) | 视觉 QA 用 `view text` 限定范围替代, 或重定向到文件 |
| L3 继承字段二次扫描 | 先 `query cell:contains(SKU)` 再 `view text` 找 D/F/X | 用 `build_inheritance_index.py` 一次 view text 生成结构化索引；禁止追加逐码查询 |
| 步骤耗时无法复盘 | 只看终端输出或聊天时间戳 | 脚本自动写 `run_timing.json`（machine 相位）+ `note_phase.py`（agent 思考），Gate 报告读取 manifest |

## 3. 执行/验证机制

| 陷阱 | 现象 | 正确做法 |
|------|------|---------|
| validated_draft 被当临时文件清理 | 执行后 draft 被删/移动, Gate 与提升前无法复查 | Draft **保留不删除** (SKILL.md §5)；复查/提升一律指向 `validated_draft.<ext>`；promote 是哈希验证复制, 永不二次执行 |
| resident 未 close 刷盘 | readback 全过但最终文件缺尾部 chunk 值 | 收尾显式 `officecli close` 刷盘 (execute_batch 已内置; 手写命令自己负责) — 见 KNOWN_TRAPS「resident 延迟写」 |
| 失败记录字段 | 新版本含 `defect_class` / `standard_fix` | **先读 standard_fix 再动手**, 不要逐格 get 侦查 |
| 手写 checks / batch | 与 Compiler 冲突 | readback 与 ops 必须由 compile_fill.py 派生; 手写即违规 (依据: SKILL.md「禁止手写 checks」/ LAYER4_EXECUTE_LOOP.md「Readback」/ compile_fill.py docstring) |

## 4. batch 生成

| 陷阱 | 现象 | 正确做法 |
|------|------|---------|
| 手写 batch.json 排序 | officecli batch 报排序违规 (排序是**全局**的, 不是按 sheet) | 一律 `compile_fill.py` 从 fill_spec.yaml 生成 plan |
| 克隆残留值 | `add --from` 克隆源行**值** (如 J=134), 未填列残留 | 规格 `nulls:` 显式置空所有未填列 |
| 长精度值溢出列宽 | text overflow issue | 值进规格前算字符数; 成本类数值用 4 位小数 |
| 浮点残值 | (报价-成本)×数量 = -4572.29999999999 | per_row 公式模板带 `ROUND(...,2)` |

## 5. 读文件习惯 (上下文瘦身)

| 陷阱 | 现象 | 正确做法 |
|------|------|---------|
| 全量读 meta.json (400-500 行) | 空列 SKIP 条目占 ~30%, 拖慢后续每回合 | 读 `structure_digest.py` 生成的 `*_结构摘要.md` (~40 行); meta 只按需 grep |
| 全量读展平 CSV | 标题行噪声 + 长备注行 | 摘要已含表头与列画像; CSV 按需 `--cols/--start/--end` |
| 目标侧 get 重查公式/numFmt | flatten 已确定性采集进 meta.formulas / column_numfmt / merge_anchors | 摘要直接读; 仅 L1 缺口清单 (≤5 项, 一次批处理) 可定向取数 |
| 读 skill 源码 | 失败时逐行读执行脚本 | 失败记录已有 defect_class/standard_fix; 先信记录, 源码最后手段 |

## V2 规范补充 (2026-08-10 复盘)

| 规范 | 内容 | 防的坑 |
|------|------|--------|
| **编码统一** | officecli 输出一律走 `_officecli.officecli()`（UTF-8 subprocess），产物一律 UTF-8 文件落盘后读取。禁止 PowerShell 管道、禁止把中文经控制台 print 再人工读。解码兜底：先按 UTF-8，若出现 U+FFFD 再用 GBK 试（不应发生——若发生说明调用绕过了共享适配器）。 | GBK 管道乱码、乱码 outline 喂给提名、试错解码浪费 |
| **路径绝对化** | 任何脚本调用（尤其带 `--cwd` 或 `workdir` 参数时）一律绝对路径：`python C:\...\scripts\flatten_workbook.py` 而非 `scripts/flatten_workbook.py`——`--cwd` 会切换子进程工作目录，相对路径直接 exit 2。V2 自带相位计时，旧 `run_timed.py` 包装已移除。 | run_timed --cwd 相对路径 exit 2 |
| **readback 不手写** | V2 的验证链只有 Compiler 派生的绝对路径 readback（`/Sheet/A1`、`/slide[N]/table[@id=M]/tr[X]/tc[Y]`）。旧版 `verify_output.py` 的裸路径歧义（`A25` → `/A25` 根路径假失败）已随该脚本删除而根治。手动抽查时一律 `Sheet!A1` 或 `/Sheet/A1` 全限定形式，禁止裸坐标。 | verify 假失败轮次、多跑一轮验证 |
