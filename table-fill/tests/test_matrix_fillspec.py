"""Ticket 06 — Matrix FillSpec + Source Lineage: compile contract tests.

Covers (per the ticket + spec D6, data-neutral fixture shape):
  1. matrix 物化契约 (field_map × record_map → 目标格): plan writes +
     readback derived from the SAME materialization (no hand-written checks);
     write count == |field_map| × |record_map|; plan 不含几百个 literal sets
     (合法 title/footer sets 之外全部 matrix 物化 — Real Case Replay 的
     fixture 替代断言, 用户禁止真实业务 run)。
  2. source lineage 一等输出: 每条 matrix 写入带 {target, source,
     transform_chain}; 抽查特定格的 lineage 指向正确源格 (fixture 坐标版
     的 SPEC!D15 例子)。
  3. transforms 可执行: 宽片→wide fin、Z 码 trim、Heating pump→Cooling and
     Heating 出现在 transform_chain 且进入物化值; round4 复用既有求值路径。
  4. bulk literal fallback: literal-heavy spec → BULK_SOURCE_DERIVED_LITERAL_FALLBACK
     警告 (MATRIX_ROLLOUT 开关下 fail-closed); matrix-correct spec → 零警告
     (reverse guarantee); 合法 sets (客户名/日期/title/footer) → 零警告。
  5. matrix 验证规则: 未知 label/列、缺失 source、朝向、与块语义混用、
     required_coverage → 结构化缺陷 (exit 3 + corrective_action)。
  6. MOD Attention Map 对齐: resolve→record_map、map→field_map、
     transform→transform_chain、validate→validation (fixture-level,
     test_mod_attention_map.py 同款接缝)。
  7. 向后兼容: 块路径 plan 的 matrix=None / source_trace=[] 不变。
  8. ticket 01 (Matrix Field Locator V2): source/target 三形式 locator
     (legacy 单标签 / composite match / guarded row) + fail-closed
     0/1/>1 解析 (首命中废止), 物化/lineage 不回退, A/B/C schema 列
     零写, 负向契约锁定 (AMBIGUOUS / NOT_FOUND / ROW_GUARD_* /
     LOCATOR_INVALID / 超宽列)。

Run: python -m pytest tests/test_matrix_fillspec.py -q
"""

from __future__ import annotations

import copy
import csv
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import compile_fill  # noqa: E402
from _probe_fixtures import (  # noqa: E402
    BASE_SPEC,
    FIX_C_ENTRY_NAME,
    FIX_C_LABELS,
    FIX_C_RECORDS,
    MATRIX_BASE_SPEC,
    MATRIX_LABELS,
    MATRIX_LOCATOR_BASE_SPEC,
    MATRIX_RECORDS,
    make_fixture_c_workdir,
    make_matrix_locator_workdir,
    make_matrix_workdir,
    make_probe_workdir,
)
from _mod_catalog import parse_attention_map  # noqa: E402


# ── Shared helpers (test_optimization.py 同款接缝) ─────────────────────

def matrix_spec_with(wd: dict, base: dict = MATRIX_BASE_SPEC, **mutations) -> dict:
    spec = copy.deepcopy(base)
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


def compile_matrix(wd: dict, spec: dict) -> dict:
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


def _all_warn_codes(plan: dict) -> set:
    return {w.get("code") for w in plan.get("warnings", [])}


# ── 1. Matrix 物化契约 ─────────────────────────────────────────────────

class MatrixMaterializationContractTests(unittest.TestCase):
    """field_map × record_map → 目标格; plan writes + readback 由同一物化
    派生; write 数 == |field_map| × |record_map|; 无几百个 literal sets。"""

    def setUp(self):
        self.tmp_ctx = tempfile.TemporaryDirectory()
        self.tmp = Path(self.tmp_ctx.name)
        self.wd = make_matrix_workdir(self.tmp)
        self.wd["workdir"] = self.tmp

    def tearDown(self):
        self.tmp_ctx.cleanup()

    def test_cartesian_materialization_write_count(self):
        spec = matrix_spec_with(self.wd)
        plan = compile_matrix(self.wd, spec)
        n_fields = len(MATRIX_BASE_SPEC["mapping"]["targets"][0]
                       ["matrix"]["field_map"])
        n_records = len(MATRIX_BASE_SPEC["mapping"]["targets"][0]
                        ["matrix"]["record_map"])
        # 物化契约: 目标格数 = field_map × record_map (Real Case Replay 的
        # "plan 无几百个 literal sets" 的 fixture 替代 — 写入全部 matrix 物化)
        self.assertEqual(len(plan["writes"]), n_fields * n_records)
        self.assertEqual(len(plan["source_trace"]), n_fields * n_records)
        # 每个写入的 op 是 value set (与既有 value writes 同形, executor 不变)
        matrix_ops = [o for o in plan["operations"] if o["command"] == "set"]
        self.assertGreaterEqual(len(matrix_ops), n_fields * n_records)
        self.assertTrue(all("value" in o.get("props", {}) for o in matrix_ops))
        # 无几百个 literal sets: 只有 4 条合法固定值 sets (title/footer/客户/日期)
        self.assertEqual(len(plan["sets"]), 4)
        # matrix readback 期待全部由同一物化派生 (无手写 checks): 每个
        # matrix write 的格都在 readback 里且期待 == 物化值
        written_paths = {f"/S/{w['col']}{w['row']}" for w in plan["writes"]}
        rb_by_path = {rb["path"]: rb for rb in plan["readback"]}
        self.assertEqual(len(written_paths), n_fields * n_records)
        for w in plan["writes"]:
            rb = rb_by_path.get(f"/S/{w['col']}{w['row']}")
            self.assertIsNotNone(rb, f"write {w} 无 readback 期待")
            self.assertEqual(rb["kind"], "value")
            self.assertEqual(rb["expect"], w["value"])

    def test_matrix_metadata_and_lineage_shape(self):
        spec = matrix_spec_with(self.wd)
        plan = compile_matrix(self.wd, spec)
        m = plan["matrix"]
        self.assertIsNotNone(m)
        self.assertEqual(m["source"], "matrix_source")
        self.assertEqual(m["target_sheet"], "S")
        self.assertEqual(m["orientation"],
                         {"field_axis": "rows", "record_axis": "columns"})
        self.assertEqual(len(m["field_map"]), len(MATRIX_LABELS))
        self.assertEqual(len(m["record_map"]), len(MATRIX_RECORDS))
        for entry in plan["source_trace"]:
            self.assertEqual(set(entry.keys()),
                             {"target", "source", "transform_chain"})
            self.assertTrue(entry["target"].startswith("/S/"))
            self.assertTrue(entry["source"].startswith("matrix_source!"))
            self.assertIsInstance(entry["transform_chain"], list)
        # matrix 覆盖报告 (行身份: 每个请求字段标签都被解析)
        cov = plan["source_coverage"][0]
        self.assertEqual(cov["block"], "matrix")
        self.assertEqual(cov["matched"], len(MATRIX_LABELS))
        # matrix 写入不进 sets → reverse guarantee (物化不产生 literal 告警)
        self.assertNotIn("BULK_SOURCE_DERIVED_LITERAL_FALLBACK",
                         _all_warn_codes(plan))

    def test_key_outputs_must_be_written(self):
        spec = matrix_spec_with(self.wd, **{"validation.key_outputs": ["B3"]})
        plan = compile_matrix(self.wd, spec)
        self.assertEqual(plan["key_outputs"],
                         [{"path": "/S/B3", "kind": "value"}])
        spec2 = matrix_spec_with(self.wd, **{"validation.key_outputs": ["A99"]})
        codes = compile_fail_codes(self.wd, spec2)
        self.assertIn("KEY_OUTPUT_UNWRITTEN", codes)


