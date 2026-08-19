#!/usr/bin/env python3
"""
scripts/compile_fill.py — the Compiler (v2.5): FillSpec → execution_plan.json.

The single deterministic compiler that replaces build_batch.py,
render_mapping.py, validate_batch.py, handwritten checks, and handwritten
batch JSON. It:
  1. Loads fill_spec.yaml and checks its fingerprints against
     prepare_manifest.json (stale spec → loud failure).
  2. Loads the flattened source CSVs and target structure facts.
  3. Materializes row values from rules (selectors + column mappings +
     lookups + transforms + constants) — the spec stores rules, not data.
  4. Computes the target layout (base_last_row + clone_roles).
  5. Generates the globally-ordered operation list
     (clear → add → remove → merge-clears → merge-sets → fills).
  6. Runs static validation (Section 9 of the v2.5 plan): unique target
     writes, clone source not a merge anchor, null residue policy,
     formula ranges inside the data block, aggregate coverage, target
     paths within the digest's dimensions, fingerprint match.
  7. Derives readback expectations from the materialized plan (no
     hand-written --checks).
  8. Writes execution_plan.json (machine) + mapping.md (human view).

Exit codes: 0=pass, 1=fatal, 3=spec defects (structured, fix and re-run).

Usage:
  python scripts/compile_fill.py --spec <fill_spec.yaml> --workdir <dir>
"""

from __future__ import annotations

import argparse
import csv
import fnmatch
import hashlib
import json
import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    yaml = None

MANIFEST_NAME = "prepare_manifest.json"

from _officecli import ensure_utf8_stdio, fail, record_timing, sha256_file  # noqa: E402
PLAN_NAME = "execution_plan.json"
MAPPING_NAME = "mapping.md"

CELL_RE = re.compile(r"^[A-Z]{1,2}$")
# 合并/聚合锚点默认样式。⚠️ 字体属性 (font.*) 不在此默认集内 —
# Case 010: 默认写死 Microsoft YaHei 10pt 会无条件覆盖模板单元格原有字体
# (如模板 A 列微软雅黑 12pt bold), 而 singleton 组不建 merge 保留原字体,
# 导致合并/未合并单元格字体不统一。字体只允许通过 spec 显式
# `styles: {anchor|label: {font.*}}` 声明后写入。
STYLE_DEFAULTS = {
    "anchor": {
        "alignment.wrapText": True, "alignment.horizontal": "center",
        "alignment.vertical": "center", "numberformat": "0.00%",
    },
    "label": {
        "alignment.wrapText": True, "alignment.horizontal": "center",
        "alignment.vertical": "center",
    },
}


def inherited_anchor_style(meta: dict, col: str, region_start: int,
                           region_end: int) -> dict:
    """占位区内同列第一个既有合并锚点的文本样式 (font/alignment)。

    Case 010 盲区修复: 合并区非锚点单元格通常无字体样式; 组锚点重建时若
    新锚点落在旧非锚点格, 将缺失模板字体。继承规则: 取 [region_start,
    region_end] 内同列**行号最小**的既有锚点样式; 无 → {}。
    优先级由调用方保证: spec 显式 `styles` > 继承值 > STYLE_DEFAULTS。"""
    styles_map = meta.get("merge_anchor_styles") or {}
    if not styles_map:
        return {}
    best = None  # (row, anchor)
    for a in meta.get("merge_anchors", []):
        rng = a.get("range", "")
        anchor = a.get("anchor", "")
        if not rng or not anchor:
            continue
        m = re.match(r"^([A-Z]+)(\d+):", rng)
        if not m or m.group(1) != col:
            continue
        row = int(m.group(2))
        if row < region_start or row > region_end:
            continue
        if best is None or row < best[0]:
            best = (row, anchor)
    if best and best[1] in styles_map:
        return dict(styles_map[best[1]])
    return {}
def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def bind_input_hashes(workdir: Path, inputs: dict) -> dict:
    """Compile-time binding of the STAGED input files' content hashes.

    plan.input_hashes = {staged_name: sha256} for every source + the target —
    the exact files execute_batch.py will read at execution time. This is
    recomputed at COMPILE time (not copied from prepare_manifest.json
    files[].sha256, which is an outline-stage snapshot that goes stale after
    repair_row_gaps modifies the staged target — repair resyncs fingerprints
    but not files[].sha256; recompile rebinds). A staged file missing at
    compile time binds None (unverifiable — execute fails closed on it)."""
    names = list(inputs.get("sources") or []) + [inputs.get("target")]
    out = {}
    for name in names:
        if not name:
            continue
        p = workdir / name
        out[name] = sha256_file(p) if p.is_file() else None
    return out


def col_idx_to_letter(idx: int) -> str:
    s = ""
    idx += 1
    while idx > 0:
        idx, rem = divmod(idx - 1, 26)
        s = chr(ord("A") + rem) + s
    return s


def col_letter_to_idx(letter: str) -> int:
    n = 0
    for ch in letter.upper():
        n = n * 26 + (ord(ch) - ord("A") + 1)
    return n - 1


def _parse_number(text: str) -> float | None:
    s = str(text).strip().replace(",", "").replace("%", "")
    try:
        return float(s)
    except ValueError:
        return None


def _fmt_number(v: float) -> str:
    return ("%.6g" % v) if v != int(v) else str(int(v))


# ── Loading ────────────────────────────────────────────────────────────

def load_spec(path: Path) -> dict:
    if not path.is_file():
        fail("SPEC_NOT_FOUND", f"fill_spec.yaml not found: {path}",
             "Provide the --spec path", exit_code=1)
    if yaml is None:
        fail("DEP_MISSING", "PyYAML is required", "pip install pyyaml", exit_code=1)
    try:
        spec = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        fail("SPEC_PARSE_ERROR", f"YAML parse failed: {e}",
             "Fix the YAML syntax in fill_spec.yaml")
    if not isinstance(spec, dict):
        fail("SPEC_INVALID", "fill_spec.yaml must be a mapping",
             "Use the schema in references/FILLSPEC.md")
    return spec


def load_manifest(workdir: Path) -> dict:
    p = workdir / MANIFEST_NAME
    if not p.is_file():
        fail("MANIFEST_NOT_FOUND", f"{MANIFEST_NAME} missing in {workdir}",
             "Run prepare_run.py first (outline + flatten stages)")
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except ValueError as e:
        fail("MANIFEST_INVALID", f"corrupt manifest: {e}",
             "Re-run prepare_run.py")


def load_csv_rows(csv_path: Path) -> list[tuple[list[str], int]]:
    """CSV rows: cells... + trailing original row number."""
    rows = []
    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        for line in csv.reader(f):
            if not line:
                continue
            try:
                orig = int(line[-1].strip())
            except (ValueError, IndexError):
                continue
            rows.append((line[:-1], orig))
    return rows


def expand_template(tpl: str, ctx: dict) -> str:
    """Expand {r}/{r1}/{r2}/{n}; missing key → loud error."""
    def _sub(m):
        key = m.group(1)
        if key not in ctx:
            raise KeyError(key)
        return str(ctx[key])
    try:
        return re.sub(r"\{(\w+)\}", _sub, tpl)
    except KeyError as e:
        raise ValueError(f"formula template references unknown key {{{e.args[0]}}}: {tpl!r}")


# ── Schema validation ──────────────────────────────────────────────────

REQUIRED_TOP = ("task", "inputs", "fingerprints", "mapping", "decisions",
                "gaps", "lineage", "validation")


def _bare_scalar_text(item: object) -> str:
    """Reconstruct the original bare-scalar line text from its parsed form.

    YAML parses `- 追加新历史块: 源文件 ...` into {'追加新历史块': '源文件 ...'};
    the corrective example must show the text the user actually wrote (a `k: v`
    join), not the dict repr — repr is not what belongs inside the quotes
    (2026-08-13 Egypt FRESH: the old hint suggested quoting the whole dict,
    which is not valid YAML)."""
    if not isinstance(item, dict):
        return str(item)
    text = ": ".join(f"{k}: {_bare_scalar_text(v)}" for k, v in item.items())
    # The reconstruction is shown inside a double-quoted YAML scalar — escape
    # embedded quotes/backslashes so the corrective example stays copy-paste-valid.
    return text.replace("\\", "\\\\").replace('"', '\\"')


def validate_schema(spec: dict, manifest: dict) -> list[dict]:
    defects = []
    for key in REQUIRED_TOP:
        if key not in spec:
            defects.append({"code": "SPEC_MISSING_KEY", "key": key,
                            "message": f"fill_spec missing required top-level key: {key}",
                            "corrective_action": "Add the key per references/FILLSPEC.md"})
    if defects:
        return defects

    task = spec["task"]
    if not isinstance(task.get("intent"), str) or not task["intent"].strip():
        defects.append({"code": "SPEC_TASK_INTENT", "message": "task.intent is required",
                        "corrective_action": "Describe the fill intent in one line"})
    if task.get("selected_mod") not in (None, "NONE"):
        if not isinstance(task["selected_mod"], str):
            defects.append({"code": "SPEC_MOD_VALUE",
                            "message": "task.selected_mod must be NONE or a MOD name",
                            "corrective_action": "Fix the value"})

    # decisions/gaps/lineage 条目必须是字符串 — YAML 里含 ": " 的裸标量会被
    # 解析成 mapping (dict), 静默丢内容, 必须报错并提示加引号 (2026-08-10)。
    for list_key in ("decisions", "gaps"):
        items = spec.get(list_key)
        if not isinstance(items, list):
            continue
        for i, item in enumerate(items):
            if not isinstance(item, str):
                defects.append({"code": "SPEC_NON_STRING_ITEM",
                                "key": list_key, "index": i,
                                "message": f"{list_key}[{i}] is {type(item).__name__} "
                                           f"(value {item!r}) — a bare scalar containing "
                                           f"': ' was parsed as a mapping",
                                "corrective_action": f"用双引号包裹整行: "
                                                     f'- "{_bare_scalar_text(item)}" '
                                                     f"(wrap the WHOLE line — colon included — "
                                                     "in double quotes, exactly as written)"})
    lineage = spec.get("lineage")
    if isinstance(lineage, list):
        for i, entry in enumerate(lineage):
            if not isinstance(entry, dict) or not isinstance(entry.get("source"), str):
                defects.append({"code": "SPEC_LINEAGE_INVALID",
                                "message": f"lineage[{i}] must be a mapping with a "
                                           f"string 'source' field",
                                "corrective_action": "Fix the lineage entry per FILLSPEC.md"})

    inputs = spec["inputs"]
    staged_names = {f["staged"] for f in manifest["files"]}
    if inputs.get("target") not in staged_names:
        defects.append({"code": "SPEC_TARGET_UNKNOWN",
                        "message": f"inputs.target {inputs.get('target')!r} was not staged",
                        "corrective_action": "Use a staged file name from the manifest"})
    if not isinstance(inputs.get("source_sheets"), list) or not inputs["source_sheets"]:
        defects.append({"code": "SPEC_SOURCE_SHEETS",
                        "message": "inputs.source_sheets must list which sheets were flattened",
                        "corrective_action": "List source/sheets pairs"})
    else:
        for ss in inputs["source_sheets"]:
            if ss.get("source") not in staged_names:
                defects.append({"code": "SPEC_SOURCE_UNKNOWN",
                                "message": f"source {ss.get('source')!r} was not staged",
                                "corrective_action": "Use a staged file name"})
    if not isinstance(inputs.get("target_sheet"), str) or not inputs["target_sheet"]:
        defects.append({"code": "SPEC_TARGET_SHEET",
                        "message": "inputs.target_sheet is required",
                        "corrective_action": "Set the target sheet name (or pptx table id)"})

    targets = spec["mapping"].get("targets")
    if not isinstance(targets, list) or not targets:
        defects.append({"code": "SPEC_TARGETS_EMPTY",
                        "message": "mapping.targets must contain at least one entry",
                        "corrective_action": "Describe the target layout"})
    elif len(targets) > 1:
        defects.append({"code": "SPEC_TARGETS_TOO_MANY",
                        "message": f"{len(targets)} targets declared — v2.5 compiles exactly ONE "
                                   "target per run; extra targets would be silently ignored",
                        "corrective_action": "Split the run into one fill_spec per target, "
                                             "or fold the extra sheets into a single target entry"})
    return defects


# ── Layout ─────────────────────────────────────────────────────────────

def parse_rows_spec(rows_val: str, n: int) -> tuple[int, int] | None:
    """'1:{n}' or '2:7' → (start, end) 1-based within [1, n]; None → invalid."""
    if not isinstance(rows_val, str):
        return None
    m = re.fullmatch(r"(\d+|\{n\})\s*:\s*(\d+|\{n\})", rows_val.strip())
    if not m:
        return None
    def _num(v):
        return n if v == "{n}" else int(v)
    a, b = _num(m.group(1)), _num(m.group(2))
    if a < 1 or b > n or a > b:
        return None
    return a, b


def parse_rel_rows(rows_spec) -> set[int] | None:
    """nulls rows spec: 'all' → None (caller treats as every data row),
    list of ints, or 'a:b' range."""
    if rows_spec == "all":
        return None
    if isinstance(rows_spec, list):
        return {int(x) for x in rows_spec}
    if isinstance(rows_spec, str) and ":" in rows_spec:
        a, _, b = rows_spec.partition(":")
        return set(range(int(a), int(b) + 1))
    return set()


def validate_nulls_rows(cfg: dict, defects: list) -> None:
    """nulls rows 格式静态校验 — 非法格式给结构化缺陷, 而不是在
    parse_rel_rows 里抛 ValueError 冒泡成 Python traceback
    (2026-08-12: rows: ['1:2','3:4'] 列表混合写法曾让 probe 崩溃)."""
    for n in cfg.get("nulls", []):
        rows = n.get("rows")
        col = n.get("col", "?")
        if rows == "all":
            continue
        ok = False
        if isinstance(rows, list):
            ok = all(isinstance(r, int) and r >= 1 for r in rows)
        elif isinstance(rows, str) and re.fullmatch(r"\d+\s*:\s*\d+", str(rows)):
            ok = True
        if not ok:
            defects.append({
                "code": "NULLS_ROWS_INVALID", "col": col, "rows": rows,
                "message": f"nulls[{col}] rows {rows!r} is not a valid row spec — "
                           "'all', an int list, or a 'a:b' range string",
                "corrective_action": "Use rows: all, rows: [1, 3], or rows: \"2:4\""})


# Block top-level key allowlist (ID-1). `resolve_blocks` passes through every
# key the author wrote, and `_emit_block_ops` only reads specific nested keys —
# a misplaced `aggregates:`/`per_row:`/`group_aggregates:` (which belong under
# `formulas:`) or a typo (singular `formula`, `column`) at the block top level
# used to be silently dropped (Case 05 U4/E4, 3 compile round-trips to reverse
# engineer). Legal keys match the FILLSPEC「blocks: 多数据块」declared surface
# (含位置模型的 mode 相关声明所属键 — clone_roles 条目内)。
BLOCK_TOP_LEVEL_KEYS = ("clone_roles", "rows", "columns", "formulas", "merges",
                        "group_merges", "nulls", "remove_rows", "styles")

# Known-but-misplaced keys → corrective_action names the correct nesting.
BLOCK_MISPLACED_KEY_NESTING = {
    "aggregates": "formulas.aggregates",
    "per_row": "formulas.per_row",
    "group_aggregates": "formulas.group_aggregates",
}

# Per-key copy-paste form for the corrective example (agg entries are lists,
# per_row is a {col: template} map — one generic shorthand would mislead).
BLOCK_MISPLACED_KEY_EXAMPLE = {
    "aggregates": "formulas: {aggregates: [{col, rows, formula, style}]}",
    "per_row": 'formulas: {per_row: {"G": "A{r}-B{r}"}}',
    "group_aggregates": "formulas: {group_aggregates: [{group_by, col, formula, style}]}",
}


