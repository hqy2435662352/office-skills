"""Ticket 03 — Compiler 反馈硬化: contract tests (反馈行为契约接缝).

Compiler 反馈契约 (spec Implementation Decisions + Testing Decisions「契约接缝：
编译器结构化输出——用于反馈行为的 contract test」) 两个行为各 1 个 contract test
(共 ≥2)，只测外部行为（结构化 JSON 字段形状），沿用既有 subprocess CLI seam 风格：

  1. 校验断言类缺陷附修复路径选项 (≥2) + 矩阵场景候选格清单:
     - `fix_options[]` — 每个校验断言类缺陷至少 2 个 {option, note} 候选，
       不删除既有 code/message/corrective_action;
     - `candidate_cells[]` — 矩阵 locator 缺陷 (AMBIGUOUS / NOT_FOUND) 附候选格
       清单 (side/orig/label/meaning)，不替 Agent 决定选哪个。
  2. 能力清单双命名空间 (转换函数 vs 能力) + 未知键命名空间不匹配:
     - `--capabilities` 输出 `transforms` + `capabilities` 两个显式分组;
     - `--capability <key>` 未知键 → exit 3 + namespace_mismatch + nearest_keys
       (difflib 最近可用键)，能力键/转换函数键各自命名空间。

Run: python -m pytest tests/test_compiler_feedback.py -q
"""

from __future__ import annotations

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from io import StringIO
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import compile_fill  # noqa: E402
from _probe_fixtures import (  # noqa: E402
    MATRIX_LOCATOR_BASE_SPEC,
    make_matrix_locator_workdir,
)


class CompilerFixOptionsContractTests(unittest.TestCase):
    """校验断言类缺陷附 ≥2 个修复路径选项 + 矩阵场景候选格清单 (外部行为:
    结构化缺陷 JSON 字段形状)。"""

    def _spec(self, field_map) -> dict:
        self.tmp = Path(tempfile.mkdtemp())
        wd = make_matrix_locator_workdir(self.tmp)
        self.wd = {**wd, "workdir": self.tmp}
        spec = copy.deepcopy(MATRIX_LOCATOR_BASE_SPEC)
        spec["fingerprints"] = {
            "source_structure": wd["manifest"]["fingerprints"]["source_structure"],
            "target_structure": wd["manifest"]["fingerprints"]["target_structure"],
        }
        spec["mapping"]["targets"][0]["matrix"]["field_map"] = field_map
        spec["validation"]["key_outputs"] = []
        return spec

    def _defects(self, spec) -> list:
        buf = StringIO()
        old = sys.stderr
        sys.stderr = buf
        try:
            compile_fill.compile_spec(spec, self.wd["manifest"], self.tmp)
            self.fail("expected exit 3")
        except SystemExit as e:
            self.assertEqual(e.code, 3)
            return json.loads(buf.getvalue())["defects"]
        finally:
            sys.stderr = old

    def test_ambiguous_matrix_defect_carries_fix_options_and_candidate_cells(self):
        """矩阵场景 (AMBIGUOUS): 缺陷附 fix_options (≥2) + candidate_cells 候选格
        清单 (逐命中行), 且保留既有 code/message/corrective_action。"""
        spec = self._spec([{"source": "Cooling", "target": "Cooling"}])
        defects = self._defects(spec)
        d = next(x for x in defects if x["code"] == "MATRIX_FIELD_LOCATOR_AMBIGUOUS")
        # 既有字段不删 (清理由 06 号票负责)
        self.assertIn("message", d)
        self.assertIn("corrective_action", d)
        # 修复路径选项 ≥2, 每项含 option 说明
        opts = d.get("fix_options") or []
        self.assertGreaterEqual(len(opts), 2)
        for o in opts:
            self.assertIn("option", o)
            self.assertIn("note", o)
        # 候选格清单: 命中 Cooling 的 3 行 (orig 2/3/4), 每项 side/orig/label/meaning
        cands = d.get("candidate_cells") or []
        self.assertGreaterEqual(len(cands), 2)
        origs = {c["orig"] for c in cands}
        self.assertIn(2, origs)  # Cooling/Capacity/Btu/h
        self.assertIn(3, origs)  # Cooling/Capacity/W
        for c in cands:
            self.assertEqual(c["side"], "source")
            self.assertIn("label", c)
            self.assertIn("meaning", c)

    def test_not_found_matrix_defect_carries_fix_options_and_nearest_label_cells(self):
        """矩阵场景 (NOT_FOUND): 缺陷附 fix_options + candidate_cells (difflib
        最近标签候选), 用于纠拼写/措辞。"""
        spec = self._spec([{"source": "Coolingg", "target": "Cooling"}])
        defects = self._defects(spec)
        d = next(x for x in defects if x["code"] == "MATRIX_FIELD_LOCATOR_NOT_FOUND")
        opts = d.get("fix_options") or []
        self.assertGreaterEqual(len(opts), 2)
        cands = d.get("candidate_cells") or []
        self.assertGreaterEqual(len(cands), 1)
        labels = [c["label"] for c in cands]
        self.assertIn("Cooling", labels)  # 最近标签 = 正确拼写
        self.assertIn("side", cands[0])