# ── 2. Fixture C 端到端编译 (prepare 产物形态) ─────────────────────────

class FixtureCEndToEndTests(unittest.TestCase):
    """fixture_c_column_record_matrix (canonical column-record matrix) 的
    prepare 产物形态 → matrix spec → compile: 18 格 (6 标签 × 3 产品列),
    全部带 lineage, 无 literal 告警。"""

    def setUp(self):
        self.tmp_ctx = tempfile.TemporaryDirectory()
        self.tmp = Path(self.tmp_ctx.name)
        self.wd = make_fixture_c_workdir(self.tmp)
        self.wd["workdir"] = self.tmp

    def tearDown(self):
        self.tmp_ctx.cleanup()

    def _spec(self) -> dict:
        spec = matrix_spec_with(self.wd)
        spec["inputs"]["sources"] = ["fixture_c_column_record_matrix.xlsx"]
        spec["inputs"]["target"] = "fixture_c_column_record_matrix.xlsx"
        spec["inputs"]["source_sheets"] = [
            {"source": "fixture_c_column_record_matrix.xlsx",
             "sheets": ["Sheet1"]}]
        spec["inputs"]["target_sheet"] = "Sheet1"
        tgt = spec["mapping"]["targets"][0]
        tgt["sheet"] = "Sheet1"
        tgt["matrix"]["source"] = {
            "flatten": FIX_C_ENTRY_NAME, "field_axis": "rows",
            "record_axis": "columns", "field_label_column": "A"}
        tgt["matrix"]["target"] = {
            "field_axis": "rows", "record_axis": "columns",
            "field_label_column": "A"}
        tgt["matrix"]["field_map"] = [
            {"source": label, "target": label} for label in FIX_C_LABELS]
        tgt["matrix"]["record_map"] = [
            {"record": rec, "source_column": col, "target_column": col}
            for rec, col in zip(FIX_C_RECORDS, ("B", "C", "D"))]
        tgt["sets"] = []          # fixture C 无固定 title/footer sets
        spec["validation"]["key_outputs"] = ["B3"]   # Capacity 12K
        spec["lineage"] = [{"source": f"{FIX_C_ENTRY_NAME}_flat.csv",
                            "role": "primary", "note": "matrix 自填充"}]
        return spec

    def test_fixture_c_compile_against_prepare_artifacts(self):
        plan = compile_matrix(self.wd, self._spec())
        self.assertEqual(len(plan["writes"]),
                         len(FIX_C_LABELS) * len(FIX_C_RECORDS))
        self.assertEqual(len(plan["source_trace"]),
                         len(FIX_C_LABELS) * len(FIX_C_RECORDS))
        # 全部写入均带 lineage (每格可溯 — Real Case Replay"lineage 完整"
        # 的 fixture 替代断言)
        write_paths = {f"/Sheet1/{w['col']}{w['row']}" for w in plan["writes"]}
        trace_paths = {t["target"] for t in plan["source_trace"]}
        self.assertEqual(write_paths, trace_paths)
        self.assertEqual(plan["sets"], [])
        self.assertEqual(plan["key_outputs"],
                         [{"path": "/Sheet1/B3", "kind": "value"}])
        # matrix 元数据: 同一展平条目承担源与目标 (自填充)
        self.assertEqual(plan["matrix"]["source"], FIX_C_ENTRY_NAME)

    def test_fixture_c_lineage_points_to_source_cell(self):
        """fixture 坐标版 SPEC!D15: Compressor/24K 目标格 D7 ← 源格 D7
        (direct copy, 空 transform_chain)。"""
        plan = compile_matrix(self.wd, self._spec())
        entry = next(t for t in plan["source_trace"]
                     if t["target"] == "/Sheet1/D7")
        self.assertEqual(entry["source"], f"{FIX_C_ENTRY_NAME}!D7")
        self.assertEqual(entry["transform_chain"], [])
        # 该格物化值 = 源格值 (VS3)
        write = next(w for w in plan["writes"]
                     if (w["col"], w["row"]) == ("D", 7))
        self.assertEqual(write["value"], "VS3")


# ── 3. Transforms 可执行 ───────────────────────────────────────────────