def validate_block_top_level_keys(blocks: list) -> list:
    """Static allowlist check for every block's top-level keys.

    Runs on the resolved block configs (after `resolve_blocks`), before any
    layout/op work: a misplaced or unknown key = `BLOCK_KEY_STRUCTURE_INVALID`
    (compile-time defect, carried on stderr with a corrective_action pointing
    at the correct nesting) — never a silent ignore again. Returns defects;
    the caller fails compilation (exit 3) when non-empty."""
    defects: list = []
    legal = list(BLOCK_TOP_LEVEL_KEYS)
    for bi, b in enumerate(blocks):
        for key in b:
            if key.startswith("_"):
                continue  # internal keys (e.g. `_rows`)
            if key in BLOCK_TOP_LEVEL_KEYS:
                continue
            label = f"block[{bi}]"
            if key in BLOCK_MISPLACED_KEY_NESTING:
                target = BLOCK_MISPLACED_KEY_NESTING[key]
                example = BLOCK_MISPLACED_KEY_EXAMPLE[key]
                defects.append({
                    "code": "BLOCK_KEY_STRUCTURE_INVALID", "block": label,
                    "key": key,
                    "message": f"{label}: 顶层键 {key!r} 位置错误 — 它属于 {target} "
                               "(嵌套在 `formulas` 之下); 写在 block 顶层会被 "
                               "静默忽略, 不再通过",
                    "corrective_action": f"把 {key} 移到 {target} 下 — 写为 "
                                         f"`{example}`",
                })
            else:
                defects.append({
                    "code": "BLOCK_KEY_STRUCTURE_INVALID", "block": label,
                    "key": key,
                    "message": f"{label}: 顶层键 {key!r} 不在合法键列表 "
                               f"{legal} 内 — 拼写错误或错位键会被静默忽略, "
                               "不再通过",
                    "corrective_action": f"检查 {key!r} 的拼写/层级; 合法顶层键 = "
                                         f"{', '.join(legal)}",
                })
    return defects


def resolve_blocks(target: dict) -> list[dict]:
    """Target block list with single-block backward compatibility.

    `mapping.targets[].blocks[]` — each block carries its own clone_roles,
    rows, and optional columns/formulas/merges/nulls/remove_rows (falling back
    to the target-level config). Without `blocks`, the target-level
    clone_roles/rows/columns/... form one implicit block (old behaviour)."""
    blocks = target.get("blocks")
    if blocks is None:
        blocks = [{
            "clone_roles": target.get("clone_roles", []),
            "rows": target.get("rows") or {},
            "columns": target.get("columns", []),
            "formulas": target.get("formulas", {}),
            "merges": target.get("merges", []),
            "group_merges": target.get("group_merges", []),
            "nulls": target.get("nulls", []),
            "remove_rows": target.get("remove_rows", []),
            "styles": target.get("styles", {}),
        }]
    out = []
    for b in blocks:
        cfg = dict(b)
        cfg.setdefault("columns", target.get("columns", []))
        cfg.setdefault("formulas", target.get("formulas", {}))
        cfg.setdefault("merges", target.get("merges", []))
        cfg.setdefault("group_merges", target.get("group_merges", []))
        cfg.setdefault("nulls", target.get("nulls", []))
        cfg.setdefault("remove_rows", target.get("remove_rows", []))
        cfg.setdefault("styles", target.get("styles", {}))
        out.append(cfg)
    return out


def inplace_roles(block_cfg: dict) -> list:
    """clone_roles entries declaring mode: inplace."""
    return [r for r in block_cfg.get("clone_roles", []) if r.get("mode") == "inplace"]


def validate_inplace_declaration(blocks_cfg: list, dims: dict,
                                 defects: list) -> dict | None:
    """Position-model compile invariants (declaration level).

    Returns the inplace context {block, role, start_row, capacity, region_end}
    or None. Runs BEFORE layout so malformed regions never crash it."""
    ibs = [b for b in blocks_cfg if inplace_roles(b)]
    if not ibs:
        return None
    if len(ibs) > 1:
        defects.append({"code": "INPLACE_MULTIPLE_BLOCKS",
                        "message": f"{len(ibs)} blocks declare mode: inplace — "
                                   "a target may contain at most one inplace block",
                        "corrective_action": "Keep exactly one inplace block per target"})
    ib = ibs[0]
    if blocks_cfg.index(ib) != len(blocks_cfg) - 1:
        defects.append({"code": "INPLACE_NOT_LAST_BLOCK",
                        "message": "the inplace block must be the LAST block "
                                   "(row shifts after trim would make a following "
                                   "block's insertion point ambiguous)",
                        "corrective_action": "Move the inplace block to the end of blocks[]"})
    role = inplace_roles(ib)[0] if inplace_roles(ib) else {}
    rest = ib.get("clone_roles", [])
    idx = next((i for i, r in enumerate(rest) if r.get("mode") == "inplace"), None)
    if idx is not None and idx != len(rest) - 1:
        defects.append({"code": "INPLACE_NOT_LAST_BLOCK",
                        "message": "the inplace data role must be the last clone_role "
                                   "of its block (roles after it would get shifted rows)",
                        "corrective_action": "Reorder clone_roles so the inplace data "
                                             "role is last"})
    start_row = role.get("start_row")
    capacity = role.get("capacity")
    if not isinstance(start_row, int) or not isinstance(capacity, int) \
            or start_row < 1 or capacity < 1:
        defects.append({"code": "INPLACE_REGION_OUT_OF_BOUNDS",
                        "message": f"inplace data role needs positive integer "
                                   f"start_row and capacity (got start_row={start_row!r}, "
                                   f"capacity={capacity!r}) — the region declaration is a "
                                   "model fact the Compiler cannot compute",
                        "corrective_action": "Declare start_row and capacity from the digest"})
        return {"block": ib, "role": role, "start_row": 1, "capacity": 0,
                "region_end": 0, "declared": False}
    region_end = start_row + capacity - 1
    if region_end > dims.get("rows", 0):
        defects.append({"code": "INPLACE_REGION_OUT_OF_BOUNDS",
                        "message": f"inplace region {start_row}..{region_end} exceeds "
                                   f"digest rows {dims.get('rows')} — the spec claims "
                                   "template rows that do not exist",
                        "corrective_action": "Re-read the digest and fix start_row/capacity"})
    if not role.get("template_row"):
        defects.append({"code": "INPLACE_NO_CLONE_SOURCE",
                        "message": "inplace data role without template_row — overflow "
                                   "rows (N > capacity) need a clone format source",
                        "corrective_action": "Add template_row (a non-anchor placeholder row)"})
    return {"block": ib, "role": role, "start_row": start_row,
            "capacity": capacity, "region_end": region_end, "declared": True}


def validate_inplace_geometry(blocks_cfg: list, ip_ctx: dict, roles: list,
                              base_last_row: int, defects: list) -> None:
    """Position-model geometry invariants (post-layout, role rows known).

    Coordinate stability = append-zone legality + region-overlap check jointly:
      - INPLACE_REGION_OVERLAP: a preceding block's structural row (add target
        or remove_rows) or an absolute write touches the region.
      - STRUCTURAL_OP_OUT_OF_ZONE: a preceding block's remove_rows targets
        rows <= base_last_row (the append zone boundary)."""
    if not ip_ctx or not ip_ctx.get("declared"):
        return
    ib = ip_ctx["block"]
    ib_index = blocks_cfg.index(ib)
    lo, hi = ip_ctx["start_row"], ip_ctx["region_end"]
    for role in roles:
        if role.get("block", 0) >= ib_index:
            continue
        r = role.get("row")
        if r is not None and lo <= r <= hi:
            defects.append({"code": "INPLACE_REGION_OVERLAP",
                            "message": f"preceding block role row {r} falls inside the "
                                       f"placeholder region {lo}..{hi} — append-zone "
                                       "legality is violated (base_last_row must sit "
                                       "below the region end)",
                            "corrective_action": "Set base_last_row >= region end, or "
                                                 "move the inplace block"})
    for bi, b in enumerate(blocks_cfg):
        if bi >= ib_index:
            continue
        for rn in b.get("remove_rows", []):
            if lo <= rn <= hi:
                defects.append({"code": "INPLACE_REGION_OVERLAP",
                                "message": f"preceding block remove_rows targets row {rn} "
                                           f"inside the placeholder region {lo}..{hi}",
                                "corrective_action": "Remove rows only outside the region"})
            elif rn <= base_last_row:
                defects.append({"code": "STRUCTURAL_OP_OUT_OF_ZONE",
                                "message": f"preceding block remove_rows targets row {rn} "
                                           f"<= base_last_row {base_last_row} — structural "
                                           "row ops outside the terminal inplace block are "
                                           "illegal (the terminal inplace block owns the "
                                           "Trim the Compiler derives); remove_rows declared "
                                           "above base_last_row would hit rows shifted by "
                                           "the block's own adds",
                                "corrective_action": "前置 append 块不声明 remove_rows; "
                                                     "收缩由终末 inplace 块 Trim (编译器"
                                                     "推导). 首选 append-only 合法终态: "
                                                     "占位行自然下沉保留"})


def validate_append_remove_zone(blocks_cfg: list, base_last_row: int,
                                defects: list) -> None:
    """REMOVE_TARGETS_APPEND_ZONE: append 块的 remove_rows 必须 ≤ base_last_row.

    append 块的 add 全部插在 base_last_row 之下, remove_rows 声明的是模板坐标;
    若 remove > base_last_row, 其执行时身份被先行的 add 推移, remove 用裸模板
    坐标命中刚插入的新数据行 — 自毁 plan (probe 2026-08-13: base=10 +
    remove_rows [12,13,14] + 3 数据行克隆 → ops = add×4 → remove 14/13/12
    正是新数据行; 最终行数断言 rows + adds − removes 恒等, 抓不住)。
    remove_rows ≤ base_last_row 的经典场景 (源行数 < 模板行数) 在 add 区之外,
    不被推移, 保持合法。inplace 块消费编译器推导的 Trim, 不在本检查范围。"""
    for bi, b in enumerate(blocks_cfg):
        if inplace_roles(b):
            continue
        for rn in b.get("remove_rows", []):
            if rn > base_last_row:
                defects.append({
                    "code": "REMOVE_TARGETS_APPEND_ZONE",
                    "row": rn, "block": f"block[{bi}]",
                    "message": f"block[{bi}]: remove_rows targets row {rn} > "
                               f"base_last_row {base_last_row} — the block's own "
                               "adds insert below base_last_row and shift every "
                               "row below it, so this remove executes against a "
                               "newly inserted row (自毁 plan, 行数断言恒等抓不住); "
                               "rows ≤ base_last_row never shift",
                    "corrective_action": "首选 append-only 合法终态: 占位行自然"
                                         "下沉保留, 无需删除; remove_rows 只能声明"
                                         "≤ base_last_row 的模板既有行 (add 区之外). "
                                         "仅当占位行携带单元格样式 (digest 样式粒度"
                                         "结论) 时, mode: inplace 才是条件选项 — "
                                         "裸行占位 inplace 填入会产出无边框块, "
                                         "违反 VAL-007 格式沿用",
                })


def compute_layout_block(clone_roles: list, n_rows: int, cursor: int,
                         block_index: int) -> tuple[list[dict], int | None]:
    """One block's row layout starting at `cursor` (base row / previous block end).

    `mode: inplace` data roles consume pre-existing template rows at
    `start_row` (template coordinates, never shifted); overflow rows are
    cloned after the region. Returns (roles, data_start) —
    data_start is the first data row of this block (None when the block has
    no data role). Roles carry their block index for region-overlap checks."""
    roles = []
    data_start = None
    for role in clone_roles:
        kind = role.get("role")
        if kind == "spacer":
            cursor += 1
            roles.append({"kind": "spacer", "row": cursor, "block": block_index})
        elif kind == "data":
            if role.get("mode") == "inplace":
                # Placeholder Region rows are template coordinates. The cursor
                # (append zone) is NOT advanced over them; overflow clones land
                # after the region in the append zone.
                start_row = role.get("start_row")
                capacity = role.get("capacity")
                template_row = role.get("template_row")
                inplace_count = min(n_rows, capacity)
                for i in range(inplace_count):
                    roles.append({"kind": "data", "row": start_row + i,
                                  "template_row": template_row,
                                  "mode": "inplace", "block": block_index})
                for i in range(n_rows - inplace_count):
                    roles.append({"kind": "data", "row": start_row + capacity + i,
                                  "template_row": template_row,
                                  "mode": "overflow_clone", "block": block_index})
                data_start = start_row
                cursor = start_row + n_rows - 1
            else:
                data_start = cursor + 1
                for i in range(n_rows):
                    roles.append({"kind": "data", "row": data_start + i,
                                  "template_row": role.get("template_row"),
                                  "block": block_index})
                cursor = data_start + n_rows - 1
        else:
            cursor += 1
            roles.append({"kind": kind, "row": cursor, "block": block_index,
                          "template_row": role.get("template_row"),
                          "value": role.get("value")})
    return roles, data_start


def compute_layout(blocks: list[dict], platform: str, target: dict,
                   defects: list, table_rows: int | None = None) -> tuple[list[dict], list]:
    """xlsx: blocks lay out sequentially from base_last_row (cursor advances
    across blocks). pptx: single block, fixed tr rows from first_data_row;
    tr coordinates are checked against the table's ACTUAL row count (issue 06:
    pptx rows are pre-built — out-of-bounds is a spec error at compile time,
    never a runtime surprise).

    Returns (all_roles, data_starts) — data_starts[i] is block i's first data
    row (xlsx) or tr index (pptx)."""
    if platform == "pptx":
        if len(blocks) > 1:
            fail("PPTX_MULTI_BLOCK", "pptx targets support exactly ONE data block",
                 "Split into separate runs for multiple blocks")
        first = target.get("first_data_row")
        if not isinstance(first, int) or first < 1:
            fail("SPEC_FIRST_ROW", "pptx targets need first_data_row (1-based tr index)",
                 "Declare first_data_row in the target entry")
        n = len(blocks[0].get("_rows", []))
        last_tr = first + n - 1
        if table_rows is not None and n > 0 and last_tr > table_rows:
            defects.append({
                "code": "PPTX_TARGET_ROWS_OUT_OF_BOUNDS",
                "message": f"first_data_row {first} + {n} matched rows ends at "
                           f"tr[{last_tr}] but the table has only {table_rows} rows — "
                           "pptx rows are pre-built and cannot be cloned",
                "corrective_action": "Re-read the digest's table row count and fix "
                                     "first_data_row, or narrow the selectors, or add "
                                     "the missing rows once with python-pptx BEFORE "
                                     "running officecli (禁止在 officecli 之后重新 import)",
            })
        roles = [{"kind": "data", "tr": first + i, "template_tr": None} for i in range(n)]
        return roles, [first]
    base = target.get("base_last_row")
    if not isinstance(base, int) or base < 0:
        fail("SPEC_BASE_ROW", f"target {target.get('sheet')} needs base_last_row",
             "Use the target digest's last existing row number")
    roles = []
    data_starts = []
    cursor = base
    for bi, b in enumerate(blocks):
        b_roles, data_start = compute_layout_block(b.get("clone_roles", []),
                                                   len(b.get("_rows", [])), cursor, bi)
        roles.extend(b_roles)
        data_starts.append(data_start)
        if data_start is not None:
            cursor = data_start + len(b.get("_rows", [])) - 1
    return roles, data_starts


# ── Selectors ──────────────────────────────────────────────────────────

def apply_selectors(rows: list[tuple[list[str], int]], rows_cfg: dict,
                    num_cols: int) -> list[tuple[list[str], int]]:
    """Filter source rows by the rows-config selectors (rows.source or an
    entry of rows.sources: {source, selectors})."""
    selectors = rows_cfg.get("selectors") or []
    if not selectors:
        return rows
    result = []
    for values, orig in rows:
        ok = True
        for sel in selectors:
            col = sel.get("column")
            if not (CELL_RE.match(col or "") and col_letter_to_idx(col) < num_cols):
                raise ValueError(f"selector column {col!r} out of range")
            v = values[col_letter_to_idx(col)] if col_letter_to_idx(col) < len(values) else ""
            pattern = sel.get("pattern")
            if pattern and not fnmatch.fnmatch(v, pattern):
                ok = False
                break
            np = sel.get("not_pattern")
            if np and fnmatch.fnmatch(v, np):
                ok = False
                break
            nv = sel.get("not_value")
            if nv is not None and v == str(nv):
                ok = False
                break
        if ok:
            result.append((values, orig))
    return result


def is_header_text_row(cells: list) -> bool:
    """Is a flattened source row a header/title TEXT row (vs a data row)?

    Mechanical fact for HEADER_ROW_CONSIDERED_DATA (issue 02, Case 08 U1): a
    flattened sheet's top row is the source table's title/header — e.g.
    类别/产品类别/型号/... — and it is a *candidate data row*. When the
    fill's rows config has no selector (or the selector lets the first row
    through) that header row gets mapped into the data region.

    Detection is data-only and deterministic so the guard never needs the
    source meta: a header row is a run of text labels — >= 2 non-empty cells
    and NONE of them parses as a number. Real data rows of a fill almost
    always carry a quantity/money/SKU number; probe fixtures' first rows
    (家用/12K/Z001/1/2/3) therefore never trip it.
    """
    nonempty = [str(c).strip() for c in cells if str(c) and str(c).strip()]
    if len(nonempty) < 2:
        return False
    return all(_parse_number(c) is None for c in nonempty)


