#!/usr/bin/env python3
"""
flatten_table_ocl.py - Pure officecli xlsx table flattener.
Uses ONLY officecli (no openpyxl, no pandas).
Same output format as flatten_table.py's xlsx path.

Usage:
  python flatten_table_ocl.py --input file.xlsx --target "SheetName" --output out.csv
"""

import json, csv, re, sys, argparse, time
from pathlib import Path

from _officecli import officecli  # noqa: E402  (shared UTF-8 adapter)


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


# ── officecli calls (all via shared _officecli.officecli() adapter, no openpyxl) ─

def officecli_get(filepath, sheet, range_str, depth=0):
    """Read cells via officecli get. Returns parsed JSON."""
    path = f"/{sheet}/{range_str}"
    result = officecli("get", str(filepath), path, "--depth", str(depth), "--json",
                       timeout=120)
    return json.loads(result.stdout)


def officecli_outline(filepath):
    """Get sheet metadata via officecli view outline."""
    result = officecli("view", str(filepath), "outline", "--json", timeout=30)
    return json.loads(result.stdout)


# ── Dimension discovery via officecli (replaces openpyxl) ─────────────

def discover_dimensions(filepath, sheet, outline_data=None):
    """
    Read a generous range via officecli and discover actual max_row, max_col,
    and rightmost_data from the returned cell paths.

    Uses outline as a hint for row count to avoid reading thousands of empty rows.
    Falls back to ZZ500 if outline unavailable.
    """
    # Try outline first for row hint
    try:
        outline = outline_data if outline_data is not None else officecli_outline(filepath)
        sheets = outline.get("data", {}).get("sheets", [])
        row_hint = 80  # safe fallback for old/partial outline responses
        col_hint = 50
        for s in sheets:
            if s.get("name") == sheet:
                row_hint = max(int(s.get("rows") or 1), 1)
                col_hint = max(int(s.get("cols") or 1), 1)
                break
    except Exception:
        row_hint = 80
        col_hint = 50

    # Outline dimensions are authoritative for normal workbooks. Keep a small
    # margin for stale dimension metadata without scanning AX100 for every sheet.
    max_col_letter = col_idx_to_letter(max(col_hint + 4, 1) - 1)
    safe_rows = row_hint + 10
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
            if ri > max_row and cell.get("text", "").strip():
                max_row = ri
            if ci > max_col and cell.get("text", "").strip():
                max_col = ci
            if cell.get("text", "").strip() and ci > rightmost_data:
                rightmost_data = ci

    # If no data found at all, return safe defaults
    if max_row == 0:
        max_row = 1
    if max_col == 0:
        max_col = 1

    # Do not let empty styled cells in the safety margin inflate the metadata to
    # 50 columns. Retain the outline width even when the last column is empty.
    num_cols = max(col_hint, max_col + 1, rightmost_data + 1)
    num_rows = max(row_hint, max_row)
    pivot_cols = rightmost_data + 1 if rightmost_data > 0 else min(num_cols, col_hint)

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

    # STANDARD: 仅纵向合并(单列跨行, 如 A3:A5 类别)向下传播锚点值;
    # 横向/块合并(标题行 A1:X1、表头组 B2:G2)的非锚点成员一律置空 —
    # 旧实现用 registers 前向填充, 会把上一数据行的值写进标题行 (虚假数据)。
    fill = {}
    if mode == "STANDARD":
        for cell in cells:
            m = cell.get("format", {}).get("merge", "")
            if not m:
                continue
            p = parse_merge(m)
            if not p:
                continue
            c1 = col_letter_to_idx(p[0]) - base_col
            c2 = col_letter_to_idx(p[2]) - base_col
            r1 = p[1] - row_start
            r2 = p[3] - row_start
            if c1 == c2 and r2 > r1:  # 纵向合并: 锚点值向下填充
                anchor = grid[r1][c1] if (0 <= r1 < num_rows and 0 <= c1 < num_cols) else None
                for r in range(r1 + 1, r2 + 1):
                    fill[(r, c1)] = anchor

    result = []
    for r in range(num_rows):
        row_data = []
        for c in range(num_cols):
            ct = grid[r][c]
            if ct is not None and ct != "":
                value = ct
            else:
                value = fill.get((r, c), "")
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


# ── Meta output (--meta) ──────────────────────────────────────────────

def to_number(text):
    """Best-effort numeric parse: strips commas/percent. Returns float or None."""
    if not text:
        return None
    s = text.strip().replace(",", "").replace("%", "")
    try:
        return float(s)
    except ValueError:
        return None


