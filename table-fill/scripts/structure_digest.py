#!/usr/bin/env python3
"""
scripts/structure_digest.py - 展平结构摘要生成器 (Layer 1/2 上下文瘦身)

flatten 的 meta.json 通常 400-500 行, 其中大量是空列 SKIP 条目——全量读入
会显著拉长后续每个 LLM 回合的生成时间。本脚本把 meta.json + 列候选 +
展平 CSV 的表头压缩成 ~100 行的紧凑摘要, LLM 只读摘要; 原始 meta 仅按需 grep。

用法:
  python scripts/structure_digest.py --meta <meta.json> --csv <展平.csv> \
      [--candidates <列候选.yaml>] --out <结构摘要.md>
退出码: 0=通过, 1=致命(文件缺失/环境)
"""

import argparse
import csv
import json
import re
import sys
from pathlib import Path

from _officecli import (  # noqa: E402
    ensure_utf8_stdio as _utf8_stdio, fail, record_timing as _record_timing,
    sha256_file,
)
from flatten_table import CLONE_ROLES  # noqa: E402

try:
    import yaml
except ImportError:
    yaml = None


def load_meta(path: Path) -> dict:
    if not path.is_file():
        fail("META_NOT_FOUND", f"meta 文件不存在: {path}",
             "先运行 flatten_table.py --meta")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_candidates(path: Path | None) -> dict | None:
    if not path:
        return None
    if not path.is_file():
        return None
    if yaml is None:
        return None
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def parse_csv_headers(csv_path: Path, header_band: dict) -> dict:
    """从展平 CSV 提取每列表头名 (取表头带内每列最后一个非空值)."""
    if not csv_path or not csv_path.is_file():
        return {}
    header_rows = header_band.get("header_rows", [])
    data_start = header_band.get("data_start_row", 1)
    # 表头带 = [data_start - len(header_rows), data_start) 的原始行号
    lo = data_start - len(header_rows)
    hi = data_start
    wanted = set(range(lo, hi))
    col_names: dict[int, str] = {}
    try:
        with open(csv_path, encoding="utf-8", newline="") as f:
            reader = csv.reader(f)
            for line in reader:
                if not line:
                    continue
                try:
                    orig = int(line[-1].strip())
                except (ValueError, IndexError):
                    continue
                if orig not in wanted:
                    continue
                for idx, val in enumerate(line[:-1]):
                    v = val.strip()
                    if v:
                        col_names[idx] = v
    except OSError as e:
        fail("CSV_READ_ERROR", f"无法读取展平 CSV: {e}",
             "检查 --csv 路径")
    return col_names


def col_letter(idx: int) -> str:
    s = ""
    idx += 1
    while idx > 0:
        idx, rem = divmod(idx - 1, 26)
        s = chr(ord("A") + rem) + s
    return s


def fmt_num(v) -> str:
    if isinstance(v, float):
        return f"{v:.4g}"
    return str(v)


def build_digest(meta: dict, csv_path: Path, candidates: dict | None,
                 for_target: bool = False) -> list[str]:
    dims = meta.get("dimensions", {})
    lines = []
    sheet = meta.get("sheet", "")
    fname = Path(meta.get("file", "?")).name
    lines.append(f"# {sheet} — 结构摘要")
    lines.append(
        f"- 文件: {fname} | sheet: {sheet} | {dims.get('rows','?')}行 × {dims.get('cols','?')}列"
        f" | {dims.get('formulas',0)}公式 | {dims.get('errorCells',0)}错误"
        f" | OLE:{dims.get('oleObjects',0)} 图表:{dims.get('charts',0)} 表:{dims.get('tables',0)}"
    )

    gaps = meta.get("row_gaps") or []
    if gaps:
        lines.append(f"- 行号空洞: {gaps} — row 元素 r 值不连续, "
                     f"`add ... after: /row[N]` 锚点链会断裂, 需 materialize 后重跑 prepare")

    if for_target:
        sg = meta.get("style_granularity") or {}
        segs = sg.get("placeholder_segments") or []
        if segs:
            verdict = "带样式" if any(s.get("styled") for s in segs) else "裸行"
            if verdict == "带样式":
                sample = next((s.get("sample") for s in segs if s.get("styled")), None)
                lines.append(f"- 占位行样式: 带样式 (样例: {sample})")
            else:
                ranges = ", ".join(f"{s.get('start')}-{s.get('end')}" for s in segs)
                lines.append(f"- 占位行样式: 裸行 ({ranges})")
        for c in sg.get("clone_source_rows") or []:
            parts = []
            for role, _off in CLONE_ROLES:
                rinfo = c.get(role)
                if rinfo:
                    v = "带样式" if rinfo.get("styled") else "裸行"
                    parts.append(f"{role}={rinfo.get('row')} {v}")
            lines.append(f"- 克隆源行样式: B{c.get('block')}(" + " | ".join(parts) + ")")

    hb = meta.get("header_band") or {}
    col_names = parse_csv_headers(csv_path, hb)
    if col_names:
        names = " | ".join(col_names.get(i, "?") for i in range(max(col_names) + 1))
        lines.append(f"- 表头: {names}")
    elif hb:
        lines.append(f"- 表头带: 行 {hb.get('header_rows')} 数据起始行 {hb.get('data_start_row')}")

    blocks = meta.get("blocks") or []
    if blocks:
        lines.append("- 数据块:")
        for b in blocks:
            title = str(b.get("title", ""))[:60]
            lines.append(
                f"  - B{b.get('id', blocks.index(b) + 1)} 行{b.get('start')}-{b.get('end')}: "
                f"\"{title}\" (score {b.get('score', '?')})"
            )
    else:
        lines.append("- 数据块: 无自动候选 (LLM 依摘要与业务上下文判定)")

    merged = meta.get("merged_ranges") or []
    if merged:
        lines.append(f"- 合并区({len(merged)}): {', '.join(merged)}")

    # 合并锚点 (L3 克隆源/聚合锚点决策依据)
    anchors = meta.get("merge_anchors") or []
    if anchors:
        parts = []
        for a in anchors:
            f = a.get("formula") or ""
            parts.append(f"{a.get('anchor')}({a.get('range')})" + (f"→{f}" if f else ""))
        lines.append(f"- 合并锚点({len(anchors)}): " + " | ".join(p[:100] for p in parts))

    # 公式链 (L3 公式模板来源, 取代手动 get)
    formulas = meta.get("formulas") or {}
    if formulas:
        def _tpl(f):
            # 范围引用 T9:T11 → T{r1}:T{r2}; 单格引用 J9 → J{r}
            f = re.sub(r"([A-Z]+)(\d+):([A-Z]+)(\d+)", r"\1{r1}:\3{r2}", f)
            return re.sub(r"([A-Z]+)\d+", r"\1{r}", f)
        tpls = sorted({_tpl(f) for f in formulas.values()})
        if len(tpls) <= 12:
            lines.append("- 公式链模板 ({{r}}=数据行, {{r1}}:{{r2}}=聚合范围, 列字母保留):")
            for t in tpls:
                lines.append(f"  - {t[:110]}")
        else:
            lines.append(f"- 公式链模板: {len(tpls)} 条唯一模板 (见 meta.formulas)")

    # 列级 numFmt (L3 写入格式/值形态依据) — 紧凑展示, 不折叠成"见 meta"
    numfmts = meta.get("column_numfmt") or {}
    if numfmts:
        def _short(nf: str) -> str:
            """提取核心格式: 去 [Red] 颜色/转义/右填充, 保留数字与分隔符形态."""
            nf = re.sub(r"\[Red\][^;]*", "", nf)
            nf = nf.replace("\\$", "$").replace('"($', "(").replace('$")', "$)")
            nf = re.sub(r"_.", "", nf)          # Excel 填充占位 `_)` / `_ ` 整体删除
            nf = nf.split(";")[0].rstrip()
            if len(nf) <= 22:
                return nf
            return nf[:20] + "…"
        nf_parts = ", ".join(f"{k}={_short(v)}" for k, v in sorted(numfmts.items()))
        if len(nf_parts) <= 500:
            lines.append(f"- 列级 numFmt: {nf_parts}")
        else:
            # 超长: 按列分行展示 (仍是全量, 不引向 meta)
            for k, v in sorted(numfmts.items()):
                lines.append(f"  - {k}={_short(v)}")

    # 列画像
    cand_map = {}
    if candidates:
        for c in candidates.get("column_classifications", []):
            cand_map[c.get("col")] = c.get("classification", "?")
    cols = meta.get("columns") or []
    nonempty = [c for c in cols if c.get("nonempty", 0) > 0]
    if nonempty:
        lines.append("- 非空列画像 (|列|非空|数值比|唯一|min~max|样例|分类):")
        for c in nonempty:
            col = c["col"]
            samples = " / ".join(str(s).replace("\n", "⏎")[:18] for s in c.get("samples", [])[:3])
            rng = ""
            if c.get("numeric_ratio", 0) >= 0.5 and "min" in c and "max" in c:
                rng = f"{fmt_num(c['min'])}~{fmt_num(c['max'])}"
            cls = cand_map.get(col, "")
            lines.append(
                f"  - {col} | {c.get('nonempty')} | {c.get('numeric_ratio',0):.2f} | "
                f"{c.get('unique','?')} | {rng} | {samples} | {cls}"
            )
        empty_cols = [c["col"] for c in cols if c.get("nonempty", 0) == 0]
        if empty_cols:
            if len(empty_cols) <= 12:
                lines.append(f"- 空列({len(empty_cols)}): {', '.join(empty_cols)}")
            else:
                lines.append(f"- 空列({len(empty_cols)}): {empty_cols[0]}...{empty_cols[-1]}")
    return lines