# ── Value materialization ──────────────────────────────────────────────

def _resolve_transform(tname: str, transforms: dict):
    """Resolve a transform name: custom transforms first, then built-ins
    round2/round4/... (numeric rounding). None when unknown."""
    fn = transforms.get(tname)
    if fn is None and re.fullmatch(r"round\d+", tname):
        return (lambda v, n=int(tname[5:]): round_value(v, n))
    return fn


def materialize_values(rows: list[tuple[list[str], int]], target: dict,
                       num_cols: int, lookups: dict, transforms: dict,
                       defects: list,
                       lookup_stats: dict | None = None) -> list[dict]:
    """Per matched source row → {row_values: {target_col: value}, key_values}."""
    out = []
    for values, orig in rows:
        row_values = {}
        for col_map in target.get("columns", []):
            tcol = col_map.get("target")
            if not (CELL_RE.match(tcol or "")) or col_letter_to_idx(tcol) >= num_cols:
                defects.append({"code": "COL_TARGET_INVALID", "target": tcol,
                                "message": f"column target {tcol!r} invalid or beyond digest cols",
                                "corrective_action": "Correct the column mapping"})
                continue
            src = col_map.get("source")
            lookup = col_map.get("lookup")
            if "value" in col_map:
                v = str(col_map["value"])
                tnames = col_map.get("transforms") \
                    or ([col_map["transform"]] if col_map.get("transform") else [])
                if not isinstance(tnames, list):
                    tnames = [tnames]
                for tname in tnames:
                    fn = _resolve_transform(tname, transforms)
                    if fn is not None:
                        v = fn(v)
                row_values[tcol] = v
            elif lookup and src is None:
                # Lookup-only mapping: value comes from the lookup table alone.
                v = resolve_lookup(lookup, values, lookups, defects,
                                   lookup_stats, tcol)
                if v is not None:
                    row_values[tcol] = v
            elif isinstance(src, list):
                # Multi-column source: sum of numeric values (e.g. 其他费用 = 其它+运费)
                vals = []
                for s in src:
                    sidx = col_letter_to_idx(s)
                    if sidx >= len(values):
                        defects.append({"code": "COL_SOURCE_INVALID", "source": s,
                                        "message": f"source column {s!r} out of range",
                                        "corrective_action": "Correct the column mapping"})
                        continue
                    vals.append(values[sidx])
                total = 0.0
                for v in vals:
                    if v is None or not str(v).strip() or str(v).strip() == "-":
                        continue  # missing input counts as 0 (0-口径)
                    num = _parse_number(v)
                    if num is None:
                        defects.append({"code": "SUM_NON_NUMERIC", "value": v,
                                        "message": f"multi-column sum found non-numeric value {v!r}",
                                        "corrective_action": "Use a single-column mapping for text columns"})
                    else:
                        total += num
                row_values[tcol] = _fmt_number(total)
            elif src is not None:
                sidx = col_letter_to_idx(src)
                if sidx >= len(values):
                    defects.append({"code": "COL_SOURCE_INVALID", "source": src,
                                    "message": f"source column {src!r} out of range",
                                    "corrective_action": "Correct the column mapping"})
                    continue
                v = values[sidx]
                # fallback: primary source empty → use the fallback column's value
                # (e.g. Model prefers 工厂型号 D, falls back to 产品描述 B).
                fb = col_map.get("fallback")
                if fb and (v is None or not str(v).strip()):
                    fidx = col_letter_to_idx(fb)
                    if fidx < len(values):
                        v = values[fidx]
                tnames = col_map.get("transforms") \
                    or ([col_map["transform"]] if col_map.get("transform") else [])
                if not isinstance(tnames, list):
                    tnames = [tnames]
                for tname in tnames:
                    fn = _resolve_transform(tname, transforms)
                    if fn is None:
                        defects.append({"code": "TRANSFORM_UNKNOWN", "name": tname,
                                        "message": f"transform {tname!r} not defined",
                                        "corrective_action": "Define it in mapping.transforms "
                                                             "(or use the built-in round2/round4)"})
                        continue
                    v = fn(v)
                lookup = col_map.get("lookup")
                if lookup:
                    v = resolve_lookup(lookup, values, lookups, defects,
                                       lookup_stats, tcol)
                    if v is None:
                        continue
                row_values[tcol] = v
        out.append({"orig": orig, "values": row_values})
    return out


def estimate_rendered_width(num, numfmt=None):
    """估算数字在某列 numFmt 下的渲染字符数.

    Excel 列宽单位 ≈ 默认字体 (Calibri 11) 数字字符宽, 因此渲染字符数可
    与 meta.column_width 直接比较。无 numFmt (General / 纯文本格式) →
    原始字符串长度; 有 numFmt → 按格式形态估算: 整数位数 (含零填充) +
    千分位逗号 + 小数位 + 小数点 + 百分号/货币符号 + 括号 + 引号字面量 +
    指数后缀 (负号计入)。启发式偏保守 (高估安全方向: 高估只会导致编译
    建议 round4 — 文档首选; 低估才会放行执行期溢出); 不执行真实 Excel
    渲染 (机器可验证的确定性估算, 边界由契约测试固定, 见 FILLSPEC Q7)。
    日期等无数字占位符的格式按原始字符串长度计。
    """
    s = str(num).strip()
    if not numfmt or ("0" not in numfmt and "#" not in numfmt):
        return len(s)
    nf = str(numfmt).split(";")[0]
    fmt_int, _, fmt_dec = nf.partition(".")
    parens = 2 if "(" in nf and ")" in nf else 0
    body = s.lstrip("-")
    int_part, _, _dec = body.partition(".")
    int_digits = max(len(int_part), len(re.findall(r"0", fmt_int)))
    # 百分号/千分号格式 ×100/×1000 缩放 (按值实算整数位数, 确定且保守)
    if "%" in nf or "‰" in nf:
        try:
            int_digits = max(int_digits, len(str(int(float(body) * 100))))
        except (ValueError, OverflowError):
            int_digits += 2
    # 指数后缀先从格式中摘除再数小数位 (E+00 的 00 是后缀, 不是小数位)
    exp_m = re.search(r"[Ee][+-]\d+", nf)
    exponent = len(exp_m.group(0)) if exp_m else 0
    if exp_m:
        fmt_dec = re.sub(r"[Ee][+-]\d+", "", fmt_dec)
    dec_show = len(re.findall(r"[0?]", fmt_dec)) if fmt_dec else 0
    commas = (int_digits - 1) // 3 if "," in fmt_int and int_digits > 3 else 0
    # 引号字面量计数; 符号计数排除引号内 (防双计)
    literals = sum(len(q) for q in re.findall(r'"([^"]*)"', nf))
    nf_unquoted = re.sub(r'"[^"]*"', "", nf)
    symbols = len(re.findall(r"[%‰$€£¥￥]", nf_unquoted))
    sign = 0 if parens else (1 if s.startswith("-") else 0)
    return (sign + int_digits + commas + dec_show
            + (1 if dec_show > 0 else 0)
            + parens + literals + exponent + symbols)


def apply_precision_policy(target: dict, data_rows: list,
                           defects: list, warnings: list,
                           col_widths: dict | None = None,
                           col_numfmt: dict | None = None) -> None:
    """Compile-time precision policy for the recurring text-overflow repair.

    Direct column values with > 4 decimal places or > 12 significant digits
    (e.g. cost values like 168.715100569657) overflow the narrow numeric
    columns of the quote template at execution time.

    Policy (2026-08-10, from repeated overflow repairs):
      - floating-point long tail (> 4 decimals)  → AUTO-ROUND to 4 decimals
        in place and record a warning (the fix is deterministic and matches
        the documented round4 convention — burning an execute round to
        rediscover it is pure waste).
      - over-long integers (≤ 4 decimals, > 12 digits) → round4 cannot help:
        hard defect (use `precision: keep` only with deliberate column width).
      - a mapping `transform: roundN` or `precision: keep` exempts the column.
    `precision: keep` 的豁免以列宽实测背书为前提 (issue 04): prepare 采集
    meta.column_width 后, 本函数对 keep 列估算最宽渲染值并与列宽比较 —
    超出 → PRECISION_KEEP_NARROW_COLUMN (exit 3, corrective_action 改用
    round4); 列宽缺失 (旧 meta) → 豁免 + PRECISION_KEEP_WIDTH_UNVERIFIED
    警告 (编译器不靠 Agent 猜列宽, 不再执行期才发现 text overflow)."""
    col_widths = col_widths or {}
    col_numfmt = col_numfmt or {}
    by_col: dict[str, tuple[int, str]] = {}
    for dr in data_rows:
        for col, val in (dr.get("values") or {}).items():
            if val is None or not str(val).strip():
                continue
            s = str(val).strip()
            num = _parse_number(s)
            if num is None:
                continue
            decimals = len(s.split(".")[1]) if "." in s else 0
            digits = len(re.sub(r"[^0-9]", "", s))
            if decimals <= 4 and digits <= 12:
                continue
            if col not in by_col:
                by_col[col] = (1, s)
            else:
                count, sample = by_col[col]
                by_col[col] = (count + 1, sample)
    cols_by_entry = {c.get("target"): c for c in target.get("columns", [])}
    for col, (count, sample) in sorted(by_col.items()):
        mapping = cols_by_entry.get(col, {})
        transform = mapping.get("transform")
        if transform and re.fullmatch(r"round\d+", str(transform)):
            continue  # already rounded by a built-in transform
        if mapping.get("precision") == "keep":
            # keep 的机械前提是列宽实测背书: 估算该列最宽渲染值, 与模板列宽
            # 比较 — 不足 → 编译拒绝 (不再执行期才发现 text_overflow)。
            widths = [
                estimate_rendered_width(str(dr["values"][col]),
                                        col_numfmt.get(col))
                for dr in data_rows
                if (dr.get("values") or {}).get(col) is not None
                and str(dr["values"][col]).strip()
            ]
            max_w = max(widths, default=0)
            col_width = col_widths.get(col)
            if col_width is None:
                warnings.append({
                    "code": "PRECISION_KEEP_WIDTH_UNVERIFIED", "column": col,
                    "message": f"column {col} uses `precision: keep` but prepare "
                               f"did not measure the template column width — the "
                               f"value (sample {sample!r}) cannot be verified to "
                               f"fit; re-run prepare (flatten) to collect "
                               f"meta.column_width",
                    "corrective_action": "Re-run prepare_run.py --flatten to "
                                         "measure column widths, or prefer "
                                         "`transform: round4`",
                })
            elif max_w > col_width:
                defects.append({
                    "code": "PRECISION_KEEP_NARROW_COLUMN", "column": col,
                    "message": f"column {col} is {col_width} chars wide but the "
                               f"longest `precision: keep` value renders ~{max_w} "
                               f"chars (sample {sample!r}) — it will overflow at "
                               f"execution; the measured width backing for keep "
                               f"is insufficient",
                    "corrective_action": "Use `transform: round4` (or widen the "
                                         "column) — `precision: keep` needs "
                                         "measured column width backing",
                })
            continue
        decimals = len(sample.split(".")[1]) if "." in sample else 0
        if decimals > 4:
            # auto-round in place (floating long tail — deterministic fix)
            rounded_total = 0
            for dr in data_rows:
                v = (dr.get("values") or {}).get(col)
                if v is None or not str(v).strip():
                    continue
                rv = round_value(str(v).strip(), 4)
                if rv != str(v).strip():
                    dr["values"][col] = rv
                    rounded_total += 1
            warnings.append({
                "code": "AUTO_ROUND4", "column": col,
                "message": f"{rounded_total} value(s) in column {col} rounded to 4 "
                           f"decimals (sample {sample!r} → {round_value(sample, 4)!r}) — "
                           "15-digit cost values overflow the template's narrow "
                           "columns; the mapping was NOT modified, values were "
                           "rounded in place",
                "corrective_action": "Review the Gate; add `transform: round4` "
                                     "to the column mapping to make it explicit",
            })
        else:
            defects.append({
                "code": "NUMERIC_OVERFLOW_RISK", "column": col,
                "message": f"{count} direct value(s) in column {col} have over-long "
                           f"integer precision (sample {sample!r}, >12 digits) — "
                           "rounding cannot shorten integers",
                "corrective_action": "Use `precision: keep` only if the target "
                                     "column is wide enough, or split the value",
            })


def resolve_lookup(lookup: dict, values: list, lookups: dict, defects: list,
                   stats: dict | None = None, tcol: str = "") -> str | None:
    """Resolve a lookup for one row. Returns the field value, '' on missing
    (when missing=empty), or None when a defect was recorded (caller skips)."""
    tbl = lookups.get(lookup["name"])
    if tbl is None:
        defects.append({"code": "LOOKUP_UNKNOWN", "name": lookup["name"],
                        "message": f"lookup {lookup['name']!r} not defined",
                        "corrective_action": "Define it in mapping.lookups"})
        return None
    field = lookup.get("field")
    kcol = lookup.get("key_column") or tbl.get("key_column")
    key = values[col_letter_to_idx(kcol)] if kcol else None
    norm_key = str(key).replace("\u00a0", " ").strip() if key is not None else None
    hit = tbl["data"].get(norm_key, {}) if norm_key is not None else {}
    if not hit:
        # Key miss: a hit-vs-miss is unambiguous here, so count before the
        # missing policy decides the outcome ("" vs LOOKUP_KEY_MISSING defect).
        if stats is not None:
            _note_lookup_outcome(stats, lookup["name"], tcol, miss=True)
        if lookup.get("missing") == "error":
            defects.append({"code": "LOOKUP_KEY_MISSING", "key": key,
                            "message": f"lookup key {key!r} not found in {lookup['name']}",
                            "corrective_action": "Record as a gap or fix the key"})
            return None
        return ""
    if field not in hit:
        # Field absent for this key. A schema-level absence (no entry in the
        # whole table carries the field) is always a defect; per-key absence
        # follows the missing policy (empty → blank, error → defect).
        schema_has = any(field in e for e in tbl["data"].values())
        if not schema_has or lookup.get("missing") == "error":
            defects.append({"code": "LOOKUP_FIELD_MISSING", "field": field,
                            "message": f"lookup {lookup['name']} has no field {field!r}"
                                       + ("" if schema_has else " (not in the index schema)"),
                            "corrective_action": "Check inheritance index fields"})
            return None
        if stats is not None:
            _note_lookup_outcome(stats, lookup["name"], tcol, miss=True)
        return ""
    if stats is not None:
        _note_lookup_outcome(stats, lookup["name"], tcol, miss=False)
    return hit.get(field, "")


def _note_lookup_outcome(stats: dict, name: str, tcol: str, miss: bool) -> None:
    """Count per-(lookup, column) resolutions for the all-missing guard.

    Recorded where the hit/miss semantics are known (inside resolve_lookup):
    a key that is found in the table counts as a hit even when its stored
    field value is empty; only actual misses (key/field absent) count against
    the column. Defect resolutions never reach this — they already failed."""
    cur = stats.setdefault((name, tcol), {"total": 0, "missing": 0})
    cur["total"] += 1
    if miss:
        cur["missing"] += 1


def note_lookup_all_missing(stats: dict, warnings: list) -> None:
    """Declared lookup columns that resolved to empty for EVERY row → warning.

    LOOKUP_COLUMN_ALL_MISSING (warn-only, compile proceeds): a non-empty index
    whose keys never hit may be a genuine absence (record as gaps — e.g. the
    Egypt FRESH 商用风管 SKU really is not in the index), a broken index, or a
    self-referencing index (the fill target sheet fed in as an index input —
    its historical rows are outputs, not field authority, and collide with
    independent data sheets into consensus conflicts); either way an entire
    column of silent blanks must not pass unremarked."""
    for (name, tcol), cur in sorted(stats.items()):
        if cur["total"] > 0 and cur["missing"] == cur["total"]:
            warnings.append({
                "code": "LOOKUP_COLUMN_ALL_MISSING", "lookup": name, "column": tcol,
                "message": f"lookup column {tcol} (lookup {name!r}) resolved to "
                           f"empty for ALL {cur['total']} row(s) — either the keys "
                           "are genuinely absent from the index (record them as "
                           "gaps), the index file is broken, or the index input "
                           "included the target sheet itself",
                "corrective_action": "Check the index file (field_consensus still "
                                     "present? rebuilt with "
                                     "build_inheritance_index.py?); check the "
                                     "index input sheets exclude the target sheet "
                                     "of this fill (a self-referencing index "
                                     "collides historical rows of the target "
                                     "sheet with independent data sheets into "
                                     "consensus conflicts, so keys go missing — "
                                     "build the index from independent data "
                                     "sheets only); if the keys really are "
                                     "absent, record them as gaps",
            })


