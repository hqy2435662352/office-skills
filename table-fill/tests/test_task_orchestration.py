"""Tests for the Task Artifact Model — issue 01 (spec S2) + Shared Flatten
Cache (issue 02, spec S3/S4/S5) + Stage Orchestrator (issue 03, spec S6).

Issue 01 coverage (two pre-agreed seams from spec Testing Decision #3):
  1. task_schema.py pure functions (import seam): validate_task_yaml /
     derive_task_manifest / derive_task_status / freeze & status checks.
  2. prepare_task.py public CLI (subprocess seam): --validate / --init,
     exit codes 0/1/3, derived-file freeze semantics.

Issue 02 coverage (same pre-agreed seam discipline; cache + prepare logic
must be importable pure functions so they are testable without Office):
  3. flatten_cache.cache_key — Task-local Flatten Cache key pure seam
     (SHA256(staged_source_hash + sheet_name + flatten_schema_version +
     officecli_version); no task identity inside).
  4. task_prepare.staged_name_for / collect_demands — deterministic staged
     naming + eager flatten demand collection (唯一需求数 dedup by key).
  5. flatten_cache.materialize_entry — materialization seam: cache products
     copied into run workdir with single-run naming, candidates/digest
     regenerated per missing piece, entry carries sha256 + cache_key.
  6. task_prepare.assemble_run_manifest — run manifest compile-facing
     isomorphism with single-run prepare_manifest.json (extra cache_key
     metadata allowed only).
  7. prepare_task.py --prepare guards (task_manifest required → --init
     first; frozen-manifest check fails closed).

Issue 03 coverage (scheduler core = pure orchestration seam, fake workers,
13-run synthetic scale; no Office):
  8. task_scheduler constants — stage table + concurrency defaults as
     implementation constants (not in task.yaml, no CLI flag).
  9. task_scheduler.run_stage — barrier semantics: stage k+1 starts only
     after stage k fully ends; in-stage max concurrency == default.
  10. task_scheduler.run_stage failure isolation — one failed/raised item
      never affects same-stage siblings; failure list aggregated.
  11. task_scheduler.apply_stage_status — single-writer boundary semantics:
      batch state advance per stage, failed runs not advanced, superseded
      untouched; the boundary write happens exactly once per stage and the
      status file is byte-stable DURING a stage (watchdog worker).
  12. progress lines — `阶段 x/y 完成` boundary summaries.
  13. prepare_task.py --run guards (no Office): needs --init first, stale
      manifest fails closed — same prelude contract as --prepare.

No Office involvement: cache/meta/digest fixtures are synthesized text;
classify_columns.py / structure_digest.py are pure text subprocesses.

Run with:
  python -m pytest table-fill/tests/test_task_orchestration.py -q
  python -m unittest tests.test_task_orchestration
"""

from __future__ import annotations

import inspect
import json
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS_DIR = SKILL_ROOT / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import task_schema  # noqa: E402
import flatten_cache  # noqa: E402
import task_prepare  # noqa: E402
import task_scheduler  # noqa: E402

FIX = Path(__file__).resolve().parent / "_fixtures" / "task_orchestration"
PREPARE_TASK = _SCRIPTS_DIR / "prepare_task.py"
VALID_RUN_IDS = {"r32-cooling", "r32-heating", "r410a-cooling"}


def run_cli(task_root: Path, mode: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(PREPARE_TASK), "--task-root", str(task_root), mode],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=60,
    )


def parse_fixture(name: str):
    """Parse a fixture task.yaml; (data, defect) via the pure parse seam."""
    return task_schema.parse_task_yaml((FIX / name).read_text(encoding="utf-8"))


class TestValidateTaskYaml(unittest.TestCase):
    """Static validation: valid example passes; invalid examples each reject
    with the defects named in the ticket (缺字段/重复 run id/引用不存在)."""

    def test_valid_example_passes(self):
        """合法示例（缺一不可的验收）: 任务根目录自带的 task.yaml 必须零缺陷."""
        data, defect = parse_fixture("task.yaml")
        self.assertIsNone(defect)
        self.assertEqual(task_schema.validate_task_yaml(data, FIX), [])

    def test_missing_task_yaml_is_fatal(self):
        """task.yaml 不存在 → fatal defect (exit 1), 不是可修复缺陷 (exit 3)."""
        data, defect = task_schema.load_task_yaml(FIX / "_no_such_dir_")
        self.assertIsNone(data)
        self.assertIsNotNone(defect)
        self.assertEqual(defect["code"], "TASK_YAML_NOT_FOUND")
        self.assertTrue(defect.get("fatal"))

    def test_parse_error_rejected(self):
        """YAML 语法错误 → TASK_YAML_INVALID 缺陷."""
        data, defect = parse_fixture("task_parse_error.yaml")
        self.assertIsNone(data)
        self.assertEqual(defect["code"], "TASK_YAML_INVALID")

    def test_missing_fields_rejected(self):
        """缺字段示例: task.id / target.output / source.sheets 均须报缺陷."""
        data, _ = parse_fixture("task_missing_fields.yaml")
        codes = {d["code"] for d in task_schema.validate_task_yaml(data, FIX)}
        self.assertIn("TASK_ID_MISSING", codes)
        self.assertIn("OUTPUT_MISSING", codes)
        self.assertIn("SHEETS_MISSING", codes)

    def test_duplicate_run_id_rejected(self):
        """重复 run id → RUN_ID_DUPLICATE."""
        data, _ = parse_fixture("task_dup_run_id.yaml")
        codes = {d["code"] for d in task_schema.validate_task_yaml(data, FIX)}
        self.assertIn("RUN_ID_DUPLICATE", codes)

    def test_bad_refs_rejected(self):
        """引用不存在 + 输出名不合法 → 对应缺陷全部报出."""
        data, _ = parse_fixture("task_bad_refs.yaml")
        defects = task_schema.validate_task_yaml(data, FIX)
        codes = [d["code"] for d in defects]
        self.assertIn("SOURCE_FILE_NOT_FOUND", codes)
        self.assertIn("TEMPLATE_NOT_FOUND", codes)
        # run-1 的 "bad output.xlsx" 与 run-2 的 "../escape.xlsx" 各一条
        self.assertEqual(codes.count("OUTPUT_NAME_INVALID"), 2)

    def test_empty_runs_rejected(self):
        """runs: [] → RUNS_EMPTY."""
        data, _ = parse_fixture("task_empty_runs.yaml")
        codes = {d["code"] for d in task_schema.validate_task_yaml(data, FIX)}
        self.assertIn("RUNS_EMPTY", codes)

    def test_missing_task_block_rejected(self):
        """顶层缺 task: 块 → TASK_MISSING."""
        data = {"runs": [{"id": "run-a"}]}
        codes = {d["code"] for d in task_schema.validate_task_yaml(data, FIX)}
        self.assertIn("TASK_MISSING", codes)

    def test_business_rule_keys_rejected(self):
        """业务规则键（mapping 等）禁止入 task.yaml — 映射永远在
        runs/<id>/fill_spec.yaml."""
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
        """合法示例不携带任何业务规则键."""
        data, _ = parse_fixture("task.yaml")
        for run in data["runs"]:
            for key in task_schema.BUSINESS_RULE_KEYS:
                self.assertNotIn(key, run)

    def test_defects_carry_the_shared_shape(self):
        """每条缺陷带有 code/message/corrective_action（既有 fail 契约）."""
        data, _ = parse_fixture("task_missing_fields.yaml")
        for d in task_schema.validate_task_yaml(data, FIX):
            self.assertIn("code", d)
            self.assertTrue(d["message"])
            self.assertTrue(d["corrective_action"])


