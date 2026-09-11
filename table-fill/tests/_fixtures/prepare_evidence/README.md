# tests/_fixtures/prepare_evidence/ — Ticket 01 evidence E2E fixture

由 `tests/_fixtures/generate_prepare_evidence_fixture.py` 预生成（提交工作簿，
测试运行时只复制，不运行时生成——与 task_orchestration/e2e 同策略）。

- `evidence_bbox_book.xlsx` — data-neutral 合成工作簿（无真实型号/价格/客户名）：
  - Row 1 标题 / Row 2 列头（参数 | 单位 | 12K | 18K | 24K | 备注）/ Rows 3-12 数据；
  - 单元格用**共享字符串**（`t="s"`，非 openpyxl inlineStr）——officecli 1.0.145
    对共享字符串报告 `SharedString`，`detect_header_rows`（Case 010 契约，本票
    禁止改动）才能真正检出 header band；
  - A1:F12 带 thin border（样式域行/列锚点），显式 `<cols>` 列宽延伸至 Z——
    值域 A1:F12 与样式域 A1:Z12 分离（价值 bbox 不膨胀）。

期望 evidence 事实：`表头带: 行 [2] 数据起始行 3`、`有效值列: A B C D E F`、
`value_bbox: A1:F12`、`style_bbox: A1:Z12`、`候选列头: A=参数 ... F=备注`。