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
from _probe_fixtures import (  # noqa: E402
    BASE_SPEC,
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
                    "pptx_group_merges"):
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

    def test_fillspec_capability_mapping_table(self):
        """能力映射表章节覆盖 MOD 规则类型 × 支持状态."""
        section = self._fillspec_section("能力映射表")
        for word in ("一等", "变通", "暂无"):
            self.assertIn(word, section)

    def test_fillspec_error_code_table_has_duplicate_target_write(self):
        """DUPLICATE_TARGET_WRITE 必须在「常见编译错误速查」表内 (防章节误删)."""
        self.assertIn("DUPLICATE_TARGET_WRITE", self._error_code_table())

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

    def test_known_traps_spike_facts(self):
        """KNOWN_TRAPS 沉淀已 spike 机械事实 (克隆携带合并 / merges×aggregates)."""
        text = (SKILL_ROOT / "references" / "KNOWN_TRAPS.md").read_text(encoding="utf-8")
        self.assertIn("克隆携带合并", text)
        self.assertIn("mergeCell", text)
        self.assertIn("A41:F41", text)

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


if __name__ == "__main__":
    unittest.main()