TITLE_KEYWORDS = [
    "核价", "报价", "价格", "需求", "预算", "计划", "汇总", "明细",
    "铜价", "汇率", "USD", "SET", "年", "月",
]


def looks_like_title(text):
    """Heuristic: is this row's first cell a block title?"""
    if not text or len(text) < 6:
        return 0
    hits = sum(1 for k in TITLE_KEYWORDS if k in text)
    if re.search(r"\d{4}[年./-]\d{1,2}", text):
        hits += 1
    return hits


def build_column_stats(cells, num_cols):
    """Per-column: nonempty, numeric_ratio, unique, samples, min/max."""
    per_col = {}
    for cell in cells:
        m = re.search(r"/([A-Z]+)(\d+)$", cell.get("path", ""))
        if not m:
            continue
        ci = col_letter_to_idx(m.group(1))
        if ci >= num_cols:
            continue
        text = cell.get("text", "")
        st = per_col.setdefault(ci, {
            "nonempty": 0, "numeric": 0, "values": set(), "samples": [], "numbers": [],
        })
        if text and text.strip():
            st["nonempty"] += 1
            if len(st["samples"]) < 3 and text not in st["samples"]:
                st["samples"].append(text)
            st["values"].add(text.strip())
            n = to_number(text)
            if n is not None:
                st["numeric"] += 1
                st["numbers"].append(n)
    cols = []
    for ci in range(num_cols):
        st = per_col.get(ci)
        if not st or st["nonempty"] == 0:
            # Empty column — keep it to ONE line so the meta file stays lean.
            # 30+ empty columns previously emitted 8 fields each, bloating the
            # JSON the LLM must read in Layer 2 (Egypt replay: ~1/3 of the
            # meta file was empty-column noise).
            cols.append({
                "col": col_idx_to_letter(ci), "nonempty": 0,
            })
            continue
        entry = {
            "col": col_idx_to_letter(ci),
            "nonempty": st["nonempty"],
            "numeric_ratio": round(st["numeric"] / st["nonempty"], 2),
            "unique": len(st["values"]),
            "samples": st["samples"],
        }
        if st["numbers"]:
            entry["min"] = min(st["numbers"])
            entry["max"] = max(st["numbers"])
        cols.append(entry)
    return cols


def detect_blocks(flat_rows):
    """Candidate data blocks: title row followed by contiguous data rows.

    flat_rows rows end with a source_row string (appended by flatten_xlsx).
    Blocks are CANDIDATES — Layer 2 may correct boundaries. Score reflects
    title-keyword strength + block length.
    """
    blocks = []
    cur = None
    for row in flat_rows:
        if not row:
            continue
        first = row[0] if row[0] else ""
        src = int(row[-1]) if str(row[-1]).isdigit() else 0
        hits = looks_like_title(first)
        if hits > 0:
            if cur:
                blocks.append(cur)
            score = round(min(1.0, 0.4 + hits * 0.15 + min(len(first) / 40, 0.3)), 2)
            cur = {"start": src, "end": src, "title": first[:60], "score": score}
        elif cur is not None and any(str(v).strip() for v in row[:-1]):
            cur["end"] = src
        elif cur is not None:
            # blank separator — close the block
            blocks.append(cur)
            cur = None
    if cur:
        blocks.append(cur)
    return blocks


def officecli_outline_meta(filepath, sheet, outline_data=None):
    """Merge per-sheet outline facts (rows/cols/formulas/ole/charts/tables)."""
    try:
        data = outline_data if outline_data is not None else officecli_outline(filepath)
        for s in data.get("data", {}).get("sheets", []):
            if s.get("name") == sheet:
                return {
                    "rows": s.get("rows"),
                    "cols": s.get("cols"),
                    "formulas": s.get("formulas"),
                    "errorCells": s.get("errorCells"),
                    "tables": s.get("tables"),
                    "charts": s.get("charts"),
                    "oleObjects": s.get("oleObjects"),
                }
    except Exception:
        pass
    return {}


