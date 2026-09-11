"""Task artifact model tests (ADR 0020 — Task runtime retired).

The Task execution state machine (RUN_STAGES / run_stage / stage workers /
task_manifest / task_status lifecycle / prepare_task CLI / collect_demands /
staged_name_for) is RETIRED. Task is a multi-run topology/container
abstraction: task.yaml (schema-validated run list) + runs/<id>/; run views
are projected by materialize_run.py after Topology.

This file retains ONLY tests whose target functions still exist:
  1. task_schema.validate_task_yaml / load_task_yaml — task.yaml static
     validation (the persistent run-list carrier, ADR 0018 Q5).
  2. flatten_cache.cache_key / cache_entry_dir / cache_hit /
     materialize_entry — legacy shared-flatten helpers retained as code
     (no helper migration this round, ADR 0020); proven pure-function seams.
  3. task_prepare.assemble_run_manifest — run manifest assembly reused by
     materialize_run.py (the single retained reuse point, Q4).
  4. New: materialize_run.py --task task.yaml end-to-end projection
     (multi-run lowering, ADR 0018 Q4/Q5): per-run prepare_manifest.json +
     target routing view + RUN_ENTRY_NOT_IN_WORKSPACE fail-closed.

No Office involvement: cache/meta/digest fixtures are synthesized text;
materialize_run subprocess drives structure_digest (pure text derivation).

Run with:
  python -m pytest table-fill/tests/test_task_orchestration.py -q
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS_DIR = SKILL_ROOT / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import task_schema  # noqa: E402
import flatten_cache  # noqa: E402
import task_prepare  # noqa: E402
from prepare_run import _entry_for  # noqa: E402 —— 单 run 条目组装的真实 seam

FIX = Path(__file__).resolve().parent / "_fixtures" / "task_orchestration"
FIX_E2E = FIX / "e2e"
VALID_RUN_IDS = {"r32-cooling", "r32-heating", "r410a-cooling"}


def parse_fixture(name: str):
    """Parse a fixture task.yaml; (data, defect) via the pure parse seam."""
    return task_schema.parse_task_yaml((FIX / name).read_text(encoding="utf-8"))


class TestValidateTaskYaml(unittest.TestCase):
    """Static validation: valid example passes; invalid examples each reject
    with the defects named in the ticket (缺字段/重复 run id/引用不存在)."""

    def test_valid_example_passes(self):
        data, defect = parse_fixture("task.yaml")
        self.assertIsNone(defect)
        self.assertEqual(task_schema.validate_task_yaml(data, FIX), [])

    def test_missing_task_yaml_is_fatal(self):
        data, defect = task_schema.load_task_yaml(FIX / "_no_such_dir_")
        self.assertIsNone(data)
        self.assertIsNotNone(defect)
        self.assertEqual(defect["code"], "TASK_YAML_NOT_FOUND")
        self.assertTrue(defect.get("fatal"))

    def test_parse_error_rejected(self):
        data, defect = parse_fixture("task_parse_error.yaml")
        self.assertIsNone(data)
        self.assertEqual(defect["code"], "TASK_YAML_INVALID")

    def test_missing_fields_rejected(self):
        data, _ = parse_fixture("task_missing_fields.yaml")
        codes = {d["code"] for d in task_schema.validate_task_yaml(data, FIX)}
        self.assertIn("TASK_ID_MISSING", codes)
        self.assertIn("OUTPUT_MISSING", codes)
        self.assertIn("SHEETS_MISSING", codes)

    def test_duplicate_run_id_rejected(self):
        data, _ = parse_fixture("task_dup_run_id.yaml")
        codes = {d["code"] for d in task_schema.validate_task_yaml(data, FIX)}
        self.assertIn("RUN_ID_DUPLICATE", codes)

    def test_bad_refs_rejected(self):
        data, _ = parse_fixture("task_bad_refs.yaml")
        defects = task_schema.validate_task_yaml(data, FIX)
        codes = [d["code"] for d in defects]
        self.assertIn("SOURCE_FILE_NOT_FOUND", codes)
        self.assertIn("TEMPLATE_NOT_FOUND", codes)
        self.assertEqual(codes.count("OUTPUT_NAME_INVALID"), 2)

    def test_empty_runs_rejected(self):
        data, _ = parse_fixture("task_empty_runs.yaml")
        codes = {d["code"] for d in task_schema.validate_task_yaml(data, FIX)}
        self.assertIn("RUNS_EMPTY", codes)

    def test_missing_task_block_rejected(self):
        data = {"runs": [{"id": "run-a"}]}
        codes = {d["code"] for d in task_schema.validate_task_yaml(data, FIX)}
        self.assertIn("TASK_MISSING", codes)

    def test_business_rule_keys_rejected(self):
        data = {
            "task": {"id": "br-task"},
            "runs": [{
                "id": "run-a",
                "mapping": {"source_col": "C", "target_col": "D"},
                "source": {"file": "sources/parameter_book.xlsx",
                           "sheets": ["R32参数"]},
                "target": {"template": "templates/filling_template.xlsx",
                           "output": "out_a.xlsx"},
            }],
        }
        codes = {d["code"] for d in task_schema.validate_task_yaml(data, FIX)}
        self.assertIn("BUSINESS_RULE_IN_TASK_YAML", codes)

    def test_valid_example_forbidden_keys_absent(self):
        data, _ = parse_fixture("task.yaml")
        for run in data["runs"]:
            for key in task_schema.BUSINESS_RULE_KEYS:
                self.assertNotIn(key, run)

    def test_policy_and_status_fields_rejected(self):
        """Task 不变量：run 清单只允许 run 引用字段；policy（业务策略）与
        status（状态）任一出现即 fail-closed 拒绝 —— Task 永不重新长成 DSL。"""
        base_run = {
            "id": "run-a",
            "source": {"file": "sources/parameter_book.xlsx",
                       "sheets": ["R32参数"]},
            "target": {"template": "templates/filling_template.xlsx",
                       "output": "out_a.xlsx"},
        }
        for forbidden in ("mapping", "transform", "policy", "status"):
            with self.subTest(field=forbidden):
                data = {"task": {"id": "br-task"},
                        "runs": [{**base_run, forbidden: {"dummy": True}}]}
                codes = {d["code"] for d in
                         task_schema.validate_task_yaml(data, FIX)}
                self.assertIn("BUSINESS_RULE_IN_TASK_YAML", codes,
                              f"{forbidden} 字段应被静态校验拒绝")

    def test_policy_and_status_top_level_rejected(self):
        for forbidden in ("policy", "status"):
            with self.subTest(field=forbidden):
                data = {"task": {"id": "br-task"},
                        "runs": [{
                            "id": "run-a",
                            "source": {"file": "sources/parameter_book.xlsx",
                                       "sheets": ["R32参数"]},
                            "target": {"template": "templates/filling_template.xlsx",
                                       "output": "out_a.xlsx"},
                        }],
                        forbidden: {"dummy": True}}
                codes = {d["code"] for d in
                         task_schema.validate_task_yaml(data, FIX)}
                self.assertIn("BUSINESS_RULE_IN_TASK_YAML", codes)

    def test_defects_carry_the_shared_shape(self):
        data, _ = parse_fixture("task_missing_fields.yaml")
        for d in task_schema.validate_task_yaml(data, FIX):
            self.assertIn("code", d)
            self.assertTrue(d["message"])
            self.assertTrue(d["corrective_action"])


class TestCacheKey(unittest.TestCase):
    """Cache key 纯函数 seam（legacy shared-flatten helper, ADR 0020）：
    SHA256(staged_source_hash + sheet_name + flatten_schema_version +
    officecli_version)；键内不含任务身份."""

    def test_deterministic(self):
        k1 = flatten_cache.cache_key("ab" * 32, "R32参数", 1, "1.0.144")
        k2 = flatten_cache.cache_key("ab" * 32, "R32参数", 1, "1.0.144")
        self.assertEqual(k1, k2)

    def test_sha256_hex_shape(self):
        key = flatten_cache.cache_key("ab" * 32, "R32参数", 1, "1.0.144")
        self.assertRegex(key, r"^[0-9a-f]{64}$")

    def test_varies_on_source_hash(self):
        a = flatten_cache.cache_key("ab" * 32, "R32参数", 1, "1.0.144")
        b = flatten_cache.cache_key("cd" * 32, "R32参数", 1, "1.0.144")
        self.assertNotEqual(a, b)

    def test_varies_on_sheet_name(self):
        a = flatten_cache.cache_key("ab" * 32, "R32参数", 1, "1.0.144")
        b = flatten_cache.cache_key("ab" * 32, "R410A参数", 1, "1.0.144")
        self.assertNotEqual(a, b)

    def test_varies_on_flatten_schema_version(self):
        a = flatten_cache.cache_key("ab" * 32, "R32参数", 1, "1.0.144")
        b = flatten_cache.cache_key("ab" * 32, "R32参数", 2, "1.0.144")
        self.assertNotEqual(a, b)

    def test_varies_on_officecli_version(self):
        a = flatten_cache.cache_key("ab" * 32, "R32参数", 1, "1.0.144")
        b = flatten_cache.cache_key("ab" * 32, "R32参数", 1, "1.0.145")
        self.assertNotEqual(a, b)

    def test_component_boundary_never_collides(self):
        v = "1.0.144"
        a = flatten_cache.cache_key("ab" * 32, "R32参数", 11, v)
        b = flatten_cache.cache_key("ab" * 32, "R32参数1", 1, v)
        self.assertNotEqual(a, b)

    def test_products_and_schema_version_constants(self):
        self.assertEqual(flatten_cache.CACHE_PRODUCTS,
                         ("flat.csv", "meta.json", "digest.md"))
        self.assertIsInstance(flatten_cache.FLATTEN_SCHEMA_VERSION, int)


def make_fake_meta() -> dict:
    """合成 flatten meta（供 classify_columns / structure_digest 纯文本消费）。"""
    return {
        "file": "C:/tmp/staged/parameter_book.xlsx",
        "sheet": "R32参数",
        "dimensions": {"rows": 6, "cols": 4, "data_rows": 5, "formulas": 0,
                       "errorCells": 0, "tables": 0, "charts": 0, "oleObjects": 0},
        "header_band": {"header_rows": [1], "data_start_row": 2},
        "merged_ranges": [],
        "merge_anchors": [],
        "blocks": [{"id": 1, "start": 2, "end": 6, "title": "测试块", "score": 0.8}],
        "formulas": {},
        "column_numfmt": {},
        "columns": [
            {"col": "A", "nonempty": 5, "numeric_ratio": 0.0, "unique": 2,
             "samples": ["R32", "R32", "R32", "R32", "R32"]},
            {"col": "B", "nonempty": 5, "numeric_ratio": 1.0, "unique": 5,
             "samples": ["1", "2", "3", "4", "5"], "min": 1, "max": 5},
        ],
        "row_gaps": [],
        "style_granularity": {
            "placeholder_segments": [{"start": 10, "end": 13, "styled": False,
                                      "sample": None}],
        },
    }


def make_fake_cache(task_root: Path, key: str) -> Path:
    """合成一个缓存条目目录（3 个白名单产物），模拟 cache hit。"""
    entry = flatten_cache.cache_entry_dir(task_root, key)
    entry.mkdir(parents=True, exist_ok=True)
    (entry / "flat.csv").write_bytes(b"R32,1,1\nR32,2,2\nR32,3,3\nR32,4,4\nR32,5,5\n")
    (entry / "meta.json").write_text(
        json.dumps(make_fake_meta(), ensure_ascii=False, indent=2), encoding="utf-8")
    (entry / "digest.md").write_text("# R32参数 — 结构摘要\n- 缓存产物\n", encoding="utf-8")
    return entry


class TestCacheEntryDirAndHit(unittest.TestCase):
    """cache 布局: <task_root>/cache/<key>/；命中 = 三个白名单产物齐全."""

    def test_entry_dir_layout(self):
        root = Path(tempfile.mkdtemp(prefix="cache_layout_"))
        try:
            key = flatten_cache.cache_key("ab" * 32, "S", 1, "v")
            self.assertEqual(flatten_cache.cache_entry_dir(root, key),
                             root / "cache" / key)
            make_fake_cache(root, key)
            self.assertTrue(flatten_cache.cache_hit(
                flatten_cache.cache_entry_dir(root, key)))
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_partial_entry_is_not_a_hit(self):
        entry = make_fake_cache(Path(tempfile.mkdtemp(prefix="cache_part_")),
                                "k" * 64)
        try:
            (entry / "digest.md").unlink()
            self.assertFalse(flatten_cache.cache_hit(entry))
        finally:
            shutil.rmtree(entry.parent.parent, ignore_errors=True)

    def test_missing_entry_is_not_a_hit(self):
        root = Path(tempfile.mkdtemp(prefix="cache_miss_"))
        try:
            self.assertFalse(flatten_cache.cache_hit(root / "cache" / ("f" * 64)))
        finally:
            shutil.rmtree(root, ignore_errors=True)


class TestMaterializeEntry(unittest.TestCase):
    """物化 seam（legacy helper）：缓存产物 → run workdir（单 run 命名）."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="materialize_"))
        self.key = flatten_cache.cache_key("ab" * 32, "R32参数",
                                           flatten_cache.FLATTEN_SCHEMA_VERSION,
                                           "1.0.144")
        make_fake_cache(self.root, self.key)
        self.run_dir = self.root / "runs" / "r32-cooling"
        self.run_dir.mkdir(parents=True)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_cache_products_copy_byte_identical_with_single_run_naming(self):
        entry = flatten_cache.materialize_entry(
            self.root, self.key, self.run_dir,
            staged_name="parameter_book.xlsx", sheet="R32参数",
            name="parameter_book_R32", is_target=False)
        cache_dir = flatten_cache.cache_entry_dir(self.root, self.key)
        self.assertEqual((self.run_dir / "parameter_book_R32_flat.csv").read_bytes(),
                         (cache_dir / "flat.csv").read_bytes())
        cached_meta = json.loads((cache_dir / "meta.json").read_text(
            encoding="utf-8"))
        self.assertEqual(cached_meta["file"], "C:/tmp/staged/parameter_book.xlsx")

    def test_entry_carries_name_source_sheet_sha256_cache_key(self):
        entry = flatten_cache.materialize_entry(
            self.root, self.key, self.run_dir,
            staged_name="parameter_book.xlsx", sheet="R32参数",
            name="parameter_book_R32", is_target=False)
        self.assertEqual(entry["file"], "parameter_book.xlsx")
        self.assertEqual(entry["sheet"], "R32参数")
        self.assertEqual(entry["name"], "parameter_book_R32")
        self.assertEqual(entry["cache_key"], self.key)
        self.assertEqual(entry["csv"], "parameter_book_R32_flat.csv")
        self.assertRegex(entry["sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(entry["sha256"],
                         task_schema.file_sha256(
                             self.run_dir / "parameter_book_R32_flat.csv"))

    def test_candidates_regenerated_in_run_workdir(self):
        flatten_cache.materialize_entry(
            self.root, self.key, self.run_dir,
            staged_name="parameter_book.xlsx", sheet="R32参数",
            name="parameter_book_R32", is_target=False)
        cand = (self.run_dir / "parameter_book_R32_candidates.yaml").read_text(
            encoding="utf-8")
        self.assertIn("column_classifications:", cand)
        cache_dir = flatten_cache.cache_entry_dir(self.root, self.key)
        self.assertEqual(sorted(p.name for p in cache_dir.iterdir()),
                         ["digest.md", "flat.csv", "meta.json"])

    def test_target_entry_digest_regenerated_with_target_facts(self):
        flatten_cache.materialize_entry(
            self.root, self.key, self.run_dir,
            staged_name="filling_template.xlsx", sheet="Sheet1",
            name="filling_template_Sheet1", is_target=True)
        digest = (self.run_dir / "filling_template_Sheet1_digest.md").read_text(
            encoding="utf-8")
        self.assertIn("占位行样式", digest)


class TestRunManifestAssembly(unittest.TestCase):
    """run 级 prepare_manifest.json 组装 (assemble_run_manifest — materialize_run
    的唯一复用点, Q4): compile-facing 字段与单 run 同构."""

    def test_compile_facing_shape_isomorphic_to_single_run(self):
        files = [{"staged": "parameter_book.xlsx", "source": "sources/parameter_book.xlsx",
                  "sha256": "ab" * 32}]
        outlines = {"parameter_book.xlsx": "parameter_book_outline.txt"}
        flat = [{
            "file": "parameter_book.xlsx", "sheet": "R32参数",
            "name": "parameter_book_R32",
            "csv": "parameter_book_R32_flat.csv",
            "meta": "parameter_book_R32_meta.json",
            "digest": "parameter_book_R32_digest.md",
            "candidates": "parameter_book_R32_candidates.yaml",
            "sha256": "cd" * 32, "cache_key": "ef" * 32,
        }]
        target = {"file": "filling_template.xlsx", "sheet": "Sheet1",
                  "name": "filling_template_Sheet1",
                  "csv": "filling_template_Sheet1_flat.csv",
                  "meta": "filling_template_Sheet1_meta.json",
                  "digest": "filling_template_Sheet1_digest.md",
                  "candidates": "filling_template_Sheet1_candidates.yaml",
                  "sha256": "ab" * 32, "cache_key": "ef" * 32}
        manifest = task_prepare.assemble_run_manifest(
            workdir=r"C:\Temp\tablefill\egypt\run",
            task_label="egypt-params-2026a",
            files=files, outlines=outlines, flattened=flat, target_entry=target,
            fingerprints={"source_structure": "11" * 32,
                          "target_structure": "22" * 32})
        self.assertEqual(manifest["schema_version"], 2)
        self.assertEqual(manifest["files"], files)
        self.assertEqual(manifest["outlines"], outlines)
        self.assertEqual(manifest["flattened"], flat)
        self.assertEqual(manifest["target"], target)
        self.assertEqual(manifest["fingerprints"],
                         {"source_structure": "11" * 32,
                          "target_structure": "22" * 32})
        self.assertIn("workdir", manifest)
        self.assertIn("task", manifest)
        self.assertIn("row_gaps", manifest)
        self.assertIn("style_granularity", manifest)


class TestTaskYamlTargetSheet(unittest.TestCase):
    """目标 sheet 契约: task.yaml 的 target.sheet 声明被静态校验强制."""

    def test_valid_fixture_declares_target_sheet(self):
        data, _ = parse_fixture("task.yaml")
        for run in data["runs"]:
            self.assertTrue(run["target"].get("sheet"),
                            "task.yaml 每条 run 必须声明 target.sheet")

    def test_missing_target_sheet_rejected(self):
        data, _ = parse_fixture("task_missing_fields.yaml")
        codes = {d["code"] for d in task_schema.validate_task_yaml(data, FIX)}
        self.assertIn("TARGET_SHEET_MISSING", codes)


class TestMaterializeRunTaskInput(unittest.TestCase):
    """materialize_run.py --task task.yaml — multi-run lowering (ADR 0018
    Q4/Q5): per-run prepare_manifest.json + target routing view +
    RUN_ENTRY_NOT_IN_WORKSPACE fail-closed. 合成 workdir, 零 Office —
    structure_digest 是纯文本派生 (meta/csv/candidates 合成)。"""

    SHEETS = "parameter_book.xlsx:R32参数,R410A参数,R22参数;filling_template.xlsx:Sheet1"

    def _synthetic_meta(self, sheet: str) -> dict:
        m = make_fake_meta()
        m["sheet"] = sheet
        m["file"] = "parameter_book.xlsx" if "参数" in sheet else "filling_template.xlsx"
        return m

    def _seed_workspace(self, root: Path) -> None:
        """合成 workspace_init 产物 (role-neutral manifest + flatten artifacts),
        不跑 officecli — materialize 只消费 manifest 条目与 meta/csv/candidates。
        同时创建 task.yaml 静态校验需要的 sources/templates 占位文件。

        Trust boundary (prepare_run.verify_workspace_facts, materialize 消费前
        强制): manifest 声明必须与物理文件自洽 — staged 文件于 root 在场且
        inputs[].sha256 真实, meta/csv/candidates/evidence 齐备, outlines 在场,
        derived[] 记录 outline + 全部产物的真实哈希。"""
        import hashlib

        def _h(p: Path) -> str:
            return hashlib.sha256(p.read_bytes()).hexdigest()

        (root / "sources").mkdir(parents=True, exist_ok=True)
        (root / "templates").mkdir(parents=True, exist_ok=True)
        (root / "sources" / "parameter_book.xlsx").write_bytes(b"db")
        (root / "templates" / "filling_template.xlsx").write_bytes(b"tb")
        # staged 文件: materialize 引用的 workdir-resident 输入身份
        (root / "parameter_book.xlsx").write_bytes(b"staged-db")
        (root / "filling_template.xlsx").write_bytes(b"staged-tb")

        entries = [
            ("parameter_book.xlsx", "R32参数", "parameter_book_R32"),
            ("parameter_book.xlsx", "R410A参数", "parameter_book_R410A"),
            ("parameter_book.xlsx", "R22参数", "parameter_book_R22"),
            ("filling_template.xlsx", "Sheet1", "filling_template_Sheet1"),
        ]
        outline_names = {
            "parameter_book.xlsx": "parameter_book_outline.txt",
            "filling_template.xlsx": "filling_template_outline.txt",
        }
        (root / "parameter_book_outline.txt").write_text(
            '{"data": {"sheets": [{"name": "R32参数"}]}}', encoding="utf-8")
        (root / "filling_template_outline.txt").write_text(
            '{"data": {"sheets": [{"name": "Sheet1"}]}}', encoding="utf-8")

        for staged, sheet, name in entries:
            meta = make_fake_meta()
            meta["sheet"] = sheet
            meta["file"] = staged
            (root / f"{name}_meta.json").write_text(
                json.dumps(meta, ensure_ascii=False), encoding="utf-8")
            (root / f"{name}_flat.csv").write_text("R32,1,1\n", encoding="utf-8")
            (root / f"{name}_candidates.yaml").write_text(
                "column_classifications: []\n", encoding="utf-8")
            (root / f"{name}_premod_evidence.md").write_text(
                f"# {name} synthetic evidence\n", encoding="utf-8")

        flattened = [
            {"file": "parameter_book.xlsx", "sheet": "R32参数",
             "name": "parameter_book_R32",
             "csv": "parameter_book_R32_flat.csv",
             "meta": "parameter_book_R32_meta.json",
             "evidence": "parameter_book_R32_premod_evidence.md",
             "digest": "deferred",
             "candidates": "parameter_book_R32_candidates.yaml",
             "structure_sha256": "cc" * 32},
            {"file": "parameter_book.xlsx", "sheet": "R410A参数",
             "name": "parameter_book_R410A",
             "csv": "parameter_book_R410A_flat.csv",
             "meta": "parameter_book_R410A_meta.json",
             "evidence": "parameter_book_R410A_premod_evidence.md",
             "digest": "deferred",
             "candidates": "parameter_book_R410A_candidates.yaml",
             "structure_sha256": "dd" * 32},
            {"file": "parameter_book.xlsx", "sheet": "R22参数",
             "name": "parameter_book_R22",
             "csv": "parameter_book_R22_flat.csv",
             "meta": "parameter_book_R22_meta.json",
             "evidence": "parameter_book_R22_premod_evidence.md",
             "digest": "deferred",
             "candidates": "parameter_book_R22_candidates.yaml",
             "structure_sha256": "d1" * 32},
            {"file": "filling_template.xlsx", "sheet": "Sheet1",
             "name": "filling_template_Sheet1",
             "csv": "filling_template_Sheet1_flat.csv",
             "meta": "filling_template_Sheet1_meta.json",
             "evidence": "filling_template_Sheet1_premod_evidence.md",
             "digest": "deferred",
             "candidates": "filling_template_Sheet1_candidates.yaml",
             "structure_sha256": "ee" * 32},
        ]

        def _product_paths(name: str) -> list[tuple[str, str]]:
            out = []
            for key in ("csv", "meta", "evidence", "candidates"):
                e = next(x for x in flattened if x["name"] == name)
                out.append((e[key], f"flatten/{name}/{key}"))
            return out

        derived = []
        for staged, outline_name in outline_names.items():
            derived.append({"path": outline_name, "kind": "outline",
                            "sha256": _h(root / outline_name)})
        for _, _, name in entries:
            for rel, kind in _product_paths(name):
                derived.append({"path": rel, "kind": kind,
                                "sha256": _h(root / rel)})

        (root / "workspace_manifest.json").write_text(json.dumps({
            "schema_version": 4,
            "kind": "workspace_init",
            "workdir": str(root),
            "task": "synthetic task materialize e2e",
            "inputs": [
                {"staged": "parameter_book.xlsx", "source": "sources/parameter_book.xlsx",
                 "sha256": _h(root / "parameter_book.xlsx")},
                {"staged": "filling_template.xlsx",
                 "source": "templates/filling_template.xlsx",
                 "sha256": _h(root / "filling_template.xlsx")},
            ],
            "outlines": outline_names,
            "flattened": flattened,
            "derived": derived,
            "inherited_from": None,
        }, ensure_ascii=False, indent=2), encoding="utf-8")

    def _run_materialize(self, root: Path) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-X", "utf8",
             str(_SCRIPTS_DIR / "materialize_run.py"),
             "--workdir", str(root), "--task", "task.yaml"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=120)

    def test_task_materialize_projects_all_runs(self):
        root = Path(tempfile.mkdtemp(prefix="mat_task_"))
        try:
            shutil.copy2(FIX_E2E / "task.yaml", root / "task.yaml")
            self._seed_workspace(root)
            proc = self._run_materialize(root)
            self.assertEqual(proc.returncode, 0, proc.stderr[-1200:])
            out = json.loads(proc.stdout)
            self.assertEqual(out["code"], "MATERIALIZED_TASK")
            self.assertEqual(len(out["runs"]), 4)
            # 每条 run 的 prepare_manifest 落在 runs/<id>/ (run-local compiler view)
            for r in out["runs"]:
                rid = r["run"]
                m = json.loads((root / "runs" / rid / "prepare_manifest.json")
                               .read_text(encoding="utf-8"))
                self.assertEqual(m["schema_version"], 2)
                self.assertEqual(m["target"]["name"], "filling_template_Sheet1")
                self.assertTrue(m["fingerprints"]["source_structure"])
                # target routing view 落盘且与 workspace evidence 不同名
                tv = m["target"]["evidence"]
                self.assertTrue(tv.endswith("_target_view.md"))
                self.assertTrue((root / "runs" / rid / tv).is_file())
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_task_materialize_run_entry_not_in_workspace_fails_closed(self):
        """run 引用 workspace 之外的 sheet → RUN_ENTRY_NOT_IN_WORKSPACE,
        绝不增量 flatten。"""
        root = Path(tempfile.mkdtemp(prefix="mat_badref_"))
        try:
            bad_task = """\
task:
  id: bad-ref-task
runs:
  - id: r1
    source:
      file: sources/parameter_book.xlsx
      sheets: [R99参数]
    target:
      template: templates/filling_template.xlsx
      sheet: Sheet1
      output: out_r1.xlsx
"""
            (root / "task.yaml").write_text(bad_task, encoding="utf-8")
            self._seed_workspace(root)
            proc = self._run_materialize(root)
            self.assertEqual(proc.returncode, 3, proc.stdout[-600:])
            err = json.loads(proc.stderr)
            self.assertEqual(err["code"], "RUN_ENTRY_NOT_IN_WORKSPACE")
            self.assertIn("重新 workspace_init", err["corrective_action"])
        finally:
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()