class MatrixTransformExecutionTests(unittest.TestCase):
    """受控翻译 / trim / round4 进入 transform_chain 且作用于物化值;
    lineage 抽查特定格 (SPEC!D15 的 fixture 版本)。"""

    def setUp(self):
        self.tmp_ctx = tempfile.TemporaryDirectory()
        self.tmp = Path(self.tmp_ctx.name)
        self.wd = make_matrix_workdir(self.tmp)
        self.wd["workdir"] = self.tmp

    def tearDown(self):
        self.tmp_ctx.cleanup()

    def _spec(self) -> dict:
        spec = matrix_spec_with(self.wd)
        spec["mapping"]["transforms"] = [
            {"name": "fin_translate", "function": "controlled_translation",
             "translations": {"宽片": "wide fin"}},
            {"name": "type_translate", "function": "controlled_translation",
             "translations": {"Heating pump": "Cooling and Heating"}},
        ]
        fm = spec["mapping"]["targets"][0]["matrix"]["field_map"]
        by_label = {fe["source"]: fe for fe in fm}
        by_label["Operation"]["transforms"] = ["type_translate"]
        by_label["Fin"]["transforms"] = ["fin_translate"]
        by_label["Z 码"]["transforms"] = ["trim"]
        by_label["Capacity"]["transforms"] = ["round4"]
        return spec

    def _cell(self, plan, col: str, row: int) -> dict:
        return next(w for w in plan["writes"]
                    if (w["col"], w["row"]) == (col, row))

    def test_controlled_translation_applied_to_materialized_values(self):
        plan = compile_matrix(self.wd, self._spec())
        # 宽片→wide fin (Fin/12K → B10); 18K/24K 宽片同样受控
        self.assertEqual(self._cell(plan, "B", 10)["value"], "wide fin")
        self.assertEqual(self._cell(plan, "C", 10)["value"], "wide fin")
        # 高效片 无词表条目 → 原样通过 (deterministic pass-through)
        self.assertEqual(self._cell(plan, "D", 10)["value"], "高效片")
        # Heating pump→Cooling and Heating (Operation/12K → B9)
        self.assertEqual(self._cell(plan, "B", 9)["value"],
                         "Cooling and Heating")
        # 已是目标词的 18K/24K → pass-through
        self.assertEqual(self._cell(plan, "D", 9)["value"],
                         "Cooling and Heating")

    def test_zcode_trim_and_round4(self):
        plan = compile_matrix(self.wd, self._spec())
        # Z 码 trim: " Z2U20101009819 " → "Z2U20101009819",
        # "Z2U20101009821 " → "Z2U20101009821"
        self.assertEqual(self._cell(plan, "B", 11)["value"],
                         "Z2U20101009819")
        self.assertEqual(self._cell(plan, "D", 11)["value"],
                         "Z2U20101009821")
        # round4 复用既有求值路径 (Capacity/12K)
        self.assertEqual(self._cell(plan, "B", 3)["value"], "12000")

    def test_transform_chain_mirrors_materialized_transforms(self):
        plan = compile_matrix(self.wd, self._spec())
        def chain(target: str) -> list:
            return next(t["transform_chain"] for t in plan["source_trace"]
                        if t["target"] == target)
        self.assertEqual(chain("/S/B10"), ["fin_translate"])
        self.assertEqual(chain("/S/B9"), ["type_translate"])
        self.assertEqual(chain("/S/B11"), ["trim"])
        self.assertEqual(chain("/S/B3"), ["round4"])
        # 无 transforms 的字段 → 空链 (direct copy), 仍在 lineage 里
        self.assertEqual(chain("/S/B6"), [])

    def test_lineage_spot_check_zcode_24k(self):
        """SPECT!D15 的 fixture 版本: Z 码/24K 目标格 D11 ← 源格 D11
        [trim] — 机器可验证的 lineage 指向正确源格。"""
        plan = compile_matrix(self.wd, self._spec())
        entry = next(t for t in plan["source_trace"]
                     if t["target"] == "/S/D11")
        self.assertEqual(entry, {
            "target": "/S/D11",
            "source": "matrix_source!D11",
            "transform_chain": ["trim"],
        })

    def test_unknown_transform_is_defect(self):
        spec = self._spec()
        spec["mapping"]["targets"][0]["matrix"]["field_map"] = \
            [fe for fe in spec["mapping"]["targets"][0]["matrix"]["field_map"]
             if fe["source"] != "Z 码"] + \
            [{"source": "Capacity", "target": "Capacity",
              "transforms": ["nope_transform"]}]
        codes = compile_fail_codes(self.wd, spec)
        self.assertIn("TRANSFORM_UNKNOWN", codes)


# ── 4. Bulk literal fallback 审计 ──────────────────────────────────────

