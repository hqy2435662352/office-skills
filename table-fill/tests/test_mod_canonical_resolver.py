"""Ticket 05 — MOD Lifecycle 解耦 + Canonical MOD Resolver 契约测试.

对外行为接缝 (与 test_mod_adjudication.py 同款 CLI seam + resolver 纯函数):

  1. Dual-source fixture (acceptance bullet 1): canonical MOD 与同名 scratch
     MOD 内容不同 — 裁决记录锁定 canonical 哈希; resolver/规则加载只读
     canonical, scratch 内容 (FLD-SCRATCH) 永不进入 loaded rules; 同名副本
     路径/错哈希/逃逸路径 fail-closed (结构化 defect + corrective_action)。
  2. form_content 命中业务 MOD → 规则仍注入 (acceptance bullet 2):
     form_content-shaped 场景 (task_shape.json) + 参数表类 MOD 命中 →
     提名 resolved + 规则经 resolver 可加载, 与 shape/route 无关
     (Real Case Replay 的 fixture 替代, 见 ticket 记录)。
  3. 记录字段 (acceptance bullet 3): 候选/选定记录含 canonical_path/
     revision/sha256; 缺字段 → re-resolve from catalog (resolution_action
     被记录) 或 fail-closed (点名缺失字段)。
  4. C2/C3 回归 (acceptance bullet 4): 新字段记录下 compile MOD 校验语义
     与旧记录完全一致 (同一 defect code; 匹配 → exit 0)。
  5. SKILL 契约文字 pinning (Ticket 02 Layer 3 同款): 生命周期解耦语言 +
     canonical resolver 语言 + 两段加载保留。

Run: python -m pytest tests/test_mod_canonical_resolver.py -q
"""

from __future__ import annotations

import copy
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

NOMINATE = SKILL_ROOT / "scripts" / "mod_nominate.py"
SKILL_MD = SKILL_ROOT / "SKILL.md"

import compile_fill  # noqa: E402
import mod_nominate  # noqa: E402
from _mod_resolver import ModCanonicalError, resolve_canonical  # noqa: E402
from _officecli import sha256_file  # noqa: E402
from _probe_fixtures import BASE_SPEC, make_probe_workdir  # noqa: E402


INDEX_HEADER = (
    "## Registered MODs\n\n"
    "| MOD Name | Aliases | Scope Signals | Exclusion Signals | Path | Revision | Visibility |\n"
    "|---|---|---|---|---|---|---|\n"
)


def _write_index(idx: Path, rows: list[str]) -> None:
    idx.write_text(INDEX_HEADER + "".join(rows), encoding="utf-8")


def _mod_file(summary: str = "- x\n", rules: str = "") -> str:
    out = "## Applicability\n- semantic_type: quotation\n\n## 业务逻辑摘要\n" + summary
    if rules:
        out += ("\n| Rule ID | Group | Gate | Description | Applies to | Notes |\n"
                "|---|---|---|---|---|---|\n" + rules)
    return out + "\n"


def run_nominate(workdir: Path, idx: Path, mods: Path, out: str,
                 task: str = "报价汇总 迁移", digest_text: str | None = None,
                 mod: str | None = None,
                 check: bool = False) -> subprocess.CompletedProcess:
    """跑一次 CLI (提名 / --mod 裁决 / --check-canonical), 返回 CompletedProcess."""
    argv = [sys.executable, str(NOMINATE), "--task", task,
            "--workdir", str(workdir), "--index", str(idx),
            "--mods-dir", str(mods), "--out", out]
    if digest_text is not None:
        dpath = workdir / "digest.md"
        dpath.write_text(digest_text, encoding="utf-8")
        argv += ["--digest", str(dpath)]
    if mod is not None:
        argv += ["--mod", mod]
    if check:
        argv += ["--check-canonical"]
    return subprocess.run(argv, capture_output=True, text=True, encoding="utf-8")


