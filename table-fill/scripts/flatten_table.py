#!/usr/bin/env python3
"""
flatten_table_ocl.py - Pure officecli xlsx table flattener.
Uses ONLY officecli (no openpyxl, no pandas).
Same output format as flatten_table.py's xlsx path.

Usage:
  python flatten_table_ocl.py --input file.xlsx --target "SheetName" --output out.csv
"""

import subprocess, json, csv, re, sys, argparse, time
from pathlib import Path


# ── Column utilities (identical to original) ──────────────────────────

def col_letter_to_idx(letter):
    result = 0
    for c in letter:
        result = result * 26 + (ord(c) - ord("A") + 1)
    return result - 1


def col_idx_to_letter(idx):
    result = ""
    idx += 1
    while idx > 0:
        idx -= 1
        result = chr(ord("A") + idx % 26) + result
        idx //= 26
    return result


def parse_merge(merge_str):
    if not merge_str:
        return None
    m = re.match(r"([A-Z]+)(\d+):([A-Z]+)(\d+)", merge_str)
    return (m.group(1), int(m.group(2)), m.group(3), int(m.group(4))) if m else None


# ── officecli calls (pure subprocess, no openpyxl) ────────────────────

def officecli_get(filepath, sheet, range_str, depth=0):
    """Read cells via officecli get. Returns parsed JSON."""
    path = f"/{sheet}/{range_str}"
    result = subprocess.run(
        ["officecli", "get", str(filepath), path, "--depth", str(depth), "--json"],
        capture_output=True,
        timeout=120,
    )
    return json.loads(result.stdout.decode("utf-8"))


def officecli_outline(filepath):
    """Get sheet metadata via officecli view outline."""
    result = subprocess.run(
        ["officecli", "view", str(filepath), "outline", "--json"],
        capture_output=True,
        timeout=30,
    )
    return json.loads(result.stdout.decode("utf-8"))


# ── Dimension discovery via officecli (replaces openpyxl) ─────────────

def discover_dimensions(filepath, sheet):
    """
    Read a generous range via officecli and discover actual max_row, max_col,
    and rightmost_data from the returned cell paths.

    Uses outline as a hint for row count to avoid reading thousands of empty rows.
    Falls back to ZZ500 if outline unavailable.
    """
    # Try outline first for row hint
    try:
        outline = officecli_outline(filepath)
        sheets = outline.get("data", {}).get("sheets", [])
        row_hint = 80  # default
        for s in sheets:
            if s.get("name") == sheet:
                row_hint = max(s.get("rows", 80), 80)
                break
    except Exception:
        row_hint = 80

    # Read generous range: 50 cols x (row_hint + 20) rows as safety margin
    max_col_letter = col_idx_to_letter(49)  # AX = column 50
    safe_rows = row_hint + 20
    data_range = f"A1:{max_col_letter}{safe_rows}"

    data = officecli_get(filepath, sheet, data_range, 0)
    cells = data["data"]["results"][0].get("children", [])

    max_row = 0
    max_col = 0
    rightmost_data = 0

    for cell in cells:
        m = re.search(r"([A-Z]+)(\d+)$", cell.get("path", ""))
        if m:
            ci = col_letter_to_idx(m.group(1))
            ri = int(m.group(2))
            if ri > max_row:
                max_row = ri
            if ci > max_col:
                max_col = ci
            if cell.get("text", "").strip() and ci > rightmost_data:
                rightmost_data = ci

    # If no data found at all, return safe defaults
    if max_row == 0:
        max_row = 1
    if max_col == 0:
        max_col = 1

    num_cols = max_col + 1
    num_rows = max_row
    pivot_cols = rightmost_data + 1 if rightmost_data > 0 else num_cols

    return cells, num_cols, num_rows, pivot_cols


# ── Pivot detection (identical to original) ───────────────────────────

def detect_pivot(cells, base_col_start, base_row_start, num_cols, num_rows):
    merge_count = sum(1 for c in cells if c.get("format", {}).get("merge", ""))
    grid = [[None] * num_cols for _ in range(num_rows)]
    for cell in cells:
        m = re.search(r"([A-Z]+)(\d+)$", cell.get("path", ""))
        if m:
            col = col_letter_to_idx(m.group(1)) - base_col_start
            row = int(m.group(2)) - base_row_start
            if 0 <= row < num_rows and 0 <= col < num_cols:
                text = cell.get("text", "")
                grid[row][col] = text if text else None
    if num_rows < 3 or num_cols < 2:
        return "STANDARD"
    col_a_blanks = sum(1 for r in range(num_rows) if grid[r][0] is None)
    col_a_ratio = col_a_blanks / num_rows if num_rows > 0 else 0
    col_b_filled = sum(1 for r in range(num_rows) if grid[r][1] is not None)
    col_b_density = col_b_filled / num_rows if num_rows > 0 else 0
    return "PIVOT" if merge_count == 0 and col_a_ratio > 0.50 and col_b_density > 0.80 else "STANDARD"


