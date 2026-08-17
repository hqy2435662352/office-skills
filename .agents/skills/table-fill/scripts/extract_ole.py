#!/usr/bin/env python3
"""
scripts/extract_ole.py - Layer 1: OLE 嵌入 Excel 提取

从 PPTX ZIP 中提取 OLE 嵌入的 xlsx。不重新打包回 PPTX。
用法: python scripts/extract_ole.py --input <file.pptx> --slide <N> --output-dir <展平元数据输出/>

选择逻辑（与 LAYER1_OLE_HANDLING.md 一致）：
  1. 解析 slide rels，只保留关系 Type 为 oleObject 的 embedding（排除 image /
     notesSlide / vbaProject 等）；
  2. 按 rels 顺序探测 officecli `get /slide[N]/ole[K]`（K 是 slide 内 OLE 对象的
     位置序号），读取返回对象的 progId；
  3. 选中第一个 progId 含 `Excel.Sheet` 的 embedding（probe 槽位经 relId 回映射
     到 rels，避免位置错位）；无匹配 → 缺陷码 OLE_NO_EXCEL_EMBEDDING。
"""

import os
import sys
import re
import json
import zipfile
import argparse
import xml.etree.ElementTree as ET
from pathlib import Path

from _officecli import officecli, fail  # noqa: E402  (shared UTF-8 adapter)

OLE_REL_TYPE_SUFFIX = "/oleObject"
EXCEL_PROGID_MARKER = "Excel.Sheet"


def _find_rels_path(z: zipfile.ZipFile, target_slide: int) -> str | None:
    """Locate the slide rels part; fall back to any rels whose name embeds
    slide{N} (defensive against nonstandard part names)."""
    rels_path = f"ppt/slides/_rels/slide{target_slide}.xml.rels"
    if rels_path in z.namelist():
        return rels_path
    for name in z.namelist():
        if name.endswith(f"slide{target_slide}.xml.rels"):
            return name
    return None


def find_ole_mappings(pptx_path, target_slide):
    """Return OLEObject embedding mappings from slide rels, in rels order.

    Only relationships whose Type ends with ``/oleObject`` are kept — image,
    notesSlide, vbaProject etc. are never candidates. Each mapping:
      {"rel_id": rId, "embedding": "embeddings/oleObjectN.bin", "number": N}
    ``number`` is None when the Target does not follow the oleObjectN.bin
    convention. Returns [] when the rels part is missing or has no OLEObject
    relationships.
    """
    with zipfile.ZipFile(pptx_path, "r") as z:
        rels_path = _find_rels_path(z, target_slide)
        if rels_path is None:
            return []
        root = ET.fromstring(z.read(rels_path))

    mappings = []
    for rel in root:
        if rel.tag.rsplit("}", 1)[-1] != "Relationship":
            continue
        if not rel.get("Type", "").endswith(OLE_REL_TYPE_SUFFIX):
            continue
        target = rel.get("Target", "")
        embedding = target.lstrip("/")
        if embedding.startswith("../"):
            embedding = embedding[3:]
        m = re.search(r"oleObject(\d+)\.bin$", target)
        mappings.append({
            "rel_id": rel.get("Id", ""),
            "embedding": embedding,
            "number": int(m.group(1)) if m else None,
        })
    return mappings


def probe_ole(pptx_path, slide, index):
    """Probe one OLE slot via officecli; return {"prog_id", "rel_id"} or None."""
    result = officecli("get", str(pptx_path), f"/slide[{slide}]/ole[{index}]",
                       "--depth", "0", "--json", timeout=15)
    try:
        data = json.loads(result.stdout or "{}")
    except ValueError:
        return None
    if not isinstance(data, dict) or not data.get("success"):
        return None
    results = ((data.get("data") or {}).get("results")) or []
    if not results:
        return None
    first = results[0]
    fmt = first.get("format") or {}
    prog_id = str(fmt.get("progId") or first.get("text") or "")
    return {"prog_id": prog_id, "rel_id": str(fmt.get("relId") or "")}


def select_excel_ole(pptx_path, slide):
    """Choose the OLE embedding whose progId contains ``Excel.Sheet``.

    officecli 的 ``/ole[K]`` 是位置序号（1 起，按 slide 内 OLE 对象顺序），与
    rels 文档顺序一致；探测槽位用返回的 relId 回映射到 rels（relId 缺失时按
    位置对应）。返回映射 dict（含 number），无匹配返回 None。
    """
    mappings = find_ole_mappings(pptx_path, slide)
    by_rel_id = {m["rel_id"]: m for m in mappings if m["rel_id"]}
    for index, mapping in enumerate(mappings, start=1):
        probe = probe_ole(pptx_path, slide, index)
        if probe is None or EXCEL_PROGID_MARKER not in probe["prog_id"]:
            continue
        return by_rel_id.get(probe["rel_id"], mapping)
    return None


