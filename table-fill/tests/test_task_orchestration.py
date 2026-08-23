"""Tests for the Task Artifact Model — issue 01 (spec S2) + Shared Flatten
Cache (issue 02, spec S3/S4/S5).

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

No Office involvement: cache/meta/digest fixtures are synthesized text;
classify_columns.py / structure_digest.py are pure text subprocesses.

Run with:
  python -m pytest table-fill/tests/test_task_orchestration.py -q
  python -m unittest tests.test_task_orchestration
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


if __name__ == "__main__":
    unittest.main()