class BulkLiteralFallbackAuditTests(unittest.TestCase):
    """bulk source-derived literal sets 告警 / fail-closed; matrix-correct
    与合法 sets (客户名/日期/title/footer) 零警告 (reverse guarantee)。"""

    def setUp(self):
        self.tmp_ctx = tempfile.TemporaryDirectory()
        self.tmp = Path(self.tmp_ctx.name)
        self.wd = make_matrix_workdir(self.tmp)
        self.wd["workdir"] = self.tmp

    def tearDown(self):
        self.tmp_ctx.cleanup()

    def _literal_heavy_spec(self) -> dict:
        """blocks: [] + 绝大多数字面值出现在源 CSV 的 sets — 客户参数表
        原病理 (316 个烘焙 literal) 的缩小版。"""
        spec = matrix_spec_with(self.wd)
        tgt = spec["mapping"]["targets"][0]
        del tgt["matrix"]
        tgt["base_last_row"] = 14
        tgt["blocks"] = []
        tgt["sets"] = [
            {"path": "A1", "value": "Product Parameter Matrix (template)"},
            {"path": "B3", "value": "12000"},     # 源值
            {"path": "C3", "value": "18000"},     # 源值
            {"path": "D3", "value": "24000"},     # 源值
            {"path": "B4", "value": "11"},        # 源值
            {"path": "C4", "value": "10.8"},      # 源值
            {"path": "B7", "value": "VS1"},       # 源值
        ]
        spec["validation"]["key_outputs"] = ["A1"]
        return spec

    def _legit_sets_spec(self) -> dict:
        """块路径 + 全部合法 sets (title/客户/日期/footer — 值不在源池)。"""
        spec = matrix_spec_with(self.wd)
        tgt = spec["mapping"]["targets"][0]
        del tgt["matrix"]
        tgt["base_last_row"] = 14
        tgt["blocks"] = []
        tgt["sets"] = [
            {"path": "A1", "value": "Product Parameter Matrix (template)"},
            {"path": "A12", "value": "* fixed footer"},
            {"path": "A13", "value": "ACME"},
            {"path": "A14", "value": "2026-01-01"},
        ]
        spec["validation"]["key_outputs"] = ["A1"]
        return spec

    def test_literal_heavy_warns_by_default(self):
        plan = compile_matrix(self.wd, self._literal_heavy_spec())
        codes = _all_warn_codes(plan)
        self.assertIn("BULK_SOURCE_DERIVED_LITERAL_FALLBACK", codes)
        entry = next(w for w in plan["warnings"]
                     if w["code"] == "BULK_SOURCE_DERIVED_LITERAL_FALLBACK")
        self.assertGreaterEqual(entry["derived"], 5)
        self.assertGreaterEqual(entry["total"], 6)

    def test_literal_heavy_fail_closed_under_rollout_switch(self):
        old = compile_fill.MATRIX_ROLLOUT["literal_fallback_fail_closed"]
        compile_fill.MATRIX_ROLLOUT["literal_fallback_fail_closed"] = True
        try:
            codes = compile_fail_codes(self.wd, self._literal_heavy_spec())
            self.assertIn("BULK_SOURCE_DERIVED_LITERAL_FALLBACK", codes)
        finally:
            compile_fill.MATRIX_ROLLOUT["literal_fallback_fail_closed"] = old

    def test_matrix_correct_spec_zero_warning(self):
        """reverse guarantee: matrix 物化写入不是 sets; 平凡固定值 sets
        (值不在源池) → 零 BULK 告警。"""
        plan = compile_matrix(self.wd, matrix_spec_with(self.wd))
        self.assertNotIn("BULK_SOURCE_DERIVED_LITERAL_FALLBACK",
                         _all_warn_codes(plan))
        self.assertEqual(len(plan["sets"]), 4)   # 固定值 sets 合法并存

    def test_legit_sets_no_warning(self):
        """合法 sets (title/客户名/日期/fixed footer) 值不在源池 → 零告警,
        即使 ≥ 审计最小条数。"""
        plan = compile_matrix(self.wd, self._legit_sets_spec())
        self.assertNotIn("BULK_SOURCE_DERIVED_LITERAL_FALLBACK",
                         _all_warn_codes(plan))

    def test_audit_does_not_route(self):
        """审计不是路由依据 (记录数量阈值禁令只约束 routing): 告警 spec 仍
        编译通过并产出 plan (warning, 不阻断)。"""
        plan = compile_matrix(self.wd, self._literal_heavy_spec())
        self.assertGreater(len(plan["operations"]), 0)


# ── 5. Matrix 验证规则 ─────────────────────────────────────────────────

class MatrixValidationTests(unittest.TestCase):
    """未知 label/列、缺失 source、朝向、块混用、required_coverage →
    结构化缺陷 (exit 3)。"""

    def setUp(self):
        self.tmp_ctx = tempfile.TemporaryDirectory()
        self.tmp = Path(self.tmp_ctx.name)
        self.wd = make_matrix_workdir(self.tmp)
        self.wd["workdir"] = self.tmp

    def tearDown(self):
        self.tmp_ctx.cleanup()

    def wd_spec(self) -> dict:
        return matrix_spec_with(self.wd)

    def test_orientation_not_rows_columns_rejected(self):
        spec = self.wd_spec()
        spec["mapping"]["targets"][0]["matrix"]["source"]["field_axis"] = "columns"
        spec["mapping"]["targets"][0]["matrix"]["source"]["record_axis"] = "rows"
        codes = compile_fail_codes(self.wd, spec)
        self.assertIn("MATRIX_ORIENTATION_NOT_ROLLED_OUT", codes)

    def test_unknown_source_flatten_rejected(self):
        spec = self.wd_spec()
        spec["mapping"]["targets"][0]["matrix"]["source"]["flatten"] = "nope"
        codes = compile_fail_codes(self.wd, spec)
        self.assertIn("MATRIX_SOURCE_UNKNOWN", codes)

    def test_unknown_field_label_rejected(self):
        """Ticket 01: 旧 MATRIX_FIELD_LABEL_NOT_FOUND 收敛进
        MATRIX_FIELD_LOCATOR_NOT_FOUND (label 形式 0 命中 → NOT_FOUND)。"""
        spec = self.wd_spec()
        spec["mapping"]["targets"][0]["matrix"]["field_map"][0] = \
            {"source": "Nope", "target": "Capacity"}
        codes = compile_fail_codes(self.wd, spec)
        self.assertIn("MATRIX_FIELD_LOCATOR_NOT_FOUND", codes)

    def test_unknown_target_label_rejected(self):
        """Ticket 01: label 形式 0 命中 → MATRIX_FIELD_LOCATOR_NOT_FOUND
        (收敛自 MATRIX_FIELD_LABEL_NOT_FOUND)。"""
        spec = self.wd_spec()
        spec["mapping"]["targets"][0]["matrix"]["field_map"][0] = \
            {"source": "Capacity", "target": "Nope"}
        codes = compile_fail_codes(self.wd, spec)
        self.assertIn("MATRIX_FIELD_LOCATOR_NOT_FOUND", codes)

    def test_record_column_out_of_range_rejected(self):
        spec = self.wd_spec()
        spec["mapping"]["targets"][0]["matrix"]["record_map"][0] = \
            {"record": "12K", "source_column": "Z", "target_column": "D"}
        codes = compile_fail_codes(self.wd, spec)
        self.assertIn("MATRIX_COLUMN_INVALID", codes)

    def test_target_column_beyond_digest_rejected(self):
        spec = self.wd_spec()
        spec["mapping"]["targets"][0]["matrix"]["record_map"][0] = \
            {"record": "12K", "source_column": "B", "target_column": "Z"}
        codes = compile_fail_codes(self.wd, spec)
        self.assertIn("MATRIX_COLUMN_INVALID", codes)

    def test_mixed_with_block_semantics_rejected(self):
        spec = self.wd_spec()
        spec["mapping"]["targets"][0]["clone_roles"] = [
            {"role": "data", "template_row": 3}]
        codes = compile_fail_codes(self.wd, spec)
        self.assertIn("MATRIX_MIXED_WITH_BLOCK_SEMANTICS", codes)

    def test_required_coverage_rejected(self):
        spec = self.wd_spec()
        spec["validation"]["required_coverage"] = [
            {"source": "matrix_source_flat.csv", "rows": [3]}]
        codes = compile_fail_codes(self.wd, spec)
        self.assertIn("MATRIX_REQUIRED_COVERAGE_UNSUPPORTED", codes)

    def test_empty_field_map_rejected(self):
        spec = self.wd_spec()
        spec["mapping"]["targets"][0]["matrix"]["field_map"] = []
        codes = compile_fail_codes(self.wd, spec)
        self.assertIn("MATRIX_INVALID", codes)

    def test_pptx_matrix_rejected(self):
        spec = self.wd_spec()
        spec["inputs"]["platform"] = "pptx"
        codes = compile_fail_codes(self.wd, spec)
        self.assertIn("PPTX_CAPABILITY_NOT_ROLLED_OUT", codes)

    def test_set_colliding_with_matrix_cell_rejected(self):
        spec = self.wd_spec()
        spec["mapping"]["targets"][0]["sets"].append(
            {"path": "B3", "value": "overwrite"})   # 与 Capacity/12K 格冲突
        codes = compile_fail_codes(self.wd, spec)
        self.assertIn("DUPLICATE_TARGET_WRITE", codes)


