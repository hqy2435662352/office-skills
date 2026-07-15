# Office Skills

An agent skill suite built on [OfficeCLI](https://github.com/iOfficeAI/OfficeCLI), packaging Office document automation workflows into reusable, auditable skill packages.

## Positioning

A professional tool suite that **prioritizes output quality and workflow correctness over speed**. Human Gates are not a bug — they are a design feature that ensures domain KNOW HOW is applied correctly.

Target audience:
- **Internal teams** who generate routine office documents
- **AI developers** integrating Office automation into OpenCode / Claude Code

## Architecture

```
OfficeCLI CLI (external tool)
        ↑
┌─────────────────────────────────┐
│  Base skills (reference manuals)│
│  officecli-xlsx                 │
│  officecli-docx                 │
│  officecli-pptx                 │
└─────────────────────────────────┘
        ↑
┌─────────────────────────────────┐
│  Workflow skills (pipelines)    │
│  table-fill                     │
│  chart-gen                      │
└─────────────────────────────────┘
```

**Base skills** are reference manuals — they teach the AI how to produce high-quality Office files using OfficeCLI (formatting standards, QA gates, visual delivery criteria).

**Workflow skills** are hardened pipelines — they wrap multi-step, gated Office automation tasks into deterministic processes (flatten → classify → map → fill, analyze → confirm → generate).

Workflow skills depend on base skills for domain knowledge but do not duplicate them.

## Skills

| Skill | Type | Description |
|-------|------|-------------|
| [officecli-xlsx](officecli-xlsx/SKILL.md) | Base · Reference | Excel workbook creation, formulas, charts, formatting, conditional formatting, data validation, QA |
| [officecli-docx](officecli-docx/SKILL.md) | Base · Reference | Word document creation, styles, tables, TOC, headers/footers, fields, tracked changes |
| [officecli-pptx](officecli-pptx/SKILL.md) | Base · Reference | PPT deck creation, design principles, charts, animations, connectors, delivery gates |
| [table-fill](table-fill/SKILL.md) | Workflow · Pipeline | 4-layer workflow (flatten → classify → map → fill), xlsx↔pptx data transfer with Human Gate |
| [chart-gen](chart-gen/SKILL.md) | Workflow · 3-step | 3-step workflow (analyze → confirm → generate), add charts to existing xlsx with Human Gate |

## Dependency Graph

Workflow skills must load their base dependencies first:

```
chart-gen ──requires──→ officecli-xlsx
table-fill ──requires──→ officecli-xlsx
                ──requires──→ officecli-pptx (when targeting PPTX)
```

Each workflow skill declares its exact loading requirements and verification steps in the `## ⚠️ 依赖加载` (Dependency Loading) section at the top of its SKILL.md.

## Getting Started

### In OpenCode

```typescript
// 1. Load a base skill
skill(name="officecli-xlsx")

// 2. Follow the SKILL.md from Quick Start through full QA

// When using a workflow skill, load dependencies first
skill(name="officecli-xlsx")
skill(name="table-fill")
```

### Install OfficeCLI

```bash
# macOS / Linux
curl -fsSL https://d.officecli.ai/install.sh | bash

# Windows (PowerShell)
irm https://d.officecli.ai/install.ps1 | iex
```

## Design Principles

### Three-Layer Knowledge Separation

| Layer | Content | Location |
|-------|---------|----------|
| Trigger | Skill name + description (< 200 Tokens) | SKILL.md frontmatter |
| Guide | Core rules and steps (< 500 lines) | SKILL.md body |
| Reference | Detailed specs, pitfalls, patterns | `references/` directory, lazy-loaded on demand |

Reference files are NOT read at load time. They are retrieved via targeted `grep` or conditional reads when a specific problem is encountered during execution.

### Deterministic Execution

- **Step anchors**: Each step in a multi-step workflow declares pre-conditions, post-assertions, and exit codes
- **Exit Code Protocol**: 0 = pass, 1 = environment error (halt), 3 = validation failure (fix and retry)
- **Human Gate**: Key decision points (data mapping, chart ranges) pause for domain KNOW HOW confirmation

### Quality Gates

Every base skill defines a visual delivery floor:
- **xlsx**: Zero formula errors, explicit column widths, no `###`, no placeholder leakage, print layout
- **docx**: Clear hierarchy, live header/footer fields, TOC, no empty paragraphs, cover ≥ 60% fill
- **pptx**: One idea per slide, explicit font sizes, ≥ 20% negative space, speaker notes, no AI tells

Workflow skills run EXIT GATE verification before delivery — files that fail the gate are not delivered.

## Windows Platform

Use **Git Bash** instead of PowerShell when running officecli commands on Windows.

Git Bash provides a Unix-style shell environment where all bash command snippets in this repo work as-is — no encoding issues, no process timeout problems.

Install Git for Windows from https://git-scm.com, then run officecli commands in Git Bash.

If you must use PowerShell, each skill's `references/PLATFORM_WINDOWS.md` provides encoding workarounds and process timeout mitigations.

## License

MIT
