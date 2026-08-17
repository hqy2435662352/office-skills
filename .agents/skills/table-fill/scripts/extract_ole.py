#!/usr/bin/env python3
"""
scripts/extract_ole.py - Layer 1: OLE 嵌入 Excel 提取

从 PPTX ZIP 中提取 OLE 嵌入的 xlsx。不重新打包回 PPTX。
用法: python scripts/extract_ole.py --input <file.pptx> --slide <N> --output-dir <展平元数据输出/>
"""

import os
import sys
import re
import json
import zipfile
import argparse
import subprocess
from pathlib import Path


def find_ole_mapping(pptx_path, target_slide):
    """通过 slide rels 找到 OLE 对象编号"""
    with zipfile.ZipFile(pptx_path, "r") as z:
        # Read slide rels
        rels_path = f"ppt/slides/_rels/slide{target_slide}.xml.rels"
        if rels_path not in z.namelist():
            # Try without underscore prefix
            for name in z.namelist():
                if f"slide{target_slide}" in name and name.endswith(".xml.rels"):
                    rels_path = name
                    break
        
        if rels_path not in z.namelist():
            print(f"[OLE_ERROR] No relationships file found for slide {target_slide}", file=sys.stderr)
            return None
        
        content = z.read(rels_path).decode("utf-8", errors="ignore")
        ole_matches = re.findall(r'oleObject(\d+)\.bin', content)
        if ole_matches:
            return int(ole_matches[0])
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
            except:
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

    assert args.input.exists(), f"[FATAL] PPTX not found: {args.input}"
    args.output_dir.mkdir(parents=True, exist_ok=True)

    print(f"[OLE] Extracting OLE from slide {args.slide} ...")

    # Step 1: Detect OLE first
    try:
        result = subprocess.run(
            ["officecli", "get", str(args.input), f"/slide[{args.slide}]/ole[1]", "--depth", "0", "--json"],
            capture_output=True, timeout=15,
        )
        data = json.loads(result.stdout.decode("utf-8"))
        if not data.get("success"):
            print(f"[OLE_ERROR] No OLE objects found on slide {args.slide}", file=sys.stderr)
            sys.exit(1)
    except:
        print(f"[OLE_ERROR] Failed to probe OLE on slide {args.slide}", file=sys.stderr)
        sys.exit(1)

    # Step 2: Find OLE number via rels
    ole_num = find_ole_mapping(str(args.input), args.slide)
    if ole_num is None:
        # Fallback: try sequential numbers
        for n in range(1, 10):
            result = subprocess.run(
                ["officecli", "get", str(args.input), f"/slide[{args.slide}]/ole[{n}]", "--depth", "0", "--json"],
                capture_output=True, timeout=10,
            )
            try:
                data = json.loads(result.stdout.decode("utf-8"))
                if data.get("success"):
                    ole_num = n
                    break
            except:
                pass
    
    if ole_num is None:
        print(f"[OLE_ERROR] Could not determine OLE object number for slide {args.slide}", file=sys.stderr)
        sys.exit(1)

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