# ── 9. Ticket 01: Matrix Field Locator V2 (三形式 + fail-closed) ────────

class MatrixFieldLocatorV2Tests(unittest.TestCase):
    """field_map[].source/.target 三形式 locator (legacy 单标签 / composite
    match / guarded explicit row) + fail-closed 0/1/>1 解析 (替换旧"首命中",
    即本票最重要的 correctness 修复): 编译接受性、缺陷码、物化写入坐标、
    lineage 形状 — 只测外部行为, 不测内部实现。

    Fixture: make_matrix_locator_workdir (层级参数表: A=section 重复
    Cool×3/Heat×2, B=sub-label 重复 Capacity×2, C=unit 最终消歧 Btu/h vs W,
    D/E/F=records 12K/18K/24K; row=展平 CSV 的 orig 行号)。"""

    def setUp(self):
        self.tmp_ctx = tempfile.TemporaryDirectory()
        self.tmp = Path(self.tmp_ctx.name)
        self.wd = make_matrix_locator_workdir(self.tmp)
        self.wd["workdir"] = self.tmp

    def tearDown(self):
        self.tmp_ctx.cleanup()

    def _spec(self, field_map, key_outputs=None) -> dict:
        mutations = {}
        if key_outputs is not None:
            mutations["validation.key_outputs"] = key_outputs
        spec = matrix_spec_with(self.wd, base=MATRIX_LOCATOR_BASE_SPEC,
                                **mutations)
        spec["mapping"]["targets"][0]["matrix"]["field_map"] = field_map
        return spec

    @staticmethod
    def _writes_by(plan) -> dict:
        return {(w["col"], w["row"]): w["value"] for w in plan["writes"]}

    def _trace(self, plan, target: str) -> dict:
        return next(t for t in plan["source_trace"] if t["target"] == target)

    # ── AC1: legacy 唯一标签继续工作 (行为不回归) ──
    def test_ac1_legacy_unique_label_materializes(self):
        spec = self._spec(
            [{"source": "Voltage", "target": "Voltage", "transforms": ["trim"]}],
            key_outputs=["D15"])
        plan = compile_matrix(self.wd, spec)
        writes = self._writes_by(plan)
        self.assertEqual(writes[("D", 15)], "220")
        self.assertEqual(writes[("E", 15)], "220")
        self.assertEqual(writes[("F", 15)], "220")
        # lineage 精确三键 + 正确源格 (orig 7)
        self.assertEqual(self._trace(plan, "/S/D15"),
                         {"target": "/S/D15",
                          "source": "matrix_locator_source!D7",
                          "transform_chain": ["trim"]})

    # ── AC2: legacy 重复标签 fail-closed (禁止首命中) ──
    def test_ac2_legacy_duplicate_label_ambiguous(self):
        spec = self._spec([{"source": "Cooling", "target": "Cooling"}],
                          key_outputs=[])
        codes = compile_fail_codes(self.wd, spec)
        self.assertIn("MATRIX_FIELD_LOCATOR_AMBIGUOUS", codes)
        self.assertNotIn("MATRIX_FIELD_LOCATOR_NOT_FOUND", codes)

    # ── AC3: composite 唯一解析 → PASS + 正确物化 + lineage ──
    def test_ac3_composite_match_unique_materializes(self):
        spec = self._spec(
            [{"source": {"match": {"A": "Cooling", "B": "Capacity", "C": "Btu/h"}},
              "target": {"match": {"A": "Cooling", "B": "Capacity", "C": "Btu/h"}},
              "transforms": ["trim"]}],
            key_outputs=["D10"])
        plan = compile_matrix(self.wd, spec)
        writes = self._writes_by(plan)
        self.assertEqual(writes[("D", 10)], "12000")
        self.assertEqual(writes[("E", 10)], "18000")
        self.assertEqual(writes[("F", 10)], "24000")
        self.assertEqual(self._trace(plan, "/S/E10"),
                         {"target": "/S/E10",
                          "source": "matrix_locator_source!E2",
                          "transform_chain": ["trim"]})

    # ── AC4: composite 仍重复 (消歧不足) → AMBIGUOUS ──
    def test_ac4_composite_match_duplicate_ambiguous(self):
        spec = self._spec(
            [{"source": {"match": {"A": "Cooling", "B": "Capacity"}},
              "target": {"match": {"A": "Cooling", "B": "Capacity"}}}],
            key_outputs=[])
        codes = compile_fail_codes(self.wd, spec)
        self.assertIn("MATRIX_FIELD_LOCATOR_AMBIGUOUS", codes)

    # ── AC5: guarded row (row + expect 吻合) → PASS + 正确值 ──
    def test_ac5_guarded_row_matching_expect_compiles(self):
        spec = self._spec(
            [{"source": {"row": 3, "expect": {"A": "Cooling", "B": "Capacity",
                                              "C": "W"}},
              "target": {"row": 11, "expect": {"A": "Cooling", "B": "Capacity",
                                               "C": "W"}}}],
            key_outputs=["D11"])
        plan = compile_matrix(self.wd, spec)
        writes = self._writes_by(plan)
        self.assertEqual(writes[("D", 11)], "3500")
        self.assertEqual(writes[("E", 11)], "5300")
        self.assertEqual(writes[("F", 11)], "7000")
        # 源格 = 记录列 × 该行 orig (guarded 也用 orig 行号 lineage)
        self.assertEqual(self._trace(plan, "/S/F11"),
                         {"target": "/S/F11",
                          "source": "matrix_locator_source!F3",
                          "transform_chain": []})

    # ── AC6: guard 事实不符 → ROW_GUARD_MISMATCH (逐列 expected vs actual) ──
    def test_ac6_guarded_row_guard_mismatch(self):
        spec = self._spec(
            [{"source": {"row": 3, "expect": {"A": "Cooling", "B": "Power",
                                              "C": "W"}},
              "target": {"row": 11, "expect": {"A": "Cooling", "B": "Capacity",
                                               "C": "W"}}}],
            key_outputs=["D11"])
        from io import StringIO
        buf = StringIO()
        old = sys.stderr
        sys.stderr = buf
        try:
            with self.assertRaises(SystemExit) as cm:
                compile_fill.compile_spec(spec, self.wd["manifest"],
                                          self.wd["workdir"])
            self.assertEqual(cm.exception.code, 3)
        finally:
            sys.stderr = old
        payload = json.loads(buf.getvalue())
        codes = [d["code"] for d in payload["defects"]]
        self.assertIn("MATRIX_FIELD_ROW_GUARD_MISMATCH", codes)
        msg = next(d["message"] for d in payload["defects"]
                   if d["code"] == "MATRIX_FIELD_ROW_GUARD_MISMATCH")
        self.assertIn("expect B='Power'", msg)
        self.assertIn("实际 B='Capacity'", msg)

    # ── AC7: lineage 两种 locator 都产 {target, source, transform_chain} ──
    def test_ac7_source_trace_shape_composite_and_guarded(self):
        spec = self._spec(
            [{"source": {"match": {"A": "Cooling", "B": "Capacity", "C": "Btu/h"}},
              "target": {"match": {"A": "Cooling", "B": "Capacity", "C": "Btu/h"}}},
             {"source": {"row": 5, "expect": {"A": "Heating", "B": "Capacity",
                                              "C": "Btu/h"}},
              "target": {"row": 13, "expect": {"A": "Heating", "B": "Capacity",
                                               "C": "Btu/h"}}}],
            key_outputs=["D10"])
        plan = compile_matrix(self.wd, spec)
        self.assertEqual(len(plan["source_trace"]), 2 * 3)
        for entry in plan["source_trace"]:
            self.assertEqual(set(entry.keys()),
                             {"target", "source", "transform_chain"})
        # composite 与 guarded-row 两条路径都在 lineage 里且指对源格
        self.assertEqual(self._trace(plan, "/S/D10")["source"],
                         "matrix_locator_source!D2")
        self.assertEqual(self._trace(plan, "/S/D13")["source"],
                         "matrix_locator_source!D5")

    # ── AC8: 模板 schema 列 (A/B/C) 永不在写集 ──
    def test_ac8_schema_columns_never_in_write_set(self):
        spec = self._spec(
            [{"source": "Voltage", "target": "Voltage"},
             {"source": {"match": {"A": "Cooling", "B": "Capacity", "C": "Btu/h"}},
              "target": {"match": {"A": "Cooling", "B": "Capacity", "C": "Btu/h"}}},
             {"source": {"row": 3, "expect": {"A": "Cooling", "B": "Capacity",
                                              "C": "W"}},
              "target": {"row": 11, "expect": {"A": "Cooling", "B": "Capacity",
                                               "C": "W"}}}],
            key_outputs=["D10"])
        plan = compile_matrix(self.wd, spec)
        writes = plan["writes"]
        self.assertEqual(len(writes), 3 * 3)   # 3 字段 × 3 record 列
        self.assertTrue(all(w["col"] in {"D", "E", "F"} for w in writes),
                        f"A/B/C 模板 schema 列出现了写入: {writes}")
        self.assertFalse(any(w["col"] in {"A", "B", "C"} for w in writes))

    # ── AC9: 不得用 literal sets 绕开 Matrix 主路径 ──
    def test_ac9_no_literal_sets_bypass_matrix_main_path(self):
        spec = self._spec(
            [{"source": "Voltage", "target": "Voltage"},
             {"source": {"match": {"A": "Cooling", "B": "Capacity", "C": "Btu/h"}},
              "target": {"match": {"A": "Cooling", "B": "Capacity", "C": "Btu/h"}}},
             {"source": {"row": 3, "expect": {"A": "Cooling", "B": "Capacity",
                                              "C": "W"}},
              "target": {"row": 11, "expect": {"A": "Cooling", "B": "Capacity",
                                               "C": "W"}}}],
            key_outputs=["D10"])
        plan = compile_matrix(self.wd, spec)
        # reverse guarantee: matrix 物化写入不是 sets → 零 BULK literal 告警
        self.assertNotIn("BULK_SOURCE_DERIVED_LITERAL_FALLBACK",
                         _all_warn_codes(plan))
        self.assertEqual(len(plan["sets"]), 4)   # 只有固定值 sets (title/客户/日期/footer)
        self.assertEqual(len(plan["writes"]), 9)   # matrix 主路径物化

    # ── 锁定负向契约 (fail-closed 的锁) ──
    def test_negative_bare_row_guard_required(self):
        spec = self._spec(
            [{"source": {"row": 3},
              "target": {"row": 11, "expect": {"B": "Capacity"}}}],
            key_outputs=[])
        codes = compile_fail_codes(self.wd, spec)
        self.assertIn("MATRIX_FIELD_ROW_GUARD_REQUIRED", codes)

    def test_negative_row_out_of_range(self):
        spec = self._spec(
            [{"source": {"row": 999, "expect": {"A": "Cooling"}},
              "target": {"row": 11, "expect": {"B": "Capacity"}}}],
            key_outputs=[])
        codes = compile_fail_codes(self.wd, spec)
        self.assertIn("MATRIX_FIELD_ROW_OUT_OF_RANGE", codes)

    def test_negative_empty_dict_invalid(self):
        spec = self._spec(
            [{"source": {},
              "target": {"row": 11, "expect": {"B": "Capacity"}}}],
            key_outputs=[])
        codes = compile_fail_codes(self.wd, spec)
        self.assertIn("MATRIX_FIELD_LOCATOR_INVALID", codes)

    def test_negative_both_match_and_row_invalid(self):
        spec = self._spec(
            [{"source": {"match": {"A": "Cooling"}, "row": 3},
              "target": {"row": 11, "expect": {"B": "Capacity"}}}],
            key_outputs=[])
        codes = compile_fail_codes(self.wd, spec)
        self.assertIn("MATRIX_FIELD_LOCATOR_INVALID", codes)

    def test_negative_non_string_non_dict_invalid(self):
        spec = self._spec(
            [{"source": 42,
              "target": {"row": 11, "expect": {"B": "Capacity"}}}],
            key_outputs=[])
        codes = compile_fail_codes(self.wd, spec)
        self.assertIn("MATRIX_FIELD_LOCATOR_INVALID", codes)

    def test_negative_composite_no_hit_not_found(self):
        spec = self._spec(
            [{"source": {"match": {"A": "Cooling", "B": "Capacity", "C": "kW"}},
              "target": {"row": 11, "expect": {"B": "Capacity"}}}],
            key_outputs=[])
        codes = compile_fail_codes(self.wd, spec)
        self.assertIn("MATRIX_FIELD_LOCATOR_NOT_FOUND", codes)

    def test_negative_match_column_out_of_width(self):
        spec = self._spec(
            [{"source": {"match": {"A": "Cooling", "Z": "x"}},
              "target": {"row": 11, "expect": {"B": "Capacity"}}}],
            key_outputs=[])
        codes = compile_fail_codes(self.wd, spec)
        self.assertIn("MATRIX_COLUMN_INVALID", codes)

    def test_negative_match_column_not_a_letter(self):
        spec = self._spec(
            [{"source": {"match": {"1": "x"}},
              "target": {"row": 11, "expect": {"B": "Capacity"}}}],
            key_outputs=[])
        codes = compile_fail_codes(self.wd, spec)
        self.assertIn("MATRIX_FIELD_LOCATOR_INVALID", codes)


