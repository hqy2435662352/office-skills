from __future__ import annotations

import copy
import csv
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

import compile_fill  # noqa: E402
import mod_nominate  # noqa: E402
import promote_output  # noqa: E402
from _mod_catalog import parse_mod_index  # noqa: E402
from _probe_fixtures import (  # noqa: E402
    BASE_SPEC,
    make_all_missing_lookup_workdir,
    make_empty_lookup_workdir,
    make_egypt_workdir,
    make_probe_inplace_workdir as make_inplace_workdir,
    make_probe_workdir as make_workdir,
)


# ── Shared fixtures ────────────────────────────────────────────────────

def spec_with(wd: dict, **mutations) -> dict:
    import copy
    spec = copy.deepcopy(BASE_SPEC)
    spec["fingerprints"] = {
        "source_structure": wd["manifest"]["fingerprints"]["source_structure"],
        "target_structure": wd["manifest"]["fingerprints"]["target_structure"],
    }
    for path, value in mutations.items():
        node = spec
        parts = path.split(".")
        for p in parts[:-1]:
            node = node[p] if isinstance(node, dict) else node[int(p)]
        if isinstance(node, list):
            node[int(parts[-1])] = value
        else:
            node[parts[-1]] = value
    return spec


def compile_spec_with(wd: dict, spec: dict) -> dict:
    return compile_fill.compile_spec(spec, wd["manifest"], wd["workdir"])


def compile_fail_codes(wd: dict, spec: dict) -> list[str]:
    """Compile; return emitted defect codes (exit 3) or [] on success."""
    from io import StringIO
    buf = StringIO()
    old = sys.stderr
    sys.stderr = buf
    try:
        compile_fill.compile_spec(spec, wd["manifest"], wd["workdir"])
        return []
    except SystemExit as e:
        assert e.code == 3
        return re.findall(r'"code": "([A-Z_]+)"', buf.getvalue())
    finally:
        sys.stderr = old


def lookup_column_spec(wd: dict, field: str = "compressor",
                       missing: str = "empty", key_column: str = "C",
                       target: str = "F") -> dict:
    """Base spec + one lookup-only column mapping (Q6/Q15 tests)."""
    spec = spec_with(wd)
    spec["mapping"]["targets"][0]["columns"].append(
        {"target": target, "lookup": {"name": "fields",
                                      "field": field, "missing": missing}})
    spec["mapping"]["targets"][0]["lookups"] = [
        {"name": "fields", "from": "inheritance.json", "key_column": key_column,
         "fields": [field], "missing": missing}]
    return spec


INPLACE_BASE_SPEC = {
    "task": {"intent": "t", "selected_mod": "NONE", "selected_mod_revision": None},
    "inputs": {"sources": ["source_maoli.xlsx"], "target": "target.xlsx",
               "source_sheets": [{"source": "source_maoli.xlsx", "sheets": ["毛利表"]}],
               "target_sheet": "S"},
    "fingerprints": {"source_structure": None, "target_structure": None},
    "mapping": {"targets": [{
        "sheet": "S", "base_last_row": 14,
        "clone_roles": [
            {"role": "data", "mode": "inplace", "start_row": 7, "capacity": 4,
             "template_row": 8},
        ],
        "rows": {"source": "source_maoli"},
        "columns": [
            {"source": "A", "target": "A"},
            {"source": "B", "target": "B"},
            {"source": "C", "target": "C"},
            {"source": "D", "target": "E"},
        ],
        "group_merges": [{"col": "A", "group_by": "A", "style": "label"}],
        "nulls": [{"col": "D", "rows": "all"}, {"col": "F", "rows": "all"}],
    }]},
    "decisions": [], "gaps": [],
    "lineage": [{"source": "source_maoli_flat.csv", "role": "primary", "note": ""}],
    "validation": {"required_coverage": [], "required_empty": [], "key_outputs": ["A7"]},
}


class CompileTests(unittest.TestCase):
    def setUp(self):
        self.tmp_ctx = tempfile.TemporaryDirectory()
        self.tmp = Path(self.tmp_ctx.name)
        self.workdir = make_workdir(self.tmp)
        self.workdir["workdir"] = self.tmp

    def tearDown(self):
        self.tmp_ctx.cleanup()

    # ── FillSpec schema validation ──
    def test_schema_requires_top_keys(self):
        spec = spec_with(self.workdir)
        del spec["lineage"]
        with self.assertRaises(SystemExit) as ctx:
            compile_fill.compile_spec(spec, self.workdir["manifest"], self.tmp)
        self.assertEqual(ctx.exception.code, 3)

    # ── Fingerprint match ──
    def test_stale_fingerprint_rejected(self):
        spec = spec_with(self.workdir)
        spec["fingerprints"]["target_structure"] = "deadbeef"
        with self.assertRaises(SystemExit) as ctx:
            compile_fill.compile_spec(spec, self.workdir["manifest"], self.tmp)
        self.assertEqual(ctx.exception.code, 3)

    # ── Selectors ──
    def test_selectors_filter_rows(self):
        spec = spec_with(self.workdir)
        spec["mapping"]["targets"][0]["rows"]["selectors"] = [
            {"column": "A", "pattern": "家用*"}]
        spec["validation"]["key_outputs"] = ["A7"]
        plan = compile_spec_with(self.workdir, spec)
        self.assertEqual(plan["source_coverage"][0]["matched"], 2)
        self.assertEqual([[s, o, t] for s, o, t in plan["row_map"]],
                         [["source_maoli_flat.csv", 101, 7], ["source_maoli_flat.csv", 102, 8]])

    def test_selector_no_match_fails(self):
        spec = spec_with(self.workdir)
        spec["mapping"]["targets"][0]["rows"]["selectors"] = [
            {"column": "A", "pattern": "NOPE*"}]
        with self.assertRaises(SystemExit) as ctx:
            compile_fill.compile_spec(spec, self.workdir["manifest"], self.tmp)
        self.assertEqual(ctx.exception.code, 3)

    # ── Column mapping / transforms ──
    def test_direct_and_constant_mapping(self):
        spec = spec_with(self.workdir)
        spec["mapping"]["targets"][0]["columns"].append(
            {"target": "E", "value": "0"})
        plan = compile_spec_with(self.workdir, spec)
        writes = {w["col"] for w in plan["writes"]}
        self.assertIn("E", writes)
        self.assertEqual([w["value"] for w in plan["writes"] if w["col"] == "E"][0], "0")

    def test_lookup_missing_key_error(self):
        spec = spec_with(self.workdir)
        spec["mapping"]["targets"][0]["columns"] = [
            {"source": "C", "target": "C"},
            {"target": "F", "lookup": {"name": "fields",
                                       "field": "compressor", "missing": "error"}},
        ]
        spec["mapping"]["targets"][0]["lookups"] = [
            {"name": "fields", "from": "inheritance.json", "key_column": "C",
             "fields": ["compressor"], "missing": "error"}]
        with self.assertRaises(SystemExit) as ctx:
            compile_fill.compile_spec(spec, self.workdir["manifest"], self.tmp)
        self.assertEqual(ctx.exception.code, 3)  # Z003 missing from index

    def test_lookup_missing_empty_ok(self):
        spec = spec_with(self.workdir)
        spec["mapping"]["targets"][0]["columns"].append(
            {"target": "F", "lookup": {"name": "fields",
                                       "field": "compressor", "missing": "empty"}})
        spec["mapping"]["targets"][0]["lookups"] = [
            {"name": "fields", "from": "inheritance.json", "key_column": "C",
             "fields": ["compressor"], "missing": "empty"}]
        plan = compile_spec_with(self.workdir, spec)
        f_vals = [w["value"] for w in plan["writes"] if w["col"] == "F"]
        self.assertEqual(f_vals, ["C-1", "C-2", ""])  # Z003 → empty

    # ── Clone anchor rejection ──
    def test_clone_source_is_anchor_rejected(self):
        spec = spec_with(self.workdir)
        spec["mapping"]["targets"][0]["clone_roles"][2]["template_row"] = 5  # A5:A6 anchor
        with self.assertRaises(SystemExit) as ctx:
            compile_fill.compile_spec(spec, self.workdir["manifest"], self.tmp)
        self.assertEqual(ctx.exception.code, 3)

    # ── Null residue detection ──
    def test_clone_residue_unhandled(self):
        spec = spec_with(self.workdir)
        spec["mapping"]["targets"][0]["nulls"] = []  # template row 3 carries D=F-1
        with self.assertRaises(SystemExit) as ctx:
            compile_fill.compile_spec(spec, self.workdir["manifest"], self.tmp)
        self.assertEqual(ctx.exception.code, 3)

    # ── Duplicate target write ──
    def test_duplicate_target_write_rejected(self):
        spec = spec_with(self.workdir)
        spec["mapping"]["targets"][0]["formulas"] = {
            "per_row": {"A": "1+{r}"}}  # collides with the A column mapping
        with self.assertRaises(SystemExit) as ctx:
            compile_fill.compile_spec(spec, self.workdir["manifest"], self.tmp)
        self.assertEqual(ctx.exception.code, 3)

    # ── Formula range validation ──
    def test_formula_template_bad_key_rejected(self):
        spec = spec_with(self.workdir)
        spec["mapping"]["targets"][0]["formulas"] = {"per_row": {"E": "{x}+1"}}
        with self.assertRaises(SystemExit) as ctx:
            compile_fill.compile_spec(spec, self.workdir["manifest"], self.tmp)
        self.assertEqual(ctx.exception.code, 3)

    def test_aggregate_range_out_of_block_rejected(self):
        spec = spec_with(self.workdir)
        spec["mapping"]["targets"][0]["formulas"] = {
            "aggregates": [{"col": "G", "rows": "1:9",
                            "formula": "SUM(A{r1}:A{r2})", "style": "anchor"}]}
        with self.assertRaises(SystemExit) as ctx:
            compile_fill.compile_spec(spec, self.workdir["manifest"], self.tmp)
        self.assertEqual(ctx.exception.code, 3)

    # ── Key outputs must be written ──
    def test_key_output_unwritten_rejected(self):
        spec = spec_with(self.workdir)
        spec["validation"]["key_outputs"] = ["Z5"]
        with self.assertRaises(SystemExit) as ctx:
            compile_fill.compile_spec(spec, self.workdir["manifest"], self.tmp)
        self.assertEqual(ctx.exception.code, 3)

    # ── Required coverage ──
    def test_required_coverage_unmatched_rejected(self):
        spec = spec_with(self.workdir)
        spec["validation"]["required_coverage"] = [
            {"source": "source_maoli_flat.csv", "rows": [101, 103, 999]}]
        with self.assertRaises(SystemExit) as ctx:
            compile_fill.compile_spec(spec, self.workdir["manifest"], self.tmp)
        self.assertEqual(ctx.exception.code, 3)

    # ── Base row / columns bounds ──
    def test_base_row_out_of_bounds_rejected(self):
        spec = spec_with(self.workdir)
        spec["mapping"]["targets"][0]["base_last_row"] = 999
        with self.assertRaises(SystemExit) as ctx:
            compile_fill.compile_spec(spec, self.workdir["manifest"], self.tmp)
        self.assertEqual(ctx.exception.code, 3)

    def test_column_out_of_digest_rejected(self):
        spec = spec_with(self.workdir)
        spec["mapping"]["targets"][0]["nulls"] = [{"col": "ZZ", "rows": "all"}]
        with self.assertRaises(SystemExit) as ctx:
            compile_fill.compile_spec(spec, self.workdir["manifest"], self.tmp)
        self.assertEqual(ctx.exception.code, 3)

    # ── Full compile happy path ──
    def test_full_compile_success(self):
        spec = spec_with(self.workdir)
        plan = compile_spec_with(self.workdir, spec)
        self.assertEqual(plan["source_coverage"][0]["matched"], 3)
        self.assertEqual(plan["blocks"][-1]["data_end"], 9)
        self.assertGreater(len(plan["operations"]), 0)
        empties = [rb for rb in plan["readback"] if rb["kind"] == "empty"]
        self.assertEqual(len(empties), 3)  # D5:D7 nulls
        self.assertEqual(plan["key_outputs"], [{"path": "/S/A7", "kind": "value"}])
        self.assertEqual(plan["fill_spec_sha256"], None)  # set by main from the file