class TestDeriveArtifacts(unittest.TestCase):
    """Derived files: run id 与 task.yaml 一一对应; 形态与 spec S2 一致."""

    @classmethod
    def setUpClass(cls):
        cls.task, cls.defect = parse_fixture("task.yaml")
        assert cls.defect is None
        cls.sha = "ab" * 32  # 固定假 sha256, 保证确定性断言
        cls.manifest = task_schema.derive_task_manifest(
            cls.task, cls.sha, frozen_at="2026-08-22T00:00:00")
        cls.status = task_schema.derive_task_status(
            cls.task, cls.sha, updated_at="2026-08-22T00:00:00")

    def test_manifest_run_ids_one_to_one(self):
        """验收: manifest 的 run id 与 task.yaml 一一对应（不多不少）."""
        self.assertEqual(set(self.manifest["runs"]), VALID_RUN_IDS)

    def test_status_run_ids_one_to_one(self):
        """验收: status 的 run id 与 task.yaml 一一对应."""
        self.assertEqual(set(self.status["runs"]), VALID_RUN_IDS)

    def test_manifest_snapshot_shape(self):
        """Prepare Snapshot 骨架: 引用注册 + 待填充的事实载体字段."""
        self.assertEqual(self.manifest["schema_version"], 1)
        self.assertEqual(self.manifest["task"]["id"], "egypt-params-2026a")
        self.assertEqual(self.manifest["task"]["yaml"], "task.yaml")
        self.assertEqual(self.manifest["task"]["yaml_sha256"], self.sha)
        self.assertEqual(self.manifest["staged_files"], [])
        self.assertEqual(self.manifest["outlines"], {})
        self.assertEqual(self.manifest["flatten_cache_refs"], {})
        self.assertEqual(self.manifest["fingerprints"], {})
        self.assertEqual(self.manifest["frozen_at"], "2026-08-22T00:00:00")
        # 引用关系: run 条目携带声明的输入/输出, 可按 run id 追溯
        r = self.manifest["runs"]["r32-cooling"]
        self.assertEqual(r["source"]["file"], "sources/parameter_book.xlsx")
        self.assertEqual(r["source"]["sheets"], ["R32参数", "R410A参数"])
        self.assertEqual(r["target"]["output"], "out_r32_cooling.xlsx")

    def test_manifest_records_template_family_only_when_declared(self):
        """template_family 仅作记录（D6 不实现）: 声明才出现, 未声明不带."""
        self.assertIn("template_family",
                      self.manifest["runs"]["r410a-cooling"])
        self.assertNotIn("template_family",
                         self.manifest["runs"]["r32-cooling"])

    def test_status_runtime_shape(self):
        """Runtime State: 全部 run 初始 planned, superseded_by 为 null."""
        self.assertEqual(self.status["schema_version"], 1)
        self.assertEqual(self.status["updated_at"], "2026-08-22T00:00:00")
        for rid, entry in self.status["runs"].items():
            self.assertIn(rid, VALID_RUN_IDS)
            self.assertEqual(entry["state"], "planned")
            self.assertIsNone(entry["superseded_by"])
            self.assertIn(entry["state"], task_schema.RUN_STATES)

    def test_derive_is_deterministic(self):
        """相同输入 + 固定时间戳 → 逐字节一致的派生结果."""
        m2 = task_schema.derive_task_manifest(
            self.task, self.sha, frozen_at="2026-08-22T00:00:00")
        s2 = task_schema.derive_task_status(
            self.task, self.sha, updated_at="2026-08-22T00:00:00")
        self.assertEqual(m2, self.manifest)
        self.assertEqual(s2, self.status)