def normalize_lookup_data(data: dict, name: str) -> dict:
    """Normalize known index formats to flat {key: {field: value}}.

    - build_inheritance_index output: {"index": {sku: {"field_consensus":
      {field: {"status": "unique|conflict", "value": v}}}}}
      → flat {sku: {field: v}}; non-unique consensus becomes absent (a
      lookup on it will fail exactly like a missing key).
    - plain flat {key: {field: value}} passes through.
    """
    if isinstance(data, dict) and isinstance(data.get("index"), dict):
        out = {}
        for key, entry in data["index"].items():
            consensus = entry.get("field_consensus", {}) if isinstance(entry, dict) else {}
            flat = {}
            for field, info in consensus.items():
                if isinstance(info, dict) and info.get("status") == "unique":
                    flat[field] = info.get("value", "")
            if flat:
                out[str(key)] = flat
        return out
    return data


def build_lookup_tables(spec_mapping: dict, target_cfg: dict, workdir: Path) -> dict:
    """Lookups may be declared at mapping level (shared) or per target."""
    entries = list(spec_mapping.get("lookups", [])) + list(target_cfg.get("lookups", []))
    tables = {}
    for lk in entries:
        p = workdir / lk["from"]
        if not p.is_file():
            fail("LOOKUP_FILE_MISSING", f"lookup source {lk['from']} not found",
                 "Run build_inheritance_index.py first and use its output path")
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except ValueError as e:
            fail("LOOKUP_INVALID", f"lookup source {lk['from']} not JSON: {e}",
                 "Fix the lookup file")
        normalized = normalize_lookup_data(data, lk["name"])
        if not normalized:
            # Egypt FRESH pitfall 1 (2026-08-13): a cleaning script rewrote
            # inheritance.json and dropped field_consensus → the table normalized
            # to 0 entries → every lookup silently resolved to "" (missing:
            # empty), D/F/X all blank, compile passed. Never silent again.
            fail("LOOKUP_TABLE_EMPTY",
                 f"lookup {lk['name']!r} ({lk['from']}) normalized to an EMPTY "
                 "table (0 entries) — every lookup on it would resolve to missing",
                 "Check the index file structure: does build_inheritance_index.py "
                 "output still carry field_consensus (was the JSON hand-rewritten "
                 "by a cleaning script)? Rebuild with build_inheritance_index.py "
                 "— never hand-edit the index JSON")
        tables[lk["name"]] = {"data": normalized, "key_column": lk.get("key_column")}
    return tables


def build_transforms(spec_mapping: dict, target_cfg: dict) -> dict:
    fns = {}
    entries = list(spec_mapping.get("transforms", [])) + list(target_cfg.get("transforms", []))
    for tr in entries:
        name = tr.get("name")
        fn = tr.get("function")
        if fn == "regex_replace":
            pattern = tr.get("pattern", "")
            repl = tr.get("replacement", "")
            def _rr(v, _p=pattern, _r=repl):
                try:
                    return re.sub(_p, _r, v)
                except re.error:
                    return v
            fns[name] = _rr
        elif fn == "strip":
            def _st(v):
                return str(v).strip()
            fns[name] = _st
        else:
            fail("TRANSFORM_FUNCTION_UNKNOWN", f"transform function {fn!r} unknown",
                 "Use regex_replace or strip")
    return fns


def round_value(value: str, decimals: int) -> str:
    """Round a numeric string to `decimals` places; non-numeric passes through.

    Built-in `round2`/`round4` transforms: the recurring text-overflow repair
    (15-digit cost values like 168.715100569657 written into narrow columns)
    is eliminated at compile time instead of failing at execution."""
    num = _parse_number(value)
    if num is None:
        return value
    if decimals <= 0:
        return str(int(round(num)))
    s = f"{round(num, decimals):.{decimals}f}".rstrip("0").rstrip(".")
    return s if s not in ("", "-") else "0"


# ── Operation generation (xlsx) ────────────────────────────────────────

def compute_groups(values: list) -> list[tuple[int, int]]:
    """Groups = consecutive equal-value runs (1-based inclusive rel rows).

    Groups come from the materialized data, not from
    `1:{n}`. Singleton runs are groups too (they never merge, but they own
    their anchor cell)."""
    groups: list[tuple[int, int]] = []
    i = 0
    n = len(values)
    while i < n:
        j = i
        while j + 1 < n and values[j + 1] == values[i]:
            j += 1
        groups.append((i + 1, j + 1))
        i = j + 1
    return groups


def split_group_aggregates(ga_spec: object, defects: list | None = None,
                           where: str = "") -> tuple[list, bool]:
    """Normalize `formulas.group_aggregates` → (per_group entries, whole_run?).

    Shapes accepted:
      list — canonical per-group entries `[{group_by, col, formula, style}]`;
             an entry carrying the key `whole_run` marks the cross-block total
             declaration (gated pre-spike in the static validation phase).
      dict — `{per_group: [...], whole_run: {...}}` (spec draft shape);
             per_group entries are lowered, whole_run is gated.

    Malformed shapes (per_group not a list / entry not a mapping) are skipped
    so lowering never crashes; when `defects` is given (static phase), each is
    reported as GROUP_AGGREGATES_INVALID instead of being silently absorbed."""
    if ga_spec is None:
        return [], False
    if isinstance(ga_spec, dict):
        per = ga_spec.get("per_group") or []
        if "per_group" in ga_spec and not isinstance(per, list):
            _ga_shape_defect(defects, where,
                             "per_group must be a list of per-group entries")
            per = []
        return per, ga_spec.get("whole_run") is not None
    if isinstance(ga_spec, list):
        per: list = []
        whole = False
        for entry in ga_spec:
            if not isinstance(entry, dict):
                _ga_shape_defect(defects, where,
                                 f"entry {entry!r} must be a mapping "
                                 "({group_by, col, formula, style})")
                continue
            if "whole_run" in entry:
                whole = True
            else:
                per.append(entry)
        return per, whole
    _ga_shape_defect(defects, where,
                     f"group_aggregates must be a list or a dict, got "
                     f"{type(ga_spec).__name__}")
    return [], False


def _ga_shape_defect(defects: list | None, where: str, detail: str) -> None:
    if defects is None:
        return
    defects.append({
        "code": "GROUP_AGGREGATES_INVALID", "where": where or "group_aggregates",
        "message": f"{where or 'group_aggregates'}: {detail}",
        "corrective_action": "Write formulas.group_aggregates as a list of "
                             "{group_by, col, formula, style} entries (or a "
                             "{per_group: [...], whole_run: {...}} dict)"})


PROPS_WHITELIST = ("numberformat",)


def validate_props(props: dict, where: str, defects: list) -> None:
    """Props whitelist (V1 = numberformat only)."""
    if not props:
        return
    for k in props:
        if k not in PROPS_WHITELIST:
            defects.append({"code": "PROPS_WHITELIST_VIOLATION",
                            "where": where, "prop": k,
                            "message": f"{where}: prop {k!r} is outside the V1 "
                                       f"whitelist {list(PROPS_WHITELIST)} — value "
                                       "semantics and presentation semantics are "
                                       "orthogonal; the whitelist prevents growth "
                                       "into a full style engine",
                            "corrective_action": f"Use only {list(PROPS_WHITELIST)}"})


def build_ops_xlsx(target: dict, blocks: list, roles: list, data_rows: list,
                   num_cols: int, style_defaults: dict,
                   defects: list, sheet_rows: int = 0) -> tuple[list, list, dict]:
    """Generate globally-ordered operations for one or more data blocks.

    Phase invariant: append blocks → sets → terminal inplace
    block's structural operations → inplace value writes. Excel's natural row
    shift relocates set cells; the Compiler only translates *readback* paths
    to final coordinates (ops keep template coordinates — they execute before
    the shift).

    blocks: [{cfg, data_start, count, inplace?}] — per-block
    clone_roles/columns/formulas/merges/group_merges/nulls/remove_rows;
    relative block rows resolve against the block's data_start."""
    sheet = target["sheet"]
    ops: list = []
    written: dict[str, str] = {}
    readback: list = []
    group_boundaries: list = []

    # Inplace context: uniform row shift for everything below the region
    # (append-zone rows + sets). Region rows are coordinate-stable.
    shift = 0
    region_lo = region_hi = None
    for b in blocks:
        ip = b.get("inplace")
        if ip:
            shift = b["count"] - ip["capacity"]
            region_lo = ip["start_row"]
            region_hi = ip["region_end"]
            break

    def cell_path(col: str, row: int) -> str:
        return f"/{sheet}/{col}{row}"

    def final_row(row: int) -> int:
        if region_hi is not None and row > region_hi:
            return row + shift
        return row

    def final_path(col: str, row: int) -> str:
        return cell_path(col, final_row(row))

    def register_path(path: str, kind: str, value: str | None) -> None:
        prev = written.get(path)
        if prev is not None:
            defects.append({"code": "DUPLICATE_TARGET_WRITE", "path": path,
                            "message": f"cell {path} written twice (first as {prev})",
                            "corrective_action": "Each target cell may be written by exactly one column mapping/null/formula/group/set"})
        written[path] = kind
        if kind == "value":
            readback.append({"path": path, "expect": value or "", "kind": "value"})
        elif kind == "empty":
            readback.append({"path": path, "expect": "EMPTY", "kind": "empty"})
        elif kind == "nonempty":
            readback.append({"path": path, "expect": "", "kind": "nonempty"})

    def register_with(final_path_fn):
        def reg(col: str, row: int, kind: str, value: str | None) -> None:
            register_path(final_path_fn(col, row), kind, value)
        return reg

    def register(col: str, row: int, kind: str, value: str | None) -> None:
        register_path(final_path(col, row), kind, value)

    def style_for(cfg: dict, style: str) -> dict:
        return dict(style_defaults[style],
                    **(cfg.get("styles") or {}).get(style, {}))

    append_blocks = [b for b in blocks if not b.get("inplace")]
    inplace_blocks = [b for b in blocks if b.get("inplace")]

    # ── 1. append-block adds (top-down). Inplace region rows already exist;
    #    overflow clones are deferred to phase 5 (after sets). Cell writes are
    #    NOT interleaved here — officecli corrupts row bookkeeping (duplicate
    #    rows) when a value write lands between row-adds.
    deferred_values: list[tuple[int, str]] = []
    for role in roles:
        if role.get("mode") == "inplace":
            continue
        if role.get("mode") == "overflow_clone":
            continue
        if role["kind"] == "spacer":
            ops.append({"command": "add", "parent": f"/{sheet}", "type": "row",
                        "props": {"cols": num_cols}})
        elif role["kind"] == "data":
            trow = role.get("template_row")
            row = role["row"]
            ops.append({"command": "add", "parent": f"/{sheet}", "type": "row",
                        "from": f"/{sheet}/row[{trow}]",
                        "after": f"/{sheet}/row[{row - 1}]"})
        else:
            row = role["row"]
            trow = role.get("template_row")
            ops.append({"command": "add", "parent": f"/{sheet}", "type": "row",
                        "from": f"/{sheet}/row[{trow}]",
                        "after": f"/{sheet}/row[{row - 1}]"})
            if role.get("value"):
                deferred_values.append((row, role["value"]))

    # ── 2. append-block removes (bottom-to-top)
    for b in append_blocks:
        for rn in sorted(b["cfg"].get("remove_rows", []), reverse=True):
            ops.append({"command": "remove", "path": f"/{sheet}/row[{rn}]"})

    # ── 3. append-block value writes (deferred + per-block)
    for row, value in deferred_values:
        ops.append({"command": "set", "path": cell_path("A", row),
                    "props": {"value": value}})
        register("A", row, "value", value)

    # ── 4. sets — absolute template-coordinate writes. Execute
    #    AFTER append blocks, BEFORE the inplace structural ops: Excel's row
    #    shift relocates them (readback paths are translated to final rows).
    set_records = _emit_sets(target, region_lo, region_hi, final_path,
                             ops, register_path, defects, sheet_rows, num_cols,
                             cell_path)

    # ── 5. inplace block's structural operations: overflow clone adds
    #    (top-down), then trim removes (bottom-up). Mutually exclusive
    #    directions of the same count comparison.
    trim_count = 0
    for role in roles:
        if role.get("mode") == "overflow_clone":
            trow = role.get("template_row")
            row = role["row"]
            ops.append({"command": "add", "parent": f"/{sheet}", "type": "row",
                        "from": f"/{sheet}/row[{trow}]",
                        "after": f"/{sheet}/row[{row - 1}]"})
    for b in inplace_blocks:
        n, cap, start = b["count"], b["inplace"]["capacity"], b["inplace"]["start_row"]
        for rn in inplace_trim_rows(n, cap, start):
            trim_count += 1
            ops.append({"command": "remove", "path": f"/{sheet}/row[{rn}]"})

    # ── 6. inplace value writes (per block: merge-clear → group_merges →
    #    merges → fills → aggregates). Inplace rows are coordinate-stable:
    #    they register at template==final coordinates, NOT shifted.
    data_cursor = 0
    for b in append_blocks + inplace_blocks:
        if b.get("inplace"):
            blk_register = register_with(cell_path)
            blk_final = lambda r: r  # noqa: E731 — region/clone rows are final
        else:
            blk_register = register
            blk_final = final_row
        _emit_block_ops(b, data_rows, data_cursor, num_cols, style_for,
                        cell_path, blk_register, group_boundaries, defects,
                        blk_final, sheet, ops)
        data_cursor += b["count"]

    return ops, readback, written, group_boundaries, trim_count, set_records


def _emit_sets(target: dict, region_lo: int, region_hi: int, final_path,
               ops: list, register_path, defects: list, sheet_rows: int,
               num_cols: int, cell_path=None) -> list:
    """Absolute cell writes (target-level `sets`). Template coordinates;
    `value: null` = explicit clear; props whitelist = numberformat (V1).

    Ops target the TEMPLATE coordinate (sets execute BEFORE the inplace
    structural shift); the plan/readback records use FINAL coordinates —
    Excel's natural row shift relocates the written cell.

    register_path(path, kind, value) — the caller's registration callback
    (xlsx translates to final coordinates via final_path; pptx DOM paths
    register as-is). Returns plan records (final paths) for mapping.md."""
    sheet = target["sheet"]
    records = []
    for s in target.get("sets") or []:
        path = str(s.get("path") or "")
        props = s.get("props") or {}
        validate_props(props, f"sets[{path}]", defects)
        value = s.get("value")
        m = re.fullmatch(r"([A-Z]{1,2})(\d+)", path)
        if not m:
            m = re.fullmatch(rf"/{re.escape(sheet)}/([A-Z]{{1,2}})(\d+)", path)
        if not m:
            m = re.fullmatch(r".*/tr\[(\d+)\]/tc\[(\d+)\]", path)  # pptx DOM cell
            if not m:
                defects.append({"code": "SET_OUT_OF_BOUNDS", "path": path,
                                "message": f"sets.path {path!r} is not a bare cell "
                                           "coordinate (xlsx), a /Sheet/A1 path, or a "
                                           "full DOM cell path (pptx)",
                                "corrective_action": "Use e.g. 'A4' or "
                                                     "'/slide[1]/table[@id=1]/tr[2]/tc[3]'"})
                continue
            row, tc_idx = int(m.group(1)), int(m.group(2))
            if row > sheet_rows or tc_idx > num_cols:
                defects.append({"code": "SET_OUT_OF_BOUNDS", "path": path,
                                "message": f"sets.path {path} (tr {row}, tc {tc_idx}) "
                                           f"exceeds table dimensions {sheet_rows}×{num_cols}",
                                "corrective_action": "Pick an existing table cell path"})
                continue
            ops.append({"command": "set", "path": path,
                        "props": {"text": value if value is not None else ""}})
            register_path(path, "value" if value is not None else "empty", value)
            records.append({"path": path, "value": value,
                            "numberformat": props.get("numberformat")})
            continue
        col, row = m.group(1), int(m.group(2))
        if row > sheet_rows:
            defects.append({"code": "SET_OUT_OF_BOUNDS", "path": path,
                            "message": f"sets.path {path} row {row} exceeds digest "
                                       f"rows {sheet_rows} — sets target existing "
                                       "template coordinates only",
                            "corrective_action": "Pick an existing template row"})
            continue
        if region_lo is not None and region_lo <= row <= region_hi:
            defects.append({"code": "INPLACE_REGION_OVERLAP", "path": path,
                            "message": f"sets.path {path} falls inside the placeholder "
                                       f"region {region_lo}..{region_hi} — sets must not "
                                       "target the region (inplace fills own those rows)",
                            "corrective_action": "Express region content as column "
                                                 "mappings / group labels instead"})
            continue
        set_props = {"value": value if value is not None else None}
        if value is not None and props.get("numberformat"):
            set_props["numberformat"] = props["numberformat"]
        if cell_path is None:
            cell_path = final_path
        ops.append({"command": "set", "path": cell_path(col, row), "props": set_props})
        fpath = final_path(col, row)
        register_path(fpath, "value" if value is not None else "empty", value)
        records.append({"path": fpath, "value": value,
                        "numberformat": props.get("numberformat")})
    return records


