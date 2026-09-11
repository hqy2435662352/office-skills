#!/usr/bin/env python3
"""Dev-time generator for the Ticket 01 evidence E2E fixture.

⛔ 测试运行时绝不 import 本脚本：与 generate_task_orchestration_e2e.py 同策略
   —— 预生成合成工作簿提交到 tests/_fixtures/prepare_evidence/，运行时只复制。

为什么手工写 XML 而不是 openpyxl：officecli 1.0.145 对 openpyxl 生成的
inlineStr 单元格报告 format.type=="InlineString"，而 detect_header_rows 按
"SharedString" 判定（Case 010 契约锁定，本票禁止改动检测算法）→ 表头带在
openpyxl fixture 上永不触发。本 fixture 用 `t="s"` 共享字符串 + 真实样式
(border s="1") + 显式列宽 (<cols> G..Z)，使 header band / value_bbox /
style_bbox 全链路在 officecli E2E 中真实可达。

Fixture 设计 (data-neutral，无任何真实型号/价格/客户名)：
  - Row 1:    A1 标题 "产品参数表"
  - Row 2:    A2..F2 列头: 参数 | 单位 | 12K | 18K | 24K | 备注
              (12K/18K/24K 为通用产品系列占位词, 同 ticket 措辞示例)
  - Rows 3-12: A=字段-N (字段标签), B=kW (单位), C/D/E=数字, F 留空
              → 数据行 text 密度低于表头带最大密度 → band=[2], 数据起始行 3
  - Styles:   A1:F12 全带 thin border (xf1) —— 样式域行/列锚点
  - 列宽:     显式 <cols> 延伸至 Z (G..Z width 8) —— 样式域列锚点超值域

期望机械事实:
  - header_band: {"header_rows": [2], "data_start_row": 3}
  - value_bbox:  A1:F12   (非空值范围)
  - style_bbox:  A1:Z12   (样式 A..F + 显式列宽至 Z; 行取自样式格 1..12)

用法:
  python tests/_fixtures/generate_prepare_evidence_fixture.py
"""

from __future__ import annotations

import zipfile
from pathlib import Path

OUT = Path(__file__).resolve().parent / "prepare_evidence" / "evidence_bbox_book.xlsx"

CT = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
<Override PartName="/xl/sharedStrings.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/>
</Types>"""

RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>"""

WB = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
 xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
<sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets>
</workbook>"""

WB_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/sharedStrings" Target="sharedStrings.xml"/>
</Relationships>"""

STYLES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<fonts count="1"><font><sz val="11"/><name val="Calibri"/></font></fonts>
<fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills>
<borders count="2">
<border><left/><right/><top/><bottom/><diagonal/></border>
<border><left style="thin"/><right style="thin"/><top style="thin"/><bottom style="thin"/><diagonal/></border>
</borders>
<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
<cellXfs count="2">
<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>
<xf numFmtId="0" fontId="0" fillId="0" borderId="1" xfId="0" applyBorder="1"/>
</cellXfs>
</styleSheet>"""


def _shared_strings(tokens: list[str]) -> str:
    si = "".join(f"<si><t>{t}</t></si>" for t in tokens)
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        f'count="{len(tokens)}" uniqueCount="{len(tokens)}">\n{si}\n</sst>'
    )


def _sheet_xml(shared_idx: dict[str, int]) -> str:
    rows = []
    # 列宽延伸: G..Z 显式宽度 (样式域列锚点超值域 F)
    cols = ('<cols><col min="7" max="26" width="8" customWidth="1"/></cols>')
    # Row 1: 标题
    r1 = '<c r="A1" s="1" t="s"><v>{}</v></c>'.format(shared_idx["产品参数表"])
    rows.append(f'<row r="1">{r1}</row>')
    # Row 2: 列头 (参数/单位/12K/18K/24K/备注)
    head = "".join(
        f'<c r="{col}2" s="1" t="s"><v>{shared_idx[tok]}</v></c>'
        for col, tok in zip("ABCDEF", ("参数", "单位", "12K", "18K", "24K", "备注")))
    rows.append(f'<row r="2">{head}</row>')
    # Rows 3-12: 数据 (A 字段标签, B 单位, C/D/E 数字, F 留空)
    for i in range(3, 13):
        cells = (
            f'<c r="A{i}" s="1" t="s"><v>{shared_idx[f"字段-{i - 2}"]}</v></c>'
            f'<c r="B{i}" s="1" t="s"><v>{shared_idx["kW"]}</v></c>'
            f'<c r="C{i}" s="1"><v>{12000 + i}</v></c>'
            f'<c r="D{i}" s="1"><v>{18000 + i}</v></c>'
            f'<c r="E{i}" s="1"><v>{24000 + i}</v></c>'
        )
        rows.append(f'<row r="{i}">{cells}</row>')
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">\n'
        '<dimension ref="A1:Z12"/>\n' + cols + "\n<sheetData>\n"
        + "\n".join(rows) + "\n</sheetData>\n</worksheet>"
    )


def main() -> None:
    tokens = ["产品参数表", "参数", "单位", "12K", "18K", "24K", "备注", "kW"]
    tokens += [f"字段-{i}" for i in range(1, 11)]
    shared_idx = {t: i for i, t in enumerate(tokens)}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", CT)
        z.writestr("_rels/.rels", RELS)
        z.writestr("xl/workbook.xml", WB)
        z.writestr("xl/_rels/workbook.xml.rels", WB_RELS)
        z.writestr("xl/styles.xml", STYLES)
        z.writestr("xl/sharedStrings.xml", _shared_strings(tokens))
        z.writestr("xl/worksheets/sheet1.xml", _sheet_xml(shared_idx))
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()