class TestPrepareTaskCLI(unittest.TestCase):
    """Public CLI seam: exit codes 0/1/3 + 缺陷清单 + 派生文件落盘语义."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="task_schema_cli_"))
        shutil.copytree(FIX, self.root, dirs_exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_validate_valid_exit0(self):
        proc = run_cli(self.root, "--validate")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = json.loads(proc.stdout)
        self.assertEqual(out["status"], "PASS")
        self.assertEqual(set(out["task"]["runs"]), VALID_RUN_IDS)

    def test_validate_duplicate_run_id_exit3_with_defects(self):
        shutil.copy2(FIX / "task_dup_run_id.yaml", self.root / "task.yaml")
        proc = run_cli(self.root, "--validate")
        self.assertEqual(proc.returncode, 3, proc.stderr)
        err = json.loads(proc.stderr)
        self.assertEqual(err["status"], "ERROR")
        codes = [d["code"] for d in err["defects"]]
        self.assertIn("RUN_ID_DUPLICATE", codes)

    def test_validate_missing_task_yaml_exit1(self):
        empty = Path(tempfile.mkdtemp(prefix="task_schema_empty_"))
        try:
            proc = run_cli(empty, "--validate")
        finally:
            shutil.rmtree(empty, ignore_errors=True)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("TASK_YAML_NOT_FOUND", proc.stderr)

    def test_validate_parse_error_exit3(self):
        shutil.copy2(FIX / "task_parse_error.yaml", self.root / "task.yaml")
        proc = run_cli(self.root, "--validate")
        self.assertEqual(proc.returncode, 3, proc.stderr)
        self.assertIn("TASK_YAML_INVALID", proc.stderr)

    def test_init_writes_both_derived_files(self):
        """验收: task_manifest.json 与 task_status.json 由脚本写入, 且 run id
        与 task.yaml 一一对应."""
        proc = run_cli(self.root, "--init")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        manifest = json.loads(
            (self.root / "task_manifest.json").read_text(encoding="utf-8"))
        status = json.loads(
            (self.root / "task_status.json").read_text(encoding="utf-8"))
        self.assertEqual(set(manifest["runs"]), VALID_RUN_IDS)
        self.assertEqual(set(status["runs"]), VALID_RUN_IDS)
        for entry in status["runs"].values():
            self.assertEqual(entry["state"], "planned")
        self.assertEqual(manifest["task"]["yaml"], "task.yaml")

    def test_init_is_idempotent_and_freeze_never_rewrites(self):
        """manifest 一旦冻结不再改写: 第二次 --init 两文件逐字节不变."""
        proc1 = run_cli(self.root, "--init")
        self.assertEqual(proc1.returncode, 0, proc1.stderr)
        before_m = (self.root / "task_manifest.json").read_bytes()
        before_s = (self.root / "task_status.json").read_bytes()
        proc2 = run_cli(self.root, "--init")
        self.assertEqual(proc2.returncode, 0, proc2.stderr)
        self.assertEqual((self.root / "task_manifest.json").read_bytes(), before_m)
        self.assertEqual((self.root / "task_status.json").read_bytes(), before_s)

    def test_init_stale_manifest_exit3(self):
        """task.yaml 变化 → 冻结的 manifest 与 task.yaml 不再一致 (MANIFEST_STALE),
        拒绝静默重派生."""
        proc1 = run_cli(self.root, "--init")
        self.assertEqual(proc1.returncode, 0, proc1.stderr)
        yaml_path = self.root / "task.yaml"
        with yaml_path.open("a", encoding="utf-8") as fh:
            fh.write("\n# agent 追加注释 — 文件内容变化, 输入快照已冻结\n")
        proc2 = run_cli(self.root, "--init")
        self.assertEqual(proc2.returncode, 3, proc2.stderr)
        self.assertIn("MANIFEST_STALE", proc2.stderr)

    def test_init_status_tamper_rejected(self):
        """status 被手改（run id 与 task.yaml 不一致 / 非法状态）→ 拒绝."""
        run_cli(self.root, "--init")
        status_path = self.root / "task_status.json"
        status = json.loads(status_path.read_text(encoding="utf-8"))
        status["runs"]["ghost-run"] = {"state": "planned", "superseded_by": None}
        status_path.write_text(json.dumps(status, ensure_ascii=False, indent=2),
                               encoding="utf-8")
        proc = run_cli(self.root, "--init")
        self.assertEqual(proc.returncode, 3, proc.stderr)
        self.assertIn("RUN_ID_MISMATCH", proc.stderr)
        # 恢复后: 非法状态值同样被拒
        del status["runs"]["ghost-run"]
        status["runs"]["r32-cooling"] = {"state": "bogus", "superseded_by": None}
        status_path.write_text(json.dumps(status, ensure_ascii=False, indent=2),
                               encoding="utf-8")
        proc = run_cli(self.root, "--init")
        self.assertEqual(proc.returncode, 3, proc.stderr)
        self.assertIn("STATUS_INVALID_STATE", proc.stderr)

    def test_init_manifest_tamper_rejected(self):
        """manifest 被手改（run id 与 task.yaml 不一致）→ RUN_ID_MISMATCH."""
        run_cli(self.root, "--init")
        manifest_path = self.root / "task_manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["runs"]["ghost-run"] = {
            "source": {"file": "x.xlsx", "sheets": []},
            "target": {"template": "t.xlsx", "output": "o.xlsx"}}
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                                 encoding="utf-8")
        proc = run_cli(self.root, "--init")
        self.assertEqual(proc.returncode, 3, proc.stderr)
        self.assertIn("RUN_ID_MISMATCH", proc.stderr)

    def test_init_status_task_fingerprint_tamper_rejected(self):
        """status 的 task 绑定指纹被手改 → STATUS_STALE（与 manifest 同等严格）."""
        run_cli(self.root, "--init")
        status_path = self.root / "task_status.json"
        status = json.loads(status_path.read_text(encoding="utf-8"))
        status["task"]["yaml_sha256"] = "0" * 64
        status_path.write_text(json.dumps(status, ensure_ascii=False, indent=2),
                               encoding="utf-8")
        proc = run_cli(self.root, "--init")
        self.assertEqual(proc.returncode, 3, proc.stderr)
        self.assertIn("STATUS_STALE", proc.stderr)

    def test_validate_non_ascii_task_root_exit1(self):
        """officecli 依赖 ASCII 路径（与 prepare_run 同约束）: 中文任务根 → exit 1."""
        root_cn = Path(tempfile.mkdtemp(prefix="任务根_"))
        try:
            shutil.copy2(FIX / "task.yaml", root_cn / "task.yaml")
            proc = run_cli(root_cn, "--validate")
        finally:
            shutil.rmtree(root_cn, ignore_errors=True)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("NON_ASCII_PATH", proc.stderr)

    def test_prepare_without_init_exit3(self):
        """--prepare 需要 --init 产物（task_manifest/task_status）; 缺失 →
        exit 3 + 指引用 --init（先于任何 officecli 调用，无 Office 可测）."""
        proc = run_cli(self.root, "--prepare")
        self.assertEqual(proc.returncode, 3, proc.stderr)
        err = json.loads(proc.stderr)
        self.assertEqual(err["status"], "ERROR")
        self.assertIn("TASK_MANIFEST_MISSING", proc.stderr)

    def test_prepare_stale_manifest_exit3(self):
        """task.yaml 变化 → --prepare 拒绝（输入快照已封存，不静默重派生）."""
        proc1 = run_cli(self.root, "--init")
        self.assertEqual(proc1.returncode, 0, proc1.stderr)
        with (self.root / "task.yaml").open("a", encoding="utf-8") as fh:
            fh.write("\n# 输入事实变化 — 快照已冻结\n")
        proc = run_cli(self.root, "--prepare")
        self.assertEqual(proc.returncode, 3, proc.stderr)
        self.assertIn("MANIFEST_STALE", proc.stderr)


# ── issue 02: Task-local Flatten Cache (spec S3/S4/S5) ──────────────────

class TestCacheKey(unittest.TestCase):
    """Cache key 纯函数 seam（spec Testing Decision #3）:
    SHA256(staged_source_hash + sheet_name + flatten_schema_version +
    officecli_version)；键内不含任务身份（未来升级全局缓存零迁移）。"""

    def test_deterministic(self):
        """相同输入 → 相同键."""
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
        """officecli 升级 → 缓存键变化（产物可能不同，缓存身份随之失效）。"""
        a = flatten_cache.cache_key("ab" * 32, "R32参数", 1, "1.0.144")
        b = flatten_cache.cache_key("ab" * 32, "R32参数", 1, "1.0.145")
        self.assertNotEqual(a, b)

    def test_component_boundary_never_collides(self):
        """键的分量边界必须无歧义：纯拼接下 sheet 'R32参数' + schema_v=11
        与 sheet 'R32参数1' + schema_v=1 会拼出相同 payload —— 长度前缀编码
        后两者必须不同键。"""
        v = "1.0.144"
        a = flatten_cache.cache_key("ab" * 32, "R32参数", 11, v)
        b = flatten_cache.cache_key("ab" * 32, "R32参数1", 1, v)
        self.assertNotEqual(a, b)

    def test_products_and_schema_version_constants(self):
        """缓存内容白名单 + 展平 schema 版本被模块显式承载."""
        self.assertEqual(flatten_cache.CACHE_PRODUCTS,
                         ("flat.csv", "meta.json", "digest.md"))
        self.assertIsInstance(flatten_cache.FLATTEN_SCHEMA_VERSION, int)


class TestStagedNameFor(unittest.TestCase):
    """确定性 staged 命名（任务级一次性 staging 的命名规则）."""

    def test_basename_when_free(self):
        self.assertEqual(
            task_prepare.staged_name_for(Path("sources/parameter_book.xlsx"), set()),
            "parameter_book.xlsx")

    def test_collision_gets_suffix(self):
        taken = {"parameter_book.xlsx"}
        self.assertEqual(
            task_prepare.staged_name_for(Path("templates/parameter_book.xlsx"), taken),
            "parameter_book_2.xlsx")

    def test_collision_suffix_skips_taken(self):
        taken = {"parameter_book.xlsx", "parameter_book_2.xlsx"}
        self.assertEqual(
            task_prepare.staged_name_for(Path("templates/parameter_book.xlsx"), taken),
            "parameter_book_3.xlsx")

    def test_non_ascii_basename_rejected(self):
        """中文文件名无法 ASCII staging（officecli 在中文路径失败）→ None,
        由调用方转为 STAGED_NAME_NON_ASCII 缺陷."""
        self.assertIsNone(
            task_prepare.staged_name_for(Path("sources/参数表.xlsx"), set()))

    def test_deterministic_given_order(self):
        a = task_prepare.staged_name_for(Path("a.xlsx"), {"a.xlsx"})
        b = task_prepare.staged_name_for(Path("a.xlsx"), {"a.xlsx"})
        self.assertEqual(a, b)


class TestCollectDemands(unittest.TestCase):
    """eager 预展平的 (file, sheet) 需求收集：任务级唯一需求数 U=3
    （2 源 sheet + 1 目标 sheet，与 issue 08 的 U=3 设计一致）."""

    @classmethod
    def setUpClass(cls):
        cls.task, cls.defect = parse_fixture("task.yaml")
        assert cls.defect is None
        cls.resolved = {
            FIX / "sources/parameter_book.xlsx": "parameter_book.xlsx",
            FIX / "templates/filling_template.xlsx": "filling_template.xlsx",
        }
        cls.demands = task_prepare.collect_demands(cls.task, cls.resolved, FIX)

    def test_demand_count(self):
        """3 run × (2|1 源 + 1 目标) = 7 条需求."""
        self.assertEqual(len(self.demands), 7)

    def test_unique_demand_count_is_three(self):
        """验收: 任务内唯一 (file, sheet) 需求数 == 3（cache/ 目录数上限）."""
        uniq = {(d["staged"], d["sheet"]) for d in self.demands}
        self.assertEqual(uniq, {
            ("parameter_book.xlsx", "R32参数"),
            ("parameter_book.xlsx", "R410A参数"),
            ("filling_template.xlsx", "Sheet1"),
        })

    def test_every_run_has_exactly_one_target_demand(self):
        """每条 run 恰好一个 target 需求（target.sheet 声明展平目标 sheet）."""
        per_run = {}
        for d in self.demands:
            if d["sheet"] == "Sheet1":
                per_run.setdefault(d["run"], []).append(d)
        for run in self.task["runs"]:
            self.assertEqual(len(per_run.get(run["id"], [])), 1)

    def test_single_run_entry_naming_convention(self):
        """物料命名与单 run 约定一致: <staged_stem>_<ascii_slug(sheet)>.
        'R32参数' → ascii_slug 'R32'（非 ASCII 字符丢弃）."""
        src = next(d for d in self.demands
                   if d["sheet"] == "R32参数" and d["kind"] == "source")
        self.assertEqual(src["name"], "parameter_book_R32")
        tgt = next(d for d in self.demands if d["kind"] == "target")
        self.assertEqual(tgt["name"], "filling_template_Sheet1")

    def test_kinds_recorded(self):
        kinds = {d["kind"] for d in self.demands}
        self.assertEqual(kinds, {"source", "target"})


def make_fake_meta() -> dict:
    """合成 flatten meta（shape 对齐 flatten_table build_meta 的子集，
    供 classify_columns / structure_digest 纯文本消费）。"""
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
        """只有 flat.csv → 不算命中（不静默以残缺产物物化）."""
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
    """物化 seam：缓存产物 → run workdir（单 run 命名）+ 逐字节复制 +
    candidates 再生成 + 目标 digest 以 --target 再生成 + 条目元数据."""

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
        """验收: 物化 CSV 与缓存产物逐字节一致，命名按
        <staged>_<sheet>_flat.csv 单 run 约定；meta 的 file 字段重定向到
        run 自身 staged 副本（run artifact 自包含，S5）。"""
        entry = flatten_cache.materialize_entry(
            self.root, self.key, self.run_dir,
            staged_name="parameter_book.xlsx", sheet="R32参数",
            name="parameter_book_R32", is_target=False)
        cache_dir = flatten_cache.cache_entry_dir(self.root, self.key)
        # flat.csv 逐字节复制（业务身份 = 物化后 hash）
        self.assertEqual((self.run_dir / "parameter_book_R32_flat.csv").read_bytes(),
                         (cache_dir / "flat.csv").read_bytes())
        # meta 复制后 file 指向 run 自己的 staged 副本（与单 run 语义一致）
        meta = json.loads((self.run_dir / "parameter_book_R32_meta.json").read_text(
            encoding="utf-8"))
        self.assertEqual(meta["file"],
                         str(self.run_dir / "parameter_book.xlsx"))
        # digest 复制后保持缓存原样（不在 run 侧额外改写）
        self.assertEqual((self.run_dir / "parameter_book_R32_digest.md").read_bytes(),
                         (cache_dir / "digest.md").read_bytes())
        # 缓存侧 meta 不被改写（Cache Identity ≠ Run Artifact Identity）
        cached_meta = json.loads((cache_dir / "meta.json").read_text(
            encoding="utf-8"))
        self.assertEqual(cached_meta["file"], "C:/tmp/staged/parameter_book.xlsx")

    def test_entry_carries_name_source_sheet_sha256_cache_key(self):
        """验收: flattened 条目记录 name/source/sheet/sha256/cache_key
        （cache_key 是 provenance metadata；CSV hash 是业务身份）."""
        entry = flatten_cache.materialize_entry(
            self.root, self.key, self.run_dir,
            staged_name="parameter_book.xlsx", sheet="R32参数",
            name="parameter_book_R32", is_target=False)
        self.assertEqual(entry["file"], "parameter_book.xlsx")
        self.assertEqual(entry["sheet"], "R32参数")
        self.assertEqual(entry["name"], "parameter_book_R32")
        self.assertEqual(entry["cache_key"], self.key)
        self.assertEqual(entry["csv"], "parameter_book_R32_flat.csv")
        self.assertEqual(entry["meta"], "parameter_book_R32_meta.json")
        self.assertEqual(entry["digest"], "parameter_book_R32_digest.md")
        self.assertEqual(entry["candidates"], "parameter_book_R32_candidates.yaml")
        self.assertRegex(entry["sha256"], r"^[0-9a-f]{64}$")
        # sha256 = 物化 CSV 的 hash（run 的业务身份）
        self.assertEqual(entry["sha256"],
                         task_schema.file_sha256(
                             self.run_dir / "parameter_book_R32_flat.csv"))

    def test_candidates_regenerated_in_run_workdir(self):
        """candidates 不入缓存（白名单 3 文件），物化时确定性再生成."""
        flatten_cache.materialize_entry(
            self.root, self.key, self.run_dir,
            staged_name="parameter_book.xlsx", sheet="R32参数",
            name="parameter_book_R32", is_target=False)
        cand = (self.run_dir / "parameter_book_R32_candidates.yaml").read_text(
            encoding="utf-8")
        self.assertIn("column_classifications:", cand)
        self.assertIn("uncertain_columns:", cand)
        # 缓存目录仍只有白名单产物（candidates 未污染缓存）
        cache_dir = flatten_cache.cache_entry_dir(self.root, self.key)
        self.assertEqual(sorted(p.name for p in cache_dir.iterdir()),
                         ["digest.md", "flat.csv", "meta.json"])

    def test_target_entry_digest_regenerated_with_target_facts(self):
        """目标条目: digest 以 --target 再生成（占位行样式决策事实入内）."""
        entry = flatten_cache.materialize_entry(
            self.root, self.key, self.run_dir,
            staged_name="filling_template.xlsx", sheet="Sheet1",
            name="filling_template_Sheet1", is_target=True)
        digest = (self.run_dir / "filling_template_Sheet1_digest.md").read_text(
            encoding="utf-8")
        self.assertIn("占位行样式", digest)
        cache_dir = flatten_cache.cache_entry_dir(self.root, self.key)
        self.assertNotIn("占位行样式",
                         (cache_dir / "digest.md").read_text(encoding="utf-8"))


class TestRunManifestAssembly(unittest.TestCase):
    """run 级 prepare_manifest.json 组装：compile-facing 字段与单 run 同构，
    仅多 cache_key 元数据（spec S5 / issue 02 验收 3）."""

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
        # compile-facing 顶层字段（与 prepare_run.py 的单 run manifest 同构）
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
        # 只比单 run 多 cache_key/sha256 元数据？断言不依赖 workdir 落盘
        for entry in manifest["flattened"]:
            self.assertIn("cache_key", entry)
            self.assertIn("sha256", entry)
            for k in ("file", "sheet", "name", "csv", "meta", "digest", "candidates"):
                self.assertIn(k, entry)


class TestTaskYamlTargetSheet(unittest.TestCase):
    """task.yaml 的 target.sheet 声明（阶段 1 收集 (file, sheet) 对的前提）."""

    def test_valid_fixture_declares_target_sheet(self):
        """合法示例: 每条 run 的 target.sheet 已声明 → 校验零缺陷."""
        data, defect = parse_fixture("task.yaml")
        self.assertIsNone(defect)
        for run in data["runs"]:
            self.assertIsInstance(run["target"].get("sheet"), str)
        self.assertEqual(task_schema.validate_task_yaml(data, FIX), [])

    def test_missing_target_sheet_rejected(self):
        run = {
            "id": "run-a",
            "source": {"file": "sources/parameter_book.xlsx",
                       "sheets": ["R32参数"]},
            "target": {"template": "templates/filling_template.xlsx",
                       "output": "out_a.xlsx"},
        }
        data = {"task": {"id": "ts-task"}, "runs": [run]}
        codes = {d["code"] for d in task_schema.validate_task_yaml(data, FIX)}
        self.assertIn("TARGET_SHEET_MISSING", codes)

    def test_derive_manifest_records_target_sheet(self):
        data, defect = parse_fixture("task.yaml")
        self.assertIsNone(defect)
        manifest = task_schema.derive_task_manifest(data, "ab" * 32,
                                                    frozen_at="2026-08-22T00:00:00")
        for rid, entry in manifest["runs"].items():
            run = next(r for r in data["runs"] if r["id"] == rid)
            self.assertEqual(entry["target"]["sheet"], run["target"]["sheet"])


# ── issue 03: Stage Orchestrator (spec S6) ──────────────────────────────

# 13 run 规模合成任务（验收规模；fake worker 驱动，无 Office）
ITEMS13 = [f"r{i:02d}" for i in range(13)]


def make_recorder():
    """阶段内并发观测器：active/max_active（带锁）+ 全事件时间线。"""
    return {"events": [], "lock": threading.Lock(), "active": 0,
            "max_active": 0}


def make_worker(rec, fail_ids=(), fail_code="SIM_FAILED", sleep=0.05):
    """fake worker：记录事件与并发峰值；fail_ids 中的 item 返回 failed。"""
    def worker(item):
        with rec["lock"]:
            rec["active"] += 1
            rec["max_active"] = max(rec["max_active"], rec["active"])
        rec["events"].append(("start", item, time.perf_counter()))
        time.sleep(sleep)
        try:
            if item in fail_ids:
                return {"run": item, "status": "failed", "code": fail_code,
                        "message": "simulated worker failure"}
            return {"run": item, "status": "ok",
                    "artifacts": {"out": item}}
        finally:
            rec["events"].append(("end", item, time.perf_counter()))
            with rec["lock"]:
                rec["active"] -= 1
    return worker


class TestStageConstants(unittest.TestCase):
    """阶段表 + 并发默认值 = implementation constant：不进入 task.yaml、
    不暴露 CLI 调参（spec S6；环境稳定性参数不得污染任务定义）。"""

    def test_stage_table_matches_spec(self):
        self.assertEqual(task_scheduler.STAGES,
                         ("source_prepare", "run_prepare", "compile",
                          "execute", "gate", "promote"))

    def test_concurrency_defaults_match_spec_table(self):
        """spec S6 表格：2 / 2 / 4~8 / 2 / 1 / 2（compile 取实现常量 4）。"""
        self.assertEqual(task_scheduler.STAGE_CONCURRENCY, {
            "source_prepare": 2, "run_prepare": 2, "compile": 4,
            "execute": 2, "gate": 1, "promote": 2,
        })

    def test_successor_states_follow_main_path(self):
        """阶段边界后继状态 = 状态机主路径；source_prepare 是任务级阶段
        （缓存构建），不推进 run 状态。"""
        self.assertIsNone(task_scheduler.STAGE_SUCCESSOR["source_prepare"])
        self.assertEqual(task_scheduler.STAGE_SUCCESSOR["run_prepare"],
                         "prepared")
        self.assertEqual(task_scheduler.STAGE_SUCCESSOR["compile"], "compiled")
        self.assertEqual(task_scheduler.STAGE_SUCCESSOR["execute"], "drafted")
        self.assertEqual(task_scheduler.STAGE_SUCCESSOR["gate"], "gated")
        self.assertEqual(task_scheduler.STAGE_SUCCESSOR["promote"], "promoted")

    def test_concurrency_not_in_task_yaml(self):
        """并发默认值不进 task.yaml（结构性断言）。"""
        data, defect = parse_fixture("task.yaml")
        self.assertIsNone(defect)
        self.assertNotIn("concurrency", data)
        for run in data["runs"]:
            self.assertNotIn("concurrency", run)

    def test_no_cli_concurrency_flag(self):
        """并发默认值不暴露 CLI 调参（结构性断言：--help 无调参旗标）。"""
        proc = subprocess.run(
            [sys.executable, str(PREPARE_TASK), "--help"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        for flag in ("--concurrency", "--workers", "--parallel"):
            self.assertNotIn(flag, proc.stdout)

    def test_labels_cover_every_stage(self):
        for stage in task_scheduler.STAGES:
            self.assertIn(stage, task_scheduler.STAGE_LABELS)


class TestRunStageBarrier(unittest.TestCase):
    """barrier 调度：阶段间 barrier（无跨阶段流水线）、阶段内并发 == 默认值、
    13 run 规模下 6 个阶段全部按序执行。"""

    def test_stages_execute_in_barrier_order(self):
        """验收：各阶段按 barrier 顺序执行 — 阶段 k 的全部 worker 结束后
        阶段 k+1 才开始（下一阶段首个 start 严格晚于上一阶段末个 end，
        无跨阶段流水线）。"""
        rec = make_recorder()
        bounds = {}
        prev_len = 0
        for stage in task_scheduler.STAGES:
            res = task_scheduler.run_stage(stage, ITEMS13, make_worker(rec))
            self.assertEqual(len(res["ok"]), 13)
            ev = rec["events"][prev_len:]
            prev_len = len(rec["events"])
            self.assertEqual(len(ev), 26)
            starts = [t for (k, _s, t) in ev if k == "start"]
            ends = [t for (k, _s, t) in ev if k == "end"]
            bounds[stage] = (min(starts), max(ends))
        names = list(task_scheduler.STAGES)
        for i in range(len(names) - 1):
            _prev_start, prev_end = bounds[names[i]]
            next_start, _next_end = bounds[names[i + 1]]
            self.assertGreater(
                next_start, prev_end,
                f"stage {names[i + 1]} started before stage {names[i]} "
                f"fully ended (pipelining)")

    def test_in_stage_concurrency_matches_default(self):
        """验收：阶段内并发符合默认值（13 项 > 任何默认并发，峰值必须到顶）。"""
        for stage in task_scheduler.STAGES:
            rec = make_recorder()
            res = task_scheduler.run_stage(stage, ITEMS13, make_worker(rec))
            expected = task_scheduler.STAGE_CONCURRENCY[stage]
            self.assertEqual(rec["max_active"], expected,
                             f"stage {stage}: max concurrency != default")
            self.assertEqual(len(res["results"]), 13)

    def test_unknown_stage_rejected(self):
        with self.assertRaises(ValueError):
            task_scheduler.run_stage("bogus", ITEMS13[:2], make_worker(make_recorder()))

    def test_run_stage_api_has_no_concurrency_or_status_knobs(self):
        """结构性断言：run_stage 只暴露 (stage, items, worker) —— 并发默认
        值是 implementation constant（无运行时调参旋钮），且 API 不含任何
        status 路径（单一写者：状态推进归调用方在阶段边界独占）。"""
        params = list(inspect.signature(task_scheduler.run_stage).parameters)
        self.assertEqual(params, ["stage", "items", "worker"])
        # 模块层：编排核心没有任何文件写入点（write_text 只属于调用方在
        # 阶段边界的边界写盘；docstring 提到 task_status.json 只是契约说明）
        src = inspect.getsource(task_scheduler)
        self.assertNotIn("write_text", src)

    def test_results_order_stable(self):
        """结果按 items 顺序收集（确定性，跨阶段依赖可预测）。"""
        rec = make_recorder()
        res = task_scheduler.run_stage("compile", ITEMS13, make_worker(rec))
        self.assertEqual([r["run"] for r in res["results"]], ITEMS13)


class TestRunStageFailureIsolation(unittest.TestCase):
    """失败传播：任一 run 失败不阻断同阶段其他 run；失败清单正确汇总。"""

    def test_failed_items_do_not_block_siblings(self):
        """模拟 worker 失败（r02/r05/r11）：同阶段其余 10 个 run 正常完成，
        失败清单恰好 3 条且带 code。"""
        rec = make_recorder()
        res = task_scheduler.run_stage(
            "execute", ITEMS13, make_worker(rec, fail_ids={"r02", "r05", "r11"}))
        self.assertEqual(len(res["results"]), 13)
        self.assertEqual(len(res["ok"]), 10)
        self.assertEqual(len(res["failed"]), 3)
        codes = {r["code"] for r in res["failed"]}
        self.assertEqual(codes, {"SIM_FAILED"})
        runs = {r["run"] for r in res["failed"]}
        self.assertEqual(runs, {"r02", "r05", "r11"})
        # 失败不终止同阶段：全部 13 个 worker 都跑完了
        self.assertEqual(len(rec["events"]), 26)

    def test_stage_error_carries_code_and_action(self):
        def worker(item):
            raise task_scheduler.StageError(
                "ENTRY_NAME_DUPLICATE", f"{item} 条目名重复", "改 staging 名")
        res = task_scheduler.run_stage("run_prepare", ITEMS13[:2], worker)
        self.assertEqual(res["failed"][0]["code"], "ENTRY_NAME_DUPLICATE")
        self.assertIn("重复", res["failed"][0]["message"])
        self.assertIn("改 staging 名", res["failed"][0]["corrective_action"])

    def test_unexpected_raise_normalized(self):
        def worker(item):
            raise RuntimeError("boom")
        res = task_scheduler.run_stage("compile", ITEMS13[:2], worker)
        self.assertEqual(res["failed"][0]["code"], "WORKER_RAISED")
        self.assertIn("boom", res["failed"][0]["message"])

    def test_system_exit_from_fail_normalized(self):
        """worker 内 fail()（sys.exit）不杀死进程 — 归一为 failed 结果。"""
        def worker(item):
            sys.exit(3)
        res = task_scheduler.run_stage("gate", ITEMS13[:2], worker)
        self.assertEqual(res["failed"][0]["code"], "WORKER_EXIT")

    def test_non_dict_result_normalized(self):
        def worker(item):
            return "not-a-dict"
        res = task_scheduler.run_stage("promote", ITEMS13[:2], worker)
        self.assertEqual(res["failed"][0]["code"], "WORKER_INVALID_RESULT")

    def test_aggregate_failures_across_stages(self):
        rec = make_recorder()
        r1 = task_scheduler.run_stage("run_prepare", ITEMS13,
                                      make_worker(rec, fail_ids={"r00"}))
        r2 = task_scheduler.run_stage("compile", ITEMS13[:12],
                                      make_worker(rec, fail_ids={"r03"}))
        agg = task_scheduler.aggregate_failures([r1, r2])
        self.assertEqual(len(agg), 2)
        self.assertEqual({f["run"] for f in agg}, {"r00", "r03"})
        self.assertEqual({f["stage"] for f in agg},
                         {"run_prepare", "compile"})
        for f in agg:
            self.assertIn("code", f)
            self.assertIn("corrective_action", f)


class TestApplyStageStatus(unittest.TestCase):
    """阶段边界批量状态推进（单一写者语义）：只推进 ok 且在前驱状态的 run；
    失败 run 不推进；superseded 不触碰；source_prepare 不推进。"""

    @classmethod
    def setUpClass(cls):
        cls.task, cls.defect = parse_fixture("task.yaml")
        assert cls.defect is None
        cls.sha = "ab" * 32

    def _status(self, states):
        """构造状态字典：states: {rid: state}（其余 run 保持 planned）。"""
        status = task_schema.derive_task_status(self.task, self.sha,
                                                updated_at="2026-08-22T00:00:00")
        for rid, state in states.items():
            status["runs"][rid]["state"] = state
        return status

    def _ok(self, rids):
        return [{"run": rid, "status": "ok", "artifacts": {}}
                for rid in rids]

    def test_run_prepare_advances_planned_to_prepared(self):
        status = self._status({})
        new = task_scheduler.apply_stage_status(
            status, "run_prepare", self._ok(["r32-cooling"]),
            updated_at="2026-08-22T01:00:00")
        self.assertEqual(new["runs"]["r32-cooling"]["state"], "prepared")
        self.assertEqual(new["runs"]["r32-heating"]["state"], "planned")
        self.assertEqual(new["updated_at"], "2026-08-22T01:00:00")

    def test_full_main_path_transitions(self):
        """compile→compiled, execute→drafted, gate→gated, promote→promoted。"""
        status = self._status({"r32-cooling": "prepared"})
        status = task_scheduler.apply_stage_status(
            status, "compile", self._ok(["r32-cooling"]))
        self.assertEqual(status["runs"]["r32-cooling"]["state"], "compiled")
        status = task_scheduler.apply_stage_status(
            status, "execute", self._ok(["r32-cooling"]))
        self.assertEqual(status["runs"]["r32-cooling"]["state"], "drafted")
        status = task_scheduler.apply_stage_status(
            status, "gate", self._ok(["r32-cooling"]))
        self.assertEqual(status["runs"]["r32-cooling"]["state"], "gated")
        status = task_scheduler.apply_stage_status(
            status, "promote", self._ok(["r32-cooling"]))
        self.assertEqual(status["runs"]["r32-cooling"]["state"], "promoted")

    def test_failed_run_not_advanced(self):
        status = self._status({})
        status = task_scheduler.apply_stage_status(status, "run_prepare", [{
            "run": "r32-cooling", "status": "failed", "code": "SIM",
            "message": "x", "corrective_action": "y"}])
        self.assertEqual(status["runs"]["r32-cooling"]["state"], "planned")

    def test_superseded_untouched(self):
        status = self._status({"r32-cooling": "superseded"})
        status = task_scheduler.apply_stage_status(
            status, "run_prepare", self._ok(["r32-cooling"]))
        self.assertEqual(status["runs"]["r32-cooling"]["state"], "superseded")

    def test_non_precursor_state_not_touched(self):
        """防御性：compile 阶段只推进 prepared 的 run；drafted 的 run 不
        回退也不推进（幂等，item 选择已按状态过滤）。"""
        status = self._status({"r32-cooling": "drafted"})
        status = task_scheduler.apply_stage_status(
            status, "compile", self._ok(["r32-cooling"]))
        self.assertEqual(status["runs"]["r32-cooling"]["state"], "drafted")

    def test_source_prepare_never_advances_runs(self):
        """阶段 1 是任务级（缓存键），不推进任何 run 状态，只刷新检查点。"""
        status = self._status({})
        new = task_scheduler.apply_stage_status(
            status, "source_prepare", [], updated_at="2026-08-22T02:00:00")
        for entry in new["runs"].values():
            self.assertEqual(entry["state"], "planned")
        self.assertEqual(new["updated_at"], "2026-08-22T02:00:00")

    def test_original_status_not_mutated(self):
        """apply 返回新字典（deepcopy）：调用方原 status 不被并发语义污染。"""
        status = self._status({})
        before = json.dumps(status, ensure_ascii=False, sort_keys=True)
        task_scheduler.apply_stage_status(
            status, "run_prepare", self._ok(["r32-cooling"]))
        self.assertEqual(json.dumps(status, ensure_ascii=False, sort_keys=True),
                         before)


class TestSingleWriterBoundary(unittest.TestCase):
    """单一写者：阶段内状态文件零并发写（字节稳定），阶段边界恰好写盘一次。"""

    def test_status_bytes_stable_during_stage_and_one_write_per_boundary(self):
        root = Path(tempfile.mkdtemp(prefix="single_writer_"))
        try:
            task, defect = parse_fixture("task.yaml")
            self.assertIsNone(defect)
            status_path = root / "task_status.json"
            status = task_schema.derive_task_status(task, "ab" * 32,
                                                    updated_at="2026-08-22T00:00:00")
            writes = []
            snapshot = status_path.read_bytes() if status_path.is_file() else None

            def write_status(s):
                writes.append(1)
                status_path.write_text(json.dumps(s, ensure_ascii=False,
                                                  indent=2),
                                       encoding="utf-8")

            stages = ("source_prepare", "run_prepare", "compile",
                      "execute", "gate")
            # worker 的 run id 必须存在于状态索引中（fixture run 集）
            items = sorted(VALID_RUN_IDS)
            for stage in stages:
                violations = []

                def watchdog(item):
                    # worker 只回报结果；期间反复校验状态文件字节不变
                    for _ in range(4):
                        time.sleep(0.02)
                        cur = status_path.read_bytes() \
                            if status_path.is_file() else None
                        if cur != snapshot:
                            violations.append((item, cur, snapshot))
                    return {"run": item, "status": "ok", "artifacts": {}}

                res = task_scheduler.run_stage(stage, items, watchdog)
                self.assertEqual(violations, [],
                                 f"stage {stage}: status file changed during "
                                 f"concurrent workers")
                status = task_scheduler.apply_stage_status(
                    status, stage, res["results"],
                    updated_at=f"2026-08-22T0{stages.index(stage)+3}:00:00")
                write_status(status)
                snapshot = status_path.read_bytes()

            # 边界写盘次数 == 阶段数（每次恰好一次；阶段内零写）
            self.assertEqual(len(writes), len(stages))
            final = json.loads(status_path.read_text(encoding="utf-8"))
            for rid, entry in final["runs"].items():
                self.assertEqual(entry["state"], "gated")
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_workers_never_receive_status_path(self):
        """运行期断言：run_stage 的 worker 载荷只有 item 本身 —— status
        路径不存在于 API（见 test_run_stage_api_has_no_concurrency_or_
        status_knobs 的结构断言），worker 从结构上无法写状态文件。"""
        rec = make_recorder()
        seen = []

        def worker(item):
            seen.append(item)
            return {"run": item, "status": "ok", "artifacts": {}}

        task_scheduler.run_stage("compile", ITEMS13, worker)
        self.assertEqual(sorted(seen), sorted(ITEMS13))

    def test_missing_status_in_result_normalized(self):
        """结果缺 status（或非法 status）不得静默当作成功 — 归为
        WORKER_INVALID_RESULT（失败二分不会被哑成功掩盖）。"""
        def worker(item):
            return {"run": item}  # 缺 status
        res = task_scheduler.run_stage("compile", ITEMS13[:1], worker)
        self.assertEqual(res["failed"][0]["code"], "WORKER_INVALID_RESULT")

        def worker2(item):
            return {"run": item, "status": "maybe"}  # 非法 status
        res = task_scheduler.run_stage("compile", ITEMS13[:1], worker2)
        self.assertEqual(res["failed"][0]["code"], "WORKER_INVALID_RESULT")


class TestProgressLines(unittest.TestCase):
    """进度报告：阶段边界输出 `阶段 x/y 完成` 摘要（对齐『超过 60 秒主动
    说明进度』的要求）。"""

    def test_start_line_format(self):
        line = task_scheduler.stage_start_line(1, 6, "source_prepare", 3)
        self.assertIn("阶段 1/6 开始", line)
        self.assertIn("3 项", line)
        self.assertIn("并发 2", line)

    def test_end_line_format(self):
        rec = make_recorder()
        res = task_scheduler.run_stage(
            "compile", ITEMS13, make_worker(rec, fail_ids={"r05"}))
        line = task_scheduler.stage_end_line(2, 6, "compile", res)
        self.assertIn("阶段 2/6 完成", line)
        self.assertIn("ok=12", line)
        self.assertIn("failed=1", line)


class TestPrepareTaskCLIRun(unittest.TestCase):
    """--run 的公共 CLI 前置守卫（无 Office）：与 --prepare 同一前置契约
    （--init 产物存在 + 冻结一致性）；完整管线在 issue 08 的 e2e 层验证。"""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="task_run_cli_"))
        shutil.copytree(FIX, self.root, dirs_exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_run_without_init_exit3(self):
        proc = run_cli(self.root, "--run")
        self.assertEqual(proc.returncode, 3, proc.stderr)
        self.assertIn("TASK_MANIFEST_MISSING", proc.stderr)

    def test_run_stale_manifest_exit3(self):
        run_cli(self.root, "--init")
        with (self.root / "task.yaml").open("a", encoding="utf-8") as fh:
            fh.write("\n# 输入事实变化 — 快照已冻结\n")
        proc = run_cli(self.root, "--run")
        self.assertEqual(proc.returncode, 3, proc.stderr)
        self.assertIn("MANIFEST_STALE", proc.stderr)

    def test_run_without_task_yaml_exit1(self):
        """--run 前置：任务根存在但无 task.yaml → fatal exit 1（与
        --validate 同契约，先于任何 officecli 调用）。"""
        empty = Path(tempfile.mkdtemp(prefix="task_run_empty_"))
        try:
            proc = run_cli(empty, "--run")
        finally:
            shutil.rmtree(empty, ignore_errors=True)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("TASK_YAML_NOT_FOUND", proc.stderr)


if __name__ == "__main__":
    unittest.main()