def _make_dual_source():
    """canonical MODS_dual/MOD_dual.md + 同名 scratch/MOD_dual.md (内容不同).

    返回 (workdir, idx, mods, out_path)。canonical 内容含规则 FLD-CANON,
    scratch 内容含 FLD-SCRATCH — 双源差异由规则 ID 断言可区分。"""
    tmp = tempfile.TemporaryDirectory()
    root = Path(tmp.name)
    workdir = root / "work"
    workdir.mkdir()
    mods = root / "MODS_dual"
    mods.mkdir()
    scratch = root / "scratch"
    scratch.mkdir()
    idx = root / "MOD_INDEX_dual.md"
    _write_index(idx, [
        "| dual | d | semantic_type::quotation |  | MOD_dual.md | 1 | private |\n"])
    (mods / "MOD_dual.md").write_text(_mod_file(rules=(
        "| FLD-CANON | business_transformation | mod_gate | "
        "canonical rule table row. | X | n |\n")), encoding="utf-8")
    (scratch / "MOD_dual.md").write_text(_mod_file(rules=(
        "| FLD-SCRATCH | business_transformation | mod_gate | "
        "scratch rule table row. | X | n |\n")), encoding="utf-8")
    return tmp, workdir, idx, mods, scratch


class DualSourceCanonicalTests(unittest.TestCase):
    """Acceptance bullet 1 — canonical vs 同名 scratch (内容不同):
    只读 canonical (sha256 校验), scratch 内容永不进入 loaded rules。"""

    CANON_ID = "FLD-CANON"
    SCRATCH_ID = "FLD-SCRATCH"

    def setUp(self):
        self._tmp, self.workdir, self.idx, self.mods, self.scratch = \
            _make_dual_source()
        self.addCleanup(self._tmp.cleanup)
        self.out = self.workdir / "mod_resolution.json"

    def test_decision_record_locks_canonical_hash(self):
        """裁决记录: 候选/选定均带 canonical_path + revision + sha256;
        sha256 == canonical 文件哈希 (≠ 同名 scratch 哈希)。"""
        r = run_nominate(self.workdir, self.idx, self.mods, str(self.out))
        self.assertEqual(r.returncode, 0, r.stderr)
        record = json.loads(self.out.read_text(encoding="utf-8"))
        self.assertEqual(record["status"], "resolved")
        cand = record["candidates"][0]
        self.assertEqual(cand["name"], "dual")
        self.assertEqual(cand["canonical_path"], "MOD_dual.md")
        self.assertEqual(cand["revision"], 1)
        self.assertEqual(cand["sha256"], sha256_file(self.mods / "MOD_dual.md"))
        self.assertNotEqual(cand["sha256"],
                            sha256_file(self.scratch / "MOD_dual.md"),
                            "scratch 内容哈希不得被当作 canonical 哈希")
        # selected 锁定字段 (C2/C3 仍只读 selected/selected_revision)
        self.assertEqual(record["selected"], "dual")
        self.assertEqual(record["selected_revision"], 1)
        self.assertEqual(record["selected_canonical_path"], "MOD_dual.md")
        self.assertEqual(record["selected_sha256"], cand["sha256"])

    def test_resolver_loads_only_canonical_rules(self):
        """resolver / load_rules_for_selected_mod 只返回 canonical 规则 —
        scratch 规则 (FLD-SCRATCH) 永不出现。"""
        entries = mod_nominate.parse_index(self.idx)
        canon_sha = sha256_file(self.mods / "MOD_dual.md")
        resolved = resolve_canonical(
            self.mods, name="dual", canonical_path="MOD_dual.md",
            revision=1, sha256=canon_sha, entries=entries)
        self.assertEqual(resolved["resolution_action"], "canonical_record")
        self.assertEqual(resolved["sha256"], canon_sha)
        self.assertEqual(resolved["canonical_path"], "MOD_dual.md")
        self.assertIn(self.CANON_ID, resolved["content"])
        self.assertNotIn(self.SCRATCH_ID, resolved["content"])
        # 记录形态经 load_rules_for_selected_mod
        rules = mod_nominate.load_rules_for_selected_mod(
            self.mods, resolution={"name": "dual", "canonical_path": "MOD_dual.md",
                                   "revision": 1, "sha256": canon_sha})
        self.assertEqual([x["id"] for x in rules], [self.CANON_ID])
        # 旧式直连 + 目录表: path == Path 列 → canonical
        legacy = mod_nominate.load_rules_for_selected_mod(
            self.mods, "MOD_dual.md", entries=entries)
        self.assertEqual([x["id"] for x in legacy], [self.CANON_ID])
        self.assertNotIn(self.SCRATCH_ID, legacy[0]["description"])

    def test_same_name_scratch_never_a_rule_source(self):
        """同名副本攻击形态全部 fail-closed (结构化 defect + corrective_action):
        逃逸路径 / 绝对路径 / 错哈希 / 目录内非 Path 列 / scratch 目录直连 /
        legacy 直连逃逸。"""
        entries = mod_nominate.parse_index(self.idx)
        canon_sha = sha256_file(self.mods / "MOD_dual.md")
        scratch_sha = sha256_file(self.scratch / "MOD_dual.md")

        def code_of(fn) -> ModCanonicalError:
            try:
                fn()
            except ModCanonicalError as e:
                return e
            self.fail("应 fail-closed 而未抛错")

        # 逃逸路径 (scratch 副本上级目录) → path invalid
        e = code_of(lambda: resolve_canonical(
            self.mods, name="dual", canonical_path="../scratch/MOD_dual.md",
            revision=1, sha256=canon_sha, entries=entries))
        self.assertEqual(e.code, "MOD_CANONICAL_PATH_INVALID")
        # 绝对路径 (scratch 副本) → path invalid
        e = code_of(lambda: resolve_canonical(
            self.mods, name="dual",
            canonical_path=str(self.scratch / "MOD_dual.md"),
            revision=1, sha256=canon_sha, entries=entries))
        self.assertEqual(e.code, "MOD_CANONICAL_PATH_INVALID")
        # 错哈希 (记录来自 scratch 内容) → hash mismatch, 不回退同名文件
        e = code_of(lambda: resolve_canonical(
            self.mods, name="dual", canonical_path="MOD_dual.md",
            revision=1, sha256=scratch_sha, entries=entries))
        self.assertEqual(e.code, "MOD_CANONICAL_HASH_MISMATCH")
        # 目录内但非 Path 列 (other/MOD_dual.md) → drift
        e = code_of(lambda: resolve_canonical(
            self.mods, name="dual", canonical_path="other/MOD_dual.md",
            revision=1, sha256=canon_sha, entries=entries))
        self.assertEqual(e.code, "MOD_CANONICAL_PATH_DRIFT")
        # loader 根部被指到 scratch 目录 (同名文件) → 记录锁定的哈希
        # 与 scratch 内容不符 → fail-closed, 绝不读取该文件
        e = code_of(lambda: resolve_canonical(
            self.scratch, name="dual", canonical_path="MOD_dual.md",
            revision=1, sha256=canon_sha, entries=entries))
        self.assertEqual(e.code, "MOD_CANONICAL_HASH_MISMATCH")
        # path 直连 + 目录表: 非 Path 列路径 → not canonical
        e = code_of(lambda: resolve_canonical(
            self.mods, path="MOD_other.md", entries=entries))
        self.assertEqual(e.code, "MOD_PATH_NOT_CANONICAL")
        # legacy 直连逃逸 (无目录表) → path invalid
        e = code_of(lambda: mod_nominate.load_rules_for_selected_mod(
            self.mods, "../scratch/MOD_dual.md"))
        self.assertEqual(e.code, "MOD_CANONICAL_PATH_INVALID")
        # 每个 defect 都是结构化 (code + message + corrective_action)
        self.assertTrue(e.message and e.corrective_action)

    def test_missing_canonical_file_fails_closed(self):
        entries = mod_nominate.parse_index(self.idx)
        (self.mods / "MOD_dual.md").unlink()
        with self.assertRaises(ModCanonicalError) as ctx:
            resolve_canonical(self.mods, name="dual",
                              canonical_path="MOD_dual.md", revision=1,
                              sha256="0" * 64, entries=entries)
        self.assertEqual(ctx.exception.code, "MOD_CANONICAL_MISSING")

    def test_check_canonical_cli_ok_when_unmodified(self):
        """--check-canonical: 记录与 canonical 文件一致 → exit 0 + 摘要
        (candidate + selected 都校验且为 canonical_record)。"""
        r = run_nominate(self.workdir, self.idx, self.mods, "mod_resolution.json")
        self.assertEqual(r.returncode, 0, r.stderr)
        r = run_nominate(self.workdir, self.idx, self.mods,
                         "mod_resolution.json", check=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        summary = json.loads(r.stdout)
        self.assertEqual(summary["status"], "ok")
        by_role = {c["role"]: c for c in summary["checked"]}
        self.assertIn("candidate", by_role)
        self.assertIn("selected", by_role)
        self.assertEqual(by_role["selected"]["canonical_path"], "MOD_dual.md")
        self.assertEqual(by_role["selected"]["revision"], 1)
        self.assertEqual(by_role["selected"]["resolution_action"],
                         "canonical_record")

    def test_check_canonical_cli_fails_closed_on_tamper(self):
        """--check-canonical: canonical 文件被改 (内容=scratch 规则) →
        哈希不匹配 → exit 3 + 结构化 defect; 恢复后通过。"""
        r = run_nominate(self.workdir, self.idx, self.mods, "mod_resolution.json")
        self.assertEqual(r.returncode, 0, r.stderr)
        (self.mods / "MOD_dual.md").write_text(
            _mod_file(rules=(
                "| FLD-SCRATCH | business_transformation | mod_gate | "
                "scratch rule table row. | X | n |\n")), encoding="utf-8")
        r = run_nominate(self.workdir, self.idx, self.mods,
                         "mod_resolution.json", check=True)
        self.assertEqual(r.returncode, 3)
        payload = json.loads(r.stderr)
        self.assertEqual(payload["code"], "MOD_CANONICAL_HASH_MISMATCH")
        self.assertIsInstance(payload["message"], str)
        self.assertIsInstance(payload["corrective_action"], str)
        self.assertGreater(len(payload["corrective_action"]), 0)
        # 恢复 canonical 内容 → 通过
        (self.mods / "MOD_dual.md").write_text(_mod_file(rules=(
            "| FLD-CANON | business_transformation | mod_gate | "
            "canonical rule table row. | X | n |\n")), encoding="utf-8")
        r = run_nominate(self.workdir, self.idx, self.mods,
                         "mod_resolution.json", check=True)
        self.assertEqual(r.returncode, 0, r.stderr)


class FormContentModInjectionTests(unittest.TestCase):
    """Acceptance bullet 2 — form_content 命中业务 MOD → 规则仍注入。

    真实 replay (客户参数表在 Task Shape 之后自动到达 MOD resolution) 的
    fixture 替代: form_content-shaped 场景 (task_shape.json) + 参数表类
    MOD 命中 → 提名 resolved → 规则经 canonical resolver 可加载, 且加载
    路径与 shape/route 完全无关。
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.workdir = root / "work"
        self.workdir.mkdir()
        self.mods = root / "MODS_param"
        self.mods.mkdir()
        self.idx = root / "MOD_INDEX_param.md"
        _write_index(self.idx, [
            "| param_sheet | tcl-param | "
            "semantic_type::internal_parameter_to_customer_parameter_sheet"
            " |  | MOD_param.md | 1 | private |\n"])
        (self.mods / "MOD_param.md").write_text(
            "## Applicability\n"
            "- semantic_type: internal_parameter_to_customer_parameter_sheet\n\n"
            "## 业务逻辑摘要\n"
            "- Z 码身份解析 / 字段白名单 / 受控翻译 / CJK 扫描\n\n"
            "| Rule ID | Group | Gate | Description | Applies to | Notes |\n"
            "|---|---|---|---|---|---|\n"
            "| ZID-001 | identity | mod_gate | Z 码身份解析与首尾空白清理。 | Z码 | |\n"
            "| CTL-002 | translation | execution_gate | 受控翻译 (宽片→wide fin)。 | Type | |\n"
            "| CJK-003 | validation | execution_gate | CJK 泄漏扫描最终 workbook。 | 外发区 | |\n",
            encoding="utf-8")

    def _write_shape(self, shape: str, route: str) -> None:
        (self.workdir / "task_shape.json").write_text(
            json.dumps({"task_shape": shape, "route": route,
                        "evidence": ["content_composition"
                                     if shape == "form_content"
                                     else "obvious_grid"]}),
            encoding="utf-8")

    def test_form_content_shape_mod_rules_still_available(self):
        """form_content 形态下命中业务 MOD → 提名 resolved, 规则经 resolver
        注入可用; 加载与 shape/route 无关 (不因 shape 被跳过)。"""
        self._write_shape("form_content", "officecli_native")
        task = "客户参数表 外发 客户版 型谱"
        r = run_nominate(self.workdir, self.idx, self.mods,
                         "mod_resolution.json", task=task)
        self.assertEqual(r.returncode, 0, r.stderr)
        record = json.loads(
            (self.workdir / "mod_resolution.json").read_text(encoding="utf-8"))
        # MOD 不因 form_content route 被跳过: 命中并 resolved
        self.assertEqual(record["status"], "resolved")
        self.assertEqual(record["selected"], "param_sheet")
        self.assertEqual(record["selected_canonical_path"], "MOD_param.md")
        # 业务治理规则经 canonical resolver 加载 → 进入使用上下文
        rules = mod_nominate.load_rules_for_selected_mod(
            self.mods, resolution=record["candidates"][0])
        self.assertEqual([x["id"] for x in rules],
                         ["ZID-001", "CTL-002", "CJK-003"])
        self.assertIn("宽片", rules[1]["description"])
        # 加载路径无 shape 参数: grid_record 形态下同一 canonical 规则
        self._write_shape("grid_record", "fillspec")
        rules2 = mod_nominate.load_rules_for_selected_mod(
            self.mods, resolution=record["candidates"][0])
        self.assertEqual(rules, rules2)


class CanonicalRecordFieldsTests(unittest.TestCase):
    """Acceptance bullet 3 — 记录字段 + 缺字段显式行为 (re-resolve / fail-closed)。"""

    def setUp(self):
        self._tmp, self.workdir, self.idx, self.mods, self.scratch = \
            _make_dual_source()
        self.addCleanup(self._tmp.cleanup)

    def test_legacy_record_without_fields_reresolves_from_catalog(self):
        """缺 canonical_path/sha256 的旧记录: 目录表可解析 → re-resolve
        from catalog (resolution_action 被记录), canonical 路径由目录表
        派生 (绝不来自任意路径)。"""
        entries = mod_nominate.parse_index(self.idx)
        resolved = resolve_canonical(self.mods, name="dual", revision=1,
                                     entries=entries)
        self.assertEqual(resolved["resolution_action"], "re-resolved_from_catalog")
        self.assertEqual(resolved["canonical_path"], "MOD_dual.md")
        self.assertEqual(resolved["revision"], 1)
        self.assertEqual(resolved["sha256"], sha256_file(self.mods / "MOD_dual.md"))
        self.assertIn("FLD-CANON", resolved["content"])
        # 旧式直连 (path + 目录表) 同样目录表校核
        rules = mod_nominate.load_rules_for_selected_mod(
            self.mods, "MOD_dual.md", entries=entries)
        self.assertEqual([x["id"] for x in rules], ["FLD-CANON"])
        # 记录形态缺字段 (name only) 也走重解析
        rules2 = mod_nominate.load_rules_for_selected_mod(
            self.mods, resolution={"name": "dual"}, entries=entries)
        self.assertEqual(rules2, rules)

    def test_unknown_name_fails_closed_naming_missing_fields(self):
        """缺字段且目录表无法解析 → fail-closed, corrective_action 点名
        缺失字段 (canonical_path + sha256)。"""
        entries = mod_nominate.parse_index(self.idx)
        with self.assertRaises(ModCanonicalError) as ctx:
            resolve_canonical(self.mods, name="ghost_mod", entries=entries)
        self.assertEqual(ctx.exception.code, "MOD_CANONICAL_FIELDS_MISSING")
        self.assertIn("canonical_path", ctx.exception.corrective_action)
        self.assertIn("sha256", ctx.exception.corrective_action)

    def test_no_reference_at_all_fails_closed(self):
        with self.assertRaises(ModCanonicalError) as ctx:
            resolve_canonical(self.mods)
        self.assertEqual(ctx.exception.code, "MOD_CANONICAL_FIELDS_MISSING")

    def test_none_adjudication_has_no_selected_lock(self):
        """--mod NONE (ambiguous → resolved + selected NONE): 无 MOD 被选定
        → 无 selected_canonical_path/sha256 (与 selected_revision 短路一致);
        候选仍带 canonical 字段。"""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        workdir = Path(tmp.name) / "work"
        workdir.mkdir()
        mods = Path(tmp.name) / "MODS_two"
        mods.mkdir()
        idx = Path(tmp.name) / "MOD_INDEX_two.md"
        _write_index(idx, [
            "| mod_a | a | semantic_type::quotation |  | MOD_a.md | 7 | private |\n",
            "| mod_b | b | semantic_type::quotation |  | MOD_b.md | 9 | private |\n",
        ])
        for name, rule in (("MOD_a.md", "FLD-006"), ("MOD_b.md", "FRM-002")):
            (mods / name).write_text(_mod_file(rules=(
                f"| {rule} | business_transformation | mod_gate | 描述{rule}。"
                " | X | n |\n")), encoding="utf-8")
        r = run_nominate(workdir, idx, mods, "mod_resolution.json", mod="NONE")
        self.assertEqual(r.returncode, 0, r.stderr)
        record = json.loads(
            (workdir / "mod_resolution.json").read_text(encoding="utf-8"))
        self.assertEqual(record["status"], "resolved")
        self.assertEqual(record["selected"], "NONE")
        self.assertNotIn("selected_canonical_path", record)
        self.assertNotIn("selected_sha256", record)
        for cand in record["candidates"]:
            self.assertIn("canonical_path", cand)
            self.assertIn("sha256", cand)


class CompileC2C3NewFieldsToleranceTests(unittest.TestCase):
    """Acceptance bullet 4 — 新字段 (候选 canonical_path/sha256、
    selected_canonical_path/selected_sha256) 下 compile C2/C3 语义与旧记录
    完全一致: 同一 defect code; 匹配时 exit 0。compile 本身不读 MOD 文件。"""

    def setUp(self):
        self.tmp_ctx = tempfile.TemporaryDirectory()
        self.tmp = Path(self.tmp_ctx.name)
        self.wd = make_probe_workdir(self.tmp)
        self.wd["workdir"] = self.tmp

    def tearDown(self):
        self.tmp_ctx.cleanup()

    @staticmethod
    def _spec_with(wd: dict, **mutations) -> dict:
        spec = copy.deepcopy(BASE_SPEC)
        spec["fingerprints"] = {
            "source_structure": wd["manifest"]["fingerprints"]["source_structure"],
            "target_structure": wd["manifest"]["fingerprints"]["target_structure"],
        }
        for path, value in mutations.items():
            node = spec
            parts = path.split(".")
            for p in parts[:-1]:
                node = node[p]
            node[parts[-1]] = value
        return spec

    def _write_resolution(self, record: dict) -> None:
        (self.tmp / "mod_resolution.json").write_text(
            json.dumps(record, ensure_ascii=False), encoding="utf-8")

    def _compile_capture(self, spec: dict) -> dict:
        from io import StringIO
        buf = StringIO()
        old = sys.stderr
        sys.stderr = buf
        try:
            plan = compile_fill.compile_spec(spec, self.wd["manifest"], self.tmp)
            return {"exit": 0, "plan": plan}
        except SystemExit as e:
            payload = None
            try:
                payload = json.loads(buf.getvalue())
            except ValueError:
                payload = None
            return {"exit": e.code, "payload": payload}
        finally:
            sys.stderr = old

    @staticmethod
    def _new_format_record(selected: str = "MOD_A", revision: int = 3) -> dict:
        return {
            "status": "resolved",
            "selected": selected,
            "selected_revision": revision,
            "selected_canonical_path": "MOD_A.md",
            "selected_sha256": "a" * 64,
            "candidates": [
                {"name": "MOD_A", "canonical_path": "MOD_A.md",
                 "revision": revision, "sha256": "a" * 64},
            ],
            "why": "user adjudication recorded",
        }

    def test_new_format_record_c2_c3_semantics_identical(self):
        # 匹配 → exit 0 (plan 产出), 与旧记录行为一致
        self._write_resolution(self._new_format_record())
        spec = self._spec_with(self.wd, **{"task.selected_mod": "MOD_A",
                                           "task.selected_mod_revision": 3})
        r = self._compile_capture(spec)
        self.assertEqual(r["exit"], 0, r)
        self.assertIsInstance(r["plan"], dict)
        self.assertIn("operations", r["plan"])
        # selected 不一致 → 同一 defect code MOD_SELECTION_MISMATCH
        self._write_resolution(self._new_format_record(selected="MOD_A"))
        spec = self._spec_with(self.wd, **{"task.selected_mod": "MOD_B",
                                           "task.selected_mod_revision": 3})
        r = self._compile_capture(spec)
        self.assertEqual(r["exit"], 3)
        self.assertEqual(r["payload"]["code"], "MOD_SELECTION_MISMATCH")
        # revision 漂移 → 同一 defect code MOD_REVISION_MISMATCH
        self._write_resolution(self._new_format_record(revision=3))
        spec = self._spec_with(self.wd, **{"task.selected_mod": "MOD_A",
                                           "task.selected_mod_revision": 999})
        r = self._compile_capture(spec)
        self.assertEqual(r["exit"], 3)
        self.assertEqual(r["payload"]["code"], "MOD_REVISION_MISMATCH")


class SkillContractTextTests(unittest.TestCase):
    """SKILL.md §2 MOD Resolution 契约文字 pinning (Ticket 02 Layer 3 同款):

    - 生命周期解耦语言: 固定顺序 + form_content 命中仍生效 + NOT_APPLICABLE
      只表示引擎层不适用 + 「form_content → 跳过 MOD」不再是合法路径;
    - canonical resolver 语言: canonical_path/revision/sha256 锁定 +
      禁止 glob 同名文件 + scratch/history/legacy 副本禁止 + fail-closed;
    - 两段加载保留: 提名阶段摘要 / 裁决后 full rules (契约词稳定, 防回退)。
    """

    def _mod_resolution_section(self) -> str:
        text = SKILL_MD.read_text(encoding="utf-8")
        m = re.search(r"^### 4\. MOD Resolution.*?(?=^### 5\.)",
                      text, re.MULTILINE | re.DOTALL)
        self.assertIsNotNone(m, "SKILL.md 缺 §4 MOD Resolution 段")
        return m.group(0)

    def test_lifecycle_decoupling_contract(self):
        section = self._mod_resolution_section()
        stripped = re.sub(r"[\s`*]", "", section)
        self.assertIn(
            "Prepare→Pre-MODEvidence→TaskShape→MODNomination/Resolution→"
            "加载selectedMOD规则→业务推导→选择/执行executor", stripped)
        self.assertIn("form_content命中业务MOD时MOD仍然生效", stripped)
        self.assertIn("「form_content→跳过MOD」不再是合法路径", stripped)
        self.assertIn("NOT_APPLICABLE只表示引擎层不适用", stripped)
        self.assertIn("不是业务规则不适用", stripped)

    def test_canonical_resolver_contract(self):
        section = self._mod_resolution_section()
        for word in ("canonical_path", "revision", "sha256",
                     "scratch", "history", "legacy",
                     "fail-closed", "corrective_action", "resolution_action",
                     "load_rules_for_selected_mod", "check-canonical"):
            self.assertIn(word, section, f"§2 缺 canonical resolver 词 {word!r}")
        stripped = re.sub(r"[\s`*]", "", section)
        self.assertIn("禁止glob同名文件", stripped)
        self.assertIn("只消费canonicalresolver返回的canonical版本", stripped)
        self.assertIn("re-resolvefromcatalog", stripped)

    def test_two_stage_loading_preserved(self):
        section = self._mod_resolution_section()
        for word in ("提名阶段", "用户裁决后", "不含完整规则集", "摘要",
                     "必须加载后才可写 spec", "不因输出形态优化放宽", "选中"):
            self.assertIn(word, section, f"§2 两段加载契约词缺失 {word!r}")


if __name__ == "__main__":
    unittest.main()