# ── 6. MOD Attention Map 对齐 (D6) ─────────────────────────────────────

class AttentionMapAlignmentTests(unittest.TestCase):
    """Attention Map 四组 (resolve/map/transform/validate) ↔ matrix FillSpec
    消费位: record_map / field_map / transform_chain / validation。
    fixture-level, parse_attention_map 同款接缝 (test_mod_attention_map)。"""

    def _mod_with_attention_map(self) -> str:
        return (
            "# MOD matrix-attention\n\n"
            "## Metadata\n\n- Scope Signals: s::1\n\n"
            "## Attention Map\n\n"
            "- resolve: SRC-001, ID-001\n"
            "- map: FLD-001, SEC-001, FMT-001\n"
            "- transform: TRN-001\n"
            "- validate: VAL-001, VAL-002\n\n"
            "## 业务场景上下文\n\n"
            "| Rule ID | Group | Gate | Description | Applies to | Notes |\n"
            "|---------|-------|------|-------------|------------|-------|\n"
            "| SRC-001 | mapping | mod_gate | Source authority. | * | |\n"
            "| ID-001 | mapping | mod_gate | Record identity. | * | |\n"
            "| FLD-001 | mapping | mod_gate | Field mapping. | * | |\n"
            "| SEC-001 | mapping | mod_gate | Field scope. | * | |\n"
            "| FMT-001 | mapping | mod_gate | Product column layout. | * | |\n"
            "| TRN-001 | mapping | mod_gate | Controlled translation. | * | |\n"
            "| VAL-001 | validation | execution_gate | Scope completeness. | * | |\n"
            "| VAL-002 | validation | execution_gate | Lineage evidence. | * | |\n"
        )

    def test_attention_map_groups_consumed_by_matrix_spec(self):
        am = parse_attention_map(self._mod_with_attention_map())
        self.assertEqual(list(am.keys()),
                         ["resolve", "map", "transform", "validate"])
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            wd = make_matrix_workdir(tmp)
            wd["workdir"] = tmp
            spec = matrix_spec_with(wd)
            # transform 组: 受控翻译进入 field_map 的 transform_chain
            spec["mapping"]["transforms"] = [
                {"name": "fin_translate", "function": "controlled_translation",
                 "translations": {"宽片": "wide fin"}}]
            fm = spec["mapping"]["targets"][0]["matrix"]["field_map"]
            by_label = {fe["source"]: fe for fe in fm}
            by_label["Fin"]["transforms"] = ["fin_translate"]
            spec["validation"]["key_outputs"] = ["B3"]
            plan = compile_matrix(wd, spec)
            matrix = spec["mapping"]["targets"][0]["matrix"]
            # resolve → record_map (哪些 record 被解析/映射进目标产品列)
            self.assertGreaterEqual(len(matrix["record_map"]), len(am["resolve"]))
            # map → field_map (字段角色映射)
            self.assertGreaterEqual(len(matrix["field_map"]), len(am["map"]))
            # transform → transform_chain (可执行受控转换)
            chains = [fe.get("transforms") or []
                      for fe in matrix["field_map"]]
            self.assertTrue(any(chains), "transform 组必须落入 field_map transforms")
            self.assertIn("fin_translate",
                          plan["source_trace"][0]["transform_chain"]
                          or next(t["transform_chain"] for t in
                                  plan["source_trace"]
                                  if t["target"] == "/S/B10"))
            # validate → Gate assertions (validation 三件套; Ticket 07 消费)
            self.assertTrue(spec["validation"].get("key_outputs"))
            self.assertEqual(plan["key_outputs"][0]["path"], "/S/B3")

    def test_attention_map_order_is_canonical(self):
        """四组固定顺序 (resolve → map → transform → validate) — 与
        D6 对齐契约同源, 防顺序漂移。"""
        am = parse_attention_map(self._mod_with_attention_map())
        self.assertEqual(list(am.keys()),
                         ["resolve", "map", "transform", "validate"])