class ModNominateTests(unittest.TestCase):
    def test_four_states(self):
        idx_dir = Path(__file__).parent / "_fixtures"
        idx_dir.mkdir(exist_ok=True)
        index = idx_dir / "MOD_INDEX_test.md"
        mods = idx_dir / "MODS_test"
        mods.mkdir(exist_ok=True)
        index.write_text(
            "| MOD Name | Aliases | Scope Signals | Exclusion Signals | Path | Revision | Visibility |\n"
            "|---|---|---|---|---|---|---|\n"
            "| test_mod | tm | semantic_type::quotation,dimension_set::product_sku |  | MOD_test.md | 1 | private |\n",
            encoding="utf-8")
        (mods / "MOD_test.md").write_text(
            "## Applicability\n- semantic_type: quotation\n\n## 业务逻辑摘要\n- x\n",
            encoding="utf-8")

        # none: no signal
        r = mod_nominate.resolve(
            mod_nominate.parse_index(index), mods, "fill the pipeline sheet", [], [])
        self.assertEqual(r["status"], "none")

        # resolved: single candidate, all signals verified (digest resolves the
        # structural signal as verified facts)
        r = mod_nominate.resolve(
            mod_nominate.parse_index(index), mods, "报价汇总 迁移 毛利表",
            ["digest-text"], [])
        self.assertEqual(r["status"], "resolved")

        # ambiguous: structural signal unverified (no digests yet)
        r = mod_nominate.resolve(
            mod_nominate.parse_index(index), mods, "报价汇总 迁移", [], [])
        self.assertEqual(r["status"], "ambiguous")

        # conflict: exclusion fired (24-col fingerprint missing)
        index2 = idx_dir / "MOD_INDEX_test2.md"
        index2.write_text(
            "| MOD Name | Aliases | Scope Signals | Exclusion Signals | Path | Revision | Visibility |\n"
            "|---|---|---|---|---|---|---|\n"
            "| t24 | t | semantic_type::quotation | 目标缺少24角色表头指纹 | MOD_test.md | 1 | private |\n",
            encoding="utf-8")
        r = mod_nominate.resolve(
            mod_nominate.parse_index(index2), mods, "报价汇总 迁移 毛利表",
            ["目标 sheet 21行 × 24列"], [])  # 24-col present → no fire
        self.assertEqual(r["status"], "resolved")
        r = mod_nominate.resolve(
            mod_nominate.parse_index(index2), mods, "报价汇总 迁移 毛利表",
            ["目标 sheet 21行 × 3列"], [])  # not 24 → exclusion fires
        self.assertEqual(r["status"], "conflict")
        # evidence-missing conflict carries the re-run hint, not a user demand
        self.assertIn("证据缺失", r["why"])
        r = mod_nominate.resolve(
            mod_nominate.parse_index(index2), mods, "报价汇总 迁移 毛利表",
            [], [])  # no evidence at all → 证据缺失 (not structural mismatch)
        self.assertEqual(r["status"], "conflict")
        self.assertIn("证据缺失", r["why"])

    def test_exclusion_role_fingerprint(self):
        """v2.5: 排除信号带角色参数 — 表头角色多数命中 = 同角色变体放行;
        角色缺失 = 结构不符触发排除; outline JSON 通道可独立提供 24 列证据."""
        idx_dir = Path(__file__).parent / "_fixtures"
        index = idx_dir / "MOD_INDEX_roles.md"
        mods = idx_dir / "MODS_roles"
        mods.mkdir(exist_ok=True)
        index.write_text(
            "| MOD Name | Aliases | Scope Signals | Exclusion Signals | Path | Revision | Visibility |\n"
            "|---|---|---|---|---|---|---|\n"
            "| t24r | t | semantic_type::quotation | "
            "目标缺少24角色表头指纹(数量,报价,原型机成本,结算价,毛利,系列盈亏) "
            "| MOD_test.md | 1 | private |\n",
            encoding="utf-8")
        (mods / "MOD_test.md").write_text(
            "## Applicability\n- semantic_type: quotation\n\n"
            "## 业务逻辑摘要\n- x\n", encoding="utf-8")
        entries = mod_nominate.parse_index(index)

        # 同角色变体: 表头含全部角色但列数 ≠ 24 → 放行 (TGT-002)
        r = mod_nominate.resolve(
            entries, mods, "报价汇总 迁移 毛利表",
            ["- 表头: 数量 | 报价 | 原型机成本 | 结算价 | 毛利 | 系列盈亏"],
            [])
        self.assertEqual(r["status"], "resolved")

        # 角色缺失: 表头仅含 1/6 角色 (型号清单类表) → 结构不符, 排除触发
        r = mod_nominate.resolve(
            entries, mods, "报价汇总 迁移 毛利表",
            ["- 表头: 型号 | 数量 | 备注 | 日期"], [])
        self.assertEqual(r["status"], "conflict")
        self.assertIn("角色", r["why"])

        # outline JSON 通道: 无 digest 但 outline 有 24 列 sheet → 放行
        r = mod_nominate.resolve(
            entries, mods, "报价汇总 迁移 毛利表", [],
            [{"data": {"sheets": [{"name": "11_FRESH本土", "rows": 21, "cols": 24}]}}])
        self.assertEqual(r["status"], "resolved")

    def test_sheet_marker_signal(self):
        """sheet_marker: outline 含业务标记 sheet (三三三/333) → 命中;
        无标记 → 未命中; outline 未喂 → 待验证."""
        idx_dir = Path(__file__).parent / "_fixtures"
        index = idx_dir / "MOD_INDEX_marker.md"
        mods = idx_dir / "MODS_marker"
        mods.mkdir(exist_ok=True)
        index.write_text(
            "| MOD Name | Aliases | Scope Signals | Exclusion Signals | Path | Revision | Visibility |\n"
            "|---|---|---|---|---|---|---|\n"
            "| tmark | t | semantic_type::quotation,sheet_marker::三三三\\|333 |  | MOD_test.md | 1 | private |\n",
            encoding="utf-8")
        (mods / "MOD_test.md").write_text(
            "## Applicability\n- semantic_type: quotation\n\n"
            "## 业务逻辑摘要\n- x\n", encoding="utf-8")
        entries = mod_nominate.parse_index(index)
        self.assertEqual(entries[0]["scope"], "semantic_type::quotation,"
                         "sheet_marker::三三三|333")  # \| 反转义

        # outline 含 三三三 sheet → marker 命中 → resolved
        r = mod_nominate.resolve(
            entries, mods, "报价汇总 迁移",
            ["digest 21行 × 24列"],
            [{"data": {"sheets": [{"name": "三三三", "rows": 29, "cols": 3},
                                  {"name": "11_FRESH本土", "rows": 21, "cols": 24}]}}])
        self.assertEqual(r["status"], "resolved")
        self.assertIn("sheet_marker::三三三|333",
                      r["candidates"][0]["hits"])

        # outline 无标记 sheet → marker missed
        r = mod_nominate.resolve(
            entries, mods, "报价汇总 迁移",
            ["digest 21行 × 24列"],
            [{"data": {"sheets": [{"name": "11_FRESH本土", "rows": 21, "cols": 24}]}}])
        self.assertEqual(r["candidates"][0]["missed"],
                         ["sheet_marker::三三三|333"])

        # outline 未喂 → marker pending (不阻断提名)
        r = mod_nominate.resolve(
            entries, mods, "报价汇总 迁移", [], [])
        self.assertEqual(r["status"], "ambiguous")
        self.assertIn("sheet_marker::三三三|333",
                      r["candidates"][0]["pending"])

    def test_rules_loaded_with_candidates(self):
        """规则随提名输出: 候选携带完整规则表 (映射/公式链直接可用)."""
        idx_dir = Path(__file__).parent / "_fixtures"
        index = idx_dir / "MOD_INDEX_rules.md"
        mods = idx_dir / "MODS_rules"
        mods.mkdir(exist_ok=True)
        index.write_text(
            "| MOD Name | Aliases | Scope Signals | Exclusion Signals | Path | Revision | Visibility |\n"
            "|---|---|---|---|---|---|---|\n"
            "| trules | t | semantic_type::quotation |  | MOD_rules.md | 1 | private |\n",
            encoding="utf-8")
        (mods / "MOD_rules.md").write_text(
            "## Applicability\n- semantic_type: quotation\n\n"
            "## 业务逻辑摘要\n- 原型机成本 = 源面价 − 铜管成本\n\n"
            "| Rule ID | Group | Gate | Description | Applies to | Notes |\n"
            "|---|---|---|---|---|---|\n"
            "| FLD-006 | business_transformation | mod_gate | 目标原型机成本等于源面价(更新)减源铜管成本。 | 原型机成本、面价(更新)、铜管成本 | 与 FLD-007 共同保证结算价等于面价 |\n"
            "| FRM-002 | business_transformation | mod_gate | 结算价等于原型机成本加铜管成本。 | 结算价 | 与成本拆分规则逐行核对 |\n",
            encoding="utf-8")
        entries = mod_nominate.parse_index(index)
        r = mod_nominate.resolve(entries, mods, "报价汇总 迁移", ["digest"], [])
        self.assertEqual(r["status"], "resolved")
        rules = r["candidates"][0]["rules"]
        self.assertEqual([x["id"] for x in rules], ["FLD-006", "FRM-002"])
        self.assertIn("源面价", rules[0]["description"])
        self.assertEqual(rules[0]["group"], "business_transformation")

    def test_out_relative_resolves_to_workdir(self):
        """--out 相对路径以 workdir 为基准 — CWD != workdir 时结果不乱落
        (2026-08-12 round2: mod_resolution.json 曾写到 CWD 需手动归位)."""
        import subprocess
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as cwd:
            workdir = Path(tmp)
            cwd = Path(cwd)
            idx = workdir / "MOD_INDEX.md"
            mods = workdir / "MODS"
            mods.mkdir(exist_ok=True)
            idx.write_text(
                "| MOD Name | Aliases | Scope Signals | Exclusion Signals | Path | Revision | Visibility |\n"
                "|---|---|---|---|---|---|---|\n"
                "| t_out | t | semantic_type::quotation |  | MOD_test.md | 1 | private |\n",
                encoding="utf-8")
            (mods / "MOD_test.md").write_text(
                "## Applicability\n- semantic_type: quotation\n\n## 业务逻辑摘要\n- x\n",
                encoding="utf-8")
            r = subprocess.run(
                [sys.executable, str(SKILL_ROOT / "scripts" / "mod_nominate.py"),
                 "--task", "报价汇总 迁移 毛利表", "--files", "a,b",
                 "--workdir", str(workdir), "--index", str(idx),
                 "--mods-dir", str(mods), "--out", "mod_resolution.json"],
                cwd=str(cwd), capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertTrue((workdir / "mod_resolution.json").is_file())
            self.assertFalse((cwd / "mod_resolution.json").exists())

    def test_explicit_alias_ignores_unrelated_mod_conflict(self):
        idx_dir = Path(__file__).parent / "_fixtures"
        index = idx_dir / "MOD_INDEX_explicit.md"
        mods = idx_dir / "MODS_explicit"
        mods.mkdir(exist_ok=True)
        index.write_text(
            "| MOD Name | Aliases | Scope Signals | Exclusion Signals | Path | Revision | Visibility |\n"
            "|---|---|---|---|---|---|---|\n"
            "| old_migration | old | semantic_type::quotation,target_title::报价汇总 | 目标缺少24角色表头指纹 | MOD_old.md | 3 | private |\n"
            "| cost_reply | tcl-email-quote-block | semantic_type::cost_reply_to_quotation_summary_block,source_pattern::*核价邮件*,target_pattern::*报价* |  | MOD_new.md | 1 | private |\n",
            encoding="utf-8")
        for name in ("MOD_old.md", "MOD_new.md"):
            (mods / name).write_text(
                "## Applicability\n- semantic_type: quotation\n\n"
                "## 业务逻辑摘要\n- x\n", encoding="utf-8")

        task = "使用 MOD tcl-email-quote-block，读取核价邮件并写入报价汇总"
        entries = mod_nominate.parse_index(index)
        explicit = mod_nominate.explicit_mod_mentions(entries, task)
        self.assertEqual(explicit, ["cost_reply"])
        r = mod_nominate.resolve(
            entries, mods, task, ["目标 sheet 8行 × 27列"], [],
            explicit_mod=explicit[0])
        self.assertEqual(r["status"], "resolved")
        self.assertEqual([c["name"] for c in r["candidates"]], ["cost_reply"])


class ReceiptHashTests(unittest.TestCase):
    def _promote(self, workdir, expect_code):
        with self.assertRaises(SystemExit) as ctx:
            sys.argv = ["promote_output", "--workdir", str(workdir),
                        "--final", str(workdir / "final.xlsx")]
            promote_output.main()
        self.assertEqual(ctx.exception.code, expect_code)

    def _gate_workdir(self, tmp) -> tuple[Path, dict]:
        """Workdir with real draft/spec/plan + receipt + (pending|confirmed) gate."""
        import zipfile
        workdir = Path(tmp)
        draft = workdir / "validated_draft.xlsx"
        with zipfile.ZipFile(draft, "w") as z:
            z.writestr("xl/workbook.xml", "<workbook/>")  # valid zip for the promote check
        (workdir / "fill_spec.yaml").write_text("task: x", encoding="utf-8")
        (workdir / "execution_plan.json").write_text("{}", encoding="utf-8")
        hashes = {
            "fill_spec_sha256": promote_output.sha256_file(workdir / "fill_spec.yaml"),
            "execution_plan_sha256": promote_output.sha256_file(workdir / "execution_plan.json"),
            "draft_sha256": promote_output.sha256_file(draft),
        }
        receipt = {"draft_path": str(draft), **hashes}
        (workdir / "draft_receipt.json").write_text(
            json.dumps(receipt, ensure_ascii=False), encoding="utf-8")
        return workdir, hashes

    def test_promotion_hash_mismatch_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            workdir, hashes = self._gate_workdir(tmp)
            (workdir / "draft_receipt.json").write_text(json.dumps(
                {"draft_path": str(workdir / "validated_draft.xlsx"),
                 "draft_sha256": "0" * 64, "execution_plan_sha256": "1" * 64,
                 "fill_spec_sha256": "2" * 64}, ensure_ascii=False), encoding="utf-8")
            # gate confirmed with the same (wrong) hashes → receipt still drifts
            (workdir / ".gate3_confirmed").write_text(json.dumps(
                {"hashes": {"fill_spec_sha256": "2" * 64,
                            "execution_plan_sha256": "1" * 64,
                            "draft_sha256": "0" * 64}}), encoding="utf-8")
            self._promote(workdir, 3)
            self.assertFalse((workdir / "final.xlsx").exists())

    def test_gate_confirm_requires_pending(self):
        import execution_gate
        with tempfile.TemporaryDirectory() as tmp:
            workdir = Path(tmp)
            with self.assertRaises(SystemExit) as ctx:
                sys.argv = ["execution_gate", "--confirm", "--workdir", str(workdir)]
                execution_gate.main()
            self.assertEqual(ctx.exception.code, 3)
            self.assertFalse((workdir / ".gate3_confirmed").exists())

    def test_gate_confirm_refuses_drifted_artifacts(self):
        import execution_gate
        with tempfile.TemporaryDirectory() as tmp:
            workdir, _hashes = self._gate_workdir(tmp)
            with self.assertRaises(SystemExit) as ctx:
                sys.argv = ["execution_gate", "--set", "--workdir", str(workdir)]
                execution_gate.main()
            self.assertEqual(ctx.exception.code, 0)
            # draft changes after presentation → confirm must refuse
            (workdir / "validated_draft.xlsx").write_bytes(b"changed")
            with self.assertRaises(SystemExit) as ctx:
                sys.argv = ["execution_gate", "--confirm", "--workdir", str(workdir)]
                execution_gate.main()
            self.assertEqual(ctx.exception.code, 3)
            self.assertFalse((workdir / ".gate3_confirmed").exists())

    def test_gate_confirm_then_promote_ok(self):
        import execution_gate
        with tempfile.TemporaryDirectory() as tmp:
            workdir, hashes = self._gate_workdir(tmp)
            with self.assertRaises(SystemExit) as ctx:
                sys.argv = ["execution_gate", "--set", "--workdir", str(workdir)]
                execution_gate.main()
            self.assertEqual(ctx.exception.code, 0)
            with self.assertRaises(SystemExit) as ctx:
                sys.argv = ["execution_gate", "--confirm", "--workdir", str(workdir)]
                execution_gate.main()
            self.assertEqual(ctx.exception.code, 0)
            self.assertTrue((workdir / ".gate3_confirmed").exists())
            self.assertFalse((workdir / ".gate3_pending").exists())
            self._promote(workdir, 0)
            self.assertTrue((workdir / "final.xlsx").exists())
            self.assertEqual(hashes["draft_sha256"],
                             promote_output.sha256_file(workdir / "final.xlsx"))

    def test_promote_requires_positive_confirmation(self):
        with tempfile.TemporaryDirectory() as tmp:
            workdir, _ = self._gate_workdir(tmp)
            # pending marker present but NO confirmed record → reject
            (workdir / ".gate3_pending").write_text("pending", encoding="utf-8")
            self._promote(workdir, 3)
            # pending marker GONE but still no confirmed record → reject
            (workdir / ".gate3_pending").unlink()
            self._promote(workdir, 3)

    def test_promote_refuses_confirmed_hash_drift(self):
        with tempfile.TemporaryDirectory() as tmp:
            workdir, _ = self._gate_workdir(tmp)
            (workdir / ".gate3_confirmed").write_text(json.dumps(
                {"hashes": {"fill_spec_sha256": "a" * 64,
                            "execution_plan_sha256": "b" * 64,
                            "draft_sha256": "c" * 64}}), encoding="utf-8")
            self._promote(workdir, 3)

    def test_promote_preserves_existing_final_on_replace_failure(self):
        """The pre-existing final file must survive a failed atomic replace —
        promotion never deletes the old final before the new one is staged."""
        import execution_gate
        from unittest import mock
        with tempfile.TemporaryDirectory() as tmp:
            workdir, _ = self._gate_workdir(tmp)
            with self.assertRaises(SystemExit):
                sys.argv = ["execution_gate", "--set", "--workdir", str(workdir)]
                execution_gate.main()
            with self.assertRaises(SystemExit):
                sys.argv = ["execution_gate", "--confirm", "--workdir", str(workdir)]
                execution_gate.main()
            final = workdir / "final.xlsx"
            final.write_bytes(b"OLD-DELIVERED-FILE")
            with mock.patch("pathlib.Path.replace", side_effect=OSError("locked by Excel")):
                self._promote(workdir, 3)
            # old final intact, staging temp cleaned up
            self.assertEqual(final.read_bytes(), b"OLD-DELIVERED-FILE")
            self.assertFalse((workdir / "final.xlsx.promoting").exists())

    def test_promote_verifies_staged_copy_before_replace(self):
        """A corrupt staged copy must be rejected BEFORE the old final is touched."""
        import execution_gate
        from unittest import mock
        with tempfile.TemporaryDirectory() as tmp:
            workdir, _ = self._gate_workdir(tmp)
            with self.assertRaises(SystemExit):
                sys.argv = ["execution_gate", "--set", "--workdir", str(workdir)]
                execution_gate.main()
            with self.assertRaises(SystemExit):
                sys.argv = ["execution_gate", "--confirm", "--workdir", str(workdir)]
                execution_gate.main()
            final = workdir / "final.xlsx"
            final.write_bytes(b"OLD-DELIVERED-FILE")
            real_copy2 = promote_output.shutil.copy2

            def corrupt_copy(src, dst, **kw):
                real_copy2(src, dst, **kw)
                with open(dst, "ab") as f:  # corrupt the staged temp
                    f.write(b"EXTRA-CORRUPTION")

            with mock.patch("promote_output.shutil.copy2", side_effect=corrupt_copy):
                self._promote(workdir, 3)
            self.assertEqual(final.read_bytes(), b"OLD-DELIVERED-FILE")


class MultiTargetTests(unittest.TestCase):
    def test_multi_target_rejected(self):
        spec = {
            "task": {"intent": "t", "selected_mod": "NONE", "selected_mod_revision": None},
            "inputs": {"sources": ["source_maoli.xlsx"], "target": "target.xlsx",
                       "source_sheets": [{"source": "source_maoli.xlsx", "sheets": ["s"]}],
                       "target_sheet": "S"},
            "fingerprints": {"source_structure": "x", "target_structure": "y"},
            "mapping": {"targets": [
                {"sheet": "S1"}, {"sheet": "S2"},
            ]},
            "decisions": [], "gaps": [],
            "lineage": [{"source": "s.csv", "role": "primary", "note": ""}],
            "validation": {"required_coverage": [], "required_empty": [], "key_outputs": []},
        }
        defects = compile_fill.validate_schema(spec, {"files": [], "flattened": []})
        codes = {d["code"] for d in defects}
        self.assertIn("SPEC_TARGETS_TOO_MANY", codes)


class PartialNullsTests(unittest.TestCase):
    def test_partial_nulls_leaves_residue_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            wd = make_workdir(Path(tmp))
            wd["workdir"] = Path(tmp)
            spec = spec_with(wd)
            # D carried by template row 3; nulls only row 1 → residue on rows 2-3
            spec["mapping"]["targets"][0]["nulls"] = [{"col": "D", "rows": [1]}]
            with self.assertRaises(SystemExit) as ctx:
                compile_fill.compile_spec(spec, wd["manifest"], Path(tmp))
            self.assertEqual(ctx.exception.code, 3)
            stderr = sys.stderr
            # CLONE_RESIDUE_PARTIAL_NULLS must be among the defects — capture via re-run
            from io import StringIO
            buf = StringIO()
            old = sys.stderr
            sys.stderr = buf
            try:
                with self.assertRaises(SystemExit):
                    compile_fill.compile_spec(spec, wd["manifest"], Path(tmp))
            finally:
                sys.stderr = old
            self.assertIn("CLONE_RESIDUE_PARTIAL_NULLS", buf.getvalue())

    def test_full_nulls_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            wd = make_workdir(Path(tmp))
            wd["workdir"] = Path(tmp)
            spec = spec_with(wd)  # base spec already nulls D rows all
            plan = compile_fill.compile_spec(spec, wd["manifest"], Path(tmp))
            self.assertGreater(len(plan["operations"]), 0)


class LookupNormalizeTests(unittest.TestCase):
    def test_inheritance_index_normalized(self):
        data = {
            "schema": "table-fill-inheritance-index-v1",
            "index": {
                "Z001": {"field_consensus": {
                    "compressor": {"status": "unique", "value": "C-1"},
                    "copper": {"status": "conflict", "value": "X"},
                }},
            },
        }
        out = compile_fill.normalize_lookup_data(data, "x")
        self.assertEqual(out, {"Z001": {"compressor": "C-1"}})

    # ── Q15: lookup 索引完整性 (埃及 FRESH 坑 1) ──
    def test_lookup_table_empty_rejected(self):
        """索引归一化后为空 (field_consensus 被清洗脚本重写丢失) →
        LOOKUP_TABLE_EMPTY (exit 3), 不再静默全空留白."""
        with tempfile.TemporaryDirectory() as tmp:
            wd = make_empty_lookup_workdir(Path(tmp))
            wd["workdir"] = Path(tmp)
            codes = compile_fail_codes(wd, lookup_column_spec(wd))
            self.assertIn("LOOKUP_TABLE_EMPTY", codes)

    def test_lookup_table_plain_empty_rejected(self):
        """扁平索引文件本身为空 ({}) → 同样 LOOKUP_TABLE_EMPTY (0 entries)."""
        with tempfile.TemporaryDirectory() as tmp:
            wd = make_workdir(Path(tmp))
            wd["workdir"] = Path(tmp)
            (Path(tmp) / "inheritance.json").write_text("{}", encoding="utf-8")
            self.assertIn("LOOKUP_TABLE_EMPTY",
                          compile_fail_codes(wd, lookup_column_spec(wd)))

    def test_lookup_column_all_missing_warns(self):
        """索引非空但声明 lookup 列全部未命中 → LOOKUP_COLUMN_ALL_MISSING 警告
        (编译继续, Gate 呈现) — 拦截整列静默空, 允许合法缺失."""
        with tempfile.TemporaryDirectory() as tmp:
            wd = make_all_missing_lookup_workdir(Path(tmp))
            wd["workdir"] = Path(tmp)
            plan = compile_fill.compile_spec(
                lookup_column_spec(wd), wd["manifest"], Path(tmp))
            codes = [w["code"] for w in plan["warnings"]]
            self.assertIn("LOOKUP_COLUMN_ALL_MISSING", codes)
            self.assertEqual(
                [w["value"] for w in plan["writes"] if w["col"] == "F"],
                ["", "", ""])  # 全列留空但不再静默
            self.assertGreater(len(plan["operations"]), 0)  # 警告不阻断编译

    def test_lookup_all_missing_corrective_action_self_reference(self):
        """整列未命中 corrective_action 提示索引输入自引用排查 (埃及 FRESH 坑 2:
        目标 sheet 误作索引输入 → 同 SKU 多值 → 共识 conflict → 静默缺失)."""
        with tempfile.TemporaryDirectory() as tmp:
            wd = make_all_missing_lookup_workdir(Path(tmp))
            wd["workdir"] = Path(tmp)
            plan = compile_fill.compile_spec(
                lookup_column_spec(wd), wd["manifest"], Path(tmp))
            w = next(w for w in plan["warnings"]
                     if w["code"] == "LOOKUP_COLUMN_ALL_MISSING")
            self.assertIn("target sheet", w["corrective_action"])
            self.assertIn("index input", w["corrective_action"])

    def test_lookup_column_partial_miss_no_warning(self):
        """部分命中 (部分行缺失是合法 gaps) → 不触发全列警告."""
        with tempfile.TemporaryDirectory() as tmp:
            wd = make_workdir(Path(tmp))
            wd["workdir"] = Path(tmp)
            (Path(tmp) / "inheritance.json").write_text(
                json.dumps({"Z001": {"compressor": "C-1"}}), encoding="utf-8")
            plan = compile_fill.compile_spec(
                lookup_column_spec(wd), wd["manifest"], Path(tmp))
            self.assertNotIn("LOOKUP_COLUMN_ALL_MISSING",
                             [w["code"] for w in plan["warnings"]])

    def test_lookup_column_all_hit_empty_values_no_warning(self):
        """键全部命中但索引存储值本身为空串 (真命中) → 不触发全列警告
        (命中 ≠ 缺失; 统计在 resolve_lookup 内按命中/缺失语义计数)."""
        with tempfile.TemporaryDirectory() as tmp:
            wd = make_workdir(Path(tmp))
            wd["workdir"] = Path(tmp)
            (Path(tmp) / "inheritance.json").write_text(json.dumps({
                "Z001": {"compressor": ""}, "Z002": {"compressor": ""},
                "Z003": {"compressor": ""},
            }), encoding="utf-8")
            plan = compile_fill.compile_spec(
                lookup_column_spec(wd), wd["manifest"], Path(tmp))
            self.assertNotIn("LOOKUP_COLUMN_ALL_MISSING",
                             [w["code"] for w in plan["warnings"]])

    # ── Q2: lookup key 归一化 (NBSP + strip) ──
    def test_lookup_key_normalized_nbsp(self):
        """源 key 含 NBSP 尾字符 → 归一化后命中索引 (non-empty)."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            wd = make_workdir(tmp, src_rows=[
                ["家用", "12K", "Z001\xa0", "F-1", "C-1", "1", "2", "3"],
                ["家用", "18K", "Z002", "F-2", "C-2", "4", "5", "6"],
                ["商用", "24K", "Z003", "F-3", "C-3", "7", "8", "9"],
            ])
            wd["workdir"] = tmp
            (tmp / "inheritance.json").write_text(
                json.dumps({"Z001": {"compressor": "C-1"},
                            "Z002": {"compressor": "C-2"}}),
                encoding="utf-8")
            plan = compile_fill.compile_spec(
                lookup_column_spec(wd), wd["manifest"], tmp)
            f_vals = [w["value"] for w in plan["writes"] if w["col"] == "F"]
            self.assertEqual(f_vals[0], "C-1")  # Z001\xa0 → hit

    def test_lookup_key_normalized_strip(self):
        """源 key 含首尾空格 → 归一化后命中索引 (non-empty)."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            wd = make_workdir(tmp, src_rows=[
                ["家用", "12K", " Z001 ", "F-1", "C-1", "1", "2", "3"],
                ["家用", "18K", "Z002", "F-2", "C-2", "4", "5", "6"],
                ["商用", "24K", "Z003", "F-3", "C-3", "7", "8", "9"],
            ])
            wd["workdir"] = tmp
            (tmp / "inheritance.json").write_text(
                json.dumps({"Z001": {"compressor": "C-1"},
                            "Z002": {"compressor": "C-2"}}),
                encoding="utf-8")
            plan = compile_fill.compile_spec(
                lookup_column_spec(wd), wd["manifest"], tmp)
            f_vals = [w["value"] for w in plan["writes"] if w["col"] == "F"]
            self.assertEqual(f_vals[0], "C-1")  # ' Z001 ' → hit


class NumericPrecisionTests(unittest.TestCase):
    def test_long_precision_value_auto_rounded_with_warning(self):
        """15-digit cost value is AUTO-ROUNDED to 4 decimals with a warning
        (no execute round burned rediscovering the fix)."""
        with tempfile.TemporaryDirectory() as tmp:
            wd = make_workdir(Path(tmp))
            wd["workdir"] = Path(tmp)
            spec = spec_with(wd)
            # force a long value via a constant mapping on top of the base columns
            spec["mapping"]["targets"][0]["columns"].append(
                {"target": "E", "value": "168.715100569657"})
            plan = compile_fill.compile_spec(spec, wd["manifest"], Path(tmp))
            vals = [w["value"] for w in plan["writes"] if w["col"] == "E"]
            self.assertEqual(vals, ["168.7151"] * 3)  # auto-rounded in place
            codes = [w["code"] for w in plan["warnings"]]
            self.assertIn("AUTO_ROUND4", codes)

    def test_round4_transform_clears_the_defect(self):
        with tempfile.TemporaryDirectory() as tmp:
            wd = make_workdir(Path(tmp))
            wd["workdir"] = Path(tmp)
            spec = spec_with(wd)
            spec["mapping"]["targets"][0]["columns"].append(
                {"target": "E", "value": "168.715100569657", "transform": "round4"})
            plan = compile_fill.compile_spec(spec, wd["manifest"], Path(tmp))
            vals = [w["value"] for w in plan["writes"] if w["col"] == "E"]
            self.assertEqual(vals, ["168.7151"] * 3)
            self.assertEqual(plan["warnings"], [])  # explicit transform → no warning

    def test_precision_keep_explicitly_accepts(self):
        with tempfile.TemporaryDirectory() as tmp:
            wd = make_workdir(Path(tmp))
            wd["workdir"] = Path(tmp)
            spec = spec_with(wd)
            spec["mapping"]["targets"][0]["columns"].append(
                {"target": "E", "value": "168.715100569657", "precision": "keep"})
            plan = compile_fill.compile_spec(spec, wd["manifest"], Path(tmp))
            self.assertEqual(plan["operation_count"] > 0, True)
            self.assertEqual(plan["warnings"], [])  # explicit keep → untouched

    def _set_column_width(self, tmp: Path, widths: dict | None) -> None:
        """重写 fixture 的 target_meta.json 的 column_width (不入指纹, 无需重算)."""
        meta_path = tmp / "target_meta.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        if widths is None:
            meta.pop("column_width", None)
        else:
            meta["column_width"] = widths
        meta_path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")

    def _keep_spec(self, wd: dict) -> dict:
        spec = spec_with(wd)
        spec["mapping"]["targets"][0]["columns"].append(
            {"target": "E", "value": "168.715100569657", "precision": "keep"})
        return spec

    def test_precision_keep_narrow_column_rejected(self):
        """prepare 已采集列宽 (E=8) 且该列最宽渲染值 (16 字符) 超出列宽 →
        PRECISION_KEEP_NARROW_COLUMN 编译拒绝 (exit 3) — 不再执行期才发现
        text_overflow."""
        with tempfile.TemporaryDirectory() as tmp:
            wd = make_workdir(Path(tmp))
            wd["workdir"] = Path(tmp)
            self._set_column_width(Path(tmp), {"E": 8})
            codes = compile_fail_codes(wd, self._keep_spec(wd))
            self.assertIn("PRECISION_KEEP_NARROW_COLUMN", codes)

    def test_precision_keep_wide_column_ok(self):
        """列宽 20 > 最宽渲染值 16 → keep 编译通过, 无警告."""
        with tempfile.TemporaryDirectory() as tmp:
            wd = make_workdir(Path(tmp))
            wd["workdir"] = Path(tmp)
            self._set_column_width(Path(tmp), {"E": 20})
            plan = compile_spec_with(wd, self._keep_spec(wd))
            self.assertGreater(plan["operation_count"], 0)
            self.assertEqual(plan["warnings"], [])

    def test_precision_keep_width_unknown_warns(self):
        """旧 meta 无 column_width → 保持豁免 (编译通过) + 警告提示无法验证."""
        with tempfile.TemporaryDirectory() as tmp:
            wd = make_workdir(Path(tmp))
            wd["workdir"] = Path(tmp)
            self._set_column_width(Path(tmp), None)
            plan = compile_spec_with(wd, self._keep_spec(wd))
            self.assertGreater(plan["operation_count"], 0)
            codes = [w["code"] for w in plan["warnings"]]
            self.assertIn("PRECISION_KEEP_WIDTH_UNVERIFIED", codes)

    def test_precision_keep_narrow_round4_clears_defect(self):
        """corrective_action 落地: 同一窄列 (E=8) 改用 transform: round4 →
        编译通过, 无警告."""
        with tempfile.TemporaryDirectory() as tmp:
            wd = make_workdir(Path(tmp))
            wd["workdir"] = Path(tmp)
            self._set_column_width(Path(tmp), {"E": 8})
            spec = spec_with(wd)
            spec["mapping"]["targets"][0]["columns"].append(
                {"target": "E", "value": "168.715100569657", "transform": "round4"})
            plan = compile_spec_with(wd, spec)
            vals = [w["value"] for w in plan["writes"] if w["col"] == "E"]
            self.assertEqual(vals, ["168.7151"] * 3)

    def test_overlong_integer_still_hard_fails(self):
        """>12-digit integers cannot be shortened by rounding → hard defect."""
        with tempfile.TemporaryDirectory() as tmp:
            wd = make_workdir(Path(tmp))
            wd["workdir"] = Path(tmp)
            spec = spec_with(wd)
            spec["mapping"]["targets"][0]["columns"].append(
                {"target": "E", "value": "12345678901234"})  # 14 digits, no decimals
            from io import StringIO
            buf = StringIO()
            old = sys.stderr
            sys.stderr = buf
            try:
                with self.assertRaises(SystemExit) as ctx:
                    compile_fill.compile_spec(spec, wd["manifest"], Path(tmp))
                self.assertEqual(ctx.exception.code, 3)
            finally:
                sys.stderr = old
            self.assertIn("NUMERIC_OVERFLOW_RISK", buf.getvalue())

    def test_short_numbers_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            wd = make_workdir(Path(tmp))
            wd["workdir"] = Path(tmp)
            spec = spec_with(wd)  # base spec values are short
            plan = compile_fill.compile_spec(spec, wd["manifest"], Path(tmp))
            self.assertGreater(plan["operation_count"], 0)