class CapabilityNamespaceContractTests(unittest.TestCase):
    """能力清单双命名空间 + 未知键命名空间不匹配 (subprocess CLI seam, 只测外部
    行为, 不测实现函数)。"""

    SCRIPTS_PATH = SKILL_ROOT / "scripts" / "compile_fill.py"

    def _run(self, *argv: str) -> subprocess.CompletedProcess:
        with tempfile.TemporaryDirectory() as td:
            return subprocess.run(
                [sys.executable, str(self.SCRIPTS_PATH), *argv],
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", cwd=td)

    def test_capabilities_reports_two_namespaces(self):
        """`--capabilities` 输出 transforms + capabilities 两个显式分组; 能力
        命名空间 7-key (semantic_gate 已于 06 号票移除; task.assembly 已于
        07 号票移除)。"""
        r = self._run("--capabilities")
        self.assertEqual(r.returncode, 0, r.stderr[-500:])
        payload = json.loads(r.stdout)
        self.assertIn("transforms", payload)
        self.assertIn("capabilities", payload)
        # 转换函数命名空间: 内置 round2/round4/trim + 自定义函数
        tnames = [t["name"] for t in payload["transforms"]]
        for fn in ("round2", "round4", "trim", "strip",
                   "regex_replace", "controlled_translation"):
            self.assertIn(fn, tnames)
        # 能力命名空间: 7-key 全集 (semantic_gate / task.assembly 移除后)
        cnames = [c["name"] for c in payload["capabilities"]]
        for key in ("matrix", "matrix.field_locator", "matrix.record_map",
                    "matrix.transforms", "matrix.literal_fallback", "inplace",
                    "inplace.placeholder_ownership"):
            self.assertIn(key, cnames)
        # 退役键已移除 (06: 语义验证层; 07: 打包机制)
        self.assertNotIn("semantic_gate", cnames)
        self.assertNotIn("task.assembly", cnames)
        self.assertEqual(len(cnames), 7)
        # probe matrix cases 仍在 (既有行为不破坏)
        self.assertIn("cases", payload)

    def test_unknown_key_returns_namespace_mismatch_and_nearest_keys(self):
        """未知键 → exit 3 + namespace_mismatch 提示 + nearest_keys (difflib
        最近可用键), 且保留既有 CAPABILITY_KEY_UNKNOWN / available_keys 兼容面。"""
        r = self._run("--capability", "matrix.field_locater")
        self.assertEqual(r.returncode, 3)
        payload = json.loads(r.stderr)
        # 既有兼容面 (不破坏既有 contract test)
        self.assertEqual(payload["code"], "CAPABILITY_KEY_UNKNOWN")
        self.assertIn("matrix.field_locator", payload["available_keys"])
        # ticket 03 新增: 命名空间不匹配 + 最近可用键
        self.assertTrue(payload.get("namespace_mismatch"))
        self.assertIn("matrix.field_locator", payload["nearest_keys"])
        self.assertIn("nearest_keys", payload)

    def test_transform_name_returns_namespace_mismatch_not_capability(self):
        """转换函数名 → exit 3 +「这是转换函数不是能力」 namespace mismatch
        (US 15: 一次查询即终结), 能力键可用键列表仍在。"""
        r = self._run("--capability", "round4")
        self.assertEqual(r.returncode, 3)
        payload = json.loads(r.stderr)
        self.assertEqual(payload["code"], "CAPABILITY_KEY_UNKNOWN")
        self.assertTrue(payload.get("namespace_mismatch"))
        self.assertIn("round4", payload["transform_functions"])
        self.assertIn("matrix", payload["available_keys"])


class TransformDefinitionStaticCheckTests(unittest.TestCase):
    """ticket 06 — 转换函数定义的静态检查并入编译器 (取代已退役的独立预检):
    build_transforms 收集结构化缺陷覆盖三类 — 未知函数 / 正则不可编译 / 缺替换值
    (外加 name 缺失 / 词表形状坏), 每个缺陷含 code/at/message/corrective_action。"""

    def _defects(self, mapping, target_cfg=None) -> list:
        defects = []
        compile_fill.build_transforms(mapping, target_cfg or {}, defects)
        return defects

    def _codes(self, defects) -> set:
        return {d["code"] for d in defects}

    def test_valid_transform_defs_no_defects(self):
        mapping = {"transforms": [
            {"name": "ct", "function": "controlled_translation",
             "translations": {"a": "b"}},
            {"name": "rx", "function": "regex_replace",
             "pattern": r"\d+", "replacement": "-"},
            {"name": "st", "function": "strip"},
        ]}
        self.assertEqual(self._defects(mapping), [])

    def test_unknown_function_reported(self):
        mapping = {"transforms": [{"name": "bad", "function": "translate"}]}
        defects = self._defects(mapping)
        self.assertIn("TRANSFORM_FUNCTION_UNKNOWN", self._codes(defects))
        d = next(x for x in defects if x["code"] == "TRANSFORM_FUNCTION_UNKNOWN")
        self.assertIn("translate", d["message"])
        self.assertIn("mapping.transforms[0]", d["at"])

    def test_uncompilable_regex_reported(self):
        mapping = {"transforms": [
            {"name": "rx", "function": "regex_replace",
             "pattern": "[unclosed", "replacement": "-"}]}
        defects = self._defects(mapping)
        self.assertIn("TRANSFORM_PATTERN_INVALID", self._codes(defects))

    def test_missing_pattern_reported(self):
        mapping = {"transforms": [
            {"name": "rx", "function": "regex_replace", "replacement": "-"}]}
        defects = self._defects(mapping)
        self.assertIn("TRANSFORM_PATTERN_MISSING", self._codes(defects))

    def test_missing_replacement_reported(self):
        mapping = {"transforms": [
            {"name": "rx", "function": "regex_replace", "pattern": r"\d+"}]}
        defects = self._defects(mapping)
        self.assertIn("TRANSFORM_REPLACEMENT_MISSING", self._codes(defects))

    def test_bad_translations_shape_reported(self):
        mapping = {"transforms": [
            {"name": "ct", "function": "controlled_translation",
             "translations": [{"from": "a", "to": "b"}]}]}
        defects = self._defects(mapping)
        self.assertIn("TRANSFORM_TRANSLATIONS_INVALID", self._codes(defects))

    def test_missing_name_reported(self):
        mapping = {"transforms": [{"function": "strip"}]}
        defects = self._defects(mapping)
        self.assertIn("TRANSFORM_NAME_MISSING", self._codes(defects))

    def test_target_transforms_checked_with_location(self):
        mapping = {"targets": [{"transforms": [
            {"name": "rx2", "function": "regex_replace", "pattern": "[x",
             "replacement": "-"}]}]}
        defects = self._defects(mapping)
        d = next(x for x in defects if x["code"] == "TRANSFORM_PATTERN_INVALID")
        self.assertIn("mapping.targets[0].transforms[0]", d["at"])

    def test_defects_carry_fix_options_after_decorate(self):
        """compile_fill.decorate_defects 为每个新缺陷码附 ≥2 fix_options。"""
        mapping = {"transforms": [
            {"name": "rx", "function": "regex_replace", "pattern": "[x",
             "replacement": "-"}]}
        defects = compile_fill.decorate_defects(self._defects(mapping))
        d = next(x for x in defects if x["code"] == "TRANSFORM_PATTERN_INVALID")
        self.assertGreaterEqual(len(d.get("fix_options") or []), 2)


if __name__ == "__main__":
    unittest.main()