class AttentionMapContractTextTests(unittest.TestCase):
    """SKILL.md §3 + FILLSPEC.md 的矩阵/Attention Map 对齐措辞 pin
    (D6 — 契约文字稳定, 测试可发现)。"""

    def test_skill_matrix_bullet_present(self):
        text = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("Matrix 一等表达", text)
        self.assertIn("source lineage 一等输出", text)
        self.assertIn("source_trace.json", text)
        self.assertIn("BULK_SOURCE_DERIVED_LITERAL_FALLBACK", text)
        self.assertIn("resolve→`record_map`", text)
        self.assertIn("transform→`transform_chain`", text)
        self.assertIn("validate→Gate assertions", text)

    def test_fillspec_matrix_section_and_alignment(self):
        text = (SKILL_ROOT / "references" / "FILLSPEC.md").read_text(encoding="utf-8")
        self.assertIn("### 矩阵映射 (matrix)", text)
        self.assertIn("MATRIX_ORIENTATION_NOT_ROLLED_OUT", text)
        self.assertIn("MATRIX_MIXED_WITH_BLOCK_SEMANTICS", text)
        self.assertIn("source_trace.json", text)
        self.assertIn("resolve → `record_map`", text)
        self.assertIn("map → `field_map`", text)
        self.assertIn("transform → `transform_chain`", text)
        self.assertIn("validate → Gate assertions", text)
        self.assertIn("compile-audit", text)
        self.assertIn("不是路由依据", text)


