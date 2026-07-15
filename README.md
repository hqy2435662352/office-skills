[English version](README_EN.md)

# Office Skills

基于 [OfficeCLI](https://github.com/iOfficeAI/OfficeCLI) 的智能体技能套件，将 Office 文档自动化的工作流固化为可复用、可审计的技能包。

## 定位

专业工具套件，**追求生成质量和流程正确性优先于速度**。Human Gate 不是缺陷，是确保行业 KNOW HOW 正确落地的设计特征。

适合以下用户群体：
- **自身团队**：日常报表生成的标准工作流
- **AI 开发者**：在 OpenCode / Claude Code 中集成 Office 自动化能力的可复用技能

## 架构

```
OfficeCLI CLI（外部工具）
        ↑
┌─────────────────────────────┐
│  基础 skill（参考手册）       │
│  officecli-xlsx              │
│  officecli-docx              │
│  officecli-pptx              │
└─────────────────────────────┘
        ↑
┌─────────────────────────────┐
│  工作流 skill（流程固化）     │
│  table-fill                  │
│  chart-gen                   │
└─────────────────────────────┘
```

**基础 skill** 是参考手册——告诉 AI 如何使用 OfficeCLI 写出高质量文件（格式规范、QA 门禁、视觉交付标准）。

**工作流 skill** 是固化流程——将多步骤、有门禁的 Office 自动化任务封装为确定性流水线（展平→分类→映射→填充、分析→确认→生成）。

工作流 skill 依赖基础 skill 提供领域知识，但不重复定义。

## 技能目录

| Skill | 类型 | 功能 |
|-------|------|------|
| [officecli-xlsx](officecli-xlsx/SKILL.md) | 基础 · 参考手册 | Excel 工作簿的创建、公式、图表、格式化、条件格式、数据验证、QA |
| [officecli-docx](officecli-docx/SKILL.md) | 基础 · 参考手册 | Word 文档的创建、样式、表格、目录、页眉页脚、域字段、修订 |
| [officecli-pptx](officecli-pptx/SKILL.md) | 基础 · 参考手册 | PPT 演示文稿的创建、设计原则、图表、动画、连接线、交付门禁 |
| [table-fill](table-fill/SKILL.md) | 工作流 · 流水线 | 四层工作流（展平→分类→映射→填充），xlsx↔pptx 任意方向的数据填充，含 Human Gate |
| [chart-gen](chart-gen/SKILL.md) | 工作流 · 三步流 | 三步工作流（分析→确认→生成），为已有数据的 xlsx 文件创建图表，含 Human Gate |

## 依赖关系

工作流 skill 必须在加载前先加载其依赖的基础 skill：

```
chart-gen ──需要──→ officecli-xlsx
table-fill ──需要──→ officecli-xlsx
                ──需要──→ officecli-pptx（当目标为 PPTX 时）
```

每个工作流 skill 的 `## ⚠️ 依赖加载` 节中声明了完整的加载要求和验证方式。

## 快速开始

### 在 OpenCode 中使用

```typescript
// 1. 加载基础 skill
skill(name="officecli-xlsx")

// 2. 创建工作簿
// SKILL.md 中包含了从 Quick Start 到完整 QA 的指南

// 加载工作流 skill 时先加载依赖
skill(name="officecli-xlsx")
skill(name="table-fill")
```

### 安装 OfficeCLI

```bash
# macOS / Linux
curl -fsSL https://d.officecli.ai/install.sh | bash

# Windows (PowerShell)
irm https://d.officecli.ai/install.ps1 | iex
```

## 设计原则

### 三层知识分离

| 层 | 内容 | 存放位置 |
|----|------|---------|
| 触发层 | skill 名称和描述（< 200 Tokens） | SKILL.md frontmatter |
| 指引层 | 核心规则和步骤（< 500 行） | SKILL.md 主体 |
| 参考层 | 详细规范、陷阱、案例 | `references/` 目录，按需懒加载 |

参考层文件不在加载时一次性读入，而是在执行过程中遇到具体问题时通过 `grep` 或按需读取检索。

### 确定性执行

- **步骤锚点**：多步骤流程每一步有前置条件、后置断言、退出码
- **Exit Code Protocol**：0=通过，1=环境错误（中断），3=业务校验失败（修复后重试）
- **Human Gate**：关键决策点（数据映射、图表范围）由行业 KNOW HOW 用户确认后继续

### 质量门禁

每个基础 skill 定义了视觉交付标准：
- **xlsx**：零公式错误、显式列宽、无 `###`、无占位符泄漏、打印布局
- **docx**：清晰层级、活页眉页脚字段、目录、无空段落、封面 ≥60% 填充
- **pptx**：单页单观点、字号显式设、≥20% 留白、speaker notes、无 AI 套路

工作流 skill 在输出前通过 EXIT GATE 验证，门禁不通过不交付。

## Windows 平台

推荐使用 **Git Bash**（而非 PowerShell）作为 shell 环境来运行 officecli 命令。

Git Bash 提供 Unix 风格的 shell 体验，本仓库中所有 SKILL.md 的 bash 命令片段可直接运行，无编码问题，无管道超时问题。

安装 Git for Windows（https://git-scm.com）后，在 Git Bash 中执行 officecli 命令即可。

## License

MIT
