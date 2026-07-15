# PLATFORM_WINDOWS.md — Windows 平台适配参考

## 核心问题

Windows 上 `officecli` 的两个致命陷阱：

1. **PowerShell 管道编码**：`$OutputEncoding` 默认 ASCII，UTF-8 输出在管道中变成乱码（中文 → `"���"`）
2. **ChildProcess.kill**：大输出触发进程超时

**根本方案**：用 Python `subprocess.run()` 调用 officecli，绕过 PowerShell 管道。

## 调用模式

### 模式 A：Python subprocess（推荐，解决所有编码问题）

```python
import subprocess, json

result = subprocess.run(
    ['officecli', 'get', filepath, path, '--depth', '0', '--json'],
    capture_output=True
)
text = result.stdout.decode('utf-8')   # ← 中文正确显示
data = json.loads(text)
```

Python 的 `subprocess` 直接和 OS 进程通信，拿到原始字节流，不经 PowerShell 管道，不会触发 `$OutputEncoding` 的 GBK 转换。对所有格式（xlsx / pptx / docx）均适用。

### 模式 B：PowerShell 写文件（Python 不可用时的备选）

```powershell
officecli get "file.xlsx" "/Sheet/A1" --depth 0 --json > out.json
python -c "
import sys,io,json
sys.stdout=io.TextIOWrapper(sys.stdout.buffer,encoding='utf-8')
d=json.load(open('out.json','r',encoding='utf-16-le'))
# officecli 写文件时可能用 UTF-16 LE + BOM
"
```

### 模式 C：中文路径兜底

```powershell
Copy-Item -LiteralPath "C:\含中文\源.xlsx" -Destination "C:\Temp\eng.xlsx" -Force
officecli get "C:\Temp\eng.xlsx" "/Sheet/A1" --json
```

中文路径在某些 PowerShell 配置下会导致 officecli 找不到文件，复制到纯英文路径即可。

## 常见错误与修复

| 错误 | 原因 | 修复 |
|------|------|------|
| `ChildProcess.kill` | 输出太大，PowerShell 管道超时 | ① 降低 `--depth` ② 用 Python subprocess ③ 缩小查询范围 |
| 中文乱码 `"���"` | PowerShell `$OutputEncoding` 默认 ASCII | 用 Python `subprocess.run()`，不要直接在 PowerShell 管道中读 |
| 中文路径找不到文件 | 路径编码问题 | 复制到纯英文路径（模式 C） |

## 与其他 skill 的关系

本文件是 `officecli-xlsx` `officecli-docx` `officecli-pptx` 三个 skill 的 Windows 平台参考。各 skill 的 SKILL.md 中已在 Shell & Execution Discipline 节包含 Windows 平台核心指引，本文件提供完整的调用模式细节。

> PPTX 特有的 `--depth` 控制（防 `ChildProcess.kill`）已在 `officecli-pptx/SKILL.md` 的 Shell & Execution Discipline 节内联，见该节"PPTX 特有 — depth 控制"。