def extract_xlsx_from_ole(pptx_path, ole_num, output_dir):
    """从 OLE 二进制中提取嵌入的 xlsx"""
    with zipfile.ZipFile(pptx_path, "r") as z:
        ole_path = f"ppt/embeddings/oleObject{ole_num}.bin"
        if ole_path not in z.namelist():
            print(f"[OLE_ERROR] OLE file not found: {ole_path}", file=sys.stderr)
            return None

        ole_data = z.read(ole_path)

        # Find all PK\x03\x04 markers (ZIP file starts)
        offsets = [m.start() for m in re.finditer(b"PK\x03\x04", ole_data)]

        if not offsets:
            print(f"[OLE_ERROR] No ZIP markers found in OLE data", file=sys.stderr)
            return None

        # Try each offset, find the one that produces a valid xlsx with sheet data
        for i, off in enumerate(offsets):
            try:
                xlsx_bytes = ole_data[off:]
                out_name = f"oleObject{ole_num}_slide_extracted.xlsx"
                out_path = os.path.join(str(output_dir), out_name)

                with open(out_path, "wb") as f:
                    f.write(xlsx_bytes)

                # Verify it's a valid xlsx with sheets
                with zipfile.ZipFile(out_path, "r") as test_z:
                    sheet_files = [n for n in test_z.namelist() if "sheet" in n.lower() and n.endswith(".xml")]
                    if sheet_files:
                        # Further verify: check that sheets contain actual cell data (not just empty template)
                        # Read the first sheet's XML
                        sheet_xml = test_z.read(sheet_files[0]).decode("utf-8", errors="ignore")
                        if "<c r=" in sheet_xml or "<v>" in sheet_xml:
                            print(f"[OLE_EXTRACT] Valid xlsx with data: {out_name} (offset {i}, {len(sheet_files)} sheets)")
                            return out_path

                # Remove invalid extraction
                os.remove(out_path)
            except Exception:
                if os.path.exists(out_path):
                    os.remove(out_path)
                continue

        # Fallback: use the first offset that produces any valid ZIP structure
        for i, off in enumerate(offsets):
            try:
                xlsx_bytes = ole_data[off:]
                out_name = f"oleObject{ole_num}_slide_extracted.xlsx"
                out_path = os.path.join(str(output_dir), out_name)
                with open(out_path, "wb") as f:
                    f.write(xlsx_bytes)
                with zipfile.ZipFile(out_path, "r") as test_z:
                    if len(test_z.namelist()) > 5:  # reasonable xlsx
                        print(f"[OLE_EXTRACT] Extracted xlsx (fallback): {out_name} (offset {i})")
                        return out_path
                os.remove(out_path)
            except Exception:
                if os.path.exists(out_path):
                    os.remove(out_path)

    print(f"[OLE_ERROR] Could not extract valid xlsx from OLE object {ole_num}", file=sys.stderr)
    return None


def main():
    parser = argparse.ArgumentParser(description="Layer 1: OLE 嵌入 Excel 提取")
    parser.add_argument("--input", type=Path, required=True, help="PPTX 文件路径")
    parser.add_argument("--slide", type=int, required=True, help="目标 slide 编号")
    parser.add_argument("--output-dir", type=Path, required=True, help="输出目录（建议与 PPTX/源文件同级）")
    args = parser.parse_args()

    if not args.input.exists():
        fail("INPUT_NOT_FOUND", f"PPTX not found: {args.input}",
             "检查 --input 路径后重试")
    args.output_dir.mkdir(parents=True, exist_ok=True)

    print(f"[OLE] Extracting OLE from slide {args.slide} ...")

    # Step 1+2: rels 定位 OLE 对象 → officecli 探测 progId → 选择 Excel.Sheet
    # embedding（探测编号与 rels 选择一致，不再硬编码 /ole[1]）。
    mappings = find_ole_mappings(str(args.input), args.slide)
    chosen = select_excel_ole(str(args.input), args.slide)
    if chosen is None:
        if not mappings:
            message = f"No OLE objects found on slide {args.slide} (no oleObject relationship in slide rels)"
            action = ("确认该 slide 确实嵌入了 Excel 表格；若确认存在，检查 PPTX 的 "
                      "ppt/slides/_rels/slide{N}.xml.rels 是否包含 oleObject 关系后重试")
        else:
            message = (f"Slide {args.slide} contains {len(mappings)} OLE object(s), "
                       "none with progId containing 'Excel.Sheet'")
            action = ("该 slide 的 OLE 对象不是 Excel 表格（可能是 Word/图片等）；"
                      "请确认目标 slide 或改用嵌入了 Excel 的 slide")
        fail("OLE_NO_EXCEL_EMBEDDING", message, action)

    if chosen["number"] is None:
        fail("OLE_NO_EXCEL_EMBEDDING",
             f"Excel OLE embedding target does not follow oleObjectN.bin convention: {chosen['embedding']}",
             "检查 slide rels 的 oleObject 关系 Target 命名后重试")
    ole_num = chosen["number"]
    print(f"[OLE] OLE object number: {ole_num}")

    # Step 3: Extract xlsx
    extracted = extract_xlsx_from_ole(str(args.input), ole_num, args.output_dir)
    if extracted:
        print(f"[OLE_EXTRACT] 提取完成: {extracted}")
        print(f"[OLE_NOTE] 此 xlsx 是独立的填充目标。填充后请用户在 Excel 中打开,")
        print(f"[OLE_NOTE] 选中表格 Ctrl+C, 回到 PPTX 的 slide {args.slide} 右键粘贴。")
        sys.exit(0)
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