class PrecisionWidthEstimatorTests(unittest.TestCase):
    """estimate_rendered_width 边界固定 (启发式的机器可验证确定性边界 —
    低估才会放行执行期溢出, 高估只会建议 round4 = 文档首选, 安全方向).

    与 Q7 契约同源: keep 列宽校验的渲染宽度就是这里的估算."""

    def test_general_uses_raw_length(self):
        est = compile_fill.estimate_rendered_width
        self.assertEqual(est("168.715100569657"), 16)
        self.assertEqual(est("168.715100569657", "General"), 16)
        # 无 0/# 占位符的格式 (如日期/纯文本) → 按原始字符串长度保守计
        self.assertEqual(est("168.715100569657", "yyyy-mm-dd"), 16)

    def test_format_truncates_decimals(self):
        est = compile_fill.estimate_rendered_width
        self.assertEqual(est("168.715100569657", "0.00"), 6)       # 168.72
        self.assertEqual(est("-168.7151", "0.00"), 7)              # -168.72

    def test_thousands_separators(self):
        est = compile_fill.estimate_rendered_width
        self.assertEqual(est("1234567.89", "#,##0.00"), 12)        # 1,234,567.89

    def test_zero_pad_integer(self):
        est = compile_fill.estimate_rendered_width
        self.assertEqual(est("168.5", "000000.00"), 9)             # 000168.50

    def test_quoted_literals(self):
        est = compile_fill.estimate_rendered_width
        self.assertEqual(est("168.5", '0"万元"'), 5)               # 168万元

    def test_parenthesized_negative(self):
        est = compile_fill.estimate_rendered_width
        self.assertEqual(est("-168.5", "(0.00)"), 8)               # (168.50), 无负号

    def test_exponent_suffix_counted(self):
        est = compile_fill.estimate_rendered_width
        self.assertEqual(est("168.715100569657", "0.00E+00"), 10)  # 保守高估 (安全方向)

    def test_currency_and_percent_symbols(self):
        est = compile_fill.estimate_rendered_width
        self.assertEqual(est("168.5", '"$"#,##0.00'), 7)           # $168.50 (引号内 $ 不双计)
        self.assertEqual(est("0.1685", "0.00%"), 6)                # 16.85% (×100 缩放计入)
        self.assertEqual(est("168.7151", "0.00%"), 9)              # 16871.51%


class OverflowDetectorBackingTests(unittest.TestCase):
    """执行期 text_overflow 检测器仍有单测背书 — keep 窄列前移到编译期拒绝后,
    execute 期检测器是第二道防线, 不得静默回归 (issue 04 契约闭环)."""

    def test_classify_issue_overflow_messages(self):
        from _defect_class import classify_issue
        self.assertEqual(classify_issue("A5", "overflow",
                                        "value overflows column width"), "text_overflow")
        self.assertEqual(classify_issue("A5", "",
                                        "需要 24pt 才能显示"), "text_overflow")
        self.assertEqual(classify_issue("A5", "empty",
                                        "cell value is empty"), "empty_cell")
        self.assertEqual(classify_issue("A5", "", "ok"), "unknown")


class NotePhaseTests(unittest.TestCase):
    def test_note_phase_records_gap(self):
        import note_phase
        with tempfile.TemporaryDirectory() as tmp:
            workdir = Path(tmp)
            (workdir / "run_timing.json").write_text(json.dumps([
                {"kind": "machine", "phase": "prepare_flatten",
                 "started_at": "2026-08-10T10:00:00", "duration_ms": 10000},
            ]), encoding="utf-8")
            with self.assertRaises(SystemExit) as ctx:
                sys.argv = ["note_phase", "--workdir", str(workdir), "--phase", "spec_authoring"]
                note_phase.main()
            self.assertEqual(ctx.exception.code, 0)
            entries = json.loads((workdir / "run_timing.json").read_text(encoding="utf-8"))
            agent = entries[-1]
            self.assertEqual(agent["kind"], "agent")
            self.assertEqual(agent["phase"], "spec_authoring")
            # gap since the machine entry finished (>=0s, measured in wall time)
            self.assertGreaterEqual(agent["duration_ms"], 0)
            self.assertEqual(agent["started_at"], "2026-08-10T10:00:10")

    def test_note_phase_chains_agent_entries(self):
        import note_phase
        with tempfile.TemporaryDirectory() as tmp:
            workdir = Path(tmp)
            (workdir / "run_timing.json").write_text(json.dumps([
                {"kind": "agent", "phase": "spec_authoring",
                 "started_at": "2026-08-10T10:00:00", "duration_ms": 600000},
            ]), encoding="utf-8")
            with self.assertRaises(SystemExit):
                sys.argv = ["note_phase", "--workdir", str(workdir), "--phase", "compile_review"]
                note_phase.main()
            entries = json.loads((workdir / "run_timing.json").read_text(encoding="utf-8"))
            self.assertEqual(entries[-1]["started_at"], "2026-08-10T10:10:00")  # 10min later
            self.assertEqual(entries[-1]["kind"], "agent")


class MultiSourceTests(unittest.TestCase):
    def test_two_sources_merge_in_order(self):
        """rows.sources concatenates matched rows of both sheets in list order."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            wd = make_workdir(tmp)
            # second source sheet: another CSV with 2 rows
            with open(tmp / "source_commercial_flat.csv", "w", newline="", encoding="utf-8-sig") as f:
                w = csv.writer(f)
                w.writerow(["商用", "10P", "Z500", "F-5", "C-5", "10", "11", "12", "201"])
                w.writerow(["商用", "20P", "Z501", "F-6", "C-6", "13", "14", "15", "202"])
            wd["manifest"]["flattened"].append(
                {"file": "source_commercial.xlsx", "sheet": "商用", "name": "source_commercial",
                 "csv": "source_commercial_flat.csv", "meta": "m.json",
                 "digest": "d.md", "candidates": "c.yaml"})
            wd["manifest"]["files"].append(
                {"staged": "source_commercial.xlsx", "source": "x", "sha256": "c"})
            wd["workdir"] = tmp
            spec = spec_with(wd)
            spec["mapping"]["targets"][0]["rows"] = {
                "sources": [
                    {"source": "source_maoli",
                     "selectors": [{"column": "A", "pattern": "家用*"}]},
                    {"source": "source_commercial"},
                ],
            }
            spec["validation"]["required_coverage"] = [
                {"source": "source_maoli_flat.csv", "rows": [101]},
                {"source": "source_commercial_flat.csv", "rows": [201, 202]},
            ]
            plan = compile_fill.compile_spec(spec, wd["manifest"], tmp)
            # 2 (家用) + 2 (商用) rows → data 7-10 (title 5, header 6)
            self.assertEqual(plan["source_coverage"][0]["matched"], 2)
            self.assertEqual(plan["source_coverage"][1]["matched"], 2)
            self.assertEqual(plan["source_coverage"][1]["source"], "source_commercial_flat.csv")
            rows = [t for _, _, t in plan["row_map"]]
            self.assertEqual(rows, [7, 8, 9, 10])
            self.assertEqual(plan["source_coverage"][0]["required_unmatched"], [])
            self.assertEqual(plan["source_coverage"][1]["required_unmatched"], [])


class PrepareMergeTests(unittest.TestCase):
    def test_merge_flattened_incremental(self):
        from prepare_run import merge_flattened
        first = [
            {"name": "source_maoli_FRESH", "csv": "a.csv"},
            {"name": "target_baojia", "csv": "t.csv"},
        ]
        second = [
            {"name": "source_maoli_COMMERCIAL", "csv": "b.csv"},
        ]
        merged = merge_flattened(first, second)
        self.assertEqual([e["name"] for e in merged],
                         ["source_maoli_FRESH", "target_baojia", "source_maoli_COMMERCIAL"])
        # 同名条目新覆盖旧
        again = merge_flattened(merged, [{"name": "source_maoli_FRESH", "csv": "a2.csv"}])
        by_name = {e["name"]: e for e in again}
        self.assertEqual(by_name["source_maoli_FRESH"]["csv"], "a2.csv")
        self.assertEqual(len(again), 3)


class PrepareStyleGranularityTests(unittest.TestCase):
    """Issue 03: prepare 阶段 B 样式粒度决策事实 (占位行裸行 vs 带样式).

    埃及形态: 占位行 23-52 裸行 (无边框/填充/字体) → clone-append 正确终态;
    MXP 报价单形态: 占位行带样式 (空单元格持边框/填充) → inplace 成立。
    指纹计算不变: style_granularity 不入 structure_facts。
    """

    def _make_template(self, path, content_rows, placeholder_runs, styled_empty):
        """Synthetic xlsx: content rows (borders+fill) + placeholder runs.

        styled_empty=True → 占位行空单元格携带样式 (MXP 报价单形态);
        False → 裸行 (row 元素仅行高, 无单元格, 埃及形态)."""
        from openpyxl import Workbook
        from openpyxl.styles import Border, PatternFill, Side
        wb = Workbook()
        ws = wb.active
        ws.title = "S"
        thin = Side(style="thin")
        border = Border(left=thin, right=thin, top=thin, bottom=thin)
        fill = PatternFill("solid", fgColor="FFF2F2F2")
        for r in range(1, content_rows + 1):
            for c in ("A", "B", "C"):
                cell = ws[f"{c}{r}"]
                cell.value = f"v{r}"
                cell.border = border
                cell.fill = fill
        for (start, end) in placeholder_runs:
            for r in range(start, end + 1):
                if styled_empty:
                    for c in ("A", "B", "C"):
                        ws[f"{c}{r}"].border = border
                else:
                    ws.row_dimensions[r].height = 24
        wb.save(path)

    def _detect(self, path, blocks, flat_rows, num_cols=6):
        from flatten_table import detect_style_granularity
        return detect_style_granularity(str(path), "S", blocks, flat_rows, num_cols)

    def test_egypt_form_placeholder_bare_rows(self):
        """埃及形态: 占位行 23-52 裸行 (无单元格 → 无样式), 克隆源行带样式."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            p = tmp / "egypt.xlsx"
            self._make_template(p, content_rows=21, placeholder_runs=[(23, 52)],
                                styled_empty=False)
            blocks = [{"start": 1, "end": 5, "title": "B1"},
                      {"start": 7, "end": 15, "title": "B2"},
                      {"start": 17, "end": 21, "title": "B3"}]
            flat_rows = [[f"v{i}", str(i)] for i in range(1, 22)]
            sg = self._detect(p, blocks, flat_rows)
            self.assertEqual(
                sg["placeholder_segments"],
                [{"start": 23, "end": 52, "styled": False, "sample": None}])
            # 克隆源行 (title/header/data) 全部带样式 — 克隆携带格式的事实依据
            b1 = sg["clone_source_rows"][0]
            for role in ("title", "header", "data"):
                self.assertTrue(b1[role]["styled"], role)
                self.assertEqual(b1[role]["row"],
                                 {"title": 1, "header": 2, "data": 3}[role])
            self.assertEqual(len(sg["clone_source_rows"]), 3)

    def test_mxp_form_placeholder_styled_rows(self):
        """MXP 报价单形态: 占位行空单元格带样式 → 带样式 (样例: A7)."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            p = tmp / "mxp.xlsx"
            self._make_template(p, content_rows=6, placeholder_runs=[(7, 24)],
                                styled_empty=True)
            blocks = [{"start": 1, "end": 6, "title": "报价单"}]
            flat_rows = [[f"v{i}", str(i)] for i in range(1, 7)]
            sg = self._detect(p, blocks, flat_rows)
            self.assertEqual(
                sg["placeholder_segments"],
                [{"start": 7, "end": 24, "styled": True, "sample": "A7"}])

    def test_digest_lines_both_forms(self):
        """digest 输出结论行: 裸行 (带段范围) / 带样式 (样例坐标); 克隆源行行."""
        from structure_digest import build_digest
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            p = tmp / "egypt.xlsx"
            self._make_template(p, content_rows=21, placeholder_runs=[(23, 52)],
                                styled_empty=False)
            blocks = [{"start": 1, "end": 5, "title": "B1"}]
            flat_rows = [[f"v{i}", str(i)] for i in range(1, 22)]
            sg = self._detect(p, blocks, flat_rows)
            meta = {"file": str(p), "sheet": "S",
                    "dimensions": {"rows": 52, "cols": 6},
                    "style_granularity": sg}
            lines = build_digest(meta, None, None, for_target=True)
            self.assertTrue(any("占位行样式: 裸行 (23-52)" in l for l in lines))
            self.assertTrue(any(l.startswith("- 克隆源行样式: B1(") for l in lines))

            p2 = tmp / "mxp.xlsx"
            self._make_template(p2, content_rows=6, placeholder_runs=[(7, 24)],
                                styled_empty=True)
            sg2 = self._detect(p2, [{"start": 1, "end": 6, "title": "报价单"}],
                               [[f"v{i}", str(i)] for i in range(1, 7)])
            meta2 = {"file": str(p2), "sheet": "S",
                     "dimensions": {"rows": 24, "cols": 6},
                     "style_granularity": sg2}
            lines2 = build_digest(meta2, None, None, for_target=True)
            self.assertTrue(any("占位行样式: 带样式 (样例: A7)" in l for l in lines2))

    def test_style_granularity_not_in_fingerprint(self):
        """style_granularity 不入指纹: 带/不带该事实的 meta 指纹相同 (旧 spec
        重编译不触发 fingerprint 失效)."""
        from prepare_run import facts_sha256, structure_facts
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            p = tmp / "egypt.xlsx"
            self._make_template(p, content_rows=21, placeholder_runs=[(23, 52)],
                                styled_empty=False)
            blocks = [{"start": 1, "end": 5, "title": "B1"}]
            flat_rows = [[f"v{i}", str(i)] for i in range(1, 22)]
            sg = self._detect(p, blocks, flat_rows)
            base_meta = {"sheet": "S",
                         "dimensions": {"rows": 52, "cols": 6, "data_rows": 21},
                         "header_band": None,
                         "merged_ranges": [],
                         "blocks": blocks,
                         "columns": [{"col": "A", "nonempty": 21, "numeric_ratio": 0.0}],
                         "formulas": {}, "column_numfmt": {},
                         "merge_anchors": []}
            self.assertEqual(facts_sha256([structure_facts(base_meta)]),
                             facts_sha256([structure_facts(
                                 {**base_meta, "style_granularity": sg})]))

    def test_fill_id_1_gray125_not_styled(self):
        """fillId=1 (gray125 占位填充) 不算带样式: 仅 fillId>=2 的真实填充才
        判带样式 (LibreOffice/默认样式引用 gray125 时不得误报 带样式)."""
        import zipfile
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            p = tmp / "gray125.xlsx"
            # 手写最小 xlsx: styles.xml 定义 3 个样式 (0=默认, 1=gray125,
            # 2=真实填充), sheet 的占位行单元格引用 s=1
            styles_xml = (
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                '<fonts count="1"><font><sz val="11"/></font></fonts>'
                '<fills count="3">'
                '<fill><patternFill patternType="none"/></fill>'
                '<fill><patternFill patternType="gray125"/></fill>'
                '<fill><patternFill patternType="solid"><fgColor rgb="FFFFF2F2"/></patternFill></fill>'
                '</fills>'
                '<borders count="1"><border/></borders>'
                '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
                '<cellXfs count="3">'
                '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
                '<xf numFmtId="0" fontId="0" fillId="1" borderId="0" xfId="0" applyFill="1"/>'
                '<xf numFmtId="0" fontId="0" fillId="2" borderId="0" xfId="0" applyFill="1"/>'
                '</cellXfs>'
                '</styleSheet>'
            )
            sheet_xml = (
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                '<sheetData>'
                '<row r="1"><c r="A1" t="inlineStr"><is><t>v1</t></is></c></row>'
                '<row r="2"><c r="A2" s="1"/></row>'
                '<row r="3"><c r="A3" s="2"/></row>'
                '</sheetData></worksheet>'
            )
            wb_xml = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                      '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                      'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                      '<sheets><sheet name="S" sheetId="1" r:id="rId1"/></sheets></workbook>')
            rels_xml = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/'
                        'officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
                        '</Relationships>')
            ct_xml = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                      '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                      '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.'
                      'relationships+xml"/>'
                      '<Default Extension="xml" ContentType="application/xml"/>'
                      '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-'
                      'officedocument.spreadsheetml.sheet.main+xml"/>'
                      '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.'
                      'openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
                      '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-'
                      'officedocument.spreadsheetml.styles+xml"/>'
                      '</Types>')
            with zipfile.ZipFile(p, "w") as z:
                z.writestr("[Content_Types].xml", ct_xml)
                z.writestr("_rels/.rels", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
                            'relationships"><Relationship Id="rId1" Type="http://schemas.'
                            'openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
                            'Target="xl/workbook.xml"/></Relationships>')
                z.writestr("xl/workbook.xml", wb_xml)
                z.writestr("xl/_rels/workbook.xml.rels", rels_xml)
                z.writestr("xl/styles.xml", styles_xml)
                z.writestr("xl/worksheets/sheet1.xml", sheet_xml)
            sg = self._detect(p, [{"start": 1, "end": 1, "title": "B1"}],
                              [["v1", "1"]], num_cols=3)
            # 占位行段: row 2 (gray125 → 裸行) 与 row 3 (solid → 带样式)
            # 连续成一段, 但 gray125 单元格不得使段判带样式 — 样例必须来自
            # row 3 的真实填充 (A3).
            self.assertEqual(sg["placeholder_segments"],
                             [{"start": 2, "end": 3, "styled": True, "sample": "A3"}])

    def test_collect_style_granularity_manifest_wiring(self):
        """prepare_run 把 flatten meta 的样式粒度事实并入 manifest (按条目名)."""
        from prepare_run import collect_style_granularity
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            p = tmp / "egypt.xlsx"
            self._make_template(p, content_rows=21, placeholder_runs=[(23, 52)],
                                styled_empty=False)
            sg = self._detect(p, [{"start": 1, "end": 5, "title": "B1"}],
                              [[f"v{i}", str(i)] for i in range(1, 22)])
            out = collect_style_granularity({
                "src": {"columns": []},
                "target": {"style_granularity": sg},
            })
            self.assertNotIn("src", out)
            self.assertIn("target", out)
            self.assertEqual(out["target"]["placeholder_segments"][0]["start"], 23)


class DecisionStringTests(unittest.TestCase):
    def test_decisions_with_colon_parsed_as_dict_rejected(self):
        """decisions 条目含 ': ' 未加引号 → YAML 解析成 dict → 编译期报错."""
        spec = {
            "task": {"intent": "t", "selected_mod": "NONE", "selected_mod_revision": None},
            "inputs": {"sources": ["source_maoli.xlsx"], "target": "target.xlsx",
                       "source_sheets": [{"source": "source_maoli.xlsx", "sheets": ["s"]}],
                       "target_sheet": "S"},
            "fingerprints": {"source_structure": "x", "target_structure": "y"},
            "mapping": {"targets": [{"sheet": "S"}]},
            "decisions": [{"标题确认": "由用户在 Gate 裁决"}],  # dict 而非 str
            "gaps": [],
            "lineage": [{"source": "s.csv", "role": "primary", "note": ""}],
            "validation": {"required_coverage": [], "required_empty": [], "key_outputs": []},
        }
        defects = compile_fill.validate_schema(spec, {"files": [], "flattened": []})
        codes = {d["code"] for d in defects}
        self.assertIn("SPEC_NON_STRING_ITEM", codes)
        msg = next(d["message"] for d in defects if d["code"] == "SPEC_NON_STRING_ITEM")
        self.assertIn("': '", msg)

    def test_quoted_decisions_pass(self):
        spec = {
            "task": {"intent": "t", "selected_mod": "NONE", "selected_mod_revision": None},
            "inputs": {"sources": ["source_maoli.xlsx"], "target": "target.xlsx",
                       "source_sheets": [{"source": "source_maoli.xlsx", "sheets": ["s"]}],
                       "target_sheet": "S"},
            "fingerprints": {"source_structure": "x", "target_structure": "y"},
            "mapping": {"targets": [{"sheet": "S"}]},
            "decisions": ["标题确认: 由用户在 Gate 裁决"],
            "gaps": [],
            "lineage": [{"source": "s.csv", "role": "primary", "note": ""}],
            "validation": {"required_coverage": [], "required_empty": [], "key_outputs": []},
        }
        defects = compile_fill.validate_schema(spec, {"files": [], "flattened": []})
        self.assertEqual([d for d in defects if d["code"] == "SPEC_NON_STRING_ITEM"], [])


class MultiBlockTests(unittest.TestCase):
    def test_two_blocks_sequential_layout(self):
        """blocks[] lays out sequentially: block1 spacer/title/header/data then
        block2 spacer/title/header/data; each block has its own aggregates."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            wd = make_workdir(tmp)
            with open(tmp / "source_commercial_flat.csv", "w", newline="", encoding="utf-8-sig") as f:
                w = csv.writer(f)
                w.writerow(["商用", "10P", "Z500", "F-5", "C-5", "10", "11", "12", "201"])
                w.writerow(["商用", "20P", "Z501", "F-6", "C-6", "13", "14", "15", "202"])
            wd["manifest"]["flattened"].append(
                {"file": "source_commercial.xlsx", "sheet": "商用", "name": "source_commercial",
                 "csv": "source_commercial_flat.csv", "meta": "m.json",
                 "digest": "d.md", "candidates": "c.yaml"})
            wd["manifest"]["files"].append(
                {"staged": "source_commercial.xlsx", "source": "x", "sha256": "c"})
            wd["workdir"] = tmp
            spec = spec_with(wd)
            block_template = {
                "clone_roles": [
                    {"role": "spacer"},
                    {"role": "title", "template_row": 1, "value": "块标题"},
                    {"role": "header", "template_row": 2},
                    {"role": "data", "template_row": 3},
                ],
                "formulas": {
                    "per_row": {"E": "A{r}*2"},
                    "aggregates": [{"col": "F", "rows": "1:{n}",
                                    "formula": "SUM(E{r1}:E{r2})", "style": "anchor"}],
                },
            }
            b1 = dict(block_template, rows={"source": "source_maoli",
                                            "selectors": [{"column": "A", "pattern": "家用*"}]})
            b2 = dict(block_template, rows={"source": "source_commercial"})
            spec["mapping"]["targets"][0]["blocks"] = [b1, b2]
            spec["validation"]["required_coverage"] = [
                {"source": "source_maoli_flat.csv", "rows": [101]},
                {"source": "source_commercial_flat.csv", "rows": [201, 202]},
            ]
            spec["validation"]["key_outputs"] = ["A8", "F8", "A13"]
            spec["validation"]["key_outputs"] = ["A8", "F8", "A13"]
            plan = compile_fill.compile_spec(spec, wd["manifest"], tmp)
            # block1: spacer5 title6 header7 data8-9; block2: spacer10 title11 header12 data13-14
            rows = [t for _, _, t in plan["row_map"]]
            self.assertEqual(rows, [8, 9, 13, 14])
            self.assertEqual(plan["source_coverage"][0]["block"], 0)
            self.assertEqual(plan["source_coverage"][1]["block"], 1)
            # aggregates: F8 (block1) and F13 (block2), each SUM over its own block
            agg_paths = [op["path"] for op in plan["operations"]
                         if "formula" in op.get("props", {}) and "SUM" in op["props"]["formula"]]
            self.assertEqual(agg_paths, ["/S/F8", "/S/F13"])
            # titles: A6 and A11
            title_ops = [op for op in plan["operations"]
                         if op.get("path", "").endswith(("A6", "A11"))
                         and "value" in op.get("props", {})]
            self.assertEqual(len(title_ops), 2)
            # per-row formula E with block-local {n}: block1 rows 8-9 → n=2, block2 rows 13-14 → n=2
            e_formulas = [op["props"]["formula"] for op in plan["operations"]
                          if op.get("path", "").endswith("/E8") or op.get("path", "").endswith("/E13")]
            self.assertEqual(e_formulas, ["A8*2", "A13*2"])