def detect_header_rows(cells, num_cols):
    """Detect the header row band (title/header rows above the data block).

    Header rows are the text-dense rows: the LAST run of consecutive rows
    (from the top) where a majority of cells are non-empty TEXT (SharedString)
    rather than numbers — i.e. the table-header band. The first data row is
    the first row after that band with numeric content or merged series labels.

    Returns {"header_rows": [r1, r2, ...], "data_start_row": N} or None when
    no header band can be identified (LLM then falls back to blocks).
    """
    from collections import defaultdict
    rows = defaultdict(list)
    for cell in cells:
        m = re.match(r"/([^/]+)/([A-Z]+)(\d+)$", cell.get("path", ""))
        if not m:
            continue
        r = int(m.group(3))
        rows[r].append(cell)

    row_scores = {}  # row -> (text_count, total_nonempty)
    for r, row_cells in rows.items():
        text = sum(1 for c in row_cells
                   if c.get("format", {}).get("type") == "SharedString"
                   and (c.get("text") or "").strip())
        nonempty = sum(1 for c in row_cells if (c.get("text") or "").strip())
        if nonempty:
            row_scores[r] = (text, nonempty)

    if not row_scores:
        return None

    # Walk from top; a header row must be majority-text and have >= 2 nonempty
    # cells. Collect consecutive header rows; stop at the first row that is
    # majority-numeric OR has < 2 nonempty cells (blank separator).
    header_rows = []
    max_row = max(row_scores)
    for r in range(1, max_row + 1):
        if r not in row_scores:
            continue  # blank row → treat as boundary only if we already have headers
        text, nonempty = row_scores[r]
        is_header = nonempty >= 2 and text >= max(1, nonempty // 2)
        if is_header and (not header_rows or r == header_rows[-1] + 1):
            header_rows.append(r)
        elif header_rows:
            break  # first non-header row after the band → data starts here

    if not header_rows:
        return None

    data_start = header_rows[-1] + 1
    return {"header_rows": header_rows, "data_start_row": data_start}


def collect_formula_facts(filepath, sheet, num_cols, data_start_row):
    """确定性采集公式 + 列级 numFmt + 合并锚点公式 — 取代 LLM 手动 get 探索.

    一次 `query <sheet>!cell:has(formula)` 取公式文本与公式单元格 numFmt;
    一次 depth-2 range get (数据区前几行) 补齐非公式列的 numberformat。
    输出:
      formulas:      {cell: formula_text}    (去重模板, 每列样本封顶)
      column_numfmt: {col: numberformat}
    任何一步失败均静默降级为空 (flatten 不因采集失败而失败)。
    """
    facts = {"formulas": {}, "column_numfmt": {}}
    try:
        proc = officecli("query", str(filepath), f"{sheet}!cell:has(formula)", "--json",
                         timeout=120)
        data = json.loads(proc.stdout)
        # 全量保留公式 (锚点/普通行都需要); 去重交给 digest 展示层做归一
        for res in data.get("data", {}).get("results", []):
            path = res.get("path", "")
            fmt = res.get("format", {}) or {}
            f = (fmt.get("formula") or "").strip()
            if not f:
                continue
            m = re.search(r"/([A-Z]+)(\d+)$", path)
            if not m:
                continue
            cell = f"{m.group(1)}{m.group(2)}"
            if cell not in facts["formulas"]:
                facts["formulas"][cell] = f
            nf = fmt.get("numberformat")
            if nf:
                facts["column_numfmt"].setdefault(m.group(1), nf)
    except Exception:
        pass
    try:
        hi = min(num_cols, 26)
        start = max(1, data_start_row)
        rng = f"A{start}:{col_idx_to_letter(hi - 1)}{start + 4}"
        proc = officecli("get", str(filepath), f"/{sheet}/{rng}", "--depth", "2", "--json",
                         timeout=120)
        data = json.loads(proc.stdout)
        for res in data.get("data", {}).get("results", []):
            for ch in res.get("children", []):
                m = re.search(r"/([A-Z]+)(\d+)$", ch.get("path", ""))
                if not m:
                    continue
                col = m.group(1)
                nf = (ch.get("format", {}) or {}).get("numberformat")
                if nf:
                    facts["column_numfmt"].setdefault(col, nf)
    except Exception:
        pass
    return facts


def build_merge_anchors(merged_ranges, formulas):
    """合并锚点 = 合并区左上角单元格; 若锚点持公式则一并记录."""
    anchors = []
    for mr in merged_ranges:
        p = parse_merge(mr)
        if not p:
            continue
        anchor = f"{p[0]}{p[1]}"
        anchors.append({
            "range": mr, "anchor": anchor,
            "formula": formulas.get(anchor, ""),
        })
    return anchors


# clone_roles 候选源行判定: title=块首行, header=+1, data=+2 (prepare 阶段无
# spec, 候选来自块结构; 该三元组是 digest/manifest 的生产消费契约).
CLONE_ROLES = (("title", 0), ("header", 1), ("data", 2))


def _read_sheet_xml(filepath, sheet):
    """解包 xlsx, 定位 sheet 的 worksheet XML 文本 (命名空间前缀 x: 或裸均可).

    返回 (ZipFile, sheet_xml_text) 或 (None, None) (解析失败/缺 sheet)。
    与 detect_row_gaps / detect_style_granularity 共享, 避免路径解析漂移。
    """
    import zipfile
    try:
        zf = zipfile.ZipFile(filepath)
    except (OSError, zipfile.BadZipFile):
        return None, None
    try:
        wb = zf.read("xl/workbook.xml").decode("utf-8", errors="replace")
        rels = zf.read("xl/_rels/workbook.xml.rels").decode("utf-8", errors="replace")
    except KeyError:
        return None, None
    m = re.search(r'<sheet[^>]*name="' + re.escape(sheet) + r'"[^>]*r:id="(rId\d+)"', wb)
    if not m:
        m = re.search(r'<sheet[^>]*r:id="(rId\d+)"[^>]*name="' + re.escape(sheet) + r'"', wb)
    if not m:
        return None, None
    rid = m.group(1)
    tm = re.search(r'Id="' + rid + r'"[^>]*Target="([^"]+)"', rels)
    if not tm:
        tm = re.search(r'Target="([^"]+)"[^>]*Id="' + rid + r'"', rels)
    if not tm:
        return None, None
    target = tm.group(1)
    if not target.endswith(".xml"):
        return None, None
    # rels Target 可能带 /xl/ 前缀 (openpyxl 生成) 或为相对路径 (Excel 保存)
    if target.startswith("/xl/"):
        target = target[4:]
    try:
        return zf, zf.read("xl/" + target.lstrip("/")).decode("utf-8", errors="replace")
    except KeyError:
        return None, None


def detect_column_widths(filepath, sheet):
    """检测 sheet 的显式列宽: worksheet XML `<cols>` 元素 (width 属性).

    列宽是 `precision: keep` 编译期校验的机械前提 — 编译器不能靠 Agent 猜
    "列宽够不够"。直读 xlsx XML (同 detect_row_gaps), 不依赖 officecli。
    解析失败 / 无 `<cols>` → {} (旧 meta 无宽度字段时编译端豁免并警告)。
    返回 {列字母: width} (Excel 列宽单位 ≈ 默认字体数字字符宽)。
    """
    zf, xl = _read_sheet_xml(filepath, sheet)
    if zf is None:
        return {}
    widths = {}
    for m in re.finditer(r'<(?:x:)?col\b[^>]*?/>', xl):
        tag = m.group(0)
        minc = re.search(r'min="(\d+)"', tag)
        maxc = re.search(r'max="(\d+)"', tag)
        wm = re.search(r'width="([\d.]+)"', tag)
        if not (minc and maxc and wm):
            continue
        w = float(wm.group(1))
        for ci in range(int(minc.group(1)), int(maxc.group(1)) + 1):
            widths[col_idx_to_letter(ci - 1)] = w
    return widths


def detect_row_gaps(filepath, sheet):
    """检测 sheet 的行号空洞: row 元素 r 值不连续 (如 1..21, 23..52 → [22]).

    officecli 的 `add ... after: /row[N]` 锚点要求 N 元素真实存在; 空洞
    会使插入行落在空洞之后的 r 值、锚点链永久断裂 (2026-08-12 埃及复盘)。
    直接解包 xlsx XML 读取 row r 值 (命名空间前缀 x: 或裸均可)。
    返回缺失 r 值列表 (空 = 无空洞)。
    """
    zf, xl = _read_sheet_xml(filepath, sheet)
    if zf is None:
        return []
    rs = {int(r) for r in re.findall(r"<(?:x:)?row r=\"(\d+)\"", xl)}
    if not rs:
        return []
    hi = max(rs)
    return sorted(r for r in range(1, hi + 1) if r not in rs)


def detect_style_granularity(filepath, sheet, blocks, flat_rows, num_cols):
    """检测目标 sheet 的样式粒度决策事实 (digest/manifest 决策事实, 不入指纹).

    占位行段: base 区 (blocks 末端 / 最后内容行) 以下的候选占位行 —
    连续空值段 (flat CSV 无此行, 判定阈值沿用既有空行逻辑)。对段内单元格
    检测样式存在性 (边框/填充/字体/对齐/数字格式): 全段无样式 → 裸行;
    任一有样式 → 带样式 (样例坐标)。各 block 的 title/header/data 候选
    源行同样输出样式粒度结论 — 克隆携带格式的事实依据 (埃及案例:
    inplace 填裸行 = 无边框块, 违反 VAL-007 格式沿用; 正确终点是
    clone-append 克隆携带格式)。

    直读 xlsx XML (同 detect_row_gaps), 不依赖 officecli。
    解析失败返回 None (digest 静默降级)。
    返回:
      {
        "placeholder_segments": [{"start": 23, "end": 52, "styled": False,
                                   "sample": None}],
        "clone_source_rows": [
          {"block": 1, "title": {"row": 1, "styled": True, "sample": "A1"},
           "header": {"row": 2, "styled": True, "sample": "A2"},
           "data": {"row": 3, "styled": True, "sample": "A3"}},
        ],
      }
    """
    zf, xl = _read_sheet_xml(filepath, sheet)
    if zf is None:
        return None
    try:
        styles = zf.read("xl/styles.xml").decode("utf-8", errors="replace")
    except KeyError:
        return None

    # cellXfs: 样式索引 → 是否携带可见格式 (边框/填充/字体/数字格式/对齐).
    # 以非零 id 为信号 (borderId 0=空边框, fillId 0/1=无填充/gray125 占位,
    # fontId 0=默认字体, numFmtId 0/164=General); 裸 apply* 标志不计数 —
    # id 为 0 时 apply 标志无可见效果 (LibreOffice 默认样式引用 gray125
    # 不得误报 带样式; 埃及教训: 裸行必须判裸行).
    styled_xfs = set()
    mx = re.search(r"<cellXfs[^>]*>(.*?)</cellXfs>", styles, re.S)
    if mx:
        for i, xf in enumerate(
                re.findall(r"<xf\b.*?</xf>|<xf\b[^>]*/>", mx.group(1), re.S)):
            if (re.search(r'borderId="[1-9]', xf)
                    or re.search(r'fillId="(?:[2-9]|\d{2,})', xf)
                    or re.search(r'fontId="[1-9]', xf)
                    or re.search(r'numFmtId="(?!0"|164")', xf)
                    or '<alignment' in xf):
                styled_xfs.add(i)

    # 每行: 已用列范围内携带样式的单元格列字母 (文档序; 样例取最左列).
    row_re = re.compile(r'<(?:x:)?row r="(\d+)"[^>]*?(?:/>|>(.*?)</(?:x:)?row>)', re.S)
    styled_cols_by_row = {}
    for mrow in row_re.finditer(xl):
        r = int(mrow.group(1))
        styled_cols = []
        for mc in re.finditer(r'<c r="([A-Z]+)\d+"[^>]*s="(\d+)"', mrow.group(2) or ""):
            col_idx = col_letter_to_idx(mc.group(1))
            if col_idx < num_cols and int(mc.group(2)) in styled_xfs:
                styled_cols.append(mc.group(1))
        if styled_cols:
            styled_cols.sort(key=col_letter_to_idx)
        styled_cols_by_row[r] = styled_cols
    if not styled_cols_by_row:
        return None

    # 占位行段: base 区 (blocks 末端 / flat CSV 最后内容行) 以下、存在于
    # XML 且无内容 (不在 flat CSV) 的行, 连续者并段.
    flat_rows_set = {int(r[-1]) for r in flat_rows if r and str(r[-1]).isdigit()}
    base_end = max([b.get("end", 0) for b in blocks] + list(flat_rows_set) + [0])
    placeholder = sorted(r for r in styled_cols_by_row
                         if r > base_end and r not in flat_rows_set)
    segments = []
    for r in placeholder:
        styled_cols = styled_cols_by_row[r]
        if segments and r == segments[-1]["end"] + 1:
            segments[-1]["end"] = r
            if styled_cols:
                segments[-1]["styled"] = True
                if not segments[-1]["sample"]:
                    segments[-1]["sample"] = f"{styled_cols[0]}{r}"
        else:
            segments.append({"start": r, "end": r,
                             "styled": bool(styled_cols),
                             "sample": f"{styled_cols[0]}{r}" if styled_cols else None})

    # 克隆源候选行: 每 block 的 title(start)/header(start+1)/data(start+2).
    clone_source_rows = []
    for bi, b in enumerate(blocks, start=1):
        start = b.get("start")
        end = b.get("end", start)
        entry = {"block": bi}
        for role, off in CLONE_ROLES:
            r = start + off
            if r > end:
                continue
            styled_cols = styled_cols_by_row.get(r) or []
            entry[role] = {"row": r, "styled": bool(styled_cols),
                           "sample": f"{styled_cols[0]}{r}" if styled_cols else None}
        clone_source_rows.append(entry)

    return {"placeholder_segments": segments,
            "clone_source_rows": clone_source_rows}


def build_meta(filepath, sheet, cells, num_cols, num_rows, flat_rows, outline_data=None):
    """Structured metadata for Layer 2 — one call replaces the old manual
    outline/get/query exploration loop."""
    outline = officecli_outline_meta(filepath, sheet, outline_data)
    meta = {
        "file": str(filepath),
        "sheet": sheet,
        "dimensions": {
            "rows": outline.get("rows", num_rows),        # sheet row count (authoritative)
            "cols": outline.get("cols", num_cols),
            "data_rows": len(flat_rows),                  # actual flattened data rows
        },
        "merged_ranges": [],
        "header_band": detect_header_rows(cells, num_cols),
        "blocks": detect_blocks(flat_rows),
        "columns": build_column_stats(cells, num_cols),
    }
    for cell in cells:
        merge = cell.get("format", {}).get("merge", "")
        if merge and merge not in meta["merged_ranges"]:
            meta["merged_ranges"].append(merge)
    hb = meta["header_band"]
    data_start = hb.get("data_start_row", 1) if hb else 1
    facts = collect_formula_facts(filepath, sheet, num_cols, data_start)
    meta["formulas"] = facts["formulas"]
    meta["column_numfmt"] = facts["column_numfmt"]
    meta["merge_anchors"] = build_merge_anchors(meta["merged_ranges"], facts["formulas"])
    meta["row_gaps"] = detect_row_gaps(filepath, sheet)
    meta["column_width"] = detect_column_widths(filepath, sheet)
    meta["style_granularity"] = detect_style_granularity(
        filepath, sheet, meta["blocks"], flat_rows, num_cols)
    for k in ("formulas", "errorCells", "tables", "charts", "oleObjects"):
        if k in outline and outline[k] is not None:
            meta["dimensions"][k] = outline[k]
    return meta


def write_meta(path, meta):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    print(f"  Written: {path} ({len(meta['columns'])} cols, "
          f"{len(meta['blocks'])} block candidates)")


# ── Main entry point ──────────────────────────────────────────────────

def flatten_xlsx_file(filepath, sheet, output_path, meta_path=None, outline_data=None):
    print(f"[FLATTEN_OCL:XLSX] {filepath} / {sheet}")

    t0 = time.perf_counter()

    # Phase 1: discover dimensions via officecli (replaces openpyxl)
    cells, num_cols, num_rows, pivot_cols = discover_dimensions(filepath, sheet, outline_data)
    print(f"  Detected range: A1:{col_idx_to_letter(num_cols - 1)}{num_rows}, DataCols: {pivot_cols}")

    # Phase 2: detect mode and flatten
    mode = detect_pivot(cells, 0, 1, pivot_cols, num_rows)
    print(f"  Mode: {mode}")

    flat = flatten_xlsx(cells, 1, num_cols, num_rows, mode)
    write_csv(output_path, flat)

    if meta_path:
        meta = build_meta(filepath, sheet, cells, num_cols, num_rows, flat, outline_data)
        write_meta(meta_path, meta)

    elapsed = time.perf_counter() - t0
    print(f"  Elapsed: {elapsed:.3f}s")
    return len(flat), elapsed


def main():
    parser = argparse.ArgumentParser(description="Pure officecli xlsx table flattener")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--target", type=str, required=True, help="Sheet name")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--meta", type=Path, default=None,
                        help="Optional: write structured metadata JSON for Layer 2 "
                             "(column stats, merged ranges, block candidates, outline facts)")
    args = parser.parse_args()

    if not args.input.exists():
        print(f"[FATAL] File not found: {args.input}", file=sys.stderr)
        sys.exit(1)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.meta:
        args.meta.parent.mkdir(parents=True, exist_ok=True)

    rows, elapsed = flatten_xlsx_file(str(args.input), args.target, args.output, args.meta)
    print(f"[FLATTEN_OCL_DONE] {rows} rows, {elapsed:.3f}s")


if __name__ == "__main__":
    main()
