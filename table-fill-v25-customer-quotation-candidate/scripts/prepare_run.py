#!/usr/bin/env python3
"""
scripts/prepare_run.py — Prepare: one orchestration for staging, outline,
flatten, classify, and digest (V2).

Replaces the old hand-written plan / repeated outline / per-sheet classify /
per-sheet digest sequence. Two invocations:

  1. Outline stage (evidence for MOD resolution + sheet selection):
       python scripts/prepare_run.py --workdir <ascii_dir> \
         --files "C:\\...\\毛利表.xlsx|source_maoli.xlsx,C:\\...\\报价.xlsx|target_baojia.xlsx" \
         [--task "任务文本"] --outline
     → preflight (officecli on PATH, ASCII workdir), stage all files,
       write one outline text per staged file, partial prepare_manifest.json.

  2. Flatten stage (mechanical, does NOT wait for MOD):
       python scripts/prepare_run.py --workdir <dir> --flatten \
         --sheets "source_maoli.xlsx:毛利表;target_baojia.xlsx:11_FRESH本土" \
         --target target_baojia.xlsx
     → one shared outline per workbook, flatten each listed sheet, classify
       candidates, structure digest, structure fingerprints for source/target,
       complete prepare_manifest.json.

Fingerprints are the structure facts (dimensions/header band/merged ranges/
blocks/formulas/numfmt) hashed deterministically. compile_fill.py compares
fill_spec fingerprints against the manifest so a stale spec fails loudly.

Exit codes: 0=pass, 1=fatal (env/file), 3=retryable (apply corrective_action).
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
from pathlib import Path

from _officecli import (  # noqa: E402
    ensure_utf8_stdio as _utf8_stdio, fail, record_timing as _record_timing,
    sha256_file,
)

sys.path.insert(0, str(Path(__file__).resolve().parent))

import preflight  # noqa: E402
import stage_files  # noqa: E402
from flatten_table import officecli_outline  # noqa: E402
import classify_columns  # noqa: E402
import structure_digest  # noqa: E402

MANIFEST_NAME = "prepare_manifest.json"




def structure_facts(meta: dict) -> dict:
    """Deterministic structure facts for fingerprinting (subset of meta)."""
    dims = meta.get("dimensions", {})
    return {
        "sheet": meta.get("sheet"),
        "dimensions": {k: dims.get(k) for k in ("rows", "cols", "data_rows")},
        "header_band": meta.get("header_band"),
        "merged_ranges": sorted(meta.get("merged_ranges") or []),
        "blocks": meta.get("blocks"),
        "columns": [
            {"col": c.get("col"), "nonempty": c.get("nonempty"),
             "numeric_ratio": c.get("numeric_ratio")}
            for c in meta.get("columns", [])
        ],
        "formulas": sorted((meta.get("formulas") or {}).items()),
        "column_numfmt": sorted((meta.get("column_numfmt") or {}).items()),
        "merge_anchors": meta.get("merge_anchors"),
    }


def facts_sha256(facts_list: list[dict]) -> str:
    payload = json.dumps(facts_list, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def load_manifest(workdir: Path) -> dict:
    p = workdir / MANIFEST_NAME
    if not p.is_file():
        fail("MANIFEST_NOT_FOUND",
             f"prepare_manifest.json not found in {workdir} — run the outline stage first",
             "Run: python scripts/prepare_run.py --workdir <dir> --files ... --outline")
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except ValueError as e:
        fail("MANIFEST_INVALID", f"corrupt manifest: {e}",
             "Delete the manifest and re-run the outline stage")


def save_manifest(workdir: Path, manifest: dict) -> None:
    (workdir / MANIFEST_NAME).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def parse_sheets_arg(raw: str) -> list[tuple[str, list[str]]]:
    """'file.xlsx:S1,S2;file2.xlsx:S3' → [(file, [S1, S2]), (file2, [S3])]."""
    out = []
    for chunk in raw.split(";"):
        chunk = chunk.strip()
        if not chunk:
            continue
        if ":" not in chunk:
            fail("SHEETS_ARG_INVALID",
                 f"sheets entry must be 'file:SheetA,SheetB', got: {chunk!r}",
                 "Fix --sheets syntax")
        file_part, _, sheets_part = chunk.partition(":")
        sheets = [s.strip() for s in sheets_part.split(",") if s.strip()]
        if not sheets:
            fail("SHEETS_ARG_INVALID", f"no sheets listed for {file_part!r}",
                 "List at least one sheet per file")
        out.append((file_part.strip(), sheets))
    if not out:
        fail("SHEETS_ARG_INVALID", "--sheets is empty",
             "Provide file:sheet pairs")
    return out


def run_outline_stage(workdir: Path, files_arg: str, task: str) -> None:
    workdir.mkdir(parents=True, exist_ok=True)
    try:
        workdir.resolve().relative_to(Path("C:/"))
    except Exception:
        pass
    try:
        str(workdir).encode("ascii")
    except UnicodeEncodeError:
        fail("NON_ASCII_PATH",
             f"workdir contains non-ASCII characters: {workdir} — officecli "
             f"batch/set fails on Chinese paths",
             "Use an ASCII workdir like C:/Temp/tablefill/<task>/", exit_code=1)

    env_issues = []
    r = preflight.check_officecli()
    if r:
        env_issues.append(r)
    if env_issues:
        fail(env_issues[0]["code"], env_issues[0]["message"],
             env_issues[0]["corrective_action"], exit_code=1)

    entries = []
    for chunk in files_arg.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "|" in chunk:
            src, _, name = chunk.partition("|")
            entries.append((src.strip(), name.strip()))
        else:
            entries.append((chunk, Path(chunk).name))
    if not entries:
        fail("NO_FILES", "--files is empty", "Provide source|name pairs")

    records = stage_files.stage_files(workdir, entries)
    errors = [r for r in records if r["status"] == "ERROR"]
    if errors:
        fail("STAGE_FAILED", f"{len(errors)} file(s) failed staging",
             "Check source paths and ASCII staged names", exit_code=1)

    preflight.check_resident_cleanup()

    files = []
    outlines = {}
    for rec in records:
        name = Path(rec["dst"]).name
        staged = workdir / name
        files.append({
            "staged": name,
            "source": rec["src"],
            "sha256": sha256_file(staged),
        })
        outline_txt = workdir / f"{Path(name).stem}_outline.txt"
        proc = officecli_outline(str(staged))
        outline_txt.write_text(json.dumps(proc, ensure_ascii=False, indent=1),
                               encoding="utf-8")
        outlines[name] = outline_txt.name

    manifest = {
        "schema_version": 2,
        "workdir": str(workdir),
        "task": task,
        "files": files,
        "outlines": outlines,
        "flattened": [],
        "target": None,
        "fingerprints": {},
    }
    save_manifest(workdir, manifest)
    _record_timing(workdir, "prepare_outline")
    print(json.dumps({"status": "PASS", "code": "OUTLINE_STAGE_DONE",
                      "files": [f["staged"] for f in files],
                      "outlines": outlines}, ensure_ascii=False, indent=2))


def ascii_slug(text: str) -> str:
    """ASCII-safe slug for artifact naming: keep [A-Za-z0-9_-], drop the rest."""
    slug = re.sub(r"[^A-Za-z0-9_-]", "", text)
    return slug or "sheet"


def _entry_for(fname: str, s: str, n: str) -> dict:
    return {
        "file": fname,
        "sheet": s,
        "name": n,
        "csv": f"{n}_flat.csv",
        "meta": f"{n}_meta.json",
        "digest": f"{n}_digest.md",
        "candidates": f"{n}_candidates.yaml",
    }


def merge_flattened(existing: list, new_entries: list) -> list:
    """Incrementally merge flatten results by entry name (new overwrites old).

    Multiple `--flatten` invocations must not clobber each other's manifest
    entries — earlier sheets stay discoverable by the compiler (2026-08-10)."""
    merged = {e["name"]: e for e in existing}
    for e in new_entries:
        merged[e["name"]] = e
    return list(merged.values())


# ── 样式粒度决策事实 (issue 03: digest 样式粒度, 免 Agent unzip XML 考古) ──

def _sheet_xmls(staged: Path, sheet: str) -> tuple[str | None, str | None]:
    """(sheet_xml, styles_xml) for the named sheet via direct xlsx unzip
    (same lookup as flatten_table.detect_row_gaps). (None, None) on failure —
    silent degrade, the fact is advisory."""
    import zipfile
    try:
        zf = zipfile.ZipFile(staged)
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
    tm = re.search(r'Id="' + m.group(1) + r'"[^>]*Target="([^"]+)"', rels)
    if not tm or not tm.group(1).endswith(".xml"):
        return None, None
    try:
        xl = zf.read("xl/" + tm.group(1).lstrip("/")).decode("utf-8", errors="replace")
    except KeyError:
        return None, None
    try:
        styles = zf.read("xl/styles.xml").decode("utf-8", errors="replace")
    except KeyError:
        styles = None
    return xl, styles


def _styled_style_indices(styles_xml: str | None) -> set[int]:
    """cellXfs 中携带样式维度的 xf 索引 (边框/填充/字体/对齐/数字格式任一).
    全零默认 xf (numFmtId/fontId/fillId/borderId 均 0 且无 alignment) 视为裸."""
    if not styles_xml:
        return set()
    m = re.search(r"<cellXfs[^>]*>(.*?)</cellXfs>", styles_xml, re.S)
    if not m:
        return set()
    out = set()
    for i, xf in enumerate(re.findall(r"<xf\b[^>]*?(?:/>|>)", m.group(1))):
        if "alignment" in xf or "applyAlignment" in xf:
            out.add(i)
            continue
        ids = {}
        for key in ("numFmtId", "fontId", "fillId", "borderId"):
            mm = re.search(rf"\b{key}=\"(\d+)\"", xf)
            ids[key] = int(mm.group(1)) if mm else 0
        if ids["fontId"] or ids["fillId"] or ids["borderId"] or ids["numFmtId"]:
            out.add(i)
    return out


def _style_presence(sheet_xml: str | None, styles_xml: str | None,
                    rows: list[int]) -> dict:
    """候选行中任一单元格携带样式记录 → {"styled": True, "sample": "A23"}.
    无 s 属性或 s 指向全零默认 xf 视为裸行."""
    if not sheet_xml or not rows:
        return {"styled": False, "sample": None}
    styled = _styled_style_indices(styles_xml)
    wanted = set(rows)
    for m in re.finditer(r'<c r="([A-Z]+)(\d+)"([^>]*)>', sheet_xml):
        if int(m.group(2)) not in wanted:
            continue
        sm = re.search(r'\bs="(\d+)"', m.group(3))
        if sm and int(sm.group(1)) in styled:
            return {"styled": True, "sample": f"{m.group(1)}{m.group(2)}"}
    return {"styled": False, "sample": None}


def detect_placeholder_rows(meta: dict, csv_path: Path) -> list[int]:
    """候选占位行段 = base_last_row 以下的尾随连续空值段 (判定阈值沿用既有
    空行逻辑: CSV 中整行无值). 末行有内容 → 空段."""
    dims = meta.get("dimensions", {})
    total = int(dims.get("rows") or 0)
    if total < 2 or not csv_path or not csv_path.is_file():
        return []
    nonempty = set()
    try:
        with open(csv_path, encoding="utf-8-sig", newline="") as f:
            for line in csv.reader(f):
                if not line:
                    continue
                try:
                    orig = int(line[-1].strip())
                except (ValueError, IndexError):
                    continue
                if any(cell.strip() for cell in line[:-1]):
                    nonempty.add(orig)
    except OSError:
        return []
    end = total
    while end > 1 and end not in nonempty:
        end -= 1
    if end == total:
        return []
    return list(range(end + 1, total + 1))


def template_candidate_rows(meta: dict) -> dict:
    """clone_roles 候选源行: title (首块起始) / header (表头带首行) / data
    (末块末行). 缺事实的 role 不输出."""
    out = {}
    hb = meta.get("header_band") or {}
    hr = hb.get("header_rows") or []
    if hr:
        out["header"] = hr[0]
    blocks = meta.get("blocks") or []
    if blocks:
        out["title"] = blocks[0].get("start")
        out["data"] = blocks[-1].get("end")
    return out


def collect_style_granularity(staged: Path, meta: dict, csv_path: Path) -> dict | None:
    """样式粒度决策事实: 占位行段 + clone 候选源行的单元格样式存在性
    (边框/填充/字体/对齐/数字格式). 失败静默降级为 None (flatten 不因采集
    失败而失败, 同 collect_formula_facts). 决策事实, 不入结构指纹."""
    try:
        sheet_xml, styles_xml = _sheet_xmls(staged, meta.get("sheet", ""))
        ph_rows = detect_placeholder_rows(meta, csv_path)
        ph = _style_presence(sheet_xml, styles_xml, ph_rows)
        out = {
            "placeholder": {
                "start": ph_rows[0] if ph_rows else None,
                "end": ph_rows[-1] if ph_rows else None,
                "styled": ph["styled"],
                "sample": ph["sample"],
            },
            "template_rows": {},
        }
        for role, row in template_candidate_rows(meta).items():
            p = _style_presence(sheet_xml, styles_xml, [row])
            out["template_rows"][role] = {"row": row, "styled": p["styled"],
                                          "sample": p["sample"]}
        return out
    except Exception:
        return None


def flatten_pptx_table(staged: Path, table_id: str, name: str, workdir: Path) -> None:
    """Flatten one PPTX table into CSV + meta (structure facts for the compiler).

    Reads the table once with officecli get --depth 2; merged cells are not
    propagated (PPTX fills are per-cell value writes)."""
    from _officecli import clean_residents, officecli
    clean_residents()
    proc = officecli("get", str(staged), f"/{table_id}", "--depth", "2", "--json",
                     timeout=300)
    if proc.returncode != 0:
        fail("PPTX_FLATTEN_FAILED",
             f"officecli get {table_id} failed: {proc.stderr[-400:]}",
             "Confirm the table id from the outline")
    try:
        data = json.loads(proc.stdout)
    except ValueError as e:
        fail("PPTX_FLATTEN_INVALID", f"bad officecli JSON: {e}",
             "Re-run the flatten stage")
    result = data.get("data", {}).get("results", [{}])[0]
    children = result.get("children", []) or []
    rows = []
    max_cells = 0
    for tr in children:
        cells = [(c.get("text") or "").strip() for c in (tr.get("children", []) or [])]
        max_cells = max(max_cells, len(cells))
        rows.append(cells)
    if not rows:
        fail("PPTX_TABLE_EMPTY", f"table {table_id} has no rows",
             "Check the table id")
    with open(workdir / f"{name}_flat.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        for i, cells in enumerate(rows, start=1):
            w.writerow(cells + [str(i)])
    meta = {
        "file": str(staged),
        "sheet": table_id,
        "dimensions": {"rows": len(rows), "cols": max_cells, "data_rows": len(rows)},
        "merged_ranges": [],
        "merge_anchors": [],
        "header_band": None,
        "blocks": [],
        "columns": [
            {"col": chr(65 + ci), "nonempty": sum(1 for r in rows if ci < len(r) and r[ci])}
            for ci in range(max_cells)
        ],
        "formulas": {},
        "column_numfmt": {},
    }
    with open(workdir / f"{name}_meta.json", "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    # digest: minimal structural view (digest consumers only read it for facts)
    with open(workdir / f"{name}_digest.md", "w", encoding="utf-8") as f:
        f.write(f"# {table_id} — 结构摘要 (PPTX)\n")
        f.write(f"- 文件: {staged.name} | table: {table_id} | "
                f"{len(rows)}行 × {max_cells}列\n")
        f.write("- 非空列画像 (|列|非空|):\n")
        for ci in range(max_cells):
            f.write(f"  - {chr(65 + ci)} | {sum(1 for r in rows if ci < len(r) and r[ci])}\n")
    with open(workdir / f"{name}_candidates.yaml", "w", encoding="utf-8") as f:
        f.write("column_classifications: []\n")  # PPTX 无确定性分类, LLM 依业务判定


def run_flatten_stage(workdir: Path, sheets_arg: str, target: str) -> None:
    manifest = load_manifest(workdir)
    staged_names = {f["staged"] for f in manifest["files"]}
    for fname, _sheets in parse_sheets_arg(sheets_arg):
        if fname not in staged_names:
            fail("FILE_NOT_STAGED", f"{fname} was not staged",
                 "List it in --files during the outline stage")

    preflight.check_resident_cleanup()
    by_file: dict[str, list[tuple[str, str]]] = {}
    for fname, sheets in parse_sheets_arg(sheets_arg):
        for i, s in enumerate(sheets, start=1):
            name = f"{Path(fname).stem}_{ascii_slug(s)}"
            by_file.setdefault(fname, []).append((s, name))

    flattened = []
    target_entry = None
    style_granularity = {}
    import subprocess as _sp
    for fname, targets in by_file.items():
        staged = workdir / fname
        if staged.suffix.lower() == ".pptx":
            # PPTX target: flatten table(s) directly via officecli get (depth 2).
            for s, n in targets:
                if not s.startswith("slide["):
                    fail("PPTX_SHEET_ARG_INVALID",
                         f"pptx flatten needs 'slide[N]/table[@id=M]' targets, got: {s!r}",
                         "Use the table id from the outline (e.g. slide[5]/table[@id=3])")
                flatten_pptx_table(staged, s, n, workdir)
                entry = _entry_for(fname, s, n)
                if fname == target:
                    target_entry = entry
                flattened.append(entry)
            continue
        plan = workdir / f"_plan_{Path(fname).stem}.json"
        plan.write_text(json.dumps(
            {"targets": [{"sheet": s, "name": n} for s, n in targets]},
            ensure_ascii=False), encoding="utf-8")
        r = _sp.run(
            [sys.executable, str(Path(__file__).resolve().parent / "flatten_workbook.py"),
             "--input", str(staged), "--plan", str(plan), "--out-dir", str(workdir)],
            capture_output=True, text=True, encoding="utf-8", errors="replace")
        if r.returncode != 0:
            fail("FLATTEN_FAILED",
                 f"flatten_workbook failed for {fname}: {r.stderr[-800:]}",
                 "Read stderr and re-run the flatten stage")
        for s, n in targets:
            entry = _entry_for(fname, s, n)
            meta_path = workdir / f"{n}_meta.json"
            if fname == target:
                # 样式粒度决策事实 (issue 03): 写入 meta 供 digest 呈现, 不入指纹.
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                sg = collect_style_granularity(staged, meta, workdir / entry["csv"])
                if sg:
                    meta["style_granularity"] = sg
                    meta_path.write_text(
                        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
                    style_granularity[n] = sg
            cand_path = workdir / entry["candidates"]
            r = _sp.run(
                [sys.executable, str(Path(__file__).resolve().parent / "classify_columns.py"),
                 "--meta", str(meta_path), "--output", str(cand_path)],
                capture_output=True, text=True, encoding="utf-8", errors="replace")
            if r.returncode != 0:
                fail("CLASSIFY_FAILED",
                     f"classify_columns failed for {n}: {r.stderr[-800:]}",
                     "Read stderr and re-run the flatten stage")
            r = _sp.run(
                [sys.executable, str(Path(__file__).resolve().parent / "structure_digest.py"),
                 "--meta", str(meta_path), "--csv", str(workdir / entry["csv"]),
                 "--candidates", str(cand_path), "--out", str(workdir / entry["digest"])],
                capture_output=True, text=True, encoding="utf-8", errors="replace")
            if r.returncode != 0:
                fail("DIGEST_FAILED",
                     f"structure_digest failed for {n}: {r.stderr[-800:]}",
                     "Read stderr and re-run the flatten stage")
            if fname == target:
                target_entry = entry
            flattened.append(entry)

    if target_entry is None:
        fail("TARGET_NOT_FLATTENED",
             f"--target {target} was not among the flattened sheets",
             "Include the target file+sheet in --sheets and pass the same name to --target")

    # 增量合并: 保留此前 flatten 调用的条目 (按 name 去重, 新覆盖旧) —
    # 多次 flatten 调用不再互相覆盖 manifest (2026-08-10 复盘)。
    manifest["flattened"] = merge_flattened(manifest.get("flattened") or [], flattened)

    source_facts = [
        structure_facts(json.loads((workdir / e["meta"]).read_text(encoding="utf-8")))
        for e in manifest["flattened"] if e["name"] != target_entry["name"]
    ]
    target_facts = [
        structure_facts(json.loads((workdir / target_entry["meta"]).read_text(encoding="utf-8")))
    ]

    manifest["target"] = target_entry
    manifest["fingerprints"] = {
        "source_structure": facts_sha256(source_facts),
        "target_structure": facts_sha256(target_facts),
    }
    # 行号空洞: 目标 sheet row 元素 r 值不连续 (officecli add-after 锚点会断链).
    # 供 compile_fill.py 编译期校验 (TEMPLATE_ROW_GAP) 与 repair_row_gaps.py 修复.
    gaps = {}
    for e in manifest["flattened"]:
        meta = json.loads((workdir / e["meta"]).read_text(encoding="utf-8"))
        rg = meta.get("row_gaps") or []
        if rg:
            gaps[e["name"]] = rg
    manifest["row_gaps"] = gaps
    if style_granularity:
        manifest["style_granularity"] = style_granularity
    save_manifest(workdir, manifest)
    _record_timing(workdir, "prepare_flatten")
    print(json.dumps({"status": "PASS", "code": "FLATTEN_STAGE_DONE",
                      "flattened": [e["name"] for e in manifest["flattened"]],
                      "target": target_entry["name"],
                      "fingerprints": manifest["fingerprints"]},
                     ensure_ascii=False, indent=2))


def main() -> None:
    _utf8_stdio()
    parser = argparse.ArgumentParser(description="Prepare: stage/outline/flatten/classify/digest")
    parser.add_argument("--workdir", type=Path, required=True, help="ASCII workdir")
    parser.add_argument("--files", type=str, default="",
                        help='Outline stage: "src|name,src|name" entries')
    parser.add_argument("--task", type=str, default="", help="任务文本 (MOD evidence)")
    parser.add_argument("--outline", action="store_true",
                        help="Outline stage: preflight + stage + outline texts")
    parser.add_argument("--flatten", action="store_true",
                        help="Flatten stage: flatten + classify + digest + fingerprints")
    parser.add_argument("--sheets", type=str, default="",
                        help='Flatten stage: "file.xlsx:SheetA,SheetB;file2.xlsx:SheetC"')
    parser.add_argument("--target", type=str, default="",
                        help="Staged target file name")
    args = parser.parse_args()

    if args.outline and args.flatten:
        fail("MODE_CONFLICT", "--outline and --flatten are exclusive",
             "Run them as two separate invocations")
    if args.outline:
        if not args.files:
            fail("NO_FILES", "--outline requires --files", "Provide src|name entries")
        run_outline_stage(args.workdir, args.files, args.task)
    elif args.flatten:
        if not args.sheets or not args.target:
            fail("NO_SHEETS", "--flatten requires --sheets and --target",
                 "Provide 'file:SheetA,SheetB' pairs and the staged target name")
        run_flatten_stage(args.workdir, args.sheets, args.target)
    else:
        fail("NO_MODE", "choose --outline or --flatten", "Pass one of the two modes")
    sys.exit(0)


if __name__ == "__main__":
    main()