def _emit_block_ops(b: dict, data_rows: list, data_cursor: int, num_cols: int,
                    style_for, cell_path, register, group_boundaries: list,
                    defects: list, final_row=None, sheet: str = "",
                    ops: list | None = None) -> None:
    """One block's value ops: merge-clear → group_merges → merges → fills →
    aggregates → group_aggregates. Shared by append blocks (phase 3) and the
    inplace block (phase 6); the phase ORDER is decided by the caller."""
    if ops is None:
        raise TypeError("_emit_block_ops requires the ops list")
    n = b["count"]
    cfg = b["cfg"]
    first_row = b["data_start"]
    blk_rows = data_rows[data_cursor:data_cursor + n]
    gm = cfg.get("group_merges", [])
    gm_by_col = {g.get("col"): g for g in gm}
    group_cols = set(gm_by_col)
    ga_entries, _ = split_group_aggregates(
        cfg.get("formulas", {}).get("group_aggregates"))
    # Columns that carry an aggregation anchor — the canonical correct shape
    # when such a column is wrongly put in group_merges is the same-scope
    # merges + aggregates pair (aggregation anchor = merge anchor, Q12).
    agg_anchor_cols = ({a.get("col") for a in cfg.get("formulas", {}).get("aggregates", [])}
                       | {g.get("col") for g in ga_entries})
    merge_cols = sorted({m.get("col") for m in cfg.get("merges", [])}
                        | agg_anchor_cols
                        | group_cols)

    # 1. merge-clears on every data row (per-block merge/group/agg columns) —
    #    breaks stale merges INCLUDING single-cell residue (A19:A19).
    for i in range(n):
        row = first_row + i
        for col in merge_cols:
            ops.append({"command": "set", "path": cell_path(col, row),
                        "props": {"merge": False}})

    # 2. group_merges lowering: groups from materialized
    #    group_by values → anchors written / non-anchors cleared → merges of
    #    length > 1 (singletons never merge).
    if final_row is None:
        final_row = lambda r: r  # noqa: E731 — unit-test fallback
    mapped_cols = {c.get("target") for c in cfg.get("columns", [])}
    v2_merge_cols = {m.get("col") for m in cfg.get("merges", [])}
    props_by_col = {c.get("target"): (c.get("props") or {})
                    for c in cfg.get("columns", [])}
    for col_map in cfg.get("columns", []):
        validate_props(col_map.get("props") or {},
                       f"columns[{col_map.get('target')}]", defects)
    for g in gm:
        col = g.get("col")
        gcol = g.get("group_by")
        if not gcol:
            defects.append({"code": "GROUP_MERGE_ANCHOR_UNCOVERED", "col": col,
                            "message": f"group_merges[{col}] needs group_by (a mapped "
                                       "target column whose materialized value groups rows)",
                            "corrective_action": "Declare group_by"})
            continue
        if gcol not in mapped_cols:
            defects.append({"code": "GROUP_BY_COLUMN_UNMAPPED", "col": gcol,
                            "message": f"group_by column {gcol} has no column mapping — "
                                       "groups need the column's logical materialized value",
                            "corrective_action": "Add a columns mapping for the "
                                                 "group_by column"})
            continue
        if col in v2_merge_cols:
            if col in agg_anchor_cols:
                corrective = ("该列承载聚合（聚合锚点 / 合并覆盖残留）: 删除其 "
                              "group_merges 条目, 改用同范围 merges + aggregates 对"
                              "（聚合锚点=合并锚点）")
            else:
                corrective = ("该列是普通标签列: 每列只保留一种合并模式 — 删除 "
                              "group_merges 或 merges 之一")
            defects.append({"code": "MERGE_MODE_CONFLICT", "col": col,
                            "message": f"column {col} appears in both merges and "
                                       "group_merges — the merge modes are mutually "
                                       "exclusive per column",
                            "corrective_action": corrective})
            continue
        groups = compute_groups([dr.get("values", {}).get(gcol, "")
                                 for dr in blk_rows])
        mapped = col in mapped_cols
        label = g.get("label")
        if not mapped and label is None:
            defects.append({"code": "GROUP_MERGE_ANCHOR_UNCOVERED", "col": col,
                            "message": f"group_merges column {col} has no column "
                                       "mapping and no label — the anchor cell would "
                                       "be uncovered",
                            "corrective_action": "Add a columns mapping or declare "
                                                 "label ('' = clear anchor)"})
            continue
        gstyle = style_for(cfg, g.get("style", "label"))
        if b.get("inplace"):
            # 锚点样式继承 (Case 010 盲区): 新组锚点落在旧非锚点格时补
            # 模板锚点字体/对齐; spec 显式 styles 声明的键优先, 不被覆盖。
            inh = (b.get("inherited_styles") or {}).get(col) or {}
            spec_styles = (cfg.get("styles") or {}).get(g.get("style", "label"), {})
            gstyle = {**gstyle,
                      **{k: v for k, v in inh.items() if k not in spec_styles}}
        g_merges = []
        for (s, e) in groups:
            anchor_row = first_row + s - 1
            if e > s:
                g_merges.append(f"{col}{final_row(anchor_row)}:"
                                f"{col}{final_row(first_row + e - 1)}")
                props = {"merge": f"{col}{anchor_row}:{col}{first_row + e - 1}"}
                props.update(gstyle)
                ops.append({"command": "set", "path": cell_path(col, anchor_row),
                            "props": props})
            if mapped:
                val = blk_rows[s - 1].get("values", {}).get(col)
                if val is None:
                    ops.append({"command": "set", "path": cell_path(col, anchor_row),
                                "props": {"value": None}})
                    register(col, anchor_row, "empty", None)
                else:
                    props = {"value": val}
                    if props_by_col.get(col, {}).get("numberformat"):
                        props["numberformat"] = props_by_col[col]["numberformat"]
                    ops.append({"command": "set", "path": cell_path(col, anchor_row),
                                "props": props})
                    register(col, anchor_row, "value", val)
            elif label == "":
                ops.append({"command": "set", "path": cell_path(col, anchor_row),
                            "props": {"value": None}})
                register(col, anchor_row, "empty", None)
            else:
                props = {"value": label}
                if props_by_col.get(col, {}).get("numberformat"):
                    props["numberformat"] = props_by_col[col]["numberformat"]
                ops.append({"command": "set", "path": cell_path(col, anchor_row),
                            "props": props})
                register(col, anchor_row, "value", label)
            for r in range(s + 1, e + 1):
                ops.append({"command": "set",
                            "path": cell_path(col, first_row + r - 1),
                            "props": {"value": None}})
                register(col, first_row + r - 1, "empty", None)
        group_boundaries.append({
            "col": col, "sheet": sheet,
            "region_start": final_row(first_row),
            "region_end": final_row(first_row + n - 1),
            "expected_merges": g_merges,
        })

    # 3. merges + styles (V2 block-wide `1:{n}` ranges)
    b_styles = {
        "anchor": style_for(cfg, "anchor"),
        "label": style_for(cfg, "label"),
    }
    for m in cfg.get("merges", []):
        col = m.get("col")
        span = parse_rows_spec(m.get("rows", ""), n)
        if not span:
            defects.append({"code": "MERGE_RANGE_INVALID", "col": col,
                            "message": f"merge rows {m.get('rows')!r} invalid for {n} data rows",
                            "corrective_action": "Use '1:{n}' or an explicit range within the block"})
            continue
        r1, r2 = span
        props = {"merge": f"{col}{first_row + r1 - 1}:{col}{first_row + r2 - 1}"}
        props.update(b_styles.get(m.get("style", "label"), b_styles["label"]))
        if b.get("inplace"):
            # 锚点样式继承 (Case 010 盲区) — 同 group_merges 规则:
            # spec 显式 styles 优先, 继承值补默认。
            inh = (b.get("inherited_styles") or {}).get(col) or {}
            spec_styles = (cfg.get("styles") or {}).get(m.get("style", "label"), {})
            props.update({k: v for k, v in inh.items() if k not in spec_styles})
        ops.append({"command": "set", "path": cell_path(col, first_row + r1 - 1),
                    "props": props})

    # 4. fills: deferred role values + per-block values/nulls/per-row formulas
    null_specs = {x["col"]: x.get("rows") for x in cfg.get("nulls", [])}
    per_row = cfg.get("formulas", {}).get("per_row", {})
    for rel in range(1, n + 1):
        row = first_row + rel - 1
        dr = blk_rows[rel - 1]
        for col, val in dr.get("values", {}).items():
            if col in group_cols:
                continue  # anchors written by the group lowering
            props = {"value": val}
            if props_by_col.get(col, {}).get("numberformat"):
                props["numberformat"] = props_by_col[col]["numberformat"]
            ops.append({"command": "set", "path": cell_path(col, row),
                        "props": props})
            register(col, row, "value", val)
        for col, rows_spec in null_specs.items():
            rel_rows = parse_rel_rows(rows_spec)
            if rel_rows is None or rel in rel_rows:
                ops.append({"command": "set", "path": cell_path(col, row),
                            "props": {"value": None}})
                register(col, row, "empty", None)
        for col, tpl in per_row.items():
            formula = expand_template(tpl, {"r": row, "n": n})
            ops.append({"command": "set", "path": cell_path(col, row),
                        "props": {"formula": formula}})
            register(col, row, "nonempty", None)

    # 5. aggregates (per block)
    for a in cfg.get("formulas", {}).get("aggregates", []):
        col = a.get("col")
        span = parse_rows_spec(a.get("rows", ""), n)
        if not span:
            defects.append({"code": "AGG_RANGE_INVALID", "col": col,
                            "message": f"aggregate rows {a.get('rows')!r} invalid for {n} data rows",
                            "corrective_action": "Use '1:{n}' or an explicit range within the block"})
            continue
        r1, r2 = span
        props = {"formula": expand_template(a["formula"], {"r1": first_row + r1 - 1,
                                                           "r2": first_row + r2 - 1,
                                                           "n": n})}
        props.update(b_styles.get(a.get("style", "anchor"), b_styles["anchor"]))
        ops.append({"command": "set", "path": cell_path(col, first_row + r1 - 1),
                    "props": props})
        register(col, first_row + r1 - 1, "nonempty", None)

    # 6. group_aggregates: per-group formulas at group anchor rows.
    #    Groups come from the materialized group_by values (compute_groups);
    #    {r1}:{r2} expands per group start/end and must stay inside the block
    #    (AGG_RANGE_INVALID). Anchor cells register nonempty readback. The
    #    whole_run gate fires in the static validation phase, before ops.
    for ga in ga_entries:
        col = ga.get("col")
        gcol = ga.get("group_by")
        if not gcol:
            defects.append({"code": "GROUP_BY_COLUMN_UNMAPPED", "col": col,
                            "message": f"group_aggregates[{col}] needs group_by — "
                                       "the mapped target column whose materialized "
                                       "values define the groups",
                            "corrective_action": "Declare group_by"})
            continue
        if gcol not in mapped_cols:
            defects.append({"code": "GROUP_BY_COLUMN_UNMAPPED", "col": gcol,
                            "message": f"group_aggregates group_by column {gcol} has no "
                                       "column mapping — groups need the column's "
                                       "logical materialized value",
                            "corrective_action": "Add a columns mapping for the "
                                                 "group_by column"})
            continue
        groups = compute_groups([dr.get("values", {}).get(gcol, "")
                                 for dr in blk_rows])
        gstyle = style_for(cfg, ga.get("style", "anchor"))
        for (s, e) in groups:
            if s < 1 or e > n:
                defects.append({"code": "AGG_RANGE_INVALID", "col": col,
                                "message": f"group_aggregates[{col}] group range "
                                           f"{s}:{e} crosses the data block boundary "
                                           f"(1:{n}) — group ranges must stay inside "
                                           "the block",
                                "corrective_action": "Check the group_by materialized "
                                                     "values and block selectors"})
                continue
            anchor_row = first_row + s - 1
            props = {"formula": expand_template(ga["formula"], {"r1": first_row + s - 1,
                                                                "r2": first_row + e - 1,
                                                                "n": n})}
            props.update(gstyle)
            ops.append({"command": "set", "path": cell_path(col, anchor_row),
                        "props": props})
            register(col, anchor_row, "nonempty", None)


def final_row_of(block_infos: list, row: int) -> int:
    """Plan coordinates (row_map/writes/blocks): region rows are stable,
    overflow clone rows are added at their final positions, and everything
    below the region shifts by (N - capacity)."""
    for b in block_infos:
        ip = b.get("inplace")
        if ip:
            lo, hi = ip["start_row"], ip["region_end"]
            n, cap = b["count"], ip["capacity"]
            if lo <= row <= hi:
                return row
            if hi < row <= hi + (n - cap):
                return row  # overflow clone position — already final
            if row > hi:
                return row + (n - cap)
            break
    return row


def spec_final_row_of(block_infos: list, row: int) -> int:
    """Spec coordinates are TEMPLATE coordinates (the spec
    never computes post-shift row numbers): every row below the region shifts.
    Overflow clone positions are never valid spec references."""
    for b in block_infos:
        ip = b.get("inplace")
        if ip:
            if row > ip["region_end"]:
                return row + (b["count"] - ip["capacity"])
            break
    return row


def build_ops_pptx(target: dict, roles: list, data_rows: list, num_cols: int,
                   defects: list) -> tuple[list, list, dict]:
    """pptx: value fills only; rows pre-created (python-pptx once, before officecli).

    FillSpec column targets are letters (A..Z) for cross-format consistency;
    they map to tr/tc indices here (A→tc[1])."""
    table = target["sheet"]  # e.g. "slide[3]/table[@id=2]"
    ops: list = []
    readback: list = []
    written: dict[str, str] = {}

    def register(path: str, kind: str, value: str | None) -> None:
        if path in written:
            defects.append({"code": "DUPLICATE_TARGET_WRITE", "path": path,
                            "message": f"cell {path} written twice",
                            "corrective_action": "Each target cell may be written once"})
        written[path] = kind
        if kind == "value":
            readback.append({"path": path, "expect": value or "", "kind": "value"})

    for role, dr in zip([r for r in roles if r["kind"] == "data"], data_rows):
        tr = role["tr"]
        for col, val in dr["values"].items():
            if not CELL_RE.match(col):
                defects.append({"code": "PPTX_TARGET_INVALID", "column": col,
                                "message": f"pptx target {col!r} is not a column letter",
                                "corrective_action": "Use A..Z column letters in columns mapping"})
                continue
            tcidx = col_letter_to_idx(col) + 1
            if tcidx > num_cols:
                defects.append({"code": "PPTX_TARGET_OUT_OF_BOUNDS", "column": col,
                                "message": f"column {col} (tc[{tcidx}]) beyond table width {num_cols}",
                                "corrective_action": "Check the target table's column count"})
                continue
            path = f"/{table}/tr[{tr}]/tc[{tcidx}]"
            # PPTX table cells expose `text` (not xlsx's `value` — officecli
            # rejects `value` on pptx cells: valid props are text/bold/...).
            ops.append({"command": "set", "path": path, "props": {"text": val}})
            register(path, "value", val)
    return ops, readback, written


def _pptx_register(path: str, kind: str, value: str | None, written: dict,
                   readback: list, defects: list) -> None:
    """Registration callback for pptx `sets` (DOM paths are final as-is)."""
    if path in written:
        defects.append({"code": "DUPLICATE_TARGET_WRITE", "path": path,
                        "message": f"cell {path} written twice",
                        "corrective_action": "Each target cell may be written once"})
    written[path] = kind
    if kind == "value":
        readback.append({"path": path, "expect": value or "", "kind": "value"})
    elif kind == "empty":
        readback.append({"path": path, "expect": "EMPTY", "kind": "empty"})


# ── Static validation ──────────────────────────────────────────────────

