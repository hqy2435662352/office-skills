"""scripts/_probe_fixtures.py — synthetic workdir fixtures + contract probe cases.

Single source of truth for the compile-time contract matrix:
- `make_probe_workdir` / `make_probe_inplace_workdir`: synthetic workdir
  (source CSV, target meta/flat, manifest, lookup index) — shared by
  compile_fill.py --probe/--capabilities and the contract tests.
- `BASE_SPEC`: the minimal valid spec skeleton the probes mutate.
- `PROBE_CASES`: one entry per FILLSPEC「组合行为契约」/「能力映射表」claim —
  the same matrix that `compile_fill.py --capabilities` executes at runtime
  and that tests/test_optimization.py asserts against. Doc, tests and the
  runtime capability report stay in lockstep because they share this list.
"""

from __future__ import annotations

import csv
import json

from prepare_run import facts_sha256, structure_facts


def make_probe_workdir(tmp, n_source_rows: int = 3, n_cols: int = 10,
                       src_rows: list | None = None) -> dict:
    """Synthetic workdir: source CSV, target meta/csv, manifest, lookup."""
    if src_rows is None:
        src_rows = [
            ["家用", "12K", "Z001", "F-1", "C-1", "1", "2", "3"],
            ["家用", "18K", "Z002", "F-2", "C-2", "4", "5", "6"],
            ["商用", "24K", "Z003", "F-3", "C-3", "7", "8", "9"],
        ]
    src_rows = src_rows[:n_source_rows]
    with open(tmp / "source_maoli_flat.csv", "w", newline="", encoding="utf-8-sig") as f:
        for i, row in enumerate(src_rows):
            csv.writer(f).writerow(row + [101 + i])

    target_meta = {
        "sheet": "S",
        "dimensions": {"rows": 20, "cols": n_cols, "data_rows": 2},
        "header_band": {"header_rows": [2], "data_start_row": 3},
        "merged_ranges": ["A5:A6"],
        "merge_anchors": [{"range": "A5:A6", "anchor": "A5", "formula": ""}],
        "blocks": [],
        "columns": [{"col": "A", "nonempty": 2}, {"col": "D", "nonempty": 1}],
        "formulas": {"D3": "B3-C3"},
        "column_numfmt": {"D": "0.00"},
        # 样式粒度决策事实 (issue 03): 裸行占位形态 — 行 5-20 无样式.
        "style_granularity": {
            "placeholder": {"start": 5, "end": 20, "styled": False, "sample": None},
            "template_rows": {"header": {"row": 2, "styled": True, "sample": "A2"}},
        },
    }
    with open(tmp / "target_meta.json", "w", encoding="utf-8") as f:
        json.dump(target_meta, f, ensure_ascii=False)

    with open(tmp / "target_flat.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        for cells, orig in (
            (["标题"], 1),
            (["类别", "规格", "型号"], 2),
            (["家用", "12K", "Z001", "F-1"], 3),
            (["家用", "18K", "Z002", "F-2"], 4),
        ):
            w.writerow(cells + [""] * (n_cols - len(cells)) + [orig])

    facts = [structure_facts(target_meta)]
    manifest = {
        "schema_version": 2,
        "files": [
            {"staged": "source_maoli.xlsx", "source": "x", "sha256": "a"},
            {"staged": "target.xlsx", "source": "x", "sha256": "b"},
        ],
        "flattened": [
            {"file": "source_maoli.xlsx", "sheet": "毛利表", "name": "source_maoli",
             "csv": "source_maoli_flat.csv", "meta": "m.json",
             "digest": "d.md", "candidates": "c.yaml"},
            {"file": "target.xlsx", "sheet": "S", "name": "target",
             "csv": "target_flat.csv", "meta": "target_meta.json",
             "digest": "d.md", "candidates": "c.yaml"},
        ],
        "target": {"file": "target.xlsx", "sheet": "S", "name": "target",
                   "csv": "target_flat.csv", "meta": "target_meta.json",
                   "digest": "d.md", "candidates": "c.yaml"},
        "fingerprints": {
            "source_structure": facts_sha256(facts),
            "target_structure": facts_sha256(facts),
        },
        "style_granularity": {"target": {
            "placeholder": {"start": 5, "end": 20, "styled": False, "sample": None},
            "template_rows": {"header": {"row": 2, "styled": True, "sample": "A2"}},
        }},
    }
    with open(tmp / "prepare_manifest.json", "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False)

    lookups = {"Z001": {"compressor": "C-1", "copper": "P-1"},
               "Z002": {"compressor": "C-2", "copper": "P-2"}}
    with open(tmp / "inheritance.json", "w", encoding="utf-8") as f:
        json.dump(lookups, f, ensure_ascii=False)
    return {"manifest": manifest, "lookups": lookups}


def make_probe_inplace_workdir(tmp, n_source_rows: int = 3) -> dict:
    """Target with a 4-row Placeholder Region (rows 7-10), Total row 11,
    notes rows 12-14 — the MXP quotation template shape. Region rows carry
    values in A/B/C/D/F (residue baseline per retained row)."""
    src_rows = [
        ["家用", "12K", "Z001", "F-1", "C-1", "1", "2", "3"],
        ["家用", "18K", "Z002", "F-2", "C-2", "4", "5", "6"],
        ["商用", "24K", "Z003", "F-3", "C-3", "7", "8", "9"],
        ["商用", "28K", "Z004", "F-4", "C-4", "10", "11", "12"],
        ["商用", "32K", "Z005", "F-5", "C-5", "13", "14", "15"],
    ][:n_source_rows]
    with open(tmp / "source_maoli_flat.csv", "w", newline="", encoding="utf-8-sig") as f:
        for i, row in enumerate(src_rows):
            csv.writer(f).writerow(row + [101 + i])

    target_meta = {
        "sheet": "S",
        "dimensions": {"rows": 14, "cols": 6, "data_rows": 4},
        "header_band": {"header_rows": [2], "data_start_row": 7},
        "merged_ranges": ["A7:A10"],
        "merge_anchors": [{"range": "A7:A10", "anchor": "A7", "formula": ""}],
        "blocks": [],
        "columns": [{"col": "A", "nonempty": 4}, {"col": "B", "nonempty": 4}],
        "formulas": {},
        "column_numfmt": {},
        # 样式粒度决策事实 (issue 03): 带样式占位形态 — 行 7-10 占位区带样式
        # (MXP 报价单形态, 占位行携带值 + 合并 A7:A10).
        "style_granularity": {
            "placeholder": {"start": 7, "end": 10, "styled": True, "sample": "A7"},
            "template_rows": {"header": {"row": 2, "styled": True, "sample": "A2"}},
        },
    }
    with open(tmp / "target_meta.json", "w", encoding="utf-8") as f:
        json.dump(target_meta, f, ensure_ascii=False)

    with open(tmp / "target_flat.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["公司标题", "", "", "", "", "", "1"])
        w.writerow(["Type", "Model", "C&H capacity", "Pipe", "", "Panel", "2"])
        w.writerow(["", "", "", "", "", "", "3"])
        w.writerow(["To Messrs: ATLAS", "", "", "", "", "", "4"])
        w.writerow(["", "", "", "", "", "", "5"])
        w.writerow(["", "", "", "", "", "", "6"])
        for i, r in enumerate(range(7, 11), start=1):
            w.writerow(["Xpro placeholder", f"M{i}", f"{i}000Btu", "/", "",
                        "Panel looking", str(r)])
        w.writerow(["Total", "", "", "", "", "", "11"])
        w.writerow(["* ship to Egypt", "", "", "", "", "", "12"])
        w.writerow(["* delivery note", "", "", "", "", "", "13"])
        w.writerow(["* validity note", "", "", "", "", "", "14"])

    facts = [structure_facts(target_meta)]
    manifest = {
        "schema_version": 2,
        "files": [
            {"staged": "source_maoli.xlsx", "source": "x", "sha256": "a"},
            {"staged": "target.xlsx", "source": "x", "sha256": "b"},
        ],
        "flattened": [
            {"file": "source_maoli.xlsx", "sheet": "毛利表", "name": "source_maoli",
             "csv": "source_maoli_flat.csv", "meta": "m.json",
             "digest": "d.md", "candidates": "c.yaml"},
            {"file": "target.xlsx", "sheet": "S", "name": "target",
             "csv": "target_flat.csv", "meta": "target_meta.json",
             "digest": "d.md", "candidates": "c.yaml"},
        ],
        "target": {"file": "target.xlsx", "sheet": "S", "name": "target",
                   "csv": "target_flat.csv", "meta": "target_meta.json",
                   "digest": "d.md", "candidates": "c.yaml"},
        "fingerprints": {
            "source_structure": facts_sha256(facts),
            "target_structure": facts_sha256(facts),
        },
        "style_granularity": {"target": {
            "placeholder": {"start": 7, "end": 10, "styled": True, "sample": "A7"},
            "template_rows": {"header": {"row": 2, "styled": True, "sample": "A2"}},
        }},
    }
    with open(tmp / "prepare_manifest.json", "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False)
    return {"manifest": manifest}


EGYPT_SRC_ROWS = [
    ["家用", "12K", "Z001", "F-1", "C-1", "1", "2", "3"],
    ["家用", "18K", "Z002", "F-2", "C-2", "4", "5", "6"],
    ["商用", "24K", "Z003", "F-3", "C-3", "7", "8", "9"],
    ["商用", "28K", "Z004", "F-4", "C-4", "10", "11", "12"],
    ["工程", "32K", "Z005", "F-5", "C-5", "13", "14", "15"],
]


def make_egypt_workdir(tmp) -> dict:
    """Egypt-equivalent contract workdir: 3 product groups (家用×2 / 商用×2 /
    工程×1) with a 22-col target so the V column (col 22) is in width."""
    return make_probe_workdir(tmp, src_rows=EGYPT_SRC_ROWS, n_cols=22,
                              n_source_rows=len(EGYPT_SRC_ROWS))


BASE_SPEC = {
    "task": {"intent": "t", "selected_mod": "NONE", "selected_mod_revision": None},
    "inputs": {"sources": ["source_maoli.xlsx"], "target": "target.xlsx",
               "source_sheets": [{"source": "source_maoli.xlsx", "sheets": ["毛利表"]}],
               "target_sheet": "S"},
    "fingerprints": {"source_structure": None, "target_structure": None},
    "mapping": {"targets": [{
        "sheet": "S", "base_last_row": 4,
        "clone_roles": [
            {"role": "title", "template_row": 1},
            {"role": "header", "template_row": 2},
            {"role": "data", "template_row": 3},
        ],
        "rows": {"source": "source_maoli"},
        "columns": [
            {"source": "A", "target": "A"},
            {"source": "B", "target": "B"},
            {"source": "C", "target": "C"},
        ],
        "nulls": [{"col": "D", "rows": "all"}],
    }]},
    "decisions": [], "gaps": [],
    "lineage": [{"source": "source_maoli_flat.csv", "role": "primary", "note": ""}],
    "validation": {"required_coverage": [], "required_empty": [], "key_outputs": ["A7"]},
}


def base_probe_spec() -> dict:
    """Minimal valid spec skeleton mutated by PROBE_CASES (deep copy)."""
    import copy
    return copy.deepcopy(BASE_SPEC)


# ── Probe case builders ────────────────────────────────────────────────

def _set(spec: dict, path: str, value) -> dict:
    node = spec
    parts = path.split(".")
    for p in parts[:-1]:
        node = node[int(p)] if isinstance(node, list) else node[p]
    if isinstance(node, list):
        node[int(parts[-1])] = value
    else:
        node[parts[-1]] = value
    return spec


def _gm_agg_same_col(spec, wd):
    _set(spec, "mapping.targets.0.group_merges", [{"col": "A", "group_by": "A"}])
    _set(spec, "mapping.targets.0.formulas",
         {"aggregates": [{"col": "A", "rows": "1:{n}",
                          "formula": "SUM(A{r1}:A{r2})", "style": "anchor"}]})
    return spec


def _gm_agg_diff_col(spec, wd):
    _set(spec, "mapping.targets.0.group_merges", [{"col": "A", "group_by": "A"}])
    _set(spec, "mapping.targets.0.formulas",
         {"aggregates": [{"col": "G", "rows": "1:{n}",
                          "formula": "SUM(A{r1}:A{r2})", "style": "anchor"}]})
    return spec


def _gm_per_row_same_col(spec, wd):
    _set(spec, "mapping.targets.0.group_merges", [{"col": "A", "group_by": "A"}])
    _set(spec, "mapping.targets.0.formulas", {"per_row": {"A": "{r}*2"}})
    return spec


def _agg_per_row_same_col(spec, wd):
    _set(spec, "mapping.targets.0.formulas",
         {"per_row": {"E": "A{r}*2"},
          "aggregates": [{"col": "E", "rows": "1:{n}",
                          "formula": "SUM(A{r1}:A{r2})", "style": "anchor"}]})
    return spec


def _merges_per_row_same_col(spec, wd):
    _set(spec, "mapping.targets.0.merges",
         [{"col": "E", "rows": "1:{n}", "style": "label"}])
    _set(spec, "mapping.targets.0.formulas", {"per_row": {"E": "A{r}*2"}})
    return spec


def _derived_subtraction(spec, wd):
    _set(spec, "mapping.targets.0.formulas",
         {"per_row": {"G": "IFERROR(ROUND(A{r}-B{r},2),0)"}})
    return spec


def _derived_on_mapped_col(spec, wd):
    _set(spec, "mapping.targets.0.columns", [
        {"source": "A", "target": "A"},
        {"source": "B", "target": "B"},
        {"source": "C", "target": "C"},
        {"source": "B", "target": "E"},
    ])
    _set(spec, "mapping.targets.0.formulas", {"per_row": {"E": "A{r}*2"}})
    return spec


def _mapped_group_column(spec, wd):
    _set(spec, "mapping.targets.0.group_merges", [{"col": "A", "group_by": "A"}])
    return spec


def _lookup_missing_empty(spec, wd):
    _set(spec, "mapping.targets.0.columns",
         [{"source": "A", "target": "A"},
          {"source": "B", "target": "B"},
          {"source": "C", "target": "C"},
          {"target": "F", "lookup": {"name": "fields",
                                     "field": "compressor", "missing": "empty"}}])
    _set(spec, "mapping.targets.0.lookups",
         [{"name": "fields", "from": "inheritance.json", "key_column": "C",
           "fields": ["compressor"], "missing": "empty"}])
    return spec


def _lookup_missing_error(spec, wd):
    _set(spec, "mapping.targets.0.columns",
         [{"source": "A", "target": "A"},
          {"source": "B", "target": "B"},
          {"source": "C", "target": "C"},
          {"target": "F", "lookup": {"name": "fields",
                                     "field": "compressor", "missing": "error"}}])
    _set(spec, "mapping.targets.0.lookups",
         [{"name": "fields", "from": "inheritance.json", "key_column": "C",
           "fields": ["compressor"], "missing": "error"}])
    return spec


def _lookup_field_missing(spec, wd):
    _set(spec, "mapping.targets.0.columns",
         [{"source": "A", "target": "A"},
          {"source": "B", "target": "B"},
          {"source": "C", "target": "C"},
          {"target": "F", "lookup": {"name": "fields",
                                     "field": "voltage", "missing": "empty"}}])
    _set(spec, "mapping.targets.0.lookups",
         [{"name": "fields", "from": "inheritance.json", "key_column": "C",
           "fields": ["voltage"], "missing": "empty"}])
    return spec


def _precision_keep(spec, wd):
    _set(spec, "mapping.targets.0.columns",
         [{"source": "A", "target": "A"},
          {"source": "B", "target": "B"},
          {"source": "C", "target": "C"},
          {"target": "E", "value": "168.715100569657", "precision": "keep"}])
    return spec


def _zero_policy(spec, wd):
    _set(spec, "mapping.targets.0.columns",
         [{"source": "A", "target": "A"},
          {"source": "B", "target": "B"},
          {"source": "C", "target": "C"},
          {"target": "F", "value": "0"},
          {"source": ["F", "G"], "target": "G"}])
    return spec


def _per_group_total_blocks(spec, wd):
    block_cfg = {
        "clone_roles": [
            {"role": "spacer"},
            {"role": "title", "template_row": 1, "value": "块标题"},
            {"role": "header", "template_row": 2},
            {"role": "data", "template_row": 3},
        ],
        "formulas": {"aggregates": [{"col": "G", "rows": "1:{n}",
                                      "formula": "SUM(A{r1}:A{r2})",
                                      "style": "anchor"}]},
    }
    _set(spec, "mapping.targets.0.blocks", [
        dict(block_cfg, rows={"source": "source_maoli",
                              "selectors": [{"column": "A", "pattern": "家用*"}]}),
        dict(block_cfg, rows={"source": "source_maoli",
                              "selectors": [{"column": "A", "pattern": "商用*"}]}),
    ])
    _set(spec, "validation.key_outputs", ["A6", "G8", "G13"])
    return spec


def _pptx_base(spec, wd):
    """Flip the workdir manifest + spec to a pptx table target."""
    wd["manifest"]["target"]["sheet"] = "slide[1]/table[@id=1]"
    spec["inputs"]["platform"] = "pptx"
    spec["inputs"]["target_sheet"] = "slide[1]/table[@id=1]"
    t = spec["mapping"]["targets"][0]
    t["sheet"] = "slide[1]/table[@id=1]"
    t["first_data_row"] = 2
    return spec


def _pptx_group_merges(spec, wd):
    _pptx_base(spec, wd)
    _set(spec, "mapping.targets.0.group_merges", [{"col": "A", "group_by": "A"}])
    return spec


def _pptx_inplace(spec, wd):
    _pptx_base(spec, wd)
    _set(spec, "mapping.targets.0.clone_roles",
         [{"role": "data", "mode": "inplace",
           "start_row": 2, "capacity": 4, "template_row": 2}])
    _set(spec, "mapping.targets.0.rows", {"source": "source_maoli"})
    spec["mapping"]["targets"][0].pop("base_last_row", None)
    return spec


def _inplace_base(spec, wd):
    """Shape of INPLACE_BASE_SPEC: inplace region 7-10, columns A/B/C/E,
    nulls D/F — used by the inplace interaction probes."""
    _set(spec, "mapping.targets.0.base_last_row", 14)
    _set(spec, "mapping.targets.0.clone_roles",
         [{"role": "data", "mode": "inplace",
           "start_row": 7, "capacity": 4, "template_row": 8}])
    _set(spec, "mapping.targets.0.columns",
         [{"source": "A", "target": "A"},
          {"source": "B", "target": "B"},
          {"source": "C", "target": "C"},
          {"source": "D", "target": "E"}])
    _set(spec, "mapping.targets.0.nulls",
         [{"col": "D", "rows": "all"}, {"col": "F", "rows": "all"}])
    return spec


def _nulls_aggregate_same_col(spec, wd):
    """nulls 列 X all + aggregate 列 X → 锚点格先被 nulls 清空又被聚合写 →
    DUPLICATE_TARGET_WRITE (通用规则, 与 inplace/append 无关)."""
    _inplace_base(spec, wd)
    _set(spec, "mapping.targets.0.formulas",
         {"aggregates": [{"col": "F", "rows": "1:{n}",
                          "formula": "SUM(A{r1}:A{r2})", "style": "anchor"}]})
    return spec


def _per_group_total_hardcoded_ranges(spec, wd):
    """每组合计的负面表达: 块内硬编码多个显式范围聚合 + nulls →
    DUPLICATE_TARGET_WRITE (组合边界由数据决定, 硬编码范围必然漂移;
    正确路径只有 blocks[] 拆块)."""
    _inplace_base(spec, wd)
    _set(spec, "mapping.targets.0.formulas",
         {"aggregates": [
             {"col": "F", "rows": "1:2",
              "formula": "SUM(A{r1}:A{r2})", "style": "anchor"},
             {"col": "F", "rows": "3:3",
              "formula": "SUM(A{r1}:A{r2})", "style": "anchor"},
         ]})
    return spec


def _per_group_total_explicit_ranges(spec, wd):
    """每组合计显式写法 (存量兼容, Q13 接受边界): 单块多条显式范围聚合,
    聚合列不进 nulls、不与 group_merges/per_row 同列、范围不越块 → 编译
    通过. 最小变异 (聚合列进 nulls) → DUPLICATE_TARGET_WRITE, 见
    test_per_group_total_trigger_minimal_mutation (issue 05)."""
    _set(spec, "mapping.targets.0.formulas",
         {"aggregates": [
             {"col": "E", "rows": "1:2",
              "formula": "SUM(A{r1}:A{r2})", "style": "anchor"},
             {"col": "E", "rows": "3:3",
              "formula": "SUM(A{r1}:A{r2})", "style": "anchor"},
         ]})
    return spec


def _append_remove_out_of_zone(spec, wd):
    """append 块 remove_rows > base_last_row (4): add 全部插在 base 之下推移
    行号, remove 用裸模板坐标命中刚插入的新数据行 → 自毁 plan → 拒绝."""
    _set(spec, "mapping.targets.0.remove_rows", [5, 6, 7])
    return spec


def _append_remove_within_base(spec, wd):
    """remove_rows ≤ base_last_row (4): add 区之外不被推移 — 经典收缩场景
    (源行数 < 模板行数) 保持合法."""
    _set(spec, "mapping.targets.0.remove_rows", [3])
    return spec


def _group_aggregates_egypt(spec, wd):
    """每组合计 (一等): 3 产品组 (家用×2 / 商用×2 / 工程×1), V 列组聚合 —
    聚合公式落各组锚点行, 组边界由 group_by 物化值决定."""
    _set(spec, "mapping.targets.0.formulas",
         {"group_aggregates": [
             {"group_by": "A", "col": "V",
              "formula": "IFERROR(ROUND(SUM(T{r1}:T{r2})/SUM(S{r1}:S{r2}),4),0)",
              "style": "anchor"}]})
    return spec


def _group_aggregates_whole_run(spec, wd):
    """whole_run (跨块总计) 落点语义 (末块尾部 vs 独立行) 需 spike 锁定 —
    spike 前声明 → CAPABILITY_NOT_ROLLED_OUT."""
    _set(spec, "mapping.targets.0.formulas",
         {"group_aggregates": {
             "per_group": [{"group_by": "A", "col": "G",
                            "formula": "SUM(A{r1}:A{r2})", "style": "anchor"}],
             "whole_run": {"col": "G", "formula": "SUM(A5:A9)",
                           "rows": "last_block_tail"}}})
    return spec


def _group_aggregates_gm_same_col(spec, wd):
    """组聚合与 group_merges 同列: 组锚点双写 → DUPLICATE_TARGET_WRITE."""
    _set(spec, "mapping.targets.0.group_merges",
         [{"col": "G", "group_by": "A", "label": "X"}])
    _set(spec, "mapping.targets.0.formulas",
         {"group_aggregates": [{"group_by": "A", "col": "G",
                                "formula": "SUM(A{r1}:A{r2})", "style": "anchor"}]})
    return spec


def _group_aggregates_per_row_same_col(spec, wd):
    """组聚合与 per_row 公式同列: 锚点格双写 → DUPLICATE_TARGET_WRITE."""
    _set(spec, "mapping.targets.0.formulas",
         {"per_row": {"G": "A{r}*2"},
          "group_aggregates": [{"group_by": "A", "col": "G",
                                "formula": "SUM(A{r1}:A{r2})", "style": "anchor"}]})
    return spec


def _group_aggregates_nulls_same_col(spec, wd):
    """组聚合列进 nulls: nulls 逐行清空 (含锚点格) 再写公式 → 锚点双写
    (特征 "first as empty") → DUPLICATE_TARGET_WRITE."""
    _set(spec, "mapping.targets.0.nulls",
         [{"col": "D", "rows": "all"}, {"col": "G", "rows": "all"}])
    _set(spec, "mapping.targets.0.formulas",
         {"group_aggregates": [{"group_by": "A", "col": "G",
                                "formula": "SUM(A{r1}:A{r2})", "style": "anchor"}]})
    return spec


def _group_aggregates_group_by_unmapped(spec, wd):
    """group_by 列无列映射: 组无从计算 → GROUP_BY_COLUMN_UNMAPPED."""
    _set(spec, "mapping.targets.0.formulas",
         {"group_aggregates": [{"group_by": "F", "col": "G",
                                "formula": "SUM(A{r1}:A{r2})", "style": "anchor"}]})
    return spec


def _group_aggregates_malformed_shape(spec, wd):
    """声明形态非法 (条目非 mapping) → GROUP_AGGREGATES_INVALID 结构化拒绝."""
    _set(spec, "mapping.targets.0.formulas",
         {"group_aggregates": ["SUM(A{r1}:A{r2})"]})
    return spec


PROBE_CASES = [
    # ── 组合行为契约 Q1: group_merges × formulas/aggregates ──
    {"id": "group_merges_aggregate_same_col", "expect": "DUPLICATE_TARGET_WRITE",
     "build": _gm_agg_same_col},
    {"id": "group_merges_aggregate_diff_col", "expect": "accept",
     "build": _gm_agg_diff_col},
    {"id": "group_merges_per_row_same_col", "expect": "DUPLICATE_TARGET_WRITE",
     "build": _gm_per_row_same_col},
    {"id": "aggregate_per_row_same_col", "expect": "DUPLICATE_TARGET_WRITE",
     "build": _agg_per_row_same_col},
    {"id": "merges_per_row_same_col", "expect": "accept",
     "build": _merges_per_row_same_col},
    # ── Q2: 算术派生列 ──
    {"id": "derived_subtraction_pattern", "expect": "accept",
     "build": _derived_subtraction},
    {"id": "derived_on_mapped_col", "expect": "DUPLICATE_TARGET_WRITE",
     "build": _derived_on_mapped_col},
    # ── Q3: 映射列 × group_merges ──
    {"id": "mapped_group_column_anchor", "expect": "accept",
     "build": _mapped_group_column},
    # ── Q6: lookup missing ──
    {"id": "lookup_missing_empty", "expect": "accept",
     "build": _lookup_missing_empty},
    {"id": "lookup_missing_error", "expect": "LOOKUP_KEY_MISSING",
     "build": _lookup_missing_error},
    {"id": "lookup_field_missing", "expect": "LOOKUP_FIELD_MISSING",
     "build": _lookup_field_missing},
    # ── Q7: precision ──
    {"id": "precision_keep", "expect": "accept",
     "build": _precision_keep},
    # ── 能力映射表 ──
    {"id": "zero_policy_expression", "expect": "accept",
     "build": _zero_policy},
    {"id": "per_group_total_blocks", "expect": "accept",
     "build": _per_group_total_blocks},
    # ── Q1 补充: nulls × aggregates 同列 / 每组合计硬编码范围 (负面表达) ──
    {"id": "nulls_aggregate_same_col", "expect": "DUPLICATE_TARGET_WRITE",
     "build": _nulls_aggregate_same_col},
    {"id": "per_group_total_hardcoded_ranges", "expect": "DUPLICATE_TARGET_WRITE",
     "build": _per_group_total_hardcoded_ranges},
    # ── Q13: 每组合计显式写法 (存量兼容) 的接受边界 ──
    {"id": "per_group_total_explicit_ranges", "expect": "accept",
     "build": _per_group_total_explicit_ranges},
    # ── 01: append 块 remove_rows 边界 (REMOVE_TARGETS_APPEND_ZONE) ──
    {"id": "append_remove_rows_out_of_zone", "expect": "REMOVE_TARGETS_APPEND_ZONE",
     "build": _append_remove_out_of_zone},
    {"id": "append_remove_rows_within_base", "expect": "accept",
     "build": _append_remove_within_base},
    {"id": "pptx_group_merges", "expect": "PPTX_CAPABILITY_NOT_ROLLED_OUT",
     "build": _pptx_group_merges},
    {"id": "pptx_inplace", "expect": "PPTX_CAPABILITY_NOT_ROLLED_OUT",
     "build": _pptx_inplace},
    # ── Q14: group_aggregates 一等能力 (组聚合写组锚点行) ──
    {"id": "group_aggregates_egypt_3_groups", "expect": "accept",
     "build": _group_aggregates_egypt, "workdir_factory": make_egypt_workdir},
    {"id": "group_aggregates_whole_run_gate", "expect": "CAPABILITY_NOT_ROLLED_OUT",
     "build": _group_aggregates_whole_run},
    {"id": "group_aggregates_gm_same_col", "expect": "DUPLICATE_TARGET_WRITE",
     "build": _group_aggregates_gm_same_col},
    {"id": "group_aggregates_per_row_same_col", "expect": "DUPLICATE_TARGET_WRITE",
     "build": _group_aggregates_per_row_same_col},
    {"id": "group_aggregates_nulls_same_col", "expect": "DUPLICATE_TARGET_WRITE",
     "build": _group_aggregates_nulls_same_col},
    {"id": "group_aggregates_group_by_unmapped", "expect": "GROUP_BY_COLUMN_UNMAPPED",
     "build": _group_aggregates_group_by_unmapped},
    {"id": "group_aggregates_malformed_shape", "expect": "GROUP_AGGREGATES_INVALID",
     "build": _group_aggregates_malformed_shape},
]