# ── Flatten logic (identical to original) ─────────────────────────────

def flatten_xlsx(cells, row_start, num_cols, num_rows, mode):
    base_col = col_letter_to_idx("A")

    covered = set()
    if mode == "STANDARD":
        for cell in cells:
            m = cell.get("format", {}).get("merge", "")
            if m:
                p = parse_merge(m)
                if p:
                    c1 = col_letter_to_idx(p[0]) - base_col
                    c2 = col_letter_to_idx(p[2]) - base_col
                    r1 = p[1] - row_start
                    r2 = p[3] - row_start
                    for r in range(r1, r2 + 1):
                        for c in range(c1, c2 + 1):
                            covered.add((r, c))

    grid = [[None] * num_cols for _ in range(num_rows)]
    for cell in cells:
        m = re.search(r"([A-Z]+)(\d+)$", cell.get("path", ""))
        if m:
            col = col_letter_to_idx(m.group(1)) - base_col
            row = int(m.group(2)) - row_start
            if 0 <= row < num_rows and 0 <= col < num_cols:
                text = cell.get("text", "")
                if text and ("#DIV" in text or "#N/A" in text or "#VALUE" in text):
                    text = None
                grid[row][col] = text if text else None

    if mode == "PIVOT":
        prev_parent = None
        result = []
        for r in range(num_rows):
            la = grid[r][0] if num_cols > 0 else None
            lb = grid[r][1] if num_cols > 1 else None
            if la is not None and la != "":
                prev_parent = la
                is_sum = True
            elif lb is not None and lb != "":
                la = prev_parent
                is_sum = False
            else:
                continue
            source_row = r + row_start
            row_data = [la if la else ""]
            for c in range(1, num_cols):
                row_data.append(grid[r][c] if grid[r][c] is not None else "")
            row_data.append("SUMMARY" if is_sum else "DETAIL")
            row_data.append(str(source_row))
            result.append(row_data)
        return result

    registers = [None] * num_cols
    result = []
    for r in range(num_rows):
        row_data = []
        for c in range(num_cols):
            ct = grid[r][c]
            if ct is not None and ct != "":
                registers[c] = ct
                value = ct
            elif (r, c) in covered:
                value = registers[c] if registers[c] is not None else ""
            else:
                value = ""
            row_data.append(value)
        if any(v and v != "" for v in row_data):
            source_row = r + row_start
            row_data.append(str(source_row))
            result.append(row_data)
    return result


# ── CSV output ────────────────────────────────────────────────────────

def write_csv(path, data):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        csv.writer(f).writerows(data)
    print(f"  Written: {path} ({len(data)} rows)")


# ── Main entry point ──────────────────────────────────────────────────

def flatten_xlsx_file(filepath, sheet, output_path):
    print(f"[FLATTEN_OCL:XLSX] {filepath} / {sheet}")

    t0 = time.perf_counter()

    # Phase 1: discover dimensions via officecli (replaces openpyxl)
    cells, num_cols, num_rows, pivot_cols = discover_dimensions(filepath, sheet)
    print(f"  Detected range: A1:{col_idx_to_letter(num_cols - 1)}{num_rows}, DataCols: {pivot_cols}")

    # Phase 2: detect mode and flatten
    mode = detect_pivot(cells, 0, 1, pivot_cols, num_rows)
    print(f"  Mode: {mode}")

    flat = flatten_xlsx(cells, 1, num_cols, num_rows, mode)
    write_csv(output_path, flat)

    elapsed = time.perf_counter() - t0
    print(f"  Elapsed: {elapsed:.3f}s")
    return len(flat), elapsed


def main():
    parser = argparse.ArgumentParser(description="Pure officecli xlsx table flattener")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--target", type=str, required=True, help="Sheet name")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    if not args.input.exists():
        print(f"[FATAL] File not found: {args.input}", file=sys.stderr)
        sys.exit(1)

    args.output.parent.mkdir(parents=True, exist_ok=True)

    rows, elapsed = flatten_xlsx_file(str(args.input), args.target, args.output)
    print(f"[FLATTEN_OCL_DONE] {rows} rows, {elapsed:.3f}s")


if __name__ == "__main__":
    main()