def validate_clone_residue(target: dict, template_row: int, target_csv: Path,
                           num_cols: int, columns: list, null_specs: dict,
                           per_row_formulas: dict, n_data_rows: int, defects: list,
                           group_merge_cols: set | None = None) -> None:
    """Template-row clone carries values; every carried column must be
    overwritten (columns/fills) or explicitly nulled for EVERY data row —
    a nulls entry covering only some rows leaves residue on the rest.

    group_merge columns are covered per-row by the lowering (anchors written,
    non-anchors explicitly cleared), so they are exempt wholesale."""
    rows = load_csv_rows(target_csv)
    carried = {}
    for values, orig in rows:
        if orig == template_row:
            for ci, v in enumerate(values):
                if v and v.strip() and ci < num_cols:
                    carried[col_idx_to_letter(ci)] = v.strip()
            break
    if not carried:
        return
    group_cols = group_merge_cols or set()
    filled_cols = {c.get("target") for c in columns}      # fills write every data row
    formula_cols = set(per_row_formulas)                  # per_row formulas write every row
    merge_cols = {m.get("col") for m in target.get("merges", [])}  # merge-clear+merge-set handles all rows
    all_rows = set(range(1, n_data_rows + 1))
    for c, val in carried.items():
        if c in filled_cols or c in formula_cols or c in merge_cols or c in group_cols:
            continue
        if c in null_specs:
            covered = parse_rel_rows(null_specs[c])       # None = all rows
            if covered is not None and covered != all_rows:
                uncovered = sorted(all_rows - covered)
                defects.append({"code": "CLONE_RESIDUE_PARTIAL_NULLS", "column": c,
                                "message": f"template row {template_row} carries {c}='{val}' "
                                           f"but nulls covers only rows {sorted(covered)} — "
                                           f"rows {uncovered} keep the cloned value",
                                "corrective_action": "Use rows: all, or add a column mapping "
                                                     f"that fills {c} on every row"})
            continue
        defects.append({"code": "CLONE_RESIDUE_UNHANDLED", "column": c,
                        "message": f"template row {template_row} carries {c}='{val}' "
                                   "into cloned rows but no fill, null, formula or merge covers it",
                        "corrective_action": "Add a column mapping or a nulls entry for column "
                                             f"{c} (or justify keeping it in decisions)"})


def validate_placeholder_residue(cfg: dict, start_row: int, capacity: int,
                                 n_rows: int, target_csv: Path, num_cols: int,
                                 data_rows: list, defects: list) -> None:
    """Double residue baseline — the retained Placeholder Region
    rows are checked against EACH row's OWN original values (not one template
    row). Coverage sources: per-row fills, nulls, per-row formulas, group
    anchors/labels — NOT sets (sets may not target the region).

    A retained row is any region row that survives trim:
    [start_row, start_row + min(n_rows, capacity))."""
    rows = load_csv_rows(target_csv)
    retained_hi = start_row + min(n_rows, capacity)
    carried_by_row: dict[int, dict[str, str]] = {}
    for values, orig in rows:
        if start_row <= orig < retained_hi:
            carried = {col_idx_to_letter(ci): v.strip()
                       for ci, v in enumerate(values)
                       if v and v.strip() and ci < num_cols}
            if carried:
                carried_by_row[orig] = carried
    if not carried_by_row:
        return
    filled_cols = {c.get("target") for c in cfg.get("columns", [])}
    formula_cols = set(cfg.get("formulas", {}).get("per_row", {}))
    group_cols = {g.get("col") for g in cfg.get("group_merges", [])}
    null_specs = {x["col"]: x.get("rows") for x in cfg.get("nulls", [])}
    for orig, carried in sorted(carried_by_row.items()):
        rel = orig - start_row + 1
        for c, val in carried.items():
            if c in filled_cols or c in formula_cols or c in group_cols:
                continue
            if c in null_specs:
                covered = parse_rel_rows(null_specs[c])   # None = all rows
                if covered is not None and rel not in covered:
                    defects.append({"code": "PLACEHOLDER_RESIDUE_PARTIAL_NULLS",
                                    "row": orig, "column": c, "value": val,
                                    "message": f"placeholder row {orig} carries {c}='{val}' "
                                               f"but nulls covers only rows {sorted(covered)} — "
                                               f"row {rel} keeps the placeholder value",
                                    "corrective_action": "Use rows: all, or add a column "
                                                         f"mapping that fills {c} on every row"})
                continue
            defects.append({"code": "PLACEHOLDER_RESIDUE_UNHANDLED",
                            "row": orig, "column": c, "value": val,
                            "message": f"retained placeholder row {orig} carries {c}='{val}' "
                                       "but no fill, null, formula or group anchor/label covers it",
                            "corrective_action": "Add a column mapping, a nulls entry, or a "
                                                 f"group_merges label for column {c}"})


def validate_formula_references(formulas: dict, n_rows: int, defects: list) -> None:
    per_row = formulas.get("per_row", {})
    for col, tpl in per_row.items():
        try:
            expand_template(tpl, {"r": 1, "n": n_rows})
        except ValueError as e:
            defects.append({"code": "FORMULA_TEMPLATE_INVALID", "col": col,
                            "message": str(e), "corrective_action": "Fix the formula template"})
    ga_entries, _ = split_group_aggregates(formulas.get("group_aggregates"))
    for ga in ga_entries:
        col = ga.get("col")
        tpl = ga.get("formula")
        if not isinstance(tpl, str):
            defects.append({"code": "FORMULA_TEMPLATE_INVALID", "col": col,
                            "message": f"group_aggregates[{col}] needs a formula "
                                       "template ({r1}:{r2} expand per group)",
                            "corrective_action": "Add the formula template"})
            continue
        try:
            expand_template(tpl, {"r1": 1, "r2": n_rows, "n": n_rows})
        except ValueError as e:
            defects.append({"code": "FORMULA_TEMPLATE_INVALID", "col": col,
                            "message": str(e), "corrective_action": "Fix the formula template"})


# ── Outputs ────────────────────────────────────────────────────────────

def inplace_trim_rows(count: int, capacity: int, start_row: int) -> list[int]:
    """Tail-trim row numbers for an inplace region (template coordinates,
    bottom-up). Single source shared by the op generator and the mechanical
    facts — both must produce the same trim."""
    if count >= capacity:
        return []
    return list(range(start_row + capacity - 1, start_row + count - 1, -1))


def derive_mechanical_facts(ops: list, target_cfg: dict, blocks_cfg: list,
                            block_infos: list) -> dict:
    """执行机械事实栏 — 从「执行顺序保证」契约派生, 非自由文本.

    execution_plan.json.mechanical_facts 与 mapping.md「执行机械事实」栏的唯一
    来源: removes 与 add 区关系 / 锚点链依赖 / shift 结论. 数值字段从已生成的
    ops 与布局机械计算; 契约常量 (op_order_invariant / bottom_up /
    gap_checked) 由 contract test 背书 — gap_checked 是 plan 存在性不变量
    (TEMPLATE_ROW_GAP 缺陷先于 plan 产出, exit 3)."""
    base = target_cfg.get("base_last_row", 0)
    append_remove_rows = sorted({
        rn for b in blocks_cfg if not inplace_roles(b)
        for rn in b.get("remove_rows", [])})
    # Phase-1 append adds form the leading run of ops; any later add is an
    # inplace overflow clone (phase 5, after sets).
    leading = 0
    while leading < len(ops) and ops[leading]["command"] == "add":
        leading += 1
    add_after_rows: list[int] = []
    add_from_rows: list[int] = []
    spacer_adds = 0
    append_insert_rows: list[int] = []
    overflow_insert_rows: list[int] = []
    for i, op in enumerate(ops):
        if op.get("command") != "add":
            continue
        m = re.search(r"/row\[(\d+)\]$", op.get("after", ""))
        if m:
            add_after_rows.append(int(m.group(1)))
        m = re.search(r"/row\[(\d+)\]$", op.get("from", ""))
        if m:
            add_from_rows.append(int(m.group(1)))
        if op.get("after"):
            (append_insert_rows if i < leading else overflow_insert_rows).append(
                int(re.search(r"/row\[(\d+)\]$", op["after"]).group(1)) + 1)
        else:
            spacer_adds += 1
    append_insert_rows = sorted(set(append_insert_rows))
    overflow_insert_rows = sorted(set(overflow_insert_rows))
    shift = 0
    region = None
    trim_rows: list[int] = []
    for b in block_infos:
        ip = b.get("inplace")
        if ip:
            shift = b["count"] - ip["capacity"]
            region = f"{ip['start_row']}-{ip['region_end']}"
            trim_rows = inplace_trim_rows(b["count"], ip["capacity"],
                                          ip["start_row"])
            break
    all_within_base = all(rn <= base for rn in append_remove_rows)
    # E1 两形态 (2026-08-13): 无 inplace → append-only 序列; inplace 混合 →
    # append 块全部操作 → sets → inplace 结构 → inplace 值写. 契约字符串
    # 与 FILLSPEC「执行顺序保证」E1 精确同源 (test 断言).
    if region is None:
        op_order_invariant = "clear → add → remove → merge → fill"
    else:
        op_order_invariant = ("append 块全部操作 → sets → 终末 inplace 块结构操作 "
                              "(overflow 克隆 add → trim remove) → inplace 值操作")
    return {
        "op_order_invariant": op_order_invariant,
        "base_last_row": base,
        "add_zone": {
            "append_insert_rows": append_insert_rows,
            "overflow_insert_rows": overflow_insert_rows,
            "spacer_adds": spacer_adds,
            "conclusion": ("全部 add 插入 base_last_row 之下 (append 区) — 不推移 "
                           "base 及以上的模板坐标" if region is None else
                           "append 区 add 插在 base_last_row 之下; overflow 克隆插在 "
                           "区末端之后 — 均不推移区内的模板坐标"),
        },
        "removes": {
            "rows": append_remove_rows,
            "all_within_base": all_within_base,
            "bottom_up": True,
            "conclusion": ("与 add 区无交互 — 全部 ≤ base_last_row, 模板坐标在 add "
                           "区之外, 不被 add 推移" if all_within_base else
                           "与 add 区交互 — 编译器已以 REMOVE_TARGETS_APPEND_ZONE "
                           "拒绝, 不会产出本 plan"),
        },
        "trim": {
            "present": bool(trim_rows),
            "rows": trim_rows,
            "conclusion": "尾部裁剪 (编译器推导), 自底向上" if trim_rows else "无",
        },
        "shift": {
            "present": region is not None,
            "region": region,
            "value": shift,
            "readback_translated": region is not None,
            "conclusion": ("无 inplace 区 → 无行移位; readback 坐标 == 模板坐标"
                           if region is None else
                           f"inplace 区 {region} → 区下所有行 (append 区/sets) "
                           f"最终坐标 = 模板坐标 {'+' if shift >= 0 else ''}{shift} "
                           "(trim 为负 / overflow 为正); readback 已翻译为最终坐标"),
        },
        "anchor_chain": {
            "after_rows": sorted(set(add_after_rows)),
            "from_rows": sorted(set(add_from_rows)),
            "gap_checked": True,
        },
    }

def render_mapping(spec: dict, plan: dict, manifest: dict) -> str:
    target = spec["mapping"]["targets"][0]
    lines = [
        "# Mapping report",
        "",
        "> Generated deterministically by compile_fill.py from fill_spec.yaml — "
        "the spec is the only business-semantics source. This report is derived; "
        "edit the spec, never this file.",
        "",
        f"- Intent: {spec['task'].get('intent', '')}",
        f"- MOD: {spec['task'].get('selected_mod') or 'NONE'}",
        f"- Target: {spec['inputs'].get('target')} / {spec['inputs'].get('target_sheet')}",
        "",
        "## 追溯表",
        "| Artifact | Basis |",
        "|---|---|",
        "| fill_spec.yaml | this report + execution_plan.json |",
        "| source data | flattened CSVs from prepare_run.py (staged inputs) |",
        "",
    ]
    if spec.get("decisions"):
        lines.append("## Decisions")
        lines.extend(f"- {d}" for d in spec["decisions"])
        lines.append("")
    if spec.get("lineage"):
        lines.append("## Lineage")
        lines.extend(f"- `{l.get('source')}` ({l.get('role')}): {l.get('note', '')}"
                     for l in spec["lineage"])
        lines.append("")

    lines.append("## Layout")
    lines.append("| Kind | Row(s) | Template row | Mode |")
    lines.append("|---|---:|---:|---|")
    for b in plan["blocks"]:
        kind = b["kind"]
        rows = str(b["row"]) if kind != "data" else f"{b['data_start']}-{b['data_end']}"
        tpl = str(b.get("template_row") or "")
        mode = b.get("mode") or ""
        lines.append(f"| {kind} | {rows} | {tpl} | {mode} |")
    lines.append("")

    mf = plan.get("mechanical_facts")
    if mf:
        lines.append("## 执行机械事实 (derived from the execution-order contract)")
        lines.append("")
        lines.append(f"- op 顺序不变量: `{mf['op_order_invariant']}` — 值写入不穿插 append 区 add (防 duplicate_row)")
        lines.append(f"- base_last_row: {mf['base_last_row']}")
        az = mf["add_zone"]
        lines.append(f"- add 区: append 插入行 {az['append_insert_rows'] or '∅'}"
                     + (f" + spacer×{az['spacer_adds']} (执行时追加到 sheet 末尾)"
                        if az["spacer_adds"] else "")
                     + (f" + overflow 克隆 {az['overflow_insert_rows']}"
                        if az["overflow_insert_rows"] else "")
                     + f" — {az['conclusion']}")
        rm = mf["removes"]
        lines.append(f"- removes: {rm['rows'] or '∅'}"
                     + (f" — {rm['conclusion']}" if rm["rows"] else " — 无 remove_rows"))
        tr = mf["trim"]
        lines.append(f"- trim: {tr['conclusion']}" + (f" (行 {tr['rows']})" if tr["rows"] else ""))
        sh = mf["shift"]
        lines.append(f"- shift: {sh['conclusion']}"
                     + (f" (值 {sh['value']:+d})" if sh["present"] else ""))
        ac = mf["anchor_chain"]
        lines.append(f"- 锚点链: add after 引用行 {ac['after_rows'] or '∅'}, "
                     f"clone from 引用行 {ac['from_rows'] or '∅'}"
                     + (" — TEMPLATE_ROW_GAP 检查已过 (plan 存在即已通过)" if ac["gap_checked"] else ""))
        lines.append("")

    cov = plan["source_coverage"]
    if isinstance(cov, dict):
        cov = [cov]
    lines.append(f"## Source coverage — {plan.get('source_csv')}")
    for c in cov:
        lines.append(f"- `{c.get('source')}`: {c.get('matched')}/{c.get('total')} "
                     f"rows matched" + (f" — required NOT consumed: {c['required_unmatched']}"
                                        if c.get("required_unmatched") else ""))
    lines.append("")
    lines.append("| Source | Source row | Target row |")
    lines.append("|---|---:|---:|")
    for src, orig, trow in plan["row_map"]:
        lines.append(f"| {src} | {orig} | {trow} |")
    lines.append("")

    lines.append("## Written values")
    lines.append("| Target row | Column | Value |")
    lines.append("|---|---:|---|")
    for w in plan["writes"]:
        lines.append(f"| {w['row']} | {w['col']} | {w['value']} |")
    if plan.get("empties"):
        lines.append("")
        lines.append("## Explicit empty cells (clone-residue nulls)")
        lines.append("| Cell |")
        lines.append("|---|")
        for p in plan["empties"]:
            lines.append(f"| `{p}` |")
    if plan.get("sets"):
        lines.append("## Absolute writes (sets)")
        lines.append("| Cell | Value | Numberformat |")
        lines.append("|---|---:|---|")
        for s in plan["sets"]:
            lines.append(f"| `{s['path']}` | {s['value']!r} | {s.get('numberformat') or ''} |")
        lines.append("")
    if plan.get("group_boundaries"):
        lines.append("## Group merges (readback boundaries)")
        for gb in plan["group_boundaries"]:
            lines.append(f"- Column `{gb['col']}` rows {gb['region_start']}-{gb['region_end']}: "
                         f"{', '.join(gb['expected_merges']) or '(singletons only)'}")
        lines.append("")
    lines.append(f"## Structural (final) — rows {plan.get('expected_final_row_count')}"
                 f" (deltas: {plan.get('structural_deltas')})")
    lines.append("")
    if target.get("merges"):
        lines.append("")
        lines.append("## Merges")
        lines.append("| Column | Rows | Style |")
        lines.append("|---|---:|---|")
        for m in target["merges"]:
            lines.append(f"| {m.get('col')} | {m.get('rows')} | {m.get('style', 'label')} |")
    if target.get("group_merges"):
        lines.append("")
        lines.append("## Group merges (spec)")
        lines.append("| Column | Group by | Label | Style |")
        lines.append("|---|---|---|---|")
        for g in target["group_merges"]:
            lines.append(f"| {g.get('col')} | {g.get('group_by')} | {g.get('label')!r} | "
                         f"{g.get('style', 'label')} |")
    if target.get("formulas"):
        lines.append("")
        lines.append("## Formulas")
        f = target["formulas"]
        for col, tpl in (f.get("per_row") or {}).items():
            lines.append(f"- Per row `{col}`: `{tpl}`")
        for a in f.get("aggregates") or []:
            lines.append(f"- Aggregate `{a.get('col')}` rows {a.get('rows')}: `{a.get('formula')}`")
    lines.append("")
    if spec.get("gaps"):
        lines.append("## Data gaps")
        lines.extend(f"- {g}" for g in spec["gaps"])
        lines.append("")
    lines.append("## Readback")
    lines.append("| Cell | Expect | Kind |")
    lines.append("|---|---:|---|")
    for rb in plan["readback"]:
        lines.append(f"| {rb['path']} | {rb['expect'] or '(non-empty)'} | {rb['kind']} |")
    lines.append("")
    if plan.get("warnings"):
        lines.append("## ⚠️ Warnings (Compiler 自动处理)")
        lines.append("")
        for w in plan["warnings"]:
            lines.append(f"- `{w.get('code')}` {w.get('column')}: {w.get('message')}")
            lines.append(f"  - 建议: {w.get('corrective_action')}")
        lines.append("")
    return "\n".join(lines) + "\n"


def compile_spec(spec: dict, manifest: dict, workdir: Path,
                  spec_path: Path | None = None) -> dict:
    defects: list = []
    defects += validate_schema(spec, manifest)
    if defects:
        fail("SPEC_INVALID", f"{len(defects)} spec defect(s)", "Fix fill_spec.yaml", defects)

    inputs = spec["inputs"]
    manifest_target = manifest["target"]
    if inputs["target_sheet"] != manifest_target["sheet"]:
        fail("SPEC_TARGET_SHEET_MISMATCH",
             f"target_sheet {inputs['target_sheet']!r} != flattened target {manifest_target['sheet']!r}",
             "Flatten the sheet you intend to fill")

    fp = spec["fingerprints"]
    mfp = manifest["fingerprints"]
    if fp.get("source_structure") != mfp.get("source_structure") or \
            fp.get("target_structure") != mfp.get("target_structure"):
        fail("FILLSPEC_FINGERPRINT_MISMATCH",
             "fill_spec fingerprints do not match prepare_manifest — the spec "
             "was written against a different structure",
             "Structure changed: re-run prepare_run.py, read the fresh digests, "
             "and update the spec. Fingerprints not yet filled: copy them from "
             "prepare_manifest.json (fingerprints.source_structure / "
             "target_structure), or generate a probe scaffold with "
             "scripts/make_probe_spec.py --workdir <dir>")

    platform = inputs.get("platform") or ("pptx" if inputs["target"].lower().endswith(".pptx") else "xlsx")
    target_cfg = spec["mapping"]["targets"][0]
    if target_cfg.get("sheet") != inputs["target_sheet"]:
        fail("SPEC_TARGET_ENTRY", "mapping.targets[0].sheet must equal inputs.target_sheet",
             "Fix the target entry")

    manifest_flat = {e["name"]: e for e in manifest["flattened"]}
    target_meta = json.loads((workdir / manifest_target["meta"]).read_text(encoding="utf-8"))
    dims = target_meta.get("dimensions", {})
    num_cols = dims.get("cols", 0)

    # Blocks — one implicit block (target-level config) or explicit blocks[].
    blocks_cfg = resolve_blocks(target_cfg)

    # Block top-level key allowlist (ID-1): misplaced/unknown keys
    # (aggregates/per_row/group_aggregates at the block top level, typos) used
    # to pass through resolve_blocks and get silently dropped by _emit_block_ops
    # (Case 05 U4/E4) — now a compile-time BLOCK_KEY_STRUCTURE_INVALID.
    defects += validate_block_top_level_keys(blocks_cfg)
    if defects:
        fail("STATIC_VALIDATION_FAILED", f"{len(defects)} static validation defect(s)",
             "Fix the spec and re-run compile_fill.py", defects)

    # Inplace declaration invariants (fail before layout: a
    # malformed region must never reach coordinate arithmetic).
    ip_ctx = validate_inplace_declaration(blocks_cfg, dims, defects)
    if defects:
        fail("STATIC_VALIDATION_FAILED", f"{len(defects)} static validation defect(s)",
             "Fix the spec and re-run compile_fill.py", defects)

    # Per-block source matching + materialization (block rows configs).
    def match_block_sources(block_cfg: dict, label: str) -> list[dict]:
        rows_cfg = block_cfg.get("rows") or {}
        if rows_cfg.get("sources"):
            src_specs = [
                {"source": s.get("source"), "selectors": s.get("selectors") or []}
                for s in rows_cfg["sources"]
            ]
        else:
            src_specs = [{"source": rows_cfg.get("source"),
                          "selectors": rows_cfg.get("selectors") or []}]
        if any(not s["source"] for s in src_specs):
            fail("SPEC_SOURCE_CSV", f"{label}: every rows.sources entry needs a flattened source name",
                 "Reference flattened entry names from the manifest")
        out = []
        for src_spec in src_specs:
            src_name = src_spec["source"]
            src_entry = manifest_flat.get(src_name)
            if src_entry is None:
                fail("SPEC_SOURCE_CSV", f"{label}: rows source {src_name!r} not among flattened sources",
                     "Reference the flattened entry name from the manifest (e.g. the "
                     "name field of a flattened sheet, not the csv filename)")
            src_rows = load_csv_rows(workdir / src_entry["csv"])
            # Selectors match SOURCE rows — validate their column letters
            # against the SOURCE's own width, not the target's (the MXP case:
            # 27-col source into a 6-col target made L selectors fail).
            src_width = max((len(r[0]) for r in src_rows), default=num_cols)
            try:
                matched = apply_selectors(src_rows, src_spec, src_width)
            except ValueError as e:
                fail("SELECTOR_INVALID", f"{label}/{src_name}: {e}", "Fix the row selectors")
            if not matched:
                fail("NO_MATCHED_ROWS", f"{label}: selectors matched zero rows in {src_name}",
                     "Fix selectors or check the source flatten")
            # issue 02 / Case 08 U1: 展平 CSV 首行（表头）是候选数据行 — rows
            # 无 selector（或 selector 未排除）且首行是表头文本行时, 表头会被
            # 映射进数据区 (失败语义不变, 记 warnings)。corrective_action 指向
            # pattern/not_pattern 排除表头行。
            if src_rows and is_header_text_row(src_rows[0][0]):
                first_orig = src_rows[0][1]
                if any(o == first_orig for _, o in matched):
                    first_label = next((str(c) for c in src_rows[0][0]
                                        if str(c).strip()), "")
                    warnings.append({
                        "code": "HEADER_ROW_CONSIDERED_DATA",
                        "source": src_name,
                        "message": f"{label}: source {src_name!r} 的展平 CSV 首行"
                                   f"（表头文本 {first_label!r}）被当作候选数据行 — "
                                   "rows 无 selector（或 selector 未排除首行）时表头会被"
                                   "映射进数据区",
                        "corrective_action": "在 rows.selectors 加 pattern/not_pattern "
                                             "排除表头行 (如 `column A pattern 业务类别*` "
                                             "或 `column A not_value 类别`)",
                    })
            out.append({"name": src_name, "csv": src_entry["csv"], "rows": src_rows,
                        "matched": matched})
        return out

    lookups = build_lookup_tables(spec["mapping"], target_cfg, workdir)
    transforms = build_transforms(spec["mapping"], target_cfg)
    warnings: list = []
    lookup_stats: dict = {}

    # Materialize per block and attach block data (rows, source, block index).
    block_infos: list[dict] = []
    data_rows: list[dict] = []
    matched_all: list[dict] = []
    for bi, bcfg in enumerate(blocks_cfg):
        label = f"block[{bi}]"
        matched = match_block_sources(bcfg, label)
        materialized = materialize_values(
            [r for m in matched for r in m["matched"]], bcfg, num_cols,
            lookups, transforms, defects, lookup_stats)
        drs = []
        # map materialized rows back to their source csv
        src_of = []
        for m in matched:
            src_of.extend([m["csv"]] * len(m["matched"]))
        for dr, src in zip(materialized, src_of):
            dr["src"] = src
            drs.append(dr)
        if not defects:
            apply_precision_policy(bcfg, drs, defects, warnings,
                                   col_widths=target_meta.get("column_width") or {},
                                   col_numfmt=target_meta.get("column_numfmt") or {})
        if defects:
            fail("MATERIALIZE_DEFECTS", f"{len(defects)} value materialization defect(s)",
                 "Fix the column mappings/lookups/transforms", defects)
        data_rows.extend(drs)
        matched_all.extend(matched)
        block_infos.append({"cfg": bcfg, "rows": drs, "count": len(drs),
                            "matched": matched, "label": label})

    note_lookup_all_missing(lookup_stats, warnings)

    for b in block_infos:
        b["cfg"]["_rows"] = b["rows"]

    roles, data_starts = compute_layout(blocks_cfg, platform, target_cfg,
                                        defects, dims.get("rows"))
    for b, start in zip(block_infos, data_starts):
        b["data_start"] = start
    for b in block_infos:
        ip_role = next((r for r in b["cfg"].get("clone_roles", [])
                        if r.get("mode") == "inplace"), None)
        if ip_role:
            b["inplace"] = {
                "start_row": ip_role.get("start_row"),
                "capacity": ip_role.get("capacity"),
                "template_row": ip_role.get("template_row"),
                "region_end": ip_role.get("start_row") + ip_role.get("capacity") - 1,
            }
            # 锚点样式继承 (Case 010 盲区): inplace 组锚点可能落在旧合并区
            # 非锚点格 (无字体样式) — 采集占位区内同列既有锚点样式, 供
            # _emit_block_ops 合并进 merge op (spec 显式 styles 优先)。
            region = (b["inplace"]["start_row"], b["inplace"]["region_end"])
            merge_cols = {g.get("col") for g in b["cfg"].get("group_merges", [])}
            merge_cols |= {m.get("col") for m in b["cfg"].get("merges", [])}
            b["inherited_styles"] = {
                col: inherited_anchor_style(target_meta, col, *region)
                for col in merge_cols if col
            }
    validate_inplace_geometry(blocks_cfg, ip_ctx, roles,
                              target_cfg.get("base_last_row", 0), defects)

    style_defaults = {
        "anchor": dict(STYLE_DEFAULTS["anchor"], **(target_cfg.get("styles") or {}).get("anchor", {})),
        "label": dict(STYLE_DEFAULTS["label"], **(target_cfg.get("styles") or {}).get("label", {})),
    }

    if platform == "pptx":
        if any(inplace_roles(b) for b in blocks_cfg):
            defects.append({"code": "PPTX_CAPABILITY_NOT_ROLLED_OUT",
                            "message": "mode: inplace is meaningless for pptx — the "
                                       "pptx model is already pre-built-row fills",
                            "corrective_action": "Drop mode: inplace from the pptx spec"})
        for b in block_infos:
            cfg = b["cfg"]
            label = b["label"]
            # fail-closed (issue 06): every declaration the pptx lowering does
            # NOT implement must be rejected here — build_ops_pptx only lowers
            # column value fills + DOM-path sets, everything else was silently
            # dropped (compile passed, no ops generated).
            if cfg.get("group_merges"):
                defects.append({"code": "PPTX_CAPABILITY_NOT_ROLLED_OUT",
                                "message": f"{label}: group_merges lowering for pptx "
                                           "(vMerge/rowspan mechanics) is staged: verify "
                                           "against the spike fixture before rollout",
                                "corrective_action": "Use xlsx for group_merges, or wait "
                                                      "for the pptx lowering rollout"})
            if cfg.get("formulas", {}).get("group_aggregates"):
                defects.append({"code": "PPTX_CAPABILITY_NOT_ROLLED_OUT",
                                "message": f"{label}: group_aggregates lowering for "
                                           "pptx is staged — formula cells are xlsx-only; "
                                           "verify against the spike fixture before rollout",
                                "corrective_action": "Use xlsx for group_aggregates, or "
                                                     "wait for the pptx lowering rollout"})
            if cfg.get("formulas", {}).get("per_row"):
                defects.append({"code": "PPTX_CAPABILITY_NOT_ROLLED_OUT",
                                "message": f"{label}: formulas.per_row lowering for pptx "
                                           "is not rolled out — pptx cells hold text, "
                                           "not formulas",
                                "corrective_action": "Use xlsx for per_row formulas, or "
                                                     "precompute the derived values into "
                                                     "column mappings"})
            if cfg.get("formulas", {}).get("aggregates"):
                defects.append({"code": "PPTX_CAPABILITY_NOT_ROLLED_OUT",
                                "message": f"{label}: formulas.aggregates lowering for "
                                           "pptx is not rolled out — formula cells are "
                                           "xlsx-only",
                                "corrective_action": "Use xlsx for aggregates, or "
                                                     "precompute the aggregate values"})
            if cfg.get("merges"):
                defects.append({"code": "PPTX_CAPABILITY_NOT_ROLLED_OUT",
                                "message": f"{label}: merges lowering for pptx is not "
                                           "rolled out — pptx merge mechanics differ "
                                           "(vMerge/rowspan spike pending)",
                                "corrective_action": "Use xlsx for merges, or drop the "
                                                     "declaration"})
            if cfg.get("nulls"):
                defects.append({"code": "PPTX_CAPABILITY_NOT_ROLLED_OUT",
                                "message": f"{label}: nulls is meaningless for pptx — "
                                           "there is no clone residue to clear (rows are "
                                           "pre-built, nothing is cloned)",
                                "corrective_action": "Drop nulls from the pptx spec"})
            if cfg.get("remove_rows"):
                defects.append({"code": "PPTX_CAPABILITY_NOT_ROLLED_OUT",
                                "message": f"{label}: remove_rows lowering for pptx is "
                                           "not rolled out — pptx has no structural row "
                                           "ops",
                                "corrective_action": "Drop remove_rows from the pptx spec"})
            if any(col.get("props") for col in cfg.get("columns", [])):
                defects.append({"code": "PPTX_CAPABILITY_NOT_ROLLED_OUT",
                                "message": f"{label}: columns[].props (numberformat) is "
                                           "not applied on pptx — pptx cells are text "
                                           "and carry no number format",
                                "corrective_action": "Drop props from pptx column "
                                                     "mappings"})
        if defects:
            fail("STATIC_VALIDATION_FAILED", f"{len(defects)} static validation defect(s)",
                 "Fix the spec and re-run compile_fill.py", defects)
        ops, readback, written = build_ops_pptx(target_cfg, roles, data_rows,
                                                num_cols, defects)
        group_boundaries: list = []
        trim_count = 0
        set_records = _emit_sets(target_cfg, None, None, None, ops,
                                 lambda p, k, v: _pptx_register(p, k, v, written,
                                                               readback, defects),
                                 defects, dims.get("rows", 0), num_cols)
    else:
        anchors = {a["anchor"] for a in target_meta.get("merge_anchors", [])}
        anchor_rows = {int(re.search(r"\d+$", a).group()) for a in anchors}
        whole_run_gated = False
        for b in block_infos:
            cfg = b["cfg"]
            validate_nulls_rows(cfg, defects)  # 先于任何 parse_rel_rows 调用
            if any(d.get("code") == "NULLS_ROWS_INVALID" for d in defects):
                fail("STATIC_VALIDATION_FAILED",
                     f"{len(defects)} static validation defect(s)",
                     "Fix the nulls rows specs and re-run compile_fill.py", defects)
            template_row = next((r.get("template_row") for r in cfg.get("clone_roles", [])
                                 if r.get("role") == "data"), None)
            if template_row is None:
                fail("SPEC_DATA_CLONE", f"{b['label']}: xlsx data blocks need a "
                     "data clone_role with template_row",
                     "Add {role: data, template_row: N} to the block's clone_roles")
            if template_row in anchor_rows:
                defects.append({"code": "CLONE_SOURCE_IS_ANCHOR",
                                "template_row": template_row,
                                "message": f"{b['label']}: template row {template_row} is a "
                                           "merge anchor; cloning it carries anchor formulas "
                                           "into non-anchor cells",
                                "corrective_action": "Pick a non-anchor data row with the same format"})
            null_specs = {x["col"]: x.get("rows") for x in cfg.get("nulls", [])}
            per_row = cfg.get("formulas", {}).get("per_row", {})
            gm_cols = {g.get("col") for g in cfg.get("group_merges", [])}
            validate_clone_residue(cfg, template_row,
                                   workdir / manifest_target["csv"], num_cols,
                                   cfg.get("columns", []), null_specs, per_row,
                                   b["count"], defects, gm_cols)
            if b.get("inplace"):
                # Double residue baseline: retained rows checked against
                # each row's OWN values; overflow clone rows against template_row
                # (covered by validate_clone_residue above).
                validate_placeholder_residue(
                    cfg, b["inplace"]["start_row"], b["inplace"]["capacity"],
                    b["count"], workdir / manifest_target["csv"], num_cols,
                    b["rows"], defects)
            validate_formula_references(cfg.get("formulas", {}), b["count"], defects)
            ga_entries, ga_whole_run = split_group_aggregates(
                cfg.get("formulas", {}).get("group_aggregates"), defects, b["label"])
            if ga_whole_run and not whole_run_gated:
                whole_run_gated = True
                defects.append({"code": "CAPABILITY_NOT_ROLLED_OUT",
                                "message": "group_aggregates.whole_run (跨块总计) 落点"
                                           "语义 (末块尾部 vs 独立行) 需一次 spike 锁定 — "
                                           "spike 前声明被结构化拒绝",
                                "corrective_action": "用逐块块级 aggregates (每组合一块) "
                                                     "表达, 或等 whole_run spike 结论"
                                                     "落地后再声明"})
            for col in (set(per_row) | set(null_specs)
                        | {m.get("col") for m in cfg.get("merges", [])}
                        | gm_cols
                        | {g.get("col") for g in ga_entries}
                        | {g.get("group_by") for g in ga_entries if g.get("group_by")}
                        | {g.get("group_by") for g in cfg.get("group_merges", []) if g.get("group_by")}):
                if col and col_letter_to_idx(col) >= num_cols:
                    defects.append({"code": "COL_OUT_OF_DIGEST", "col": col,
                                    "message": f"{b['label']}: column {col} beyond digest width {num_cols}",
                                    "corrective_action": "Check the target structure"})
        if target_cfg.get("base_last_row", 0) > dims.get("rows", 0):
            defects.append({"code": "BASE_ROW_OUT_OF_BOUNDS",
                            "message": f"base_last_row {target_cfg['base_last_row']} > digest rows {dims.get('rows')}",
                            "corrective_action": "Use the digest's row count"})
        validate_append_remove_zone(blocks_cfg,
                                    target_cfg.get("base_last_row", 0), defects)
        row_gaps = sorted(set(target_meta.get("row_gaps") or []))
        if row_gaps:
            for role in roles:
                if role.get("mode") in ("inplace", "overflow_clone"):
                    continue
                r = role.get("row")
                trow = role.get("template_row")
                # 锚点链: data/title/header 克隆 add after /row[r-1]; spacer 无 after.
                anchor = (r - 1) if (isinstance(r, int) and role.get("kind") != "spacer") else None
                for a, kind in ((anchor, "add anchor (after)"), (trow, "clone source (from)")):
                    if a in row_gaps:
                        defects.append({
                            "code": "TEMPLATE_ROW_GAP",
                            "row": a,
                            "kind": kind,
                            "message": f"role {role.get('kind','?')} at row {r}: "
                                       f"{kind} row {a} is a row-number gap in the target "
                                       f"sheet (row elements: missing {row_gaps}) — officecli "
                                       f"`add after/from /row[{a}]` would fail at runtime",
                            "corrective_action": "Materialize the missing row "
                                                 "elements (scripts/repair_row_gaps.py "
                                                 "--workdir <dir> — fingerprints "
                                                 "auto re-synced), then update the "
                                                 "spec target_structure fingerprint "
                                                 "(or --patch-spec) and recompile",
                        })
        if defects:
            fail("STATIC_VALIDATION_FAILED", f"{len(defects)} static validation defect(s)",
                 "Fix the spec and re-run compile_fill.py", defects)

        ops, readback, written, group_boundaries, trim_count, set_records = \
            build_ops_xlsx(target_cfg, block_infos, roles, data_rows, num_cols,
                           style_defaults, defects, sheet_rows=dims.get("rows", 0))
        if defects:
            fail("STATIC_VALIDATION_FAILED", f"{len(defects)} static validation defect(s)",
                 "Fix the spec and re-run compile_fill.py", defects)

    if not ops:
        fail("PLAN_EMPTY", "compilation produced zero operations",
             "Check the target mapping")

    # Coverage — per (block, source) + combined row map (final coordinates).
    positions = []
    for b in block_infos:
        if platform == "xlsx":
            for i in range(b["count"]):
                positions.append(final_row_of(block_infos, b["data_start"] + i))
        else:
            positions.extend(roles[i]["tr"] for i in range(len(data_rows)))
    row_map = [(d["src"], d["orig"], positions[i]) for i, d in enumerate(data_rows)]

    # Global exactly-once invariant (issue 05): every (source, original_row)
    # enters at most one target row. Blocks (or rows.sources entries) whose
    # selectors overlap silently double quantities/amounts/aggregates while
    # coverage (an existence lower bound, not an upper bound) still passes —
    # reject fail-closed (there is no reuse syntax; FILLSPEC Q17).
    consumed: dict[tuple[str, int], list[int]] = {}
    for src, orig, trow in row_map:
        consumed.setdefault((src, orig), []).append(trow)
    doubled = {k: v for k, v in consumed.items() if len(v) > 1}
    if doubled:
        desc = "; ".join(
            f"{src} row {orig} → target rows {trows}"
            for (src, orig), trows in sorted(doubled.items()))
        fail("SOURCE_ROW_CONSUMED_TWICE",
             f"source rows consumed more than once: {desc}",
             "Every (source, original_row) must enter exactly one target row. "
             "Narrow the overlapping selectors across blocks (or rows.sources "
             "entries in one block) and drop duplicate required_coverage "
             "declarations — there is no reuse syntax yet")

    cov_entries = []
    for bi, b in enumerate(block_infos):
        for m in b["matched"]:
            matched_origs = [d["orig"] for d in b["rows"] if d["src"] == m["csv"]]
            unmatched = []
            for rc in spec["validation"].get("required_coverage", []):
                ref = rc.get("source", m["name"])
                if ref != m["name"] and ref != m["csv"]:
                    continue
                for rn in rc.get("rows", []):
                    if rn not in matched_origs:
                        unmatched.append(rn)
            if unmatched:
                fail("REQUIRED_COVERAGE_UNMATCHED",
                     f"block[{bi}]/{m['name']}: required source rows not consumed: {unmatched}",
                     "Fix selectors or record the reason in gaps")
            cov_entries.append({
                "block": bi,
                "source": m["csv"],
                "name": m["name"],
                "total": len(m["rows"]),
                "matched": len(m["matched"]),
                "required_unmatched": unmatched,
            })

    # Extra required empties + key outputs
    for cell in spec["validation"].get("required_empty", []):
        path = cell if cell.startswith("/") else f"/{target_cfg['sheet']}/{cell}"
        readback.append({"path": path, "expect": "EMPTY", "kind": "empty"})
    key_outputs = []
    for cell in spec["validation"].get("key_outputs", []):
        path = cell if cell.startswith("/") else f"/{target_cfg['sheet']}/{cell}"
        if platform == "xlsx":
            # Spec coordinates are template-era; written/readback are final.
            m = re.search(r"/([A-Z]+)(\d+)$", path)
            if m:
                fr = spec_final_row_of(block_infos, int(m.group(2)))
                path = f"{path[:m.start(2)]}{fr}"
        if path in written:
            key_outputs.append({"path": path, "kind": written[path]})
        else:
            defects.append({"code": "KEY_OUTPUT_UNWRITTEN", "path": path,
                            "message": f"key_output {path} is never written by this plan",
                            "corrective_action": "Reference a cell that the mapping fills "
                                                 "(or a formula cell); row numbers can be "
                                                 "taken straight from this plan's "
                                                 "blocks[].data_start / merge & aggregate "
                                                 "anchor cells, or from combination_patterns "
                                                 "skeleton key_output slots — don't "
                                                 "hand-derive template rows"})
    if defects:
        fail("STATIC_VALIDATION_FAILED", f"{len(defects)} static validation defect(s)",
             "Fix the spec and re-run compile_fill.py", defects)

    blocks = []
    for role in roles:
        if role["kind"] == "data":
            pos = role.get("row") or role.get("tr")
            if platform == "xlsx":
                pos = final_row_of(block_infos, pos)
            if not blocks or blocks[-1]["kind"] != "data":
                entry = {"kind": "data", "sheet": target_cfg["sheet"],
                         "template_row": role.get("template_row"),
                         "data_start": pos, "data_end": pos}
                if role.get("mode"):
                    entry["mode"] = role["mode"]
                blocks.append(entry)
            else:
                blocks[-1]["data_end"] = pos
        else:
            blocks.append({"kind": role["kind"], "sheet": target_cfg["sheet"],
                           "row": role.get("row"), "template_row": role.get("template_row")})

    add_count = sum(1 for op in ops if op.get("command") == "add")
    remove_count = sum(1 for op in ops if op.get("command") == "remove")
    expected_final_row_count = dims.get("rows", 0) + add_count - remove_count
    inplace_overflow = 0
    for b in block_infos:
        ip = b.get("inplace")
        if ip:
            inplace_overflow = max(0, b["count"] - ip["capacity"])
    if platform == "xlsx":
        max_col = 1
        for op in ops:
            m = re.search(r"/([A-Z]+)\d+$", op.get("path", ""))
            if m:
                max_col = max(max_col, col_letter_to_idx(m.group(1)) + 1)
        render_region = (f"/{target_cfg['sheet']}/A1:"
                         f"{col_idx_to_letter(max_col - 1)}{expected_final_row_count}")
    else:
        render_region = f"/{target_cfg['sheet']}"

    plan = {
        "schema_version": "2.5",  # v3 保留给 plugin-化世代
        "fill_spec": None,
        "fill_spec_sha256": sha256_text(spec_path.read_bytes().decode("utf-8"))
        if spec_path is not None else None,
        "platform": platform,
        "target": inputs["target"],
        "target_sheet": inputs["target_sheet"],
        "input_hashes": bind_input_hashes(workdir, inputs),
        "fingerprints": {"source_structure": mfp.get("source_structure"),
                         "target_structure": mfp.get("target_structure")},
        "blocks": blocks,
        "operations": ops,
        "operation_count": len(ops),
        "readback": readback,
        "source_csv": ", ".join(m["csv"] for m in matched_all),
        "source_coverage": cov_entries,
        "row_map": row_map,
        "warnings": warnings,
        "writes": [
            {"row": trow, "col": col, "value": val}
            for drow, (_, _, trow) in zip(data_rows, row_map)
            for col, val in drow["values"].items()
        ],
        "empties": [rb["path"] for rb in readback if rb["kind"] == "empty"],
        "key_outputs": key_outputs,
        "expected_final_row_count": expected_final_row_count,
        "structural_deltas": {"adds": add_count, "removes": remove_count,
                              "inplace_trim": trim_count,
                              "inplace_overflow": inplace_overflow},
        "group_boundaries": group_boundaries,
        "sets": set_records,
        "mechanical_facts": derive_mechanical_facts(
            ops, target_cfg, blocks_cfg, block_infos),
        "render_qa": {"region": render_region},
    }
    return plan


# ── Probe (compile-only verification) ─────────────────────────────────

def probe_spec(spec: dict, manifest: dict, workdir: Path) -> dict:
    """Compile-only probe: does the compiler ACCEPT this spec?

    Runs the exact same pipeline as a real compile (so the answer is always
    the same), but writes nothing: no execution_plan.json, no mapping.md,
    no run_timing entry. Result:
      accepted=True  → {"accepted", "operations", "warnings"}
      accepted=False → {"accepted", "exit_code", "code", "defects", "message"}
    """
    import io as _io
    buf = _io.StringIO()
    old_err = sys.stderr
    sys.stderr = buf
    try:
        plan = compile_spec(spec, manifest, workdir)
    except SystemExit as e:
        payload = None
        raw = buf.getvalue()
        try:
            payload = json.loads(raw)
        except ValueError:
            pass
        return {"accepted": False, "exit_code": e.code,
                "code": (payload or {}).get("code"),
                "defects": (payload or {}).get("defects", []),
                "message": (payload or {}).get("message", raw.strip())}
    finally:
        sys.stderr = old_err
    return {"accepted": True, "exit_code": 0,
            "operations": len(plan["operations"]),
            "warnings": plan["warnings"],
            "defects": []}


def run_probe_cases(workdir: Path) -> list[dict]:
    """Execute the contract probe matrix (PROBE_CASES) on a fresh synthetic
    workdir and report what the compiler itself accepts/rejects.

    The same matrix drives `compile_fill.py --capabilities`, the contract
    tests (tests/test_optimization.py) and — via the doc-coverage guards —
    FILLSPEC.md: the three can never drift apart because they share one list.
    """
    import _probe_fixtures as pf
    out = []
    for case in pf.PROBE_CASES:
        wd = (case.get("workdir_factory") or pf.make_probe_workdir)(workdir)  # fresh per case
        spec = case["build"](pf.base_probe_spec(), wd)
        spec["fingerprints"] = {
            "source_structure": wd["manifest"]["fingerprints"]["source_structure"],
            "target_structure": wd["manifest"]["fingerprints"]["target_structure"],
        }
        r = probe_spec(spec, wd["manifest"], workdir)
        codes = [d.get("code") for d in r.get("defects", [])]
        warn_codes = sorted({w.get("code") for w in r.get("warnings", [])})
        out.append({
            "id": case["id"],
            "accepted": r["accepted"],
            "code": codes[0] if codes else r.get("code"),
            "warnings": warn_codes,
            "expect": case["expect"],
        })
    return out


def main() -> None:
    ensure_utf8_stdio()
    parser = argparse.ArgumentParser(description="Compiler: FillSpec → execution plan + mapping")
    parser.add_argument("--spec", type=Path, help="fill_spec.yaml (required unless --capabilities)")
    parser.add_argument("--workdir", type=Path, help="workdir (required unless --capabilities)")
    parser.add_argument("--probe", action="store_true",
                        help="compile-only probe: accepted? no plan/mapping/timing written — "
                             "the authoritative answer to 'will the compiler accept this spec?'")
    parser.add_argument("--capabilities", action="store_true",
                        help="run the contract probe matrix and report acceptance per "
                             "combination (the FILLSPEC「组合行为契约」/「能力映射表」claims, "
                             "as the compiler itself sees them)")
    args = parser.parse_args()

    if args.capabilities:
        import tempfile as _tmp
        with _tmp.TemporaryDirectory() as td:
            results = run_probe_cases(Path(td))
        print(json.dumps({
            "status": "SUCCESS", "code": "CAPABILITIES_REPORTED",
            "schema_version": "2.5",
            "note": "acceptance as compile_fill.py itself judges it — "
                    "contract tests assert this matrix matches FILLSPEC.md",
            "patterns": "assets/combination_patterns.yaml (copyable fragments "
                        "for the recommended combinations)",
            "cases": results,
        }, ensure_ascii=False, indent=2))
        sys.exit(0)

    if args.spec is None or args.workdir is None:
        parser.error("--spec and --workdir are required (or use --capabilities)")

    spec = load_spec(args.spec)
    manifest = load_manifest(args.workdir)
    if args.probe:
        result = probe_spec(spec, manifest, args.workdir)
        print(json.dumps({"status": "PROBED", **result}, ensure_ascii=False, indent=2))
        sys.exit(0 if result["accepted"] else 3)

    plan = compile_spec(spec, manifest, args.workdir)
    plan["fill_spec"] = str(args.spec)
    plan["fill_spec_sha256"] = sha256_text(args.spec.read_bytes().decode("utf-8"))

    plan_path = args.workdir / PLAN_NAME
    plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    mapping_path = args.workdir / MAPPING_NAME
    mapping_path.write_text(render_mapping(spec, plan, manifest), encoding="utf-8")

    record_timing(args.workdir, "compile")
    print(json.dumps({
        "status": "SUCCESS", "code": "PLAN_GENERATED",
        "operations": len(plan["operations"]),
        "readback": len(plan["readback"]),
        "matched_rows": sum(c["matched"] for c in plan["source_coverage"]),
        "warnings": plan["warnings"],
        "blocks": plan["blocks"],
        "plan": str(plan_path),
        "mapping": str(mapping_path),
    }, ensure_ascii=False, indent=2))
    sys.exit(0)


if __name__ == "__main__":
    main()
