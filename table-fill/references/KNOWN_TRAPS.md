# Known Traps

已知陷阱速查。遇到异常时先查此表。

| 陷阱 | 症状 | 根因 | 修复 |
|------|------|------|------|
| **python-pptx 覆盖** | 输出文件有表结构但数据全空 | `pptx.save()` 将整个 PPTX 重写为 ZIP，覆盖 officecli 增量写入 | 先 python-pptx（建表）→ 保存关闭 → 再 officecli 填充。**禁止在 officecli 之后再调用 python-pptx save()** |
| **验证跑在错误文件** | 验证通过但输出文件为空 | 验证脚本读取了工作文件(temp)，不是最终输出文件 | 验证时使用 OUTPUT 变量路径，不是 WORK 变量路径。`verify_output.py --output <最终文件>` |
| **pp vs % 格式错误** | 增减率列显示 `+4.06pp` | 未扫描目标表已有格式约定 | Layer 3 映射前必须通读目标表。增减率统一用 `%` |
| **OLE 漏检** | 报告"slide 无表格" | 只查了 `table` 没查 `ole` | 总是先探测 `/slide[N]/ole[M]`，再报告 |
| **多数据块分配错误** | S20 和 S21 内容相同 | 未识别源文件的两块数据边界 | Layer 2 元数据 YAML 必须标注 block boundaries |
| **Access denied on set** | officecli set 报 PermissionError | 模板文件只读 | `os.chmod(file, stat.S_IWRITE)` after copy |
| **中文路径乱码** | officecli 输出乱码 | PowerShell GBK 编码 | 用 Python `subprocess.run()` + ASCII temp path |
| **PPTX merge corner cell leak** | 2×2 合并区域填充了多余格 | Cartesian product 覆盖集构建错误 | 用嵌套 `for dr in range(rs): for dc in range(cs):` |
| **Pivot 误判为 STANDARD** | pivot 表展平结果不对 | 读入范围过大稀释统计参数 | 先用 100 行确定实际数据边界，再精确读取 |
| **Layer 2 被跳过** | 没有 `_元数据.yaml` | 多轮交互后忘记产出 | `layer_gate.py --target 3` 会阻断 |
| **误以为需要 officecli open** | 在 batch 前尝试 `officecli open`，报错或浪费时间 | 按 openpyxl 思维理解 officecli——以为必须先"打开"文件再操作 | officecli 所有命令直接读写文件，不需要显式 open/save。`open` 只是可选的性能优化（启动常驻进程加速多次操作）。batch 操作直接跑即可 |
| **删行重建导致格式丢失** | 扩表后新行的边框、填充、字体全部丢失 | 先 `remove` 旧数据行再 `add` 新行——新行没有模板格式 | 正确顺序：① `set text=""` 清空旧值（保留格式）② 不够则 `add --from /Sheet/row[K]` 克隆格式行 ③ 只有源数据行数少于模板时才 `remove` 多余行 ④ 填充新值 |
| **`from` 不等于插入位置** | 克隆行全跑到 sheet 末尾，fill 操作在原位置写空 | `add` 的 `from` 只决定格式源，不指定位置时默认追加到末尾 | 必须同时设置位置参数：`"from": "/Sheet/row[K]", "after": "/Sheet/row[K]"`。`from` 选格式源，`after`/`before`/`index` 选插入位置，两者独立 |
| **`set text=""` 破坏公式格** | 公式变成纯文本数值，不再计算 | `set text=""` 将单元格标记为 literal 类型，后续 `set formula` 失效 | 清空步骤跳过公式列（将接收 `formula` 而非 `text` 的单元格）。公式格直接 `set formula="..."` 覆盖原格，无需先清空 |
| **flatten_table.py 漏数据** | 展平 CSV 行数少于源文件实际行数 | 旧版硬编码 `A1:Z80` 范围 | 2026-07-13 重写为纯 officecli 读取：先用 `view outline --json` 取行数提示，再用 `get` 宽范围读 + 从 cell path 反算真实范围 |
| **GBK 编码乱码** | 中文显示为 `���` | 源文件 Shared Strings 用 GBK 存储但 XML 声明 UTF-8 | 用 zipfile 提取 `xl/sharedStrings.xml`，以 `bytes.decode('gbk')` 手动解码 |
