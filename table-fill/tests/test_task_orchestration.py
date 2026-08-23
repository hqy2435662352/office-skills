"""Tests for the Task Artifact Model — issue 01 (spec S2).

Covers the two pre-agreed seams (spec Testing Decision #3):
  1. task_schema.py pure functions (import seam): validate_task_yaml /
     derive_task_manifest / derive_task_status / freeze & status checks.
  2. prepare_task.py public CLI (subprocess seam): --validate / --init,
     exit codes 0/1/3, derived-file freeze semantics.

No Office involvement: fixtures are YAML examples + minimal placeholder
workbooks that only satisfy static existence checks.

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


if __name__ == "__main__":
    unittest.main()