# ── 7. 向后兼容 + 内置 transform 接缝 ──────────────────────────────────

class MatrixBackwardCompatTests(unittest.TestCase):
    """块路径 spec 的 plan 形状不变 (matrix=None / source_trace=[]);
    内置 trim / controlled_translation 沿用 columns 求值路径。"""

    def setUp(self):
        self.tmp_ctx = tempfile.TemporaryDirectory()
        self.tmp = Path(self.tmp_ctx.name)

    def tearDown(self):
        self.tmp_ctx.cleanup()

    def test_block_path_plan_unchanged(self):
        wd = make_probe_workdir(self.tmp)
        spec = copy.deepcopy(BASE_SPEC)
        spec["fingerprints"] = {
            "source_structure": wd["manifest"]["fingerprints"]["source_structure"],
            "target_structure": wd["manifest"]["fingerprints"]["target_structure"],
        }
        plan = compile_fill.compile_spec(spec, wd["manifest"], self.tmp)
        self.assertIsNone(plan["matrix"])
        self.assertEqual(plan["source_trace"], [])
        self.assertGreater(len(plan["writes"]), 0)

    def test_trim_builtin_in_columns_chain(self):
        wd = make_probe_workdir(self.tmp, n_source_rows=1)
        # 给源值加尾随空白 → trim 内置转换在 columns 路径生效 (共享求值路径)
        with open(self.tmp / "source_maoli_flat.csv", "w", newline="",
                  encoding="utf-8-sig") as f:
            csv.writer(f).writerow(["家用", "12K", " Z001 ", "F-1", "C-1",
                                    "1", "2", "3", 101])
        spec = copy.deepcopy(BASE_SPEC)
        spec["fingerprints"] = {
            "source_structure": wd["manifest"]["fingerprints"]["source_structure"],
            "target_structure": wd["manifest"]["fingerprints"]["target_structure"],
        }
        spec["mapping"]["targets"][0]["columns"] = [
            {"source": "A", "target": "A"},
            {"source": "B", "target": "B"},
            {"source": "C", "target": "C", "transforms": ["trim"]}]
        spec["validation"]["key_outputs"] = ["C7"]
        plan = compile_fill.compile_spec(spec, wd["manifest"], self.tmp)
        writes = {w["col"]: w for w in plan["writes"]}
        self.assertEqual(writes["C"]["value"], "Z001")

    def test_controlled_translation_in_columns_chain(self):
        wd = make_probe_workdir(self.tmp, n_source_rows=1)
        spec = copy.deepcopy(BASE_SPEC)
        spec["fingerprints"] = {
            "source_structure": wd["manifest"]["fingerprints"]["source_structure"],
            "target_structure": wd["manifest"]["fingerprints"]["target_structure"],
        }
        spec["mapping"]["transforms"] = [
            {"name": "translate", "function": "controlled_translation",
             "translations": {"家用": "Household"}}]
        spec["mapping"]["targets"][0]["columns"] = [
            {"source": "A", "target": "A", "transforms": ["translate"]},
            {"source": "B", "target": "B"},
            {"source": "C", "target": "C"}]
        spec["validation"]["key_outputs"] = ["A7"]
        plan = compile_fill.compile_spec(spec, wd["manifest"], self.tmp)
        writes = {w["col"]: w for w in plan["writes"]}
        self.assertEqual(writes["A"]["value"], "Household")


# ── 8. compile_fill.py CLI: source_trace.json 工件 ─────────────────────

class SourceTraceArtifactTests(unittest.TestCase):
    """main() 把 plan.source_trace 聚合到 workdir/source_trace.json ——
    与 execution_plan.json 内镜像同源 (Gate/语义验证消费文件)。"""

    def test_source_trace_json_written_by_cli(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            wd = make_matrix_workdir(tmp)
            spec = matrix_spec_with(wd)
            spec_path = tmp / "fill_spec.yaml"
            import yaml
            spec_path.write_text(
                yaml.safe_dump(spec, allow_unicode=True, sort_keys=False),
                encoding="utf-8")
            proc = subprocess.run(
                [sys.executable, "-X", "utf8",
                 str(SCRIPTS / "compile_fill.py"),
                 "--spec", str(spec_path), "--workdir", str(tmp)],
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", cwd=str(tmp))
            self.assertEqual(proc.returncode, 0, proc.stderr[-800:])
            trace_path = tmp / "source_trace.json"
            self.assertTrue(trace_path.is_file())
            trace = json.loads(trace_path.read_text(encoding="utf-8"))
            plan = json.loads((tmp / "execution_plan.json").read_text(
                encoding="utf-8"))
            self.assertEqual(trace, plan["source_trace"])
            n_fields = len(MATRIX_BASE_SPEC["mapping"]["targets"][0]
                           ["matrix"]["field_map"])
            n_records = len(MATRIX_BASE_SPEC["mapping"]["targets"][0]
                            ["matrix"]["record_map"])
            self.assertEqual(len(trace), n_fields * n_records)


if __name__ == "__main__":
    unittest.main()