class InplacePositionModelTests(unittest.TestCase):
    """ADR 0007 position-model boundary: inplace reuse / trim / hybrid overflow /
    group_merges / sets / props."""

    def setUp(self):
        self.tmp_ctx = tempfile.TemporaryDirectory()
        self.tmp = Path(self.tmp_ctx.name)
        self.wd = make_inplace_workdir(self.tmp)
        self.wd["workdir"] = self.tmp

    def tearDown(self):
        self.tmp_ctx.cleanup()

    def _spec(self, **mutations):
        spec = copy.deepcopy(INPLACE_BASE_SPEC)
        spec["fingerprints"] = {
            "source_structure": self.wd["manifest"]["fingerprints"]["source_structure"],
            "target_structure": self.wd["manifest"]["fingerprints"]["target_structure"],
        }
        for path, value in mutations.items():
            node = spec
            for p in path.split(".")[:-1]:
                node = node[p] if isinstance(node, dict) else node[int(p)]
            if isinstance(node, list):
                node[int(path.split(".")[-1])] = value
            else:
                node[path.split(".")[-1]] = value
        return spec

    def _compile(self, spec):
        return compile_fill.compile_spec(spec, self.wd["manifest"], self.wd["workdir"])

    def _compile_fails_with(self, spec, code):
        from io import StringIO
        buf = StringIO()
        old = sys.stderr
        sys.stderr = buf
        try:
            with self.assertRaises(SystemExit) as ctx:
                self._compile(spec)
            self.assertEqual(ctx.exception.code, 3)
        finally:
            sys.stderr = old
        self.assertIn(code, buf.getvalue())
        return buf.getvalue()

    # ── Row layout: inplace region, trim, hybrid overflow ──
    def test_inplace_region_no_adds_trim_tail(self):
        plan = self._compile(self._spec())
        # region rows exist — no add ops; trim removes the surplus tail
        self.assertEqual([op for op in plan["operations"] if op["command"] == "add"], [])
        removes = [op["path"] for op in plan["operations"] if op["command"] == "remove"]
        self.assertEqual(removes, ["/S/row[10]"])
        # fills land on the retained region rows 7-9
        filled = {op["path"] for op in plan["operations"]
                  if op.get("props", {}).get("value") is not None}
        self.assertIn("/S/A7", filled)
        self.assertIn("/S/C9", filled)
        self.assertEqual(plan["expected_final_row_count"], 13)
        self.assertEqual(plan["structural_deltas"], {"adds": 0, "removes": 1,
                                                     "inplace_trim": 1,
                                                     "inplace_overflow": 0})
        self.assertEqual(plan["schema_version"], "2.5")
        self.assertEqual(plan["blocks"][-1]["mode"], "inplace")

    def test_inplace_hybrid_overflow_clones_after_region(self):
        wd = make_inplace_workdir(self.tmp, n_source_rows=5)
        wd["workdir"] = self.tmp
        spec = self._spec()
        spec["fingerprints"] = wd["manifest"]["fingerprints"]
        plan = compile_fill.compile_spec(spec, wd["manifest"], self.tmp)
        adds = [op for op in plan["operations"] if op["command"] == "add"]
        self.assertEqual(len(adds), 1)  # N=5 > capacity=4 → 1 overflow clone
        self.assertEqual([a["after"] for a in adds], ["/S/row[10]"])
        self.assertEqual([op for op in plan["operations"] if op["command"] == "remove"], [])
        self.assertEqual(plan["expected_final_row_count"], 15)
        rows = [t for _, _, t in plan["row_map"]]
        self.assertEqual(rows, [7, 8, 9, 10, 11])
        filled = {op["path"] for op in plan["operations"]
                  if op.get("props", {}).get("value") is not None}
        self.assertIn("/S/B11", filled)

    # ── Declaration invariants ──
    def test_inplace_multiple_blocks_rejected(self):
        spec = self._spec()
        blk = {"clone_roles": [{"role": "data", "mode": "inplace", "start_row": 7,
                                "capacity": 4, "template_row": 8}],
               "rows": {"source": "source_maoli"}}
        spec["mapping"]["targets"][0]["blocks"] = [blk, blk]
        self._compile_fails_with(spec, "INPLACE_MULTIPLE_BLOCKS")

    def test_inplace_not_last_block_rejected(self):
        spec = self._spec()
        spec["mapping"]["targets"][0]["blocks"] = [
            {"clone_roles": [{"role": "data", "mode": "inplace", "start_row": 7,
                              "capacity": 4, "template_row": 8}],
             "rows": {"source": "source_maoli"}},
            {"clone_roles": [{"role": "data", "template_row": 3}],
             "rows": {"source": "source_maoli"}},
        ]
        self._compile_fails_with(spec, "INPLACE_NOT_LAST_BLOCK")

    def test_inplace_region_out_of_bounds_rejected(self):
        spec = self._spec()
        spec["mapping"]["targets"][0]["clone_roles"][0]["capacity"] = 99
        self._compile_fails_with(spec, "INPLACE_REGION_OUT_OF_BOUNDS")

    def test_inplace_no_clone_source_rejected(self):
        spec = self._spec()
        del spec["mapping"]["targets"][0]["clone_roles"][0]["template_row"]
        self._compile_fails_with(spec, "INPLACE_NO_CLONE_SOURCE")

    def test_inplace_role_not_last_in_block_rejected(self):
        spec = self._spec()
        spec["mapping"]["targets"][0]["clone_roles"] = [
            {"role": "data", "mode": "inplace", "start_row": 7, "capacity": 4,
             "template_row": 8},
            {"role": "spacer"},
        ]
        self._compile_fails_with(spec, "INPLACE_NOT_LAST_BLOCK")

    # ── Geometry invariants ──
    def test_inplace_region_overlap_rejected(self):
        spec = self._spec()
        spec["mapping"]["targets"][0]["blocks"] = [
            {"clone_roles": [{"role": "data", "template_row": 3}],
             "rows": {"source": "source_maoli"}, "remove_rows": [8]},
            {"clone_roles": [{"role": "data", "mode": "inplace", "start_row": 7,
                              "capacity": 4, "template_row": 8}],
             "rows": {"source": "source_maoli"}},
        ]
        self._compile_fails_with(spec, "INPLACE_REGION_OVERLAP")

    def test_structural_op_out_of_zone_rejected(self):
        spec = self._spec()
        spec["mapping"]["targets"][0]["blocks"] = [
            {"clone_roles": [{"role": "data", "template_row": 3}],
             "rows": {"source": "source_maoli"}, "remove_rows": [6]},
            {"clone_roles": [{"role": "data", "mode": "inplace", "start_row": 7,
                              "capacity": 4, "template_row": 8}],
             "rows": {"source": "source_maoli"}},
        ]
        self._compile_fails_with(spec, "STRUCTURAL_OP_OUT_OF_ZONE")

    def test_per_group_total_trigger_minimal_mutation(self):
        """每组合计被拒 fixture 的触发因素最小变异实证 (2026-08-13):
        被拒形态 (聚合列 F 进 nulls D/F) → 只把聚合列移出 nulls 列
        (F→G, nulls 与其余完全不动) → 编译通过 — 触发因素就是
        "聚合列进 nulls", 与"硬编码范围"无关."""
        import _probe_fixtures as pf
        spec = pf._per_group_total_hardcoded_ranges(
            copy.deepcopy(BASE_SPEC), self.wd)
        spec["fingerprints"] = {
            "source_structure": self.wd["manifest"]["fingerprints"]["source_structure"],
            "target_structure": self.wd["manifest"]["fingerprints"]["target_structure"],
        }
        self._compile_fails_with(spec, "DUPLICATE_TARGET_WRITE")
        spec["mapping"]["targets"][0]["formulas"]["aggregates"] = [
            {"col": "G", "rows": "1:2",
             "formula": "SUM(A{r1}:A{r2})", "style": "anchor"},
            {"col": "G", "rows": "3:3",
             "formula": "SUM(A{r1}:A{r2})", "style": "anchor"},
        ]
        self._compile(spec)  # 不抛 SystemExit = 编译通过

    def test_sets_in_region_rejected(self):
        spec = self._spec()
        spec["mapping"]["targets"][0]["sets"] = [{"path": "A8", "value": "x"}]
        self._compile_fails_with(spec, "INPLACE_REGION_OVERLAP")

    # ── Placeholder residue double baseline ──
    def test_placeholder_residue_unhandled(self):
        spec = self._spec()
        spec["mapping"]["targets"][0]["nulls"] = [{"col": "D", "rows": "all"}]  # F uncovered
        self._compile_fails_with(spec, "PLACEHOLDER_RESIDUE_UNHANDLED")

    def test_placeholder_residue_partial_nulls(self):
        spec = self._spec()
        spec["mapping"]["targets"][0]["nulls"] = [
            {"col": "D", "rows": "all"}, {"col": "F", "rows": [1]}]
        self._compile_fails_with(spec, "PLACEHOLDER_RESIDUE_PARTIAL_NULLS")

    # ── group_merges lowering ──
    def test_group_merges_inplace_rebuild(self):
        plan = self._compile(self._spec())
        merges = [op["props"]["merge"] for op in plan["operations"]
                  if op.get("props", {}).get("merge")]
        # 家用×2 group merges A7:A8; 商用 singleton never merges
        self.assertEqual(merges, ["A7:A8"])
        gb = plan["group_boundaries"][0]
        self.assertEqual(gb["col"], "A")
        self.assertEqual(gb["region_start"], 7)
        self.assertEqual(gb["region_end"], 9)
        self.assertEqual(gb["expected_merges"], ["A7:A8"])
        # anchors written, non-anchors explicitly cleared
        writes = {op["path"]: op["props"].get("value") for op in plan["operations"]
                  if op["command"] == "set" and "value" in op["props"]
                  and op["path"].startswith("/S/A")}
        self.assertEqual(writes["/S/A7"], "家用")
        self.assertEqual(writes["/S/A9"], "商用")
        self.assertIsNone(writes["/S/A8"])
        self.assertNotIn("/S/A7", [rb["path"] for rb in plan["readback"]
                                   if rb["kind"] == "empty"])

    def test_group_merges_append_block_general_capability(self):
        """Q8.6: group_merges is a general block capability (append included)."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            wd = make_workdir(tmp)
            wd["workdir"] = tmp
            spec = spec_with(wd)
            spec["mapping"]["targets"][0]["group_merges"] = [{"col": "A", "group_by": "A"}]
            plan = compile_fill.compile_spec(spec, wd["manifest"], tmp)
            merges = [op["props"]["merge"] for op in plan["operations"]
                      if op.get("props", {}).get("merge")]
            self.assertEqual(merges, ["A7:A8"])  # rows 7-8 家用; row 9 singleton
            gb = plan["group_boundaries"][0]
            self.assertEqual(gb["region_start"], 7)
            self.assertEqual(gb["region_end"], 9)

    def test_group_merge_anchor_uncovered(self):
        spec = self._spec()
        spec["mapping"]["targets"][0]["group_merges"] = [{"col": "F", "group_by": "A"}]
        spec["mapping"]["targets"][0]["nulls"] = [{"col": "D", "rows": "all"}]
        self._compile_fails_with(spec, "GROUP_MERGE_ANCHOR_UNCOVERED")

    def test_group_by_column_unmapped(self):
        spec = self._spec()
        spec["mapping"]["targets"][0]["group_merges"] = [{"col": "A", "group_by": "F"}]
        self._compile_fails_with(spec, "GROUP_BY_COLUMN_UNMAPPED")

    def test_merge_mode_conflict(self):
        spec = self._spec()
        spec["mapping"]["targets"][0]["merges"] = [{"col": "A", "rows": "1:{n}"}]
        self._compile_fails_with(spec, "MERGE_MODE_CONFLICT")

    # ── sets ──
    def test_sets_write_clear_and_final_translation(self):
        spec = self._spec()
        spec["mapping"]["targets"][0]["sets"] = [
            {"path": "A4", "value": "To Messrs: MXP"},
            {"path": "F13", "value": None},
            {"path": "A14", "value": "* ship to Algeria"},
        ]
        spec["validation"]["key_outputs"] = ["A7", "A14"]
        plan = self._compile(spec)
        ops_by_path = {op["path"]: op for op in plan["operations"]
                       if op["command"] == "set"}
        # ops execute at TEMPLATE coordinates (before the trim shift)
        self.assertEqual(ops_by_path["/S/A4"]["props"]["value"], "To Messrs: MXP")
        self.assertEqual(ops_by_path["/S/F13"]["props"]["value"], None)
        self.assertEqual(ops_by_path["/S/A14"]["props"]["value"], "* ship to Algeria")
        # readback/registry use FINAL coordinates (A14→13, F13→12)
        rb = {rb["path"]: rb for rb in plan["readback"]}
        self.assertIn("/S/A4", rb)
        self.assertIn("/S/A13", rb)
        self.assertEqual(rb["/S/A13"]["expect"], "* ship to Algeria")
        self.assertIn("/S/F12", rb)
        self.assertEqual(rb["/S/F12"]["kind"], "empty")
        # key_outputs translated: A14 → final A13
        self.assertIn({"path": "/S/A13", "kind": "value"}, plan["key_outputs"])
        self.assertEqual([s["path"] for s in plan["sets"]],
                         ["/S/A4", "/S/F12", "/S/A13"])

    def test_sets_out_of_bounds_rejected(self):
        spec = self._spec()
        spec["mapping"]["targets"][0]["sets"] = [{"path": "A99", "value": "x"}]
        self._compile_fails_with(spec, "SET_OUT_OF_BOUNDS")

    def test_sets_duplicate_write_rejected(self):
        spec = self._spec()
        spec["mapping"]["targets"][0]["sets"] = [
            {"path": "A4", "value": "x"}, {"path": "A4", "value": "y"}]
        self._compile_fails_with(spec, "DUPLICATE_TARGET_WRITE")

    # ── props whitelist ──
    def test_column_numberformat_props_applied(self):
        spec = self._spec()
        spec["mapping"]["targets"][0]["columns"] = [
            {"source": "A", "target": "A"},
            {"source": "B", "target": "B"},
            {"source": "C", "target": "C"},
            {"source": "D", "target": "E", "props": {"numberformat": "$#,##0.00"}},
        ]
        plan = self._compile(spec)
        e_ops = [op for op in plan["operations"] if op.get("path", "").endswith(("/E7", "/E8", "/E9"))]
        self.assertEqual(len(e_ops), 3)
        self.assertTrue(all(op["props"].get("numberformat") == "$#,##0.00" for op in e_ops))

    def test_props_whitelist_violation_rejected(self):
        spec = self._spec()
        spec["mapping"]["targets"][0]["columns"].append(
            {"source": "D", "target": "F", "props": {"font.bold": True}})
        self._compile_fails_with(spec, "PROPS_WHITELIST_VIOLATION")

    def test_set_props_numberformat_legal_with_null(self):
        spec = self._spec()
        spec["mapping"]["targets"][0]["sets"] = [
            {"path": "E13", "value": None, "props": {"numberformat": "$#,##0.00"}}]
        plan = self._compile(spec)
        op = next(op for op in plan["operations"] if op["command"] == "set"
                  and op["path"] == "/S/E13")
        self.assertEqual(op["props"]["value"], None)
        self.assertIn("/S/E12", [rb["path"] for rb in plan["readback"] if rb["kind"] == "empty"])

    # ── mapping features added for the MXP scenario ──
    def test_fallback_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            wd = make_workdir(tmp)
            with open(tmp / "source_maoli_flat.csv", "w", newline="", encoding="utf-8-sig") as f:
                csv.writer(f).writerow(["家用", "12K", "Z001", "", "C-1", "1", "2", "3", "101"])
            wd["workdir"] = tmp
            spec = spec_with(wd)
            spec["mapping"]["targets"][0]["columns"] = [
                {"source": "A", "target": "A"},
                {"source": "B", "target": "B"},
                {"source": "C", "target": "C"},
                {"source": "D", "target": "E", "fallback": "B"},
            ]
            plan = compile_fill.compile_spec(spec, wd["manifest"], tmp)
            e_vals = [w["value"] for w in plan["writes"] if w["col"] == "E"]
            self.assertEqual(e_vals, ["12K"])  # D empty → falls back to B

    def test_transforms_chain(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            wd = make_workdir(tmp)
            wd["workdir"] = tmp
            spec = spec_with(wd)
            spec["mapping"]["transforms"] = [
                {"name": "rename_z", "function": "regex_replace",
                 "pattern": "^Z001$", "replacement": "ZED"},
                {"name": "strip", "function": "strip"},
            ]
            spec["mapping"]["targets"][0]["columns"] = [
                {"source": "A", "target": "A"},
                {"source": "B", "target": "B"},
                {"source": "C", "target": "E", "transforms": ["rename_z", "strip"]},
            ]
            spec["mapping"]["targets"][0]["nulls"] = [{"col": "C", "rows": "all"},
                                                      {"col": "D", "rows": "all"}]
            plan = compile_fill.compile_spec(spec, wd["manifest"], tmp)
            e_vals = [w["value"] for w in plan["writes"] if w["col"] == "E"]
            self.assertEqual(e_vals, ["ZED", "Z002", "Z003"])

    # ── pptx DOM-path sets ──
    def test_pptx_dom_path_sets(self):
        ops = []
        records = []
        compile_fill._emit_sets(
            {"sheet": "slide[1]/table[@id=1]",
             "sets": [{"path": "/slide[1]/table[@id=1]/tr[2]/tc[3]", "value": "x"}]},
            None, None, None, ops, lambda p, k, v: records.append((p, k, v)),
            [], 6, 3)
        self.assertEqual(ops[0]["props"], {"text": "x"})
        self.assertEqual(records, [("/slide[1]/table[@id=1]/tr[2]/tc[3]", "value", "x")])


class FillSpecContractTests(unittest.TestCase):
    """组合行为契约 (FILLSPEC「组合行为契约」章节) 的编译用例背书.

    只测外部行为: 照契约声明写的 spec, 编译器接受 (编译通过) 或按文档
    声明的错误码拒绝. 每条契约声明都对应一个用例, 防止文档与编译器行为漂移.
    """

    def setUp(self):
        self.tmp_ctx = tempfile.TemporaryDirectory()
        self.tmp = Path(self.tmp_ctx.name)
        self.wd = make_workdir(self.tmp)
        self.wd["workdir"] = self.tmp

    def tearDown(self):
        self.tmp_ctx.cleanup()

    def _fail_codes(self, spec) -> list[str]:
        return compile_fail_codes(self.wd, spec)

    def _fail_payload(self, spec) -> dict:
        """Compile-fail payload (parsed stderr JSON); asserts exit 3."""
        from io import StringIO
        buf = StringIO()
        old = sys.stderr
        sys.stderr = buf
        try:
            with self.assertRaises(SystemExit) as ctx:
                compile_fill.compile_spec(spec, self.wd["manifest"],
                                          self.wd["workdir"])
            self.assertEqual(ctx.exception.code, 3)
        finally:
            sys.stderr = old
        return json.loads(buf.getvalue())

    # ── Q1: group_merges × formulas / aggregates ──
    def test_group_merges_plus_aggregate_different_column(self):
        """聚合在独立列: 一等支持 — 编译通过, 锚点聚合公式写在块首行."""
        spec = spec_with(self.wd)
        spec["mapping"]["targets"][0]["group_merges"] = [{"col": "A", "group_by": "A"}]
        spec["mapping"]["targets"][0]["formulas"] = {
            "aggregates": [{"col": "G", "rows": "1:{n}",
                            "formula": "SUM(A{r1}:A{r2})", "style": "anchor"}]}
        self.assertEqual(self._fail_codes(spec), [])
        plan = compile_spec_with(self.wd, spec)
        agg = [op for op in plan["operations"] if op["command"] == "set"
               and "formula" in op.get("props", {}) and "SUM" in op["props"]["formula"]]
        self.assertEqual([op["path"] for op in agg], ["/S/G7"])
        self.assertEqual(agg[0]["props"]["formula"], "SUM(A7:A9)")

    def test_group_merges_and_aggregate_same_column_duplicate_write(self):
        """同列: 组锚点写与聚合锚点写都落在块首行 → DUPLICATE_TARGET_WRITE."""
        spec = spec_with(self.wd)
        spec["mapping"]["targets"][0]["group_merges"] = [{"col": "A", "group_by": "A"}]
        spec["mapping"]["targets"][0]["formulas"] = {
            "aggregates": [{"col": "A", "rows": "1:{n}",
                            "formula": "SUM(A{r1}:A{r2})", "style": "anchor"}]}
        self.assertIn("DUPLICATE_TARGET_WRITE", self._fail_codes(spec))
    def test_group_merges_and_per_row_formula_same_column_duplicate_write(self):
        """同列: group lowering 拥有该列每一行 (锚点写/非锚点清空), 与 per_row
        公式冲突 → DUPLICATE_TARGET_WRITE."""
        spec = spec_with(self.wd)
        spec["mapping"]["targets"][0]["group_merges"] = [{"col": "A", "group_by": "A"}]
        spec["mapping"]["targets"][0]["formulas"] = {"per_row": {"A": "{r}*2"}}
        self.assertIn("DUPLICATE_TARGET_WRITE", self._fail_codes(spec))

    def test_aggregate_and_per_row_formula_same_column_duplicate_write(self):
        """聚合与逐行公式同列: 首行锚点格双写 → DUPLICATE_TARGET_WRITE."""
        spec = spec_with(self.wd)
        spec["mapping"]["targets"][0]["formulas"] = {
            "per_row": {"E": "A{r}*2"},
            "aggregates": [{"col": "E", "rows": "1:{n}",
                            "formula": "SUM(A{r1}:A{r2})", "style": "anchor"}]}
        self.assertIn("DUPLICATE_TARGET_WRITE", self._fail_codes(spec))

    def test_nulls_rows_invalid_list_of_ranges_rejected(self):
        """nulls rows 用 ['1:2','3:4'] 混合列表 → NULLS_ROWS_INVALID 结构化
        拒绝 (曾以 Python traceback 崩溃)."""
        spec = spec_with(self.wd)
        spec["mapping"]["targets"][0]["nulls"] = [
            {"col": "D", "rows": ["1:2", "3:4"]}]
        codes = self._fail_codes(spec)
        self.assertIn("NULLS_ROWS_INVALID", codes)

    def test_null_specs_valid_forms_pass(self):
        """nulls rows 合法形态 ('all' / int 列表 / 'a:b' 字符串) 不被
        NULLS_ROWS_INVALID 拒绝 (残留/部分覆盖仍按既有规则报错)."""
        for rows in ("all", [1, 3], "2:4"):
            spec = spec_with(self.wd)
            spec["mapping"]["targets"][0]["nulls"] = [{"col": "D", "rows": rows}]
            codes = self._fail_codes(spec)
            self.assertNotIn("NULLS_ROWS_INVALID", codes, f"rows={rows!r}")

    def test_plain_merges_register_no_readback(self):
        """Q10: plain merges (1:{n}) 只写 merge 属性, 不产生 readback 条目."""
        spec = spec_with(self.wd)
        spec["mapping"]["targets"][0]["merges"] = [
            {"col": "E", "rows": "1:{n}", "style": "label"}]
        self.assertEqual(self._fail_codes(spec), [])
        plan = compile_spec_with(self.wd, spec)
        e_readback = [rb for rb in plan["readback"] if rb["path"].startswith("/S/E")]
        self.assertEqual(e_readback, [])
        merges = [op["props"]["merge"] for op in plan["operations"]
                  if op.get("props", {}).get("merge")]
        self.assertEqual(merges, ["E7:E9"])

    def test_title_clone_from_anchor_row_ok(self):
        """Q8: title/header 的 template_row 选锚点行无编译检查 (编译通过),
        data 的 template_row 选锚点行 → CLONE_SOURCE_IS_ANCHOR."""
        spec = spec_with(self.wd)
        spec["mapping"]["targets"][0]["clone_roles"] = [
            {"role": "title", "template_row": 5, "value": "块标题"},  # A5:A6 锚点
            {"role": "header", "template_row": 2},
            {"role": "data", "template_row": 3},
        ]
        self.assertEqual(self._fail_codes(spec), [])
        spec2 = spec_with(self.wd)
        spec2["mapping"]["targets"][0]["clone_roles"][2]["template_row"] = 5
        self.assertIn("CLONE_SOURCE_IS_ANCHOR", self._fail_codes(spec2))

    def test_title_value_deferred_to_fill_phase(self):
        """Q9: 标题 value 延迟到 adds 之后写入 (op 顺序恒为 add→...→set)."""
        spec = spec_with(self.wd)
        spec["mapping"]["targets"][0]["clone_roles"] = [
            {"role": "title", "template_row": 1, "value": "块标题"},
            {"role": "header", "template_row": 2},
            {"role": "data", "template_row": 3},
        ]
        self.assertEqual(self._fail_codes(spec), [])
        plan = compile_spec_with(self.wd, spec)
        kinds = [op["command"] for op in plan["operations"]]
        first_set = kinds.index("set")
        self.assertNotIn("set", kinds[:first_set])
        self.assertGreater(kinds.count("add"), 0)

    def test_merges_and_aggregates_same_column_ok(self):
        """Q12: merges 1:{n} + aggregates 1:{n} 同列 — 编译通过
        (聚合锚点 = 块首行 = 合并锚点)."""
        spec = spec_with(self.wd)
        spec["mapping"]["targets"][0]["merges"] = [
            {"col": "E", "rows": "1:{n}", "style": "label"}]
        spec["mapping"]["targets"][0]["formulas"] = {
            "aggregates": [{"col": "E", "rows": "1:{n}",
                            "formula": "SUM(A{r1}:A{r2})", "style": "anchor"}]}
        self.assertEqual(self._fail_codes(spec), [])

    def test_multi_range_aggregates_same_column(self):
        """Q12: 同列多条显式范围聚合 — 编译通过, 每条落在各自显式行
        (块内多组小计行写法)."""
        spec = spec_with(self.wd)
        spec["mapping"]["targets"][0]["formulas"] = {
            "aggregates": [
                {"col": "E", "rows": "1:2",
                 "formula": "SUM(A{r1}:A{r2})", "style": "anchor"},
                {"col": "E", "rows": "3:3",
                 "formula": "SUM(A{r1}:A{r2})", "style": "anchor"},
            ]}
        self.assertEqual(self._fail_codes(spec), [])
        plan = compile_spec_with(self.wd, spec)
        agg_paths = [op["path"] for op in plan["operations"]
                     if "formula" in op.get("props", {}) and "SUM" in op["props"]["formula"]]
        self.assertEqual(agg_paths, ["/S/E7", "/S/E9"])

    def test_merges_and_per_row_formula_same_column_ok(self):
        """merges (1:{n}) 只写 merge 属性不写值 → 无映射的列上可与 per_row
        公式共存 — 编译通过."""
        spec = spec_with(self.wd)
        spec["mapping"]["targets"][0]["merges"] = [
            {"col": "E", "rows": "1:{n}", "style": "label"}]
        spec["mapping"]["targets"][0]["formulas"] = {"per_row": {"E": "A{r}*2"}}
        self.assertEqual(self._fail_codes(spec), [])

    # ── Q13: group_aggregates 一等能力 (组聚合写组锚点行) ──
    def test_group_aggregates_egypt_3_groups_anchors(self):
        """埃及等价: 3 产品组 (家用×2 / 商用×2 / 工程×1) + V 列组聚合 — 编译
        通过, 聚合公式落各组锚点行 ({r1}:{r2} 按组起止展开), readback 自动
        登记各锚点 nonempty."""
        wd = make_egypt_workdir(self.tmp)
        wd["workdir"] = self.tmp
        spec = spec_with(wd)
        spec["fingerprints"] = wd["manifest"]["fingerprints"]
        spec["mapping"]["targets"][0]["formulas"] = {
            "group_aggregates": [
                {"group_by": "A", "col": "V",
                 "formula": "IFERROR(ROUND(SUM(T{r1}:T{r2})/SUM(S{r1}:S{r2}),4),0)",
                 "style": "anchor"}]}
        self.assertEqual(compile_fail_codes(wd, spec), [])
        plan = compile_spec_with(wd, spec)
        agg = [op for op in plan["operations"] if op["command"] == "set"
               and op["path"].startswith("/S/V")
               and "formula" in op.get("props", {})]
        # 数据行 7-11, 组 (1,2)/(3,4)/(5,5) → 锚点 V7/V9/V11
        self.assertEqual([op["path"] for op in agg], ["/S/V7", "/S/V9", "/S/V11"])
        self.assertEqual(agg[0]["props"]["formula"],
                         "IFERROR(ROUND(SUM(T7:T8)/SUM(S7:S8),4),0)")
        self.assertEqual(agg[1]["props"]["formula"],
                         "IFERROR(ROUND(SUM(T9:T10)/SUM(S9:S10),4),0)")
        self.assertEqual(agg[2]["props"]["formula"],
                         "IFERROR(ROUND(SUM(T11:T11)/SUM(S11:S11),4),0)")
        ga_readback = [(rb["path"], rb["kind"]) for rb in plan["readback"]
                       if rb["path"].startswith("/S/V")]
        self.assertEqual(ga_readback,
                         [("/S/V7", "nonempty"), ("/S/V9", "nonempty"),
                          ("/S/V11", "nonempty")])
        # 验收 4 (观测形态): 每个公式的展开范围恒在数据块 7-11 内 —
        # 组范围由数据派生, 越块由编译器以 AGG_RANGE_INVALID 内部守卫拒绝
        for op in agg:
            rows = re.findall(r"(?:T|S)(\d+):(?:T|S)(\d+)", op["props"]["formula"])
            self.assertTrue(rows,
                            f"公式缺 {r'{r1}:{r2}'} 范围: {op['props']['formula']}")
            for lo, hi in rows:
                self.assertTrue(7 <= int(lo) <= int(hi) <= 11,
                                f"组范围越块: {op['props']['formula']}")

    def test_group_aggregates_missing_group_by_rejected(self):
        """group_aggregates 条目缺 group_by → GROUP_BY_COLUMN_UNMAPPED."""
        spec = spec_with(self.wd)
        spec["mapping"]["targets"][0]["formulas"] = {
            "group_aggregates": [{"col": "G",
                                  "formula": "SUM(A{r1}:A{r2})", "style": "anchor"}]}
        self.assertIn("GROUP_BY_COLUMN_UNMAPPED", self._fail_codes(spec))

    def test_group_aggregates_malformed_shape_rejected(self):
        """group_aggregates 声明形态非法 (条目非 mapping / per_group 非列表) →
        GROUP_AGGREGATES_INVALID 结构化拒绝 (曾会静默吞掉或 AttributeError
        崩溃)."""
        for formulas in (
                {"group_aggregates": ["SUM(A{r1}:A{r2})"]},
                {"group_aggregates": {"per_group": {"group_by": "A",
                                                    "col": "G",
                                                    "formula": "SUM(A{r1}:A{r2})"},
                                      "whole_run": None}},
                {"group_aggregates": "SUM(A{r1}:A{r2})"}):
            spec = spec_with(self.wd)
            spec["mapping"]["targets"][0]["formulas"] = formulas
            self.assertIn("GROUP_AGGREGATES_INVALID",
                          self._fail_codes(spec),
                          f"formulas={formulas!r}")

    def test_group_aggregates_with_group_merges_same_col_duplicate(self):
        """组聚合与 group_merges 同列: 组锚点双写 (块首行两组锚点重合) →
        DUPLICATE_TARGET_WRITE."""
        spec = spec_with(self.wd)
        spec["mapping"]["targets"][0]["group_merges"] = [
            {"col": "G", "group_by": "A", "label": "X"}]
        spec["mapping"]["targets"][0]["formulas"] = {
            "group_aggregates": [{"group_by": "A", "col": "G",
                                  "formula": "SUM(A{r1}:A{r2})", "style": "anchor"}]}
        self.assertIn("DUPLICATE_TARGET_WRITE", self._fail_codes(spec))

    def test_group_aggregates_with_per_row_same_col_duplicate(self):
        """组聚合与 per_row 公式同列: 锚点格双写 → DUPLICATE_TARGET_WRITE."""
        spec = spec_with(self.wd)
        spec["mapping"]["targets"][0]["formulas"] = {
            "per_row": {"G": "A{r}*2"},
            "group_aggregates": [{"group_by": "A", "col": "G",
                                  "formula": "SUM(A{r1}:A{r2})", "style": "anchor"}]}
        self.assertIn("DUPLICATE_TARGET_WRITE", self._fail_codes(spec))

    def test_group_aggregates_col_in_nulls_duplicate(self):
        """组聚合列进 nulls → 锚点先被 nulls 清空再写公式 (特征 "first as
        empty") → DUPLICATE_TARGET_WRITE (组聚合列必须独立于 nulls)."""
        spec = spec_with(self.wd)
        spec["mapping"]["targets"][0]["nulls"] = [
            {"col": "D", "rows": "all"}, {"col": "G", "rows": "all"}]
        spec["mapping"]["targets"][0]["formulas"] = {
            "group_aggregates": [{"group_by": "A", "col": "G",
                                  "formula": "SUM(A{r1}:A{r2})", "style": "anchor"}]}
        self.assertIn("DUPLICATE_TARGET_WRITE", self._fail_codes(spec))

    def test_group_aggregates_whole_run_gated_pre_spike(self):
        """whole_run (跨块总计) 落点语义 (末块尾部 vs 独立行) 需 spike 锁定 —
        spike 前声明 (dict 形态) → CAPABILITY_NOT_ROLLED_OUT."""
        spec = spec_with(self.wd)
        spec["mapping"]["targets"][0]["formulas"] = {
            "group_aggregates": {
                "per_group": [{"group_by": "A", "col": "G",
                               "formula": "SUM(A{r1}:A{r2})", "style": "anchor"}],
                "whole_run": {"col": "G", "formula": "SUM(A5:A9)",
                              "rows": "last_block_tail"}}}
        self.assertIn("CAPABILITY_NOT_ROLLED_OUT", self._fail_codes(spec))

    def test_group_aggregates_whole_run_list_entry_gated(self):
        """列表条目形态的 whole_run 声明同样被门拒绝 (spec 草案形态)."""
        spec = spec_with(self.wd)
        spec["mapping"]["targets"][0]["formulas"] = {
            "group_aggregates": [
                {"group_by": "A", "col": "G",
                 "formula": "SUM(A{r1}:A{r2})", "style": "anchor"},
                {"whole_run": {"col": "G", "formula": "SUM(A5:A9)",
                               "rows": "last_block_tail"}},
            ]}
        self.assertIn("CAPABILITY_NOT_ROLLED_OUT", self._fail_codes(spec))

    def test_group_aggregates_group_by_unmapped_rejected(self):
        """group_by 列无列映射 → GROUP_BY_COLUMN_UNMAPPED."""
        spec = spec_with(self.wd)
        spec["mapping"]["targets"][0]["formulas"] = {
            "group_aggregates": [{"group_by": "F", "col": "G",
                                  "formula": "SUM(A{r1}:A{r2})", "style": "anchor"}]}
        self.assertIn("GROUP_BY_COLUMN_UNMAPPED", self._fail_codes(spec))

    def test_group_aggregates_formula_template_invalid(self):
        """公式模板未知键 → FORMULA_TEMPLATE_INVALID (静态期拒绝)."""
        spec = spec_with(self.wd)
        spec["mapping"]["targets"][0]["formulas"] = {
            "group_aggregates": [{"group_by": "A", "col": "G",
                                  "formula": "SUM(A{r9}:A{r2})", "style": "anchor"}]}
        self.assertIn("FORMULA_TEMPLATE_INVALID", self._fail_codes(spec))

    # ── Q2: 算术派生列 (FLD-006 减法) 标准模式 ──
    def test_derived_column_subtraction_per_row_formula(self):
        """减法派生列标准模式: per_row formula + ROUND(...,2) 写在独立未映射列 —
        编译通过且公式按数据行展开写入 plan."""
        spec = spec_with(self.wd)
        spec["mapping"]["targets"][0]["formulas"] = {
            "per_row": {"G": "IFERROR(ROUND(A{r}-B{r},2),0)"}}
        self.assertEqual(self._fail_codes(spec), [])
        plan = compile_spec_with(self.wd, spec)
        formulas = {op["path"]: op["props"]["formula"] for op in plan["operations"]
                    if op["command"] == "set" and op.get("path", "").startswith("/S/G")}
        self.assertEqual(formulas, {
            "/S/G7": "IFERROR(ROUND(A7-B7,2),0)",
            "/S/G8": "IFERROR(ROUND(A8-B8,2),0)",
            "/S/G9": "IFERROR(ROUND(A9-B9,2),0)",
        })

    def test_derived_column_cannot_share_a_mapped_column(self):
        """派生列必须独立: 同列既有列映射又有 per_row 公式 → 双写
        DUPLICATE_TARGET_WRITE."""
        spec = spec_with(self.wd)
        spec["mapping"]["targets"][0]["columns"].append(
            {"source": "B", "target": "E"})
        spec["mapping"]["targets"][0]["formulas"] = {"per_row": {"E": "A{r}*2"}}
        self.assertIn("DUPLICATE_TARGET_WRITE", self._fail_codes(spec))

    # ── Q3: 映射列 × 合并列落值 (锚点写 / 非锚点抑制+清空) ──
    def test_mapped_group_column_anchor_writes_non_anchor_cleared(self):
        """映射列 + group_merges 同列: 一等支持 — 锚点写物化值, 非锚点抑制
        并显式清空 (EMPTY readback), singleton 组永不合并."""
        spec = spec_with(self.wd)
        spec["mapping"]["targets"][0]["group_merges"] = [{"col": "A", "group_by": "A"}]
        self.assertEqual(self._fail_codes(spec), [])
        plan = compile_spec_with(self.wd, spec)
        writes = {op["path"]: op["props"].get("value") for op in plan["operations"]
                  if op["command"] == "set" and "value" in op["props"]
                  and op["path"].startswith("/S/A")}
        self.assertEqual(writes["/S/A7"], "家用")   # 锚点写物化值
        self.assertEqual(writes["/S/A9"], "商用")
        self.assertIsNone(writes["/S/A8"])           # 非锚点抑制 (显式清空)
        empties = [rb["path"] for rb in plan["readback"] if rb["kind"] == "empty"]
        self.assertIn("/S/A8", empties)
        merges = [op["props"]["merge"] for op in plan["operations"]
                  if op.get("props", {}).get("merge")]
        self.assertEqual(merges, ["A7:A8"])

    # ── Q5: 空值 / 0-口径 ──
    def test_empty_source_value_left_blank(self):
        """空源值 → 缺失格留空 (空串写入, readback 期待空串) — 不是 EMPTY 清空."""
        with open(self.tmp / "source_maoli_flat.csv", "w", newline="",
                  encoding="utf-8-sig") as f:
            csv.writer(f).writerow(["家用", "12K", "Z001", "", "C-1", "1", "2", "3", "101"])
        spec = spec_with(self.wd)
        spec["mapping"]["targets"][0]["columns"] = [
            {"source": "A", "target": "A"},
            {"source": "B", "target": "B"},
            {"source": "C", "target": "C"},
            {"source": "D", "target": "E"}]
        self.assertEqual(self._fail_codes(spec), [])
        plan = compile_spec_with(self.wd, spec)
        e_op = next(op for op in plan["operations"] if op.get("path") == "/S/E7")
        self.assertEqual(e_op["props"]["value"], "")
        rb = next(rb for rb in plan["readback"] if rb["path"] == "/S/E7")
        self.assertEqual(rb["kind"], "value")
        self.assertEqual(rb["expect"], "")

    def test_zero_source_value_is_not_missing(self):
        """数值 0 不是缺失 — 0-口径原样写入 '0'."""
        with open(self.tmp / "source_maoli_flat.csv", "w", newline="",
                  encoding="utf-8-sig") as f:
            csv.writer(f).writerow(["家用", "12K", "Z001", "F-1", "C-1", "0", "2", "3", "101"])
        spec = spec_with(self.wd)
        spec["mapping"]["targets"][0]["columns"] = [
            {"source": "A", "target": "A"},
            {"source": "B", "target": "B"},
            {"source": "C", "target": "C"},
            {"source": "F", "target": "G"}]
        self.assertEqual(self._fail_codes(spec), [])
        plan = compile_spec_with(self.wd, spec)
        g_op = next(op for op in plan["operations"] if op.get("path") == "/S/G7")
        self.assertEqual(g_op["props"]["value"], "0")

    # ── Q6: lookup missing 语义 ──
    def test_lookup_missing_empty_leaves_blank_cell(self):
        """missing: empty → 缺失 key 的格留空 (空串 readback); 命中行正常取值."""
        spec = spec_with(self.wd)
        spec["mapping"]["targets"][0]["columns"].append(
            {"target": "F", "lookup": {"name": "fields",
                                       "field": "compressor", "missing": "empty"}})
        spec["mapping"]["targets"][0]["lookups"] = [
            {"name": "fields", "from": "inheritance.json", "key_column": "C",
             "fields": ["compressor"], "missing": "empty"}]
        self.assertEqual(self._fail_codes(spec), [])
        plan = compile_spec_with(self.wd, spec)
        rb = {rb["path"]: rb for rb in plan["readback"]}
        self.assertEqual(rb["/S/F7"]["expect"], "C-1")   # Z001 命中
        self.assertEqual(rb["/S/F9"]["expect"], "")       # Z003 缺失 → 留空

    def test_lookup_field_missing_schema_absent(self):
        """字段不在索引 schema → LOOKUP_FIELD_MISSING (missing 策略不豁免)."""
        spec = spec_with(self.wd)
        spec["mapping"]["targets"][0]["columns"].append(
            {"target": "F", "lookup": {"name": "fields",
                                       "field": "voltage", "missing": "empty"}})
        spec["mapping"]["targets"][0]["lookups"] = [
            {"name": "fields", "from": "inheritance.json", "key_column": "C",
             "fields": ["voltage"], "missing": "empty"}]
        self.assertIn("LOOKUP_FIELD_MISSING", self._fail_codes(spec))

    # ── Q7: precision: keep vs round4 ──
    def test_precision_keep_compiles(self):
        """precision: keep 显式接受长精度 — 编译通过且无警告 (round4 仍为推荐序,
        见 doc-coverage 守卫 test_fillspec_precision_recommendation_order)."""
        spec = spec_with(self.wd)
        spec["mapping"]["targets"][0]["columns"].append(
            {"target": "E", "value": "168.715100569657", "precision": "keep"})
        self.assertEqual(self._fail_codes(spec), [])
        plan = compile_spec_with(self.wd, spec)
        self.assertEqual(plan["warnings"], [])

    # ── Q8: CLONE_SOURCE_IS_ANCHOR 作用域 (只查 data role) ──
    def test_anchor_check_applies_to_data_role_only(self):
        """fixture 锚点 = A5. title/header 克隆源选锚点行无编译检查 (边界已文档化,
        FILLSPEC Q8 — 公式残留风险标注); data role 克隆源选锚点行 → 拒绝."""
        for role in ("title", "header"):
            spec = spec_with(self.wd)
            spec["mapping"]["targets"][0]["clone_roles"] = [
                {"role": role, "template_row": 5, "value": "X"},
                {"role": "data", "template_row": 7},
            ]
            self.assertEqual(self._fail_codes(spec), [],
                             f"{role} 克隆源=锚点行应无编译检查 (文档化边界)")
        spec = spec_with(self.wd)
        spec["mapping"]["targets"][0]["clone_roles"] = [
            {"role": "title", "template_row": 2, "value": "X"},
            {"role": "data", "template_row": 5},
        ]
        self.assertIn("CLONE_SOURCE_IS_ANCHOR", self._fail_codes(spec))

    # ── Q9: title/header value 延迟到 fills 阶段 (adds 之后) ──
    def test_title_value_written_after_all_adds(self):
        """标题值 op 恒在所有 add/remove 之后 (deferred_values — 防 duplicate_row:
        add 之间穿插 cell 写入破坏 officecli 行簿记)."""
        spec = spec_with(self.wd)
        spec["mapping"]["targets"][0]["clone_roles"] = [
            {"role": "spacer"},
            {"role": "title", "template_row": 1, "value": "HDR"},
            {"role": "header", "template_row": 2},
            {"role": "data", "template_row": 3},
        ]
        spec["validation"]["key_outputs"] = ["A8"]  # spacer 后移: 数据行 8-10
        plan = compile_spec_with(self.wd, spec)
        ops = plan["operations"]
        last_add = max(i for i, o in enumerate(ops) if o["command"] == "add")
        title_idx = next(i for i, o in enumerate(ops)
                         if o["command"] == "set"
                         and o.get("props", {}).get("value") == "HDR")
        self.assertGreater(title_idx, last_add)
        # add 之间不得穿插任何 cell 写入
        add_positions = [i for i, o in enumerate(ops) if o["command"] == "add"]
        for a, b in zip(add_positions, add_positions[1:]):
            self.assertTrue(all(ops[i]["command"] == "add"
                                for i in range(a + 1, b)),
                            "add 之间穿插非 add op")

    # ── Q10: readback 断言种类 (register 语义) ──
    def test_readback_kinds_by_write_source(self):
        """value→值断言; nulls/清空→EMPTY; 公式/合并锚点→nonempty; 一格一 kind."""
        spec = spec_with(self.wd)
        spec["mapping"]["targets"][0]["formulas"] = {"per_row": {"F": "A{r}*2"}}
        plan = compile_spec_with(self.wd, spec)
        by_kind = {}
        for rb in plan["readback"]:
            by_kind.setdefault(rb["kind"], []).append(rb["path"])
        self.assertIn("/S/D7", by_kind["empty"])     # nulls (BASE_SPEC D 列) → EMPTY
        self.assertIn("/S/A7", by_kind["value"])     # 列映射 → 值断言
        self.assertIn("/S/F7", by_kind["nonempty"])  # per_row 公式 → nonempty
        self.assertEqual(len({rb["path"] for rb in plan["readback"]}),
                         len(plan["readback"]), "一格一断言")

    # ── 布局决策树: append 块 remove_rows 越界 → REMOVE_TARGETS_APPEND_ZONE ──
    def test_remove_rows_beyond_base_append_zone_rejected(self):
        """埃及等价 (probe 2026-08-13): append 克隆 + remove_rows > base_last_row
        → 先行的 add 全部插在 base 之下推移行号, remove 用裸模板坐标命中刚插入的
        新数据行 (自毁 plan, 行数断言恒等抓不住) → 拒绝, 缺陷携带行号、块标签与
        拦截理由, 且不无条件指向 inplace."""
        spec = spec_with(self.wd)  # base_last_row=4, 数据行 5-7
        spec["mapping"]["targets"][0]["remove_rows"] = [5, 6, 7]
        payload = self._fail_payload(spec)
        self.assertEqual(payload["code"], "STATIC_VALIDATION_FAILED")
        defects = [d for d in payload["defects"]
                   if d["code"] == "REMOVE_TARGETS_APPEND_ZONE"]
        self.assertEqual([d["row"] for d in defects], [5, 6, 7])
        for d in defects:
            self.assertEqual(d["block"], "block[0]")
            self.assertIn("base_last_row 4", d["message"])
            self.assertIn("自毁", d["message"])
            ca = d["corrective_action"]
            self.assertIn("append-only", ca)
            self.assertIn("占位行自然下沉", ca)
            self.assertIn("inplace", ca)
            self.assertIn("样式", ca)
            self.assertLess(ca.index("append-only"), ca.index("inplace"),
                            "append-only 是首选, inplace 只能是条件选项")

    def test_remove_rows_within_base_classic_shrink_compiles(self):
        """经典场景 (源行数 < 模板行数): remove_rows ≤ base_last_row 在 add 区
        之外不被推移 → 编译通过, plan 与无 remove_rows 的基线相比仅多该 remove op
        (既有运行不受影响)."""
        baseline = compile_spec_with(self.wd, spec_with(self.wd))
        spec = spec_with(self.wd)
        spec["mapping"]["targets"][0]["remove_rows"] = [3]  # ≤ base 4
        self.assertEqual(self._fail_codes(spec), [])
        plan = compile_spec_with(self.wd, spec)
        diff = [op for op in plan["operations"] if op not in baseline["operations"]]
        self.assertEqual(len(diff), 1)
        self.assertEqual(diff[0]["command"], "remove")
        self.assertEqual(diff[0]["path"], "/S/row[3]")
        self.assertEqual(plan["structural_deltas"]["removes"], 1)
        # 执行顺序: remove 恒在全部 add 之后
        ops = plan["operations"]
        last_add = max(i for i, o in enumerate(ops) if o["command"] == "add")
        remove_idx = next(i for i, o in enumerate(ops) if o["command"] == "remove")
        self.assertGreater(remove_idx, last_add)

    def test_remove_zone_multi_block_any_out_of_bounds_rejected(self):
        """blocks[] 多块: 任一非 inplace 块 remove_rows 越界 → 拒绝, 缺陷携带
        越界块的标签."""
        spec = spec_with(self.wd)
        spec["mapping"]["targets"][0]["blocks"] = [
            {"clone_roles": [{"role": "data", "template_row": 3}],
             "rows": {"source": "source_maoli",
                      "selectors": [{"column": "A", "pattern": "家用*"}]},
             "remove_rows": [2]},   # ≤ base 4 — 合法
            {"clone_roles": [{"role": "data", "template_row": 3}],
             "rows": {"source": "source_maoli",
                      "selectors": [{"column": "A", "pattern": "商用*"}]},
             "remove_rows": [7]},   # > base 4 — 自毁
        ]
        payload = self._fail_payload(spec)
        bad = [d for d in payload["defects"]
               if d["code"] == "REMOVE_TARGETS_APPEND_ZONE"]
        self.assertEqual(len(bad), 1)
        self.assertEqual(bad[0]["block"], "block[1]")
        self.assertEqual(bad[0]["row"], 7)

    def test_remove_zone_multi_block_all_legal_compiles(self):
        """blocks[] 多块: 全部 ≤ base_last_row → 编译通过, 每块 remove op 依块序
        自底向上生成."""
        spec = spec_with(self.wd)
        spec["mapping"]["targets"][0]["blocks"] = [
            {"clone_roles": [{"role": "data", "template_row": 3}],
             "rows": {"source": "source_maoli",
                      "selectors": [{"column": "A", "pattern": "家用*"}]},
             "remove_rows": [2]},
            {"clone_roles": [{"role": "data", "template_row": 3}],
             "rows": {"source": "source_maoli",
                      "selectors": [{"column": "A", "pattern": "商用*"}]},
             "remove_rows": [3]},
        ]
        self.assertEqual(self._fail_codes(spec), [])
        plan = compile_spec_with(self.wd, spec)
        removes = [op["path"] for op in plan["operations"]
                   if op["command"] == "remove"]
        self.assertEqual(removes, ["/S/row[2]", "/S/row[3]"])

    def test_remove_zone_skips_inplace_block(self):
        """inplace 块消费编译器推导的 Trim, 不消费 remove_rows — 不在检查范围
        (remove_rows 越界值也不触发 REMOVE_TARGETS_APPEND_ZONE)."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            wd = make_inplace_workdir(tmp)
            wd["workdir"] = tmp
            spec = copy.deepcopy(INPLACE_BASE_SPEC)
            spec["fingerprints"] = wd["manifest"]["fingerprints"]
            spec["mapping"]["targets"][0]["remove_rows"] = [15]  # > base 14
            codes = compile_fail_codes(wd, spec)
            self.assertNotIn("REMOVE_TARGETS_APPEND_ZONE", codes)
            compile_fill.compile_spec(spec, wd["manifest"], tmp)  # 编译通过


class ExecutionOrderContractTests(unittest.TestCase):
    """「执行顺序保证」契约 (FILLSPEC 章节) 的编译用例背书 — 文档声称与编译器
    行为 lockstep: op 全局顺序不变量 (E1) / remove 目标身份 (E2) / 自底向上
    (E3) / 坐标翻译边界 (E4) / 机械事实派生 (mechanical_facts + mapping.md)."""

    def setUp(self):
        self.tmp_ctx = tempfile.TemporaryDirectory()
        self.tmp = Path(self.tmp_ctx.name)
        self.wd = make_workdir(self.tmp)
        self.wd["workdir"] = self.tmp

    def tearDown(self):
        self.tmp_ctx.cleanup()

    def _compile(self, spec):
        return compile_fill.compile_spec(spec, self.wd["manifest"], self.wd["workdir"])

    def _remove_rows_of(self, plan) -> list[int]:
        return [int(re.search(r"/row\[(\d+)\]$", op["path"]).group(1))
                for op in plan["operations"] if op["command"] == "remove"]

    # ── E1: op 全局顺序不变量 (clear → add → remove → merge → fill) ──
    def test_global_op_order_invariant_append_only(self):
        """E1 (append-only 形态): 全局序列 = clear → add → remove → merge → fill —
        add 序列连续 (之间零 cell 写入 — 值写入穿插 add 破坏行簿记 →
        duplicate_row); 全部 remove 在最后一个 add 之后; merge 属性写入与值写入
        全部在全部结构操作之后."""
        spec = spec_with(self.wd)
        spec["mapping"]["targets"][0]["clone_roles"] = [
            {"role": "title", "template_row": 1, "value": "HDR"},
            {"role": "header", "template_row": 2},
            {"role": "data", "template_row": 3},
        ]
        spec["mapping"]["targets"][0]["remove_rows"] = [3]
        spec["mapping"]["targets"][0]["formulas"] = {
            "per_row": {"E": "A{r}*2"},
            "aggregates": [{"col": "F", "rows": "1:{n}",
                            "formula": "SUM(E{r1}:E{r2})", "style": "anchor"}]}
        spec["mapping"]["targets"][0]["merges"] = [
            {"col": "D", "rows": "1:{n}", "style": "label"}]
        plan = self._compile(spec)
        ops = plan["operations"]
        add_idx = [i for i, o in enumerate(ops) if o["command"] == "add"]
        remove_idx = [i for i, o in enumerate(ops) if o["command"] == "remove"]
        set_idx = [i for i, o in enumerate(ops) if o["command"] == "set"]
        # 任意两个 add 之间零 cell 写入
        for a, b in zip(add_idx, add_idx[1:]):
            self.assertTrue(all(ops[i]["command"] == "add" for i in range(a + 1, b)),
                            "add 之间穿插非 add op — 破坏行簿记 (duplicate_row)")
        # 全部 remove 在最后一个 add 之后
        self.assertTrue(remove_idx, "本 spec 应有 remove")
        self.assertGreater(max(remove_idx), max(add_idx))
        # merge 属性写入与值写入全部在最后一个结构操作之后 (append-only 成立)
        merge_set_idx = [i for i in set_idx if "merge" in ops[i].get("props", {})]
        value_set_idx = [i for i in set_idx
                         if "value" in ops[i].get("props", {})
                         or "formula" in ops[i].get("props", {})]
        last_struct = max(add_idx + remove_idx)
        self.assertTrue(merge_set_idx and value_set_idx, "本 spec 应有 merge 与值写入")
        self.assertTrue(all(i > last_struct for i in merge_set_idx + value_set_idx),
                        "append-only: merge/值写入必须全部在 add/remove 之后")

    def test_inplace_hybrid_op_order(self):
        """E1 (inplace 混合形态): 全局序列 = append 块全部操作 → sets → 终末
        inplace 结构操作 (overflow add / trim remove) → inplace 值操作. sets 是
        值写却先于 inplace 结构操作 — 位置由 Excel 行移位搬移 (契约精确化,
        FILLSPEC「执行顺序保证」E1 与「v2.5: Row Layout Mode」同源)."""
        # trim 形态 (N=3 < capacity 4): sets → trim removes → 值写, 无 add
        wd2 = make_inplace_workdir(self.tmp, n_source_rows=3)
        wd2["workdir"] = self.tmp
        spec2 = copy.deepcopy(INPLACE_BASE_SPEC)
        spec2["fingerprints"] = wd2["manifest"]["fingerprints"]
        spec2["mapping"]["targets"][0]["sets"] = [
            {"path": "A4", "value": "To Messrs: MXP"}]
        ops2 = compile_fill.compile_spec(spec2, wd2["manifest"], self.tmp)["operations"]
        set_a4 = next(i for i, o in enumerate(ops2) if o.get("path") == "/S/A4")
        remove_idx = [i for i, o in enumerate(ops2) if o["command"] == "remove"]
        self.assertEqual([o["command"] for o in ops2[:set_a4]], [],
                         "sets 先于 inplace 结构操作 (trim 前执行, 由移位搬移)")
        self.assertGreater(min(remove_idx), set_a4, "trim removes 在 sets 之后")
        self.assertEqual([o["command"] for o in ops2[max(remove_idx) + 1:]], ["set"] * len(ops2[max(remove_idx) + 1:]),
                         "trim removes 之后只剩 inplace 值写, 无结构 op")
        # overflow 形态 (N=5 > capacity 4): sets → overflow add → 值写, 无 remove
        wd3 = make_inplace_workdir(self.tmp, n_source_rows=5)
        wd3["workdir"] = self.tmp
        spec3 = copy.deepcopy(INPLACE_BASE_SPEC)
        spec3["fingerprints"] = wd3["manifest"]["fingerprints"]
        spec3["mapping"]["targets"][0]["sets"] = [
            {"path": "A4", "value": "To Messrs: MXP"}]
        ops3 = compile_fill.compile_spec(spec3, wd3["manifest"], self.tmp)["operations"]
        set_a4 = next(i for i, o in enumerate(ops3) if o.get("path") == "/S/A4")
        add_idx = [i for i, o in enumerate(ops3) if o["command"] == "add"]
        self.assertEqual(len(add_idx), 1, "N=5 → 1 overflow 克隆")
        self.assertGreater(min(add_idx), set_a4, "overflow add 在 sets 之后")
        self.assertEqual([o["command"] for o in ops3[max(add_idx) + 1:]],
                         ["set"] * len(ops3[max(add_idx) + 1:]),
                         "overflow add 之后只剩 inplace 值写, 无结构 op")

    # ── E2: add 之后 remove 的目标身份 ──
    def test_remove_targets_template_coordinates_not_shifted(self):
        """E2: remove_rows 是模板坐标, 不随 add 推移 — remove op 的 path 精确
        等于 spec 声明的行号; 被删行全部 ≤ base_last_row; add 插入行全部 >
        base_last_row (两者无交集, remove 永不命中刚插入的新数据行)."""
        spec = spec_with(self.wd)  # base_last_row=4, 数据行 5-7
        spec["mapping"]["targets"][0]["remove_rows"] = [2, 3]
        plan = self._compile(spec)
        base = spec["mapping"]["targets"][0]["base_last_row"]
        removes = self._remove_rows_of(plan)
        self.assertEqual(sorted(removes), [2, 3],
                         "remove op 行号必须 == spec 声明的模板坐标")
        self.assertTrue(all(r <= base for r in removes))
        insert_rows = []
        for op in plan["operations"]:
            if op["command"] == "add" and "after" in op:
                anchor = int(re.search(r"/row\[(\d+)\]$", op["after"]).group(1))
                insert_rows.append(anchor + 1)
        self.assertTrue(all(r > base for r in insert_rows),
                        "add 全部插在 base_last_row 之下 (append 区)")
        self.assertEqual(set(removes) & set(insert_rows), set(),
                         "remove 目标与 add 插入行无交集")
        mf = plan["mechanical_facts"]
        self.assertTrue(mf["removes"]["all_within_base"])
        self.assertIn("不被 add 推移", mf["removes"]["conclusion"])
        self.assertTrue(all(r > base for r in mf["add_zone"]["append_insert_rows"]),
                        "机械事实: append 插入行全部在 base_last_row 之下")
        self.assertEqual(mf["add_zone"]["overflow_insert_rows"], [],
                         "append-only plan 无 overflow 克隆")

    # ── E3: remove 底上序 ──
    def test_removes_bottom_up_order(self):
        """E3: remove 自底向上 — 每个 append 块内 remove_rows 按行号降序生成
        (先删上行会让按位置解析的执行器下行坐标失效); inplace Trim 同理由
        自底向上 (尾部裁剪)."""
        spec = spec_with(self.wd)
        spec["mapping"]["targets"][0]["remove_rows"] = [1, 2, 3, 4]
        plan = self._compile(spec)
        self.assertEqual(self._remove_rows_of(plan), [4, 3, 2, 1])
        self.assertTrue(plan["mechanical_facts"]["removes"]["bottom_up"])
        # inplace Trim: region 7-10, N=2 → 尾部裁剪 10, 9 (自底向上)
        wd2 = make_inplace_workdir(self.tmp, n_source_rows=2)
        wd2["workdir"] = self.tmp
        spec2 = copy.deepcopy(INPLACE_BASE_SPEC)
        spec2["fingerprints"] = wd2["manifest"]["fingerprints"]
        plan2 = compile_fill.compile_spec(spec2, wd2["manifest"], self.tmp)
        self.assertEqual(self._remove_rows_of(plan2), [10, 9])
        self.assertEqual(plan2["mechanical_facts"]["trim"]["rows"], [10, 9])
        self.assertIn("自底向上", plan2["mechanical_facts"]["trim"]["conclusion"])

    # ── E4: 坐标翻译边界 ──
    def test_ops_template_readback_final_coordinates(self):
        """E4: ops 用模板坐标, readback 用最终坐标. append-only 无移位 →
        readback 坐标 == op 坐标; inplace trim → set op 保持模板坐标, readback /
        sets 记录翻译为最终坐标 (区下所有行 −1)."""
        # append-only: 无 inplace → 无行移位
        plan = self._compile(spec_with(self.wd))
        mf = plan["mechanical_facts"]
        self.assertFalse(mf["shift"]["present"])
        op_rows = {int(re.search(r"/([A-Z]+)(\d+)$", op["path"]).group(2))
                   for op in plan["operations"] if op["command"] == "set"}
        rb_rows = {int(re.search(r"/([A-Z]+)(\d+)$", rb["path"]).group(2))
                   for rb in plan["readback"]}
        self.assertEqual(op_rows, rb_rows, "无移位时 readback 坐标应等于模板坐标")
        # inplace trim: 3 数据行, capacity 4 → trim 1, shift −1
        wd2 = make_inplace_workdir(self.tmp, n_source_rows=3)
        wd2["workdir"] = self.tmp
        spec2 = copy.deepcopy(INPLACE_BASE_SPEC)
        spec2["fingerprints"] = wd2["manifest"]["fingerprints"]
        spec2["mapping"]["targets"][0]["sets"] = [
            {"path": "A4", "value": "To Messrs: MXP"},
            {"path": "A14", "value": "* ship to Algeria"},
        ]
        plan2 = compile_fill.compile_spec(spec2, wd2["manifest"], self.tmp)
        mf2 = plan2["mechanical_facts"]
        self.assertTrue(mf2["shift"]["present"])
        self.assertEqual(mf2["shift"]["region"], "7-10")
        self.assertEqual(mf2["shift"]["value"], -1)
        self.assertTrue(mf2["shift"]["readback_translated"])
        # op path 保持模板坐标
        set_paths = {op["path"] for op in plan2["operations"]
                     if op["command"] == "set" and op["path"].endswith(("A4", "A14"))}
        self.assertEqual(set_paths, {"/S/A4", "/S/A14"})
        # readback 与 sets 记录翻译为最终坐标 (A14 → A13)
        rb = {rb["path"] for rb in plan2["readback"]}
        self.assertIn("/S/A13", rb)
        self.assertNotIn("/S/A14", rb)
        self.assertEqual([s["path"] for s in plan2["sets"]], ["/S/A4", "/S/A13"])

    # ── 机械事实栏 (从契约派生, 非自由文本) ──
    def test_mechanical_facts_derived_not_free_text(self):
        """mechanical_facts 与 ops/布局机械一致: 对同一 plan 重算 removes、
        锚点链与 add 插入行, 断言事实栏 == 重算结果 (机械派生, 非自由文本)."""
        spec = spec_with(self.wd)
        spec["mapping"]["targets"][0]["remove_rows"] = [3]
        plan = self._compile(spec)
        mf = plan["mechanical_facts"]
        self.assertEqual(mf["removes"]["rows"], sorted(self._remove_rows_of(plan)))
        self.assertEqual(mf["base_last_row"],
                         spec["mapping"]["targets"][0]["base_last_row"])
        after_rows = sorted({int(re.search(r"/row\[(\d+)\]$", op["after"]).group(1))
                             for op in plan["operations"]
                             if op["command"] == "add" and "after" in op})
        self.assertEqual(mf["anchor_chain"]["after_rows"], after_rows)
        insert_rows = [a + 1 for a in after_rows]
        self.assertEqual(mf["add_zone"]["append_insert_rows"], sorted(set(insert_rows)))
        self.assertEqual(mf["add_zone"]["overflow_insert_rows"], [])
        # 契约声明的顺序不变量与 plan 一致
        self.assertEqual(mf["op_order_invariant"], "clear → add → remove → merge → fill")

    def test_mapping_md_mechanical_facts_rendered(self):
        """mapping.md「执行机械事实」栏由 mechanical_facts 派生渲染 — 对 append
        运行, removes ≤ base 的结论显式可见 (验收标准: 埃及案例 Agent 不读源码
        即可回答 remove/add 交互问题)."""
        spec = spec_with(self.wd)
        spec["mapping"]["targets"][0]["remove_rows"] = [3]
        plan = self._compile(spec)
        md = compile_fill.render_mapping(spec, plan, self.wd["manifest"])
        self.assertIn("## 执行机械事实", md)
        self.assertIn("clear → add → remove → merge → fill", md)
        self.assertIn("removes: [3]", md)
        self.assertIn("不被 add 推移", md)
        self.assertIn("readback 坐标 == 模板坐标", md)
        self.assertIn("TEMPLATE_ROW_GAP", md)

    def test_op_order_invariant_two_forms(self):
        """E1 两形态 (2026-08-13 修正): mechanical_facts.op_order_invariant 按
        plan 形态派生 — append-only → clear→add→remove→merge→fill; inplace
        混合 → append 块全部操作→sets→inplace 结构→inplace 值写. 契约字符串
        与 FILLSPEC E1 同源 (防止回退到单一恒式)."""
        # append-only 形态
        plan = self._compile(spec_with(self.wd))
        self.assertEqual(plan["mechanical_facts"]["op_order_invariant"],
                         "clear → add → remove → merge → fill")
        # inplace 混合形态 (INPLACE_BASE_SPEC: region 7-10 + sets)
        wd2 = make_inplace_workdir(self.tmp, n_source_rows=3)
        wd2["workdir"] = self.tmp
        spec2 = copy.deepcopy(INPLACE_BASE_SPEC)
        spec2["fingerprints"] = wd2["manifest"]["fingerprints"]
        spec2["mapping"]["targets"][0]["sets"] = [
            {"path": "A4", "value": "To Messrs: MXP"},
        ]
        plan2 = compile_fill.compile_spec(spec2, wd2["manifest"], self.tmp)
        self.assertEqual(
            plan2["mechanical_facts"]["op_order_invariant"],
            "append 块全部操作 → sets → 终末 inplace 块结构操作 "
            "(overflow 克隆 add → trim remove) → inplace 值操作")
        self.assertNotEqual(plan["mechanical_facts"]["op_order_invariant"],
                            plan2["mechanical_facts"]["op_order_invariant"],
                            "两形态的顺序不变量必须不同 (E1 双表述)")

    def test_anchor_chain_gap_rejected_before_plan(self):
        """锚点链事实的背书: add 引用行若落在目标行号空洞 → TEMPLATE_ROW_GAP 拒绝
        (exit 3) — plan 能产出 ⟹ 锚点链引用行均存在. mapping.md 的
        gap_checked 是 plan 存在性不变量, 非自由文本."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            wd = make_workdir(tmp)
            wd["workdir"] = tmp
            meta = json.loads((tmp / "target_meta.json").read_text(encoding="utf-8"))
            meta["row_gaps"] = [4]  # title add 的 after 锚点 /row[4] (BASE_SPEC)
            (tmp / "target_meta.json").write_text(json.dumps(meta), encoding="utf-8")
            spec = spec_with(wd)
            codes = compile_fail_codes(wd, spec)
            self.assertIn("TEMPLATE_ROW_GAP", codes)


class CapabilityMappingContractTests(unittest.TestCase):
    """能力映射表 (FILLSPEC「能力映射表」章节) 的编译用例背书.

    每条"一等/变通"表达模式 → 一个编译用例, 保证新 MOD 规则入库时可对照
    验证"这条业务规则能否表达、用什么模式表达".
    """

    def setUp(self):
        self.tmp_ctx = tempfile.TemporaryDirectory()
        self.tmp = Path(self.tmp_ctx.name)
        self.wd = make_workdir(self.tmp)
        self.wd["workdir"] = self.tmp

    def tearDown(self):
        self.tmp_ctx.cleanup()

    def _fail_codes(self, spec) -> list[str]:
        return compile_fail_codes(self.wd, spec)

    def test_arith_derived_expression(self):
        """算术派生 (减法) → per_row formula — 一等."""
        spec = spec_with(self.wd)
        spec["mapping"]["targets"][0]["formulas"] = {
            "per_row": {"G": "IFERROR(ROUND(A{r}-B{r},2),0)"}}
        self.assertEqual(self._fail_codes(spec), [])

    def test_field_inheritance_expression(self):
        """字段继承 → columns + lookups (missing: empty) — 一等."""
        spec = spec_with(self.wd)
        spec["mapping"]["targets"][0]["columns"].append(
            {"target": "F", "lookup": {"name": "fields",
                                       "field": "copper", "missing": "empty"}})
        spec["mapping"]["targets"][0]["lookups"] = [
            {"name": "fields", "from": "inheritance.json", "key_column": "C",
             "fields": ["copper"], "missing": "empty"}]
        self.assertEqual(self._fail_codes(spec), [])
        plan = compile_spec_with(self.wd, spec)
        self.assertEqual([w["value"] for w in plan["writes"] if w["col"] == "F"],
                         ["P-1", "P-2", ""])

    def test_routing_expression(self):
        """路由 (条件取列) → selectors 行过滤 + fallback 列回退 — 一等."""
        spec = spec_with(self.wd)
        spec["mapping"]["targets"][0]["rows"]["selectors"] = [
            {"column": "A", "pattern": "家用*"}]
        spec["mapping"]["targets"][0]["columns"] = [
            {"source": "A", "target": "A"},
            {"source": "B", "target": "B"},
            {"source": "C", "target": "C"},
            {"source": "D", "target": "E", "fallback": "B"}]
        self.assertEqual(self._fail_codes(spec), [])

    def test_zero_policy_expression(self):
        """0-口径 → 常量 value "0" + 多列求和缺失输入按 0 — 一等."""
        spec = spec_with(self.wd)
        spec["mapping"]["targets"][0]["columns"].extend([
            {"target": "F", "value": "0"},
            {"source": ["F", "G"], "target": "G"}])
        self.assertEqual(self._fail_codes(spec), [])
        plan = compile_spec_with(self.wd, spec)
        self.assertEqual(sorted({w["value"] for w in plan["writes"] if w["col"] == "G"}),
                         ["15", "3", "9"])
        self.assertIn("F", {w["col"] for w in plan["writes"]})

    def test_constant_expression(self):
        """常量 → columns.value — 一等."""
        spec = spec_with(self.wd)
        spec["mapping"]["targets"][0]["columns"].append(
            {"target": "F", "value": "DP AT SIGHT"})
        self.assertEqual(self._fail_codes(spec), [])
        plan = compile_spec_with(self.wd, spec)
        self.assertEqual(sorted({w["value"] for w in plan["writes"] if w["col"] == "F"}),
                         ["DP AT SIGHT"])

    def test_per_group_total_workaround(self):
        """每组合计 → 变通: blocks[] 每组合一块 + 块级 aggregates 1:{n}
        (组合边界由数据决定, spec 无法表达动态组内范围)."""
        spec = spec_with(self.wd)
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
        spec["mapping"]["targets"][0]["blocks"] = [
            dict(block_cfg, rows={"source": "source_maoli",
                                  "selectors": [{"column": "A", "pattern": "家用*"}]}),
            dict(block_cfg, rows={"source": "source_maoli",
                                  "selectors": [{"column": "A", "pattern": "商用*"}]}),
        ]
        spec["validation"]["key_outputs"] = ["A6", "G8", "G13"]
        self.assertEqual(self._fail_codes(spec), [])
        plan = compile_spec_with(self.wd, spec)
        agg = [op["path"] for op in plan["operations"]
               if "formula" in op.get("props", {}) and "SUM" in op["props"]["formula"]]
        # block1: spacer5 title6 header7 data8-9 → 聚合 G8; block2: spacer10 title11 header12 data13
        self.assertEqual(agg, ["/S/G8", "/S/G13"])

    def test_per_group_total_first_class(self):
        """每组合计 → 一等: group_aggregates (group_by + col + formula,
        组锚点行落公式, 组边界由数据决定)."""
        wd = make_egypt_workdir(self.tmp)
        wd["workdir"] = self.tmp
        spec = spec_with(wd)
        spec["fingerprints"] = wd["manifest"]["fingerprints"]
        spec["mapping"]["targets"][0]["formulas"] = {
            "group_aggregates": [
                {"group_by": "A", "col": "V",
                 "formula": "SUM(T{r1}:T{r2})", "style": "anchor"}]}
        self.assertEqual(compile_fail_codes(wd, spec), [])
        plan = compile_spec_with(wd, spec)
        self.assertEqual(
            [op["path"] for op in plan["operations"]
             if op["command"] == "set" and op["path"].startswith("/S/V")
             and "formula" in op.get("props", {})],
            ["/S/V7", "/S/V9", "/S/V11"])

    def test_per_group_total_explicit_ranges_same_block(self):
        """每组合计 → 一等: 单块 + 多条显式范围聚合 (V/W 同块, 聚合列不进
        nulls) — 埃及案例最终方案的同形脱敏, 编译通过."""
        spec = spec_with(self.wd)
        spec["mapping"]["targets"][0]["clone_roles"] = [
            {"role": "spacer"},
            {"role": "title", "template_row": 1, "value": "块标题"},
            {"role": "header", "template_row": 2},
            {"role": "data", "template_row": 3},
        ]
        spec["mapping"]["targets"][0]["columns"] = [
            {"source": "A", "target": "A"},
            {"source": "B", "target": "B"},
            {"source": "C", "target": "C"},
            {"source": "D", "target": "E"},
        ]
        spec["mapping"]["targets"][0]["formulas"] = {
            "aggregates": [
                {"col": "V", "rows": "1:2",
                 "formula": "IFERROR(ROUND(SUM(T{r1}:T{r2})/SUM(S{r1}:S{r2}),4),0)",
                 "style": "anchor"},
                {"col": "W", "rows": "1:2",
                 "formula": "IFERROR(ROUND(SUM(U{r1}:U{r2})/SUM(S{r1}:S{r2}),4),0)",
                 "style": "anchor"},
                {"col": "V", "rows": "3:3",
                 "formula": "IFERROR(ROUND(SUM(T{r1}:T{r2})/SUM(S{r1}:S{r2}),4),0)",
                 "style": "anchor"},
                {"col": "W", "rows": "3:3",
                 "formula": "IFERROR(ROUND(SUM(U{r1}:U{r2})/SUM(S{r1}:S{r2}),4),0)",
                 "style": "anchor"},
            ]}
        spec["validation"]["key_outputs"] = ["A8", "V8", "W8", "V10", "W10"]
        self.assertEqual(self._fail_codes(spec), [])
        plan = compile_spec_with(self.wd, spec)
        formulas = {op["path"]: op["props"]["formula"] for op in plan["operations"]
                    if "formula" in op.get("props", {})}
        # spacer5 title6 header7 data8-10: 组1 = 行 8-9, 组2 = 行 10
        self.assertIn("/S/V8", formulas)
        self.assertIn("/S/W8", formulas)
        self.assertIn("/S/V10", formulas)
        self.assertIn("/S/W10", formulas)
        self.assertEqual(formulas["/S/V8"], "IFERROR(ROUND(SUM(T8:T9)/SUM(S8:S9),4),0)")
        self.assertEqual(formulas["/S/V10"], "IFERROR(ROUND(SUM(T10:T10)/SUM(S10:S10),4),0)")

    def test_per_group_total_aggregate_col_in_nulls_rejected(self):
        """每组合计负面表达: 同形 spec 聚合列 (F) 进 nulls → 锚点双写
        ("first as empty" — nulls 先清空锚点格, 聚合再写公式)."""
        spec = spec_with(self.wd)
        spec["mapping"]["targets"][0]["clone_roles"] = [
            {"role": "spacer"},
            {"role": "title", "template_row": 1, "value": "块标题"},
            {"role": "header", "template_row": 2},
            {"role": "data", "template_row": 3},
        ]
        spec["mapping"]["targets"][0]["columns"] = [
            {"source": "A", "target": "A"},
            {"source": "B", "target": "B"},
            {"source": "C", "target": "C"},
            {"source": "D", "target": "E"},
        ]
        spec["mapping"]["targets"][0]["nulls"] = [
            {"col": "D", "rows": "all"}, {"col": "F", "rows": "all"}]
        spec["mapping"]["targets"][0]["formulas"] = {
            "aggregates": [
                {"col": "F", "rows": "1:2",
                 "formula": "SUM(A{r1}:A{r2})", "style": "anchor"},
                {"col": "F", "rows": "3:3",
                 "formula": "SUM(A{r1}:A{r2})", "style": "anchor"},
            ]}
        spec["validation"]["key_outputs"] = ["A8"]
        codes = self._fail_codes(spec)
        self.assertIn("DUPLICATE_TARGET_WRITE", codes)
        self.assertNotIn("AGG_RANGE_INVALID", codes)

    def test_pptx_group_merges_not_rolled_out(self):
        """暂无表达 (pptx group_merges) → PPTX_CAPABILITY_NOT_ROLLED_OUT."""
        self.wd["manifest"]["target"]["sheet"] = "slide[1]/table[@id=1]"
        spec = spec_with(self.wd)
        spec["inputs"]["platform"] = "pptx"
        spec["inputs"]["target_sheet"] = "slide[1]/table[@id=1]"
        t = spec["mapping"]["targets"][0]
        t["sheet"] = "slide[1]/table[@id=1]"
        t["first_data_row"] = 2
        t["group_merges"] = [{"col": "A", "group_by": "A"}]
        self.assertIn("PPTX_CAPABILITY_NOT_ROLLED_OUT", self._fail_codes(spec))

    def test_pptx_inplace_not_rolled_out(self):
        """暂无表达 (pptx inplace) → PPTX_CAPABILITY_NOT_ROLLED_OUT."""
        self.wd["manifest"]["target"]["sheet"] = "slide[1]/table[@id=1]"
        spec = spec_with(self.wd)
        spec["inputs"]["platform"] = "pptx"
        spec["inputs"]["target_sheet"] = "slide[1]/table[@id=1]"
        t = spec["mapping"]["targets"][0]
        t["sheet"] = "slide[1]/table[@id=1]"
        t["first_data_row"] = 2
        t["clone_roles"] = [{"role": "data", "mode": "inplace",
                             "start_row": 2, "capacity": 4, "template_row": 2}]
        t["rows"] = {"source": "source_maoli"}
        t.pop("base_last_row", None)
        self.assertIn("PPTX_CAPABILITY_NOT_ROLLED_OUT", self._fail_codes(spec))


class ProbeTests(unittest.TestCase):
    """compile_fill.py --probe: compile-only verification, zero side effects.

    The probe runs the exact same pipeline as a real compile — its answer is
    authoritative and always matches what a full compile would do."""

    def setUp(self):
        self.tmp_ctx = tempfile.TemporaryDirectory()
        self.tmp = Path(self.tmp_ctx.name)
        self.wd = make_workdir(self.tmp)
        self.wd["workdir"] = self.tmp

    def tearDown(self):
        self.tmp_ctx.cleanup()

    def _probe(self, spec) -> dict:
        return compile_fill.probe_spec(spec, self.wd["manifest"], self.tmp)

    def test_probe_accepted_spec(self):
        spec = spec_with(self.wd)
        r = self._probe(spec)
        self.assertTrue(r["accepted"])
        self.assertEqual(r["exit_code"], 0)
        self.assertGreater(r["operations"], 0)
        self.assertEqual(r["defects"], [])

    def test_probe_rejected_with_code(self):
        spec = spec_with(self.wd)
        spec["mapping"]["targets"][0]["group_merges"] = [{"col": "A", "group_by": "A"}]
        spec["mapping"]["targets"][0]["formulas"] = {
            "aggregates": [{"col": "A", "rows": "1:{n}",
                            "formula": "SUM(A{r1}:A{r2})", "style": "anchor"}]}
        r = self._probe(spec)
        self.assertFalse(r["accepted"])
        self.assertEqual(r["exit_code"], 3)
        self.assertEqual(r["code"], "STATIC_VALIDATION_FAILED")
        codes = {d["code"] for d in r["defects"]}
        self.assertIn("DUPLICATE_TARGET_WRITE", codes)

    def test_probe_matches_full_compile(self):
        """probe 结论与完整编译一致 (同管线, 不会出现 probe 通过但 compile 拒绝)."""
        spec = spec_with(self.wd)
        spec["mapping"]["targets"][0]["formulas"] = {
            "per_row": {"G": "IFERROR(ROUND(A{r}-B{r},2),0)"}}
        r = self._probe(spec)
        self.assertTrue(r["accepted"])
        plan = compile_spec_with(self.wd, spec)
        self.assertEqual(r["operations"], len(plan["operations"]))

    def test_probe_writes_no_artifacts(self):
        """probe 零副作用: 不写 execution_plan.json / mapping.md / run_timing.json."""
        spec = spec_with(self.wd)
        self._probe(spec)
        self.assertFalse((self.tmp / "execution_plan.json").exists())
        self.assertFalse((self.tmp / "mapping.md").exists())
        self.assertFalse((self.tmp / "run_timing.json").exists())

    def test_probe_rejected_writes_no_artifacts(self):
        spec = spec_with(self.wd)
        spec["mapping"]["targets"][0]["formulas"] = {
            "aggregates": [{"col": "G", "rows": "1:9",
                            "formula": "SUM(A{r1}:A{r2})", "style": "anchor"}]}
        r = self._probe(spec)
        self.assertFalse(r["accepted"])
        self.assertFalse((self.tmp / "execution_plan.json").exists())
        self.assertFalse((self.tmp / "mapping.md").exists())
        self.assertFalse((self.tmp / "run_timing.json").exists())

    def test_combination_pattern_renamed_columns_compiles(self):
        """组合模式片段复制即用实证: combination_patterns.yaml
        `per_group_total_explicit_ranges` 片段改列名 (V→G, W→H) 后直接编译
        通过 — 零 probe 起步承诺 (2026-08-13 契约修正)."""
        import yaml
        patterns = yaml.safe_load(
            (SKILL_ROOT / "assets" / "combination_patterns.yaml").read_text(encoding="utf-8"))
        frag = next(p["fragment"] for p in patterns["patterns"]
                    if p["id"] == "per_group_total_explicit_ranges")
        block = yaml.safe_load(frag)["formulas"]["aggregates"]
        rename = {"V": "G", "W": "H"}
        aggregates = [dict(a, col=rename.get(a["col"], a["col"])) for a in block]
        self.wd = make_workdir(self.tmp, n_source_rows=5)  # 片段范围 1:2 / 3:5 需 ≥5 行
        self.wd["workdir"] = self.tmp
        spec = spec_with(self.wd)
        spec["mapping"]["targets"][0]["clone_roles"] = [
            {"role": "spacer"},
            {"role": "title", "template_row": 1, "value": "块标题"},
            {"role": "header", "template_row": 2},
            {"role": "data", "template_row": 3},
        ]
        spec["mapping"]["targets"][0]["formulas"] = {"aggregates": aggregates}
        spec["validation"]["key_outputs"] = ["A8", "G8", "H8", "G10", "H10"]
        r = self._probe(spec)
        self.assertTrue(r["accepted"], f"改列名后应编译通过: {r}")
        self.assertEqual(r["defects"], [])


class CapabilitiesTests(unittest.TestCase):
    """compile_fill.py --capabilities: the contract matrix as the compiler
    itself judges it — same PROBE_CASES list as the contract tests, so the
    runtime report, the tests and FILLSPEC.md can never drift apart."""

    def test_capabilities_matrix_matches_expectations(self):
        from _probe_fixtures import PROBE_CASES
        with tempfile.TemporaryDirectory() as tmp:
            results = compile_fill.run_probe_cases(Path(tmp))
        by_id = {r["id"]: r for r in results}
        self.assertEqual(sorted(by_id), sorted(c["id"] for c in PROBE_CASES))
        for case in PROBE_CASES:
            r = by_id[case["id"]]
            if case["expect"] == "accept":
                self.assertTrue(r["accepted"], f"{case['id']} 应被接受")
                self.assertIsNone(r["code"])
            else:
                self.assertFalse(r["accepted"], f"{case['id']} 应被拒绝")
                self.assertEqual(r["code"], case["expect"],
                                 f"{case['id']} 错误码应为 {case['expect']}")

    def test_capabilities_covers_contract_claims(self):
        """契约关键组合必须在 capabilities 报告中 (防探针集被误删)."""
        with tempfile.TemporaryDirectory() as tmp:
            results = compile_fill.run_probe_cases(Path(tmp))
        by_id = {r["id"]: r for r in results}
        for cid in ("group_merges_aggregate_same_col", "group_merges_aggregate_diff_col",
                    "derived_subtraction_pattern", "mapped_group_column_anchor",
                    "lookup_missing_empty", "precision_keep", "per_group_total_blocks",
                    "per_group_total_hardcoded_ranges", "nulls_aggregate_same_col",
                    "pptx_group_merges", "group_aggregates_egypt_3_groups",
                    "group_aggregates_whole_run_gate", "lookup_table_empty",
                    "lookup_column_all_missing"):
            self.assertIn(cid, by_id, f"capabilities 缺契约探针 {cid}")


class ProbeScaffoldTests(unittest.TestCase):
    """make_probe_spec.py: skeleton spec with fingerprints/inputs auto-filled
    from the manifest — the boilerplate elimination that makes --probe cheap."""

    def _scaffold(self, tmp: Path) -> dict:
        import json as _json
        import subprocess
        sys.path.insert(0, str(SKILL_ROOT / "scripts"))
        out = tmp / "probe_spec.yaml"
        r = subprocess.run(
            [sys.executable, str(SKILL_ROOT / "scripts" / "make_probe_spec.py"),
             "--workdir", str(tmp), "--out", str(out)],
            capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(r.returncode, 0, r.stderr)
        payload = _json.loads(r.stdout)
        self.assertEqual(payload["code"], "PROBE_SCAFFOLD_WRITTEN")
        text = out.read_text(encoding="utf-8")
        return _json.loads(text[text.index("{"):])  # 跳过注释头

    def test_scaffold_fills_fingerprints_and_inputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            wd = make_workdir(tmp)
            spec = self._scaffold(tmp)
            mfp = wd["manifest"]["fingerprints"]
            self.assertEqual(spec["fingerprints"]["source_structure"], mfp["source_structure"])
            self.assertEqual(spec["fingerprints"]["target_structure"], mfp["target_structure"])
            self.assertEqual(spec["inputs"]["target"], "target.xlsx")
            self.assertEqual(spec["inputs"]["target_sheet"], "S")
            self.assertEqual(spec["inputs"]["sources"], ["source_maoli.xlsx"])

    def test_scaffold_probes_without_fingerprint_mismatch(self):
        """骨架生成的 spec 直接 probe 不因指纹报错 (样板是骨架, 结果接受与否
        取决于片段; 至少不该死在指纹样板手上)."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            wd = make_workdir(tmp)
            spec = self._scaffold(tmp)
            r = compile_fill.probe_spec(spec, wd["manifest"], tmp)
            self.assertNotEqual(r.get("code"), "FILLSPEC_FINGERPRINT_MISMATCH")
            self.assertIn("accepted", r)

    def test_scaffold_missing_manifest_fails(self):
        import subprocess
        with tempfile.TemporaryDirectory() as tmp:
            r = subprocess.run(
                [sys.executable, str(SKILL_ROOT / "scripts" / "make_probe_spec.py"),
                 "--workdir", tmp, "--out", str(Path(tmp) / "p.yaml")],
                capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(r.returncode, 3)
            self.assertIn("MANIFEST_NOT_FOUND", r.stdout)

    def test_scaffold_refuses_overwrite_without_force(self):
        """默认输出 probe_spec.yaml; 已存在文件须 --force, 防止误覆盖
        真实 fill_spec.yaml (2026-08-12 round1: 骨架曾静默覆盖丢失 spec)."""
        import json as _json
        import subprocess
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            wd = make_workdir(tmp)
            # 默认路径 probe_spec.yaml 首次生成成功
            r = subprocess.run(
                [sys.executable, str(SKILL_ROOT / "scripts" / "make_probe_spec.py"),
                 "--workdir", str(tmp)],
                capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertTrue((tmp / "probe_spec.yaml").is_file())
            # 再跑一次 → 拒绝覆盖
            r = subprocess.run(
                [sys.executable, str(SKILL_ROOT / "scripts" / "make_probe_spec.py"),
                 "--workdir", str(tmp)],
                capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(r.returncode, 3)
            self.assertIn("PROBE_SCAFFOLD_EXISTS", r.stdout)
            # --force → 覆盖成功
            r = subprocess.run(
                [sys.executable, str(SKILL_ROOT / "scripts" / "make_probe_spec.py"),
                 "--workdir", str(tmp), "--force"],
                capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("PROBE_SCAFFOLD_WRITTEN", r.stdout)


class DocCoverageGuardTests(unittest.TestCase):
    """doc-coverage 守卫: 关键交互词必须留在文档中, 章节被误删时测试变红.

    组合行为契约只写经编译用例背书的声明 (见 FillSpecContractTests /
    CapabilityMappingContractTests); 本类只做存在性断言, 不复制行为声明.
    """

    def _fillspec_section(self, heading: str) -> str:
        text = (SKILL_ROOT / "references" / "FILLSPEC.md").read_text(encoding="utf-8")
        m = re.search(rf"^##\s+{re.escape(heading)}", text, re.MULTILINE)
        self.assertIsNotNone(m, f"FILLSPEC.md 缺章节 ## {heading}")
        nxt = re.search(r"^##\s", text[m.end():], re.MULTILINE)
        return text[m.end():m.end() + (nxt.start() if nxt else len(text))]

    def _error_code_table(self) -> str:
        """常见编译错误速查表 (错误码表项守卫只认表内条目)."""
        text = (SKILL_ROOT / "references" / "FILLSPEC.md").read_text(encoding="utf-8")
        m = re.search(r"^##\s+常见编译错误速查", text, re.MULTILINE)
        self.assertIsNotNone(m, "FILLSPEC.md 缺章节 ## 常见编译错误速查")
        return text[m.end():]

    def test_fillspec_combination_contract_section(self):
        """「组合行为契约」章节同时解释 group_merges 与 aggregates 的交互
        (Agent 按问题定位, 而不是按特性清单)."""
        section = self._fillspec_section("组合行为契约")
        self.assertIn("group_merges", section)
        self.assertIn("aggregates", section)
        self.assertIn("DUPLICATE_TARGET_WRITE", section)

    def test_fillspec_derived_column_pattern_word(self):
        """减法派生列标准模式词 (FLD-006) 在契约章节中."""
        section = self._fillspec_section("组合行为契约")
        self.assertIn("减法", section)
        self.assertIn("per_row", section)
        self.assertIn("ROUND", section)

    def test_fillspec_precision_recommendation_order(self):
        """round4 推荐次序在契约章节中: Q7 小节正文内 round4 必须先于 keep
        出现 (推荐序被反转时测试变红)."""
        section = self._fillspec_section("组合行为契约")
        m = re.search(r"^### Q7:.*\n", section, re.MULTILINE)
        self.assertIsNotNone(m, "契约章节缺 Q7 小节")
        q7 = section[m.end():]
        self.assertIn("round4", q7)
        self.assertIn("keep", q7)
        self.assertLess(q7.index("round4"), q7.index("keep"))

    def test_fillspec_q7_keep_column_width_check(self):
        """Q7 含 keep 列宽实测背书契约 (issue 04): 缺陷码 + 列宽未知豁免
        语义都必须在正文 (防契约条目被误删)."""
        section = self._fillspec_section("组合行为契约")
        m = re.search(r"^### Q7:.*\n", section, re.MULTILINE)
        self.assertIsNotNone(m, "契约章节缺 Q7 小节")
        q7 = section[m.end():]
        self.assertIn("PRECISION_KEEP_NARROW_COLUMN", q7)
        self.assertIn("PRECISION_KEEP_WIDTH_UNVERIFIED", q7)
        self.assertIn("column_width", q7)
        self.assertIn("PRECISION_KEEP_NARROW_COLUMN", self._error_code_table())

    def test_fillspec_capability_mapping_table(self):
        """能力映射表章节覆盖 MOD 规则类型 × 支持状态."""
        section = self._fillspec_section("能力映射表")
        for word in ("一等", "变通", "暂无"):
            self.assertIn(word, section)

    def test_fillspec_error_code_table_has_duplicate_target_write(self):
        """DUPLICATE_TARGET_WRITE 必须在「常见编译错误速查」表内 (防章节误删)."""
        self.assertIn("DUPLICATE_TARGET_WRITE", self._error_code_table())

    def test_fillspec_error_code_table_has_append_remove_zone(self):
        """REMOVE_TARGETS_APPEND_ZONE 必须在「常见编译错误速查」表内且行内
        首选语义为 append-only (inplace 仅是带样式时的条件选项, 防无条件
        指向 inplace 的误导)."""
        table = self._error_code_table()
        self.assertIn("REMOVE_TARGETS_APPEND_ZONE", table)
        m = re.search(r"^\|\s*REMOVE_TARGETS_APPEND_ZONE.*$", table,
                      re.MULTILINE)
        self.assertIsNotNone(m, "速查表缺 REMOVE_TARGETS_APPEND_ZONE 行")
        row = m.group(0)
        self.assertIn("append-only", row)
        self.assertIn("inplace", row)
        self.assertIn("样式", row)
        self.assertLess(row.index("append-only"), row.index("inplace"),
                        "append-only 是首选, inplace 只能是条件选项")

    def test_skill_md_authoring_procedure_words(self):
        """SKILL.md 撰写规程: 先写后编译 + scratch 纪律词存在."""
        text = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("先写后编译", text)
        self.assertIn("scratch", text)

    def test_known_traps_source_reading_discipline(self):
        """KNOWN_TRAPS 含源码阅读/实验纪律条目 (precision: keep 反例)."""
        text = (SKILL_ROOT / "references" / "KNOWN_TRAPS.md").read_text(encoding="utf-8")
        self.assertIn("precision: keep", text)
        self.assertIn("scratch", text)

    def test_skill_md_probe_rules(self):
        """SKILL.md 撰写规程: probe 是唯一允许的确认手段 (防规程被误删)."""
        text = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("--probe", text)
        self.assertIn("--capabilities", text)
        self.assertIn("make_probe_spec.py", text)

    def test_combination_patterns_exist(self):
        """assets/combination_patterns.yaml 存在且含关键模式 (防模板被误删)."""
        p = SKILL_ROOT / "assets" / "combination_patterns.yaml"
        self.assertTrue(p.is_file(), "缺 assets/combination_patterns.yaml")
        text = p.read_text(encoding="utf-8")
        self.assertIn("group_merges", text)
        self.assertIn("round4", text)
        self.assertIn("per_group_total_explicit_ranges", text)

    def test_fillspec_q8_anchor_scope_words(self):
        """契约章节含 Q8 小节: data role 作用域 + title/header 无检查边界."""
        section = self._fillspec_section("组合行为契约")
        m = re.search(r"^### Q8:.*\n", section, re.MULTILINE)
        self.assertIsNotNone(m, "契约章节缺 Q8 小节")
        q8 = section[m.end():]
        self.assertIn("data", q8)
        self.assertIn("title/header", q8)

    def test_fillspec_q9_q10_sections(self):
        """契约章节含 Q9 (value 延迟写入) 与 Q10 (readback 种类) 小节."""
        section = self._fillspec_section("组合行为契约")
        self.assertIsNotNone(re.search(r"^### Q9:", section, re.MULTILINE), "缺 Q9")
        self.assertIsNotNone(re.search(r"^### Q10:", section, re.MULTILINE), "缺 Q10")
        self.assertIn("deferred_values", section)
        self.assertIn("nonempty", section)

    def test_skill_md_failure_cost_quantified(self):
        """SKILL.md 撰写规程量化失败成本: 第 1 轮失败是预期路径, 修复 <2 分钟,
        预算约束连续失败而非单次失败 (消除'怕失败读源码'的动机)."""
        text = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("预期路径", text)
        self.assertIn("2 分钟", text)
        self.assertIn("连续失败", text)

    def test_fillspec_q11_q12_sections(self):
        """契约章节含 Q11 (克隆携带合并) 与 Q12 (merges×aggregates/多组聚合)."""
        section = self._fillspec_section("组合行为契约")
        self.assertIsNotNone(re.search(r"^### Q11:", section, re.MULTILINE), "缺 Q11")
        self.assertIsNotNone(re.search(r"^### Q12:", section, re.MULTILINE), "缺 Q12")
        self.assertIn("mergeCell", section)
        self.assertIn("显式范围", section)

    def test_fillspec_q13_per_group_total_boundary(self):
        """契约章节含 Q13: 每组合计接受边界 — 聚合列不进 nulls 是触发因素
        (负面表达与通过形态同源, fixture 漂移以 Q&A 文字锁定)."""
        section = self._fillspec_section("组合行为契约")
        m = re.search(r"^### Q13:", section, re.MULTILINE)
        self.assertIsNotNone(m, "缺 Q13")
        q13 = section[m.end():]
        self.assertIn("nulls", q13)
        self.assertIn("DUPLICATE_TARGET_WRITE", q13)
        self.assertIn("first as empty", q13)
        self.assertIn("per_group_total_explicit_ranges", q13)

    def test_fillspec_q14_group_aggregates_section(self):
        """契约章节含 Q14 小节 (group_aggregates 一等能力 + whole_run 门)."""
        section = self._fillspec_section("组合行为契约")
        m = re.search(r"^### Q14:", section, re.MULTILINE)
        self.assertIsNotNone(m, "契约章节缺 Q14 小节")
        q14 = section[m.end():]
        self.assertIn("group_aggregates", q14)
        self.assertIn("group_by", q14)
        self.assertIn("CAPABILITY_NOT_ROLLED_OUT", q14)
        self.assertIn("DUPLICATE_TARGET_WRITE", q14)

    def test_fillspec_capability_table_group_aggregates_first_class(self):
        """能力映射表「每组合计」→ 一等 (group_aggregates 表达)."""
        section = self._fillspec_section("能力映射表")
        self.assertIn("group_aggregates", section)
        self.assertIn("一等", section)
        self.assertIn("CAPABILITY_NOT_ROLLED_OUT", section)

    def test_fillspec_error_code_table_has_capability_gate(self):
        """CAPABILITY_NOT_ROLLED_OUT 在「常见编译错误速查」表内 (whole_run 门)."""
        self.assertIn("CAPABILITY_NOT_ROLLED_OUT", self._error_code_table())

    def test_combination_patterns_group_aggregates_pattern(self):
        """combination_patterns.yaml 含 group_aggregates 一等模式 (改列名即可)."""
        p = SKILL_ROOT / "assets" / "combination_patterns.yaml"
        text = p.read_text(encoding="utf-8")
        self.assertIn("group_aggregates", text)
        self.assertIn("group_by", text)

    def test_known_traps_spike_facts(self):
        """KNOWN_TRAPS 沉淀已 spike 机械事实 (克隆携带合并 / merges×aggregates)."""
        text = (SKILL_ROOT / "references" / "KNOWN_TRAPS.md").read_text(encoding="utf-8")
        self.assertIn("克隆携带合并", text)
        self.assertIn("mergeCell", text)
        self.assertIn("A41:F41", text)

    def test_fillspec_execution_order_section(self):
        """「执行顺序保证」章节存在且覆盖全部四条锁定声明: op 顺序不变量 /
        remove 目标身份 / 自底向上 / 坐标翻译边界 (防章节误删或声明回退)."""
        section = self._fillspec_section("执行顺序保证")
        for word in ("clear", "add", "remove", "merge", "fill",
                     "duplicate_row", "模板坐标", "自底向上", "readback",
                     "REMOVE_TARGETS_APPEND_ZONE", "deferred_values",
                     "final_row"):
            self.assertIn(word, section, f"执行顺序保证章节缺 {word!r}")
        self.assertIsNotNone(re.search(r"^### E1:", section, re.MULTILINE), "缺 E1")
        self.assertIsNotNone(re.search(r"^### E2:", section, re.MULTILINE), "缺 E2")
        self.assertIsNotNone(re.search(r"^### E3:", section, re.MULTILINE), "缺 E3")
        self.assertIsNotNone(re.search(r"^### E4:", section, re.MULTILINE), "缺 E4")

    def test_known_traps_remove_add_interaction(self):
        """KNOWN_TRAPS 沉淀 remove/add 交互权威事实 (埃及案例重放 oracle:
        Agent 无需读源码即可回答 add 后 remove 目标是谁 — 指向 FILLSPEC 章节)."""
        text = (SKILL_ROOT / "references" / "KNOWN_TRAPS.md").read_text(encoding="utf-8")
        self.assertIn("不被 add 推移", text)
        self.assertIn("执行顺序保证", text)
        self.assertIn("mechanical_facts", text)

    def test_mod_index_execution_boundary(self):
        """MOD_INDEX 标注执行 vs 治理文档边界: 执行任务不读 MOD_TEMPLATE."""
        text = (SKILL_ROOT / "references" / "MOD_INDEX.md").read_text(encoding="utf-8")
        self.assertIn("不读", text)
        self.assertIn("MOD_TEMPLATE", text)

    def test_skill_md_prepare_sheets_all_at_once(self):
        """SKILL.md prepare 阶段 B: 一次列出全部源 sheet, 增量展平是兜底."""
        text = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("一次列出全部源 sheet", text)
        self.assertIn("兜底", text)

    def test_fillspec_q15_lookup_integrity_section(self):
        """契约章节含 Q15: 空索引 → LOOKUP_TABLE_EMPTY (exit 3); 整列未命中 →
        LOOKUP_COLUMN_ALL_MISSING 警告 (防章节误删 / 静默全空回退)."""
        section = self._fillspec_section("组合行为契约")
        m = re.search(r"^### Q15:", section, re.MULTILINE)
        self.assertIsNotNone(m, "契约章节缺 Q15 小节")
        q15 = section[m.end():]
        self.assertIn("LOOKUP_TABLE_EMPTY", q15)
        self.assertIn("LOOKUP_COLUMN_ALL_MISSING", q15)
        self.assertIn("field_consensus", q15)

    def test_fillspec_error_code_table_has_lookup_integrity(self):
        """LOOKUP_TABLE_EMPTY / LOOKUP_COLUMN_ALL_MISSING 在「常见编译错误速查」
        表内 (防章节误删)."""
        table = self._error_code_table()
        self.assertIn("LOOKUP_TABLE_EMPTY", table)
        self.assertIn("LOOKUP_COLUMN_ALL_MISSING", table)

    def test_known_traps_lookup_index_rebuild(self):
        """KNOWN_TRAPS 沉淀索引清洗机械事实: LOOKUP_TABLE_EMPTY 拦截 + 禁止手改
        JSON, 用 build_inheritance_index.py 重建."""
        text = (SKILL_ROOT / "references" / "KNOWN_TRAPS.md").read_text(encoding="utf-8")
        self.assertIn("LOOKUP_TABLE_EMPTY", text)
        self.assertIn("手改 JSON", text)
        self.assertIn("build_inheritance_index.py", text)

    def test_fillspec_lookups_source_excludes_target(self):
        """「lookups」契约条目: 索引输入 sheet 排除目标 sheet — 自引用 → 共识
        conflict → 按缺失处理 (静默), 排查线索见 KNOWN_TRAPS (埃及 FRESH 坑 2)."""
        text = (SKILL_ROOT / "references" / "FILLSPEC.md").read_text(encoding="utf-8")
        m = re.search(r"^### lookups\s*$", text, re.MULTILINE)
        self.assertIsNotNone(m, "FILLSPEC.md 缺 ### lookups 小节")
        nxt = re.search(r"^### ", text[m.end():], re.MULTILINE)
        section = text[m.end():m.end() + (nxt.start() if nxt else len(text))]
        self.assertIn("目标 sheet", section)
        self.assertIn("自引用", section)
        self.assertIn("KNOWN_TRAPS", section)

    def test_fillspec_q15_self_reference_hint(self):
        """Q15 整列未命中排查含自引用线索 (索引输入误含目标 sheet)."""
        section = self._fillspec_section("组合行为契约")
        m = re.search(r"^### Q15:", section, re.MULTILINE)
        self.assertIsNotNone(m, "契约章节缺 Q15 小节")
        q15 = section[m.end():]
        self.assertIn("目标 sheet", q15)
        self.assertIn("自引用", q15)

    def test_known_traps_lookup_source_self_reference(self):
        """KNOWN_TRAPS 沉淀索引自引用机械事实: 输入含目标 sheet → 同 SKU 多值
        → 共识 conflict → 按缺失处理; 构建索引只喂独立数据 sheet."""
        text = (SKILL_ROOT / "references" / "KNOWN_TRAPS.md").read_text(encoding="utf-8")
        self.assertIn("目标 sheet", text)
        self.assertIn("共识 conflict", text)

    def test_fillspec_layout_decision_tree_style_first(self):
        """FILLSPEC 布局决策树: 以样式为第一判定条件 (带样式分支先于裸行分支),
        三分支齐全, 各分支与缺陷码对应 (文档与编译器裁决同源)."""
        section = self._fillspec_section("布局决策树")
        self.assertIn("以样式为第一判定条件", section)
        for word in ("带样式", "裸行", "inplace", "clone-append",
                     "append-only", "remove_rows", "自然下沉", "收缩"):
            self.assertIn(word, section)
        self.assertLess(section.index("带样式"), section.index("裸行"),
                        "样式条件必须作为第一判定条件: 带样式分支先于裸行分支")
        self.assertLess(section.index("①"), section.index("②"),
                        "分支 ① (带样式→inplace) 必须先于分支 ② (裸行→clone-append)")
        self.assertLess(section.index("②"), section.index("③"),
                        "分支 ② 必须先于分支 ③ (既有块收缩)")
        for code in ("REMOVE_TARGETS_APPEND_ZONE", "TEMPLATE_ROW_GAP",
                     "STRUCTURAL_OP_OUT_OF_ZONE"):
            self.assertIn(code, section)
        self.assertLess(section.index("≤ base_last_row"),
                        section.index("TEMPLATE_ROW_GAP"),
                        "分支 ③ 必须声明 remove_rows ≤ base_last_row 边界")

    def test_skill_md_mod_loading_output_form(self):
        """SKILL.md MOD 段输出形态优化: 提名阶段只给摘要不含完整规则集,
        裁决后才加载选中 MOD 完整规则; 「候选规则必须加载后才可写 spec」
        的硬性要求保留 (改变加载时机与粒度, 不是是否加载)."""
        text = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        m = re.search(r"^### 2\. MOD Resolution.*?(?=^### 3\.)",
                      text, re.MULTILINE | re.DOTALL)
        self.assertIsNotNone(m, "SKILL.md 缺 MOD Resolution 段")
        section = m.group(0)
        for word in ("摘要", "不含完整规则集", "裁决后", "选中"):
            self.assertIn(word, section)
        self.assertIn("必须加载后才可写 spec", section)
        self.assertIn("不因输出形态优化放宽", section)

    def test_skill_md_distrust_conversion_section(self):
        """SKILL.md「不信任事件转换纪律」: 四类触发条件 + 契约漂移为最高优先
        触发条件 + 三件套 + 产出物 (防制度小节被误删或触发条件被降级)."""
        text = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("不信任事件转换", text)
        for word in ("三件套", "契约漂移", "最高优先", "contract test",
                     "缺陷码", "契约 Q&A", "回归测试", "KNOWN_TRAPS"):
            self.assertIn(word, text)
        # 契约漂移必须单独列为最高优先触发条件 (issue 05 类)
        m = re.search(r"最高优先触发条件[^\n]*", text)
        self.assertIsNotNone(m, "缺最高优先触发条件声明")
        self.assertIn("契约漂移", m.group(0))

    def test_known_traps_three_workflows_oracles(self):
        """KNOWN_TRAPS 与三个工作流产物对应 (07 验收 #2): remove/add 交互
        (01/02), 裸行占位 → append-only 终态 (03/04), 显式范围 nulls 触发
        条件 (05), 组聚合落点 (06) 全部有重放 oracle."""
        text = (SKILL_ROOT / "references" / "KNOWN_TRAPS.md").read_text(encoding="utf-8")
        for word in ("不被 add 推移", "裸行占位", "占位行样式", "first as empty",
                     "group_aggregates", "append-only"):
            self.assertIn(word, text, f"KNOWN_TRAPS 缺机械事实词 {word!r}")


class ModCatalogIndexTests(unittest.TestCase):
    """MOD_INDEX.md 目录解析: 转义管道 + 修订号漂移守护."""

    def test_escaped_pipe_keeps_revision_column(self):
        """信号格含 \\| 转义时, 列对齐不被破坏, revision 解析正确."""
        text = (
            "## Registered MODs\n\n"
            "| MOD Name | Aliases | Scope Signals (+) | Exclusion Signals (-) | Path | Revision | Visibility |\n"
            "|---|---|---|---|---|---|---|\n"
            "| m1 | alias | semantic_type::quotation,sheet_marker::三三三\\|333 |  | MOD_m1.md | 5 | private |\n"
        )
        entries = parse_mod_index(text)
        self.assertEqual(len(entries), 1)
        e = entries[0]
        self.assertEqual(e.mod_name, "m1")
        self.assertEqual(e.scope_signals, "semantic_type::quotation,sheet_marker::三三三|333")
        self.assertEqual(e.revision, 5)
        self.assertEqual(e.path, "MOD_m1.md")
        self.assertEqual(e.visibility, "private")

    def test_live_index_revisions_synced_with_mod_files(self):
        """真实 MOD_INDEX 与 MOD 文件头修订号一致 (漂移守护).

        发布仓库不携带私有客户 MOD — Registered MODs 为空时本守卫跳过
        (私有 MOD 随捕获流程存在于本地, 不随 office-skills 推送)."""
        refs = SKILL_ROOT / "references"
        index_text = (refs / "MOD_INDEX.md").read_text(encoding="utf-8")
        entries = parse_mod_index(index_text)
        if not entries:
            return
        for entry in entries:
            mod_file = refs / entry.path
            self.assertTrue(mod_file.is_file(), f"索引指向缺失文件: {entry.path}")
            mod_text = mod_file.read_text(encoding="utf-8")
            m = re.search(r"^Revision:\s*(\d+)", mod_text, re.MULTILINE)
            self.assertIsNotNone(m, f"{entry.path} 缺 Revision 头")
            self.assertEqual(
                entry.revision, int(m.group(1)),
                f"{entry.mod_name} 修订号漂移: 索引 {entry.revision} vs 文件 {m.group(1)}")


if __name__ == "__main__":
    unittest.main()