def build_premod_evidence(meta: dict, csv_path: Path, candidates: dict | None,
                          for_target: bool = False) -> list[str]:
    """最小结构标签视图 (Pre-MOD Evidence): 只保留决定任务形状与 MOD 适用性的事实.

    与 build_digest 共享同一批 load_meta / parse_csv_headers / 行格式辅助函数,
    但不复用 build_digest 本身: 物理剔除一切"任务怎么填"的信息 (公式链模板、
    合并锚点公式、列级 numFmt、非空列画像、列分类、块标题、占位样例值)。"""
    dims = meta.get("dimensions", {})
    lines = []
    sheet = meta.get("sheet", "")
    fname = Path(meta.get("file", "?")).name
    lines.append(f"# {sheet} — 结构摘要")
    lines.append(
        f"- 文件: {fname} | sheet: {sheet} | {dims.get('rows','?')}行 × {dims.get('cols','?')}列"
        f" | {dims.get('formulas',0)}公式 | {dims.get('errorCells',0)}错误"
        f" | OLE:{dims.get('oleObjects',0)} 图表:{dims.get('charts',0)} 表:{dims.get('tables',0)}"
    )

    gaps = meta.get("row_gaps") or []
    if gaps:
        lines.append(f"- 行号空洞: {gaps} — row 元素 r 值不连续, "
                     f"`add ... after: /row[N]` 锚点链会断裂, 需 materialize 后重跑 prepare")

    if for_target:
        sg = meta.get("style_granularity") or {}
        segs = sg.get("placeholder_segments") or []
        if segs:
            verdict = "带样式" if any(s.get("styled") for s in segs) else "裸行"
            if verdict == "带样式":
                styled_ranges = ", ".join(
                    f"{s.get('start')}-{s.get('end')}" for s in segs if s.get("styled"))
                lines.append(f"- 占位行样式: 带样式 (段: {styled_ranges})")
            else:
                ranges = ", ".join(f"{s.get('start')}-{s.get('end')}" for s in segs)
                lines.append(f"- 占位行样式: 裸行 ({ranges})")
        for c in sg.get("clone_source_rows") or []:
            parts = []
            for role, _off in CLONE_ROLES:
                rinfo = c.get(role)
                if rinfo:
                    v = "带样式" if rinfo.get("styled") else "裸行"
                    parts.append(f"{role}={rinfo.get('row')} {v}")
            lines.append(f"- 克隆源行样式: B{c.get('block')}(" + " | ".join(parts) + ")")

    hb = meta.get("header_band") or {}
    col_names = parse_csv_headers(csv_path, hb)
    if col_names:
        names = " | ".join(col_names.get(i, "?") for i in range(max(col_names) + 1))
        lines.append(f"- 表头: {names}")
    elif hb:
        lines.append(f"- 表头带: 行 {hb.get('header_rows')} 数据起始行 {hb.get('data_start_row')}")

    blocks = meta.get("blocks") or []
    if blocks:
        lines.append("- 数据块:")
        for b in blocks:
            lines.append(
                f"  - B{b.get('id', blocks.index(b) + 1)} 行{b.get('start')}-{b.get('end')} "
                f"(score {b.get('score', '?')})"
            )
    else:
        lines.append("- 数据块: 无自动候选 (LLM 依摘要与业务上下文判定)")

    merged = meta.get("merged_ranges") or []
    if merged:
        lines.append(f"- 合并区({len(merged)}): {', '.join(merged)}")
    return lines


def main():
    parser = argparse.ArgumentParser(description="展平结构摘要生成器")
    parser.add_argument("--meta", type=Path, required=True, help="flatten meta.json")
    parser.add_argument("--csv", type=Path, default=None, help="展平 CSV (提取表头名)")
    parser.add_argument("--candidates", type=Path, default=None, help="classify_columns 列候选 YAML (可选)")
    parser.add_argument("--target", action="store_true",
                        help="目标 sheet: 输出样式粒度决策事实 (占位行/克隆源行样式)")
    parser.add_argument("--pre-mod", action="store_true",
                        help="最小结构标签视图 (Pre-MOD Evidence): 只保留任务形状/MOD 适用性事实")
    parser.add_argument("--out", type=Path, required=True, help="输出摘要 md")
    args = parser.parse_args()

    meta = load_meta(args.meta)
    cands = load_candidates(args.candidates)
    if args.pre_mod:
        lines = build_premod_evidence(meta, args.csv, cands, for_target=args.target)
    else:
        lines = build_digest(meta, args.csv, cands, for_target=args.target)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"status": "SUCCESS", "code": "DIGEST_WRITTEN",
                      "lines": len(lines), "out": str(args.out)}, ensure_ascii=False))
    sys.exit(0)


if __name__ == "__main__":
    main()
