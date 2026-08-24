"""Tests for the Task Artifact Model — issue 01 (spec S2) + Shared Flatten
Cache (issue 02, spec S3/S4/S5) + Stage Orchestrator (issue 03, spec S6) +
Lifecycle / Resume / Supersede (issue 04, spec S7).

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

Issue 04 coverage (lifecycle / resume / supersede; checkpoint determination
= artifact existence + SHA-256, NO Office — spec S7 / Testing Decision #2):
  14. task_resume.gather_run_facts / classify_run_facts — the spec S7
      scenario matrix: no artifacts→planned; manifest valid + materialized
      hash match→prepared; plan fill_spec_sha256 + input_hashes→compiled;
      draft+receipt→drafted; **draft w/o receipt or hash mismatch (execute
      crash window)→execute_retry**; pending trio valid→gated (no bypass);
      final receipt→promoted; superseded→skip (--rebuild re-enters);
      receipt binding drift→blocked + supersede suggestion;
      materialized drift→planned (re-materialize).
  15. task_resume.schedule_resume — decisions → stage plan (barrier order,
      empty stages dropped, terminal states never scheduled).
  16. task_resume.resume_with_ctx — fake workers: resume continues from the
      actual checkpoint at every level, skips promoted/superseded, does not
      re-run pending gates, gate stage runs only for drafted+ runs whose
      own execute passed (failure isolation), status advances at stage
      boundaries only.
  17. resume_task.py CLI — guards (manifest required, stale manifest blocks
      with supersede suggestion) + full --supersede flow (re-derive +
      mark superseded + superseded_by link) + supersede precondition
      defects (unknown old/new, already superseded, unmapped changed run,
      removed run, chain/duplicate maps).
  18. Source hash drift blocks resume prelude (SOURCE_HASH_DRIFT) with
      supersede suggestion — acceptance #3 (in-process seam, officecli
      probes patched).

Issue 05 coverage (aggregate gate: gate_summary generation + per-run confirm
expansion; no Office — spec S6 phase 5 / Implementation Decision 31 /
Testing Decision #8):
  19. task_gate summary pure seam — timing_totals (machine+agent 双栏),
      mod_summary (MOD 裁决), spec_mod (selected_mod), receipt_validation
      (readback/source_coverage/issue_delta/structural/render_qa/validate),
      collect_gate_summary (13-run synthetic: gate_summary only contains
      drafted/gated runs, gaps complete, terminal runs excluded).
  20. task_gate.confirm_plan — presented-set vs current-evidence guard:
      matching partition (confirm/already_confirmed/promote in task.yaml
      order), stale (presented but no longer confirmable), not_presented
      (confirmable but never presented → cannot authorize unseen content).
  21. task_gate.run_confirm_expansion (injectable workers) — per-run
      .gate3_confirmed each bound to its OWN hash trio (real execution_gate
      --confirm), promote stage concurrency contract via task_scheduler,
      **confirm failure stops everything and reports the run (no promote)**,
      promote failure isolated with partial status advance, single-writer
      status advance gated→promoted at the promote boundary.
  22. gate_task.py CLI — --set aggregates only drafted runs (+ runs
      pending set for evidence-drafted), gaps listed; nothing-to-present
      fails closed; --confirm without gate_summary fails closed; full
      --set → --confirm flow with REAL execution_gate/promote_output
      subprocesses (final outputs land in <task_root>/outputs/, final hash
      == confirmed draft hash, status all promoted, re-run is a no-op);
      stale presentation blocks without promote; in-flight confirm failure
      (patched confirmer) stops and reports the run with no promote;
      promote before confirmation rejected by promote_output itself
      (GATE_NOT_CONFIRMED — fail-closed preserved, existing script zero
      change).

No Office involvement: cache/meta/digest fixtures are synthesized text;
classify_columns.py / structure_digest.py are pure text subprocesses;
execution_gate.py / promote_output.py are pure Python (zipfile check).

Run with:
  python -m pytest table-fill/tests/test_task_orchestration.py -q
  python -m unittest tests.test_task_orchestration
"""

from __future__ import annotations

import inspect
import io
import json
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import zipfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

SKILL_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS_DIR = SKILL_ROOT / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import task_schema  # noqa: E402
import flatten_cache  # noqa: E402
import task_prepare  # noqa: E402
import task_scheduler  # noqa: E402
import task_resume  # noqa: E402
import task_gate  # noqa: E402
import gate_task  # noqa: E402

FIX = Path(__file__).resolve().parent / "_fixtures" / "task_orchestration"
PREPARE_TASK = _SCRIPTS_DIR / "prepare_task.py"
RESUME_TASK = _SCRIPTS_DIR / "resume_task.py"
GATE_TASK = _SCRIPTS_DIR / "gate_task.py"
VALID_RUN_IDS = {"r32-cooling", "r32-heating", "r410a-cooling"}


def run_gate_cli(task_root: Path, mode: str) -> subprocess.CompletedProcess:
    """gate_task.py 的公共 CLI seam（无 Office：呈现/确认/断言全链路）。"""
    return subprocess.run(
        [sys.executable, str(GATE_TASK), "--task-root", str(task_root), mode],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=60,
    )


def run_cli(task_root: Path, mode: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(PREPARE_TASK), "--task-root", str(task_root), mode],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=60,
    )


def run_resume_cli(task_root: Path, *args: str) -> subprocess.CompletedProcess:
    """resume_task.py 的公共 CLI seam（无 Office 前置守卫 / --supersede）。"""
    return subprocess.run(
        [sys.executable, str(RESUME_TASK), "--task-root", str(task_root), *args],
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


# ── issue 04: Lifecycle / Resume / Supersede（spec S7） ─────────────────────

def write_run_at(root: Path, rid: str, level: str, *,
                 status_state: str | None = None,
                 spec_text: str = "fill_spec: level") -> Path:
    """在合成任务根下按断点层级生成一个 run 目录（纯文件系统 + 真实 SHA-256，
    无 Office）。level ∈ planned / prepared / compiled / drafted /
    crash_noreceipt / crash_receipt_mismatch / gated / confirmed / promoted /
    binding_spec_changed / binding_plan_changed / materialized_modified。
    spec_text 可注入以区分 run 的哈希三元组（plan/receipt 按实际内容重算）。
    返回 run_dir。"""
    run_dir = root / "runs" / rid
    run_dir.mkdir(parents=True, exist_ok=True)
    staged = run_dir / "parameter_book.xlsx"
    staged.write_bytes(b"source-bytes")
    tpl = run_dir / "filling_template.xlsx"
    tpl.write_bytes(b"template-bytes")

    if level in ("prepared", "compiled", "drafted", "crash_noreceipt",
                 "crash_receipt_mismatch", "gated", "confirmed", "promoted",
                 "binding_spec_changed", "binding_plan_changed",
                 "materialized_modified"):
        csv = run_dir / "parameter_book_R32_flat.csv"
        csv.write_bytes(b"header\n1,2,3\n")
        meta = run_dir / "parameter_book_R32_flat_meta.json"
        meta.write_text('{"schema_version": 1, "row_gaps": []}',
                        encoding="utf-8")
        digest = run_dir / "parameter_book_R32_flat_digest.md"
        digest.write_text("digest\n", encoding="utf-8")
        manifest = {
            "schema_version": 2,
            "workdir": str(run_dir),
            "task": "egypt-params-2026a",
            "files": [
                {"staged": "parameter_book.xlsx",
                 "source": "sources/parameter_book.xlsx",
                 "sha256": task_schema.file_sha256(staged)},
                {"staged": "filling_template.xlsx",
                 "source": "templates/filling_template.xlsx",
                 "sha256": task_schema.file_sha256(tpl)},
            ],
            "outlines": {},
            "flattened": [{
                "name": "parameter_book_R32_flat", "file": "parameter_book.xlsx",
                "sheet": "R32参数", "csv": csv.name, "meta": meta.name,
                "digest": digest.name,
                "sha256": task_schema.file_sha256(csv)},
            ],
            "target": {"file": "filling_template.xlsx", "sheet": "Sheet1"},
            "fingerprints": {}, "row_gaps": {}, "style_granularity": {},
        }
        (run_dir / "prepare_manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    if level in ("compiled", "drafted", "crash_noreceipt",
                 "crash_receipt_mismatch", "gated", "confirmed", "promoted",
                 "binding_spec_changed", "binding_plan_changed"):
        spec = run_dir / "fill_spec.yaml"
        spec.write_text(spec_text + "\n", encoding="utf-8")
        plan = {
            "schema_version": "2.5",
            "fill_spec_sha256": task_schema.file_sha256(spec),
            "target": "filling_template.xlsx", "target_sheet": "Sheet1",
            "input_hashes": {"parameter_book.xlsx":
                             task_schema.file_sha256(staged),
                             "filling_template.xlsx":
                             task_schema.file_sha256(tpl)},
            "fingerprints": {}, "blocks": [], "operations": [],
        }
        (run_dir / "execution_plan.json").write_text(
            json.dumps(plan, ensure_ascii=False), encoding="utf-8")

    if level in ("drafted", "crash_noreceipt", "crash_receipt_mismatch",
                 "gated", "confirmed", "promoted", "binding_spec_changed",
                 "binding_plan_changed"):
        draft = run_dir / "validated_draft.xlsx"
        draft.write_bytes(b"draft-bytes")
        receipt = {
            "schema_version": "2.5",
            "fill_spec_sha256": task_schema.file_sha256(spec),
            "execution_plan_sha256":
                task_schema.file_sha256(run_dir / "execution_plan.json"),
            "draft_sha256": (task_schema.file_sha256(draft)
                             if level != "crash_receipt_mismatch"
                             else "0" * 64),
            "draft_path": str(draft),
        }
        if level != "crash_noreceipt":
            (run_dir / "draft_receipt.json").write_text(
                json.dumps(receipt, ensure_ascii=False, indent=2),
                encoding="utf-8")

    # execute 之后输入事实改变：receipt 绑定的是“变更前”的 spec/plan
    if level == "binding_spec_changed":
        spec.write_text("fill_spec: level_changed\n", encoding="utf-8")
    if level == "binding_plan_changed":
        plan_path = run_dir / "execution_plan.json"
        plan_path.write_text('{"schema_version": "2.5", "regenerated": true}',
                             encoding="utf-8")

    if level == "gated":
        trio = {
            "fill_spec_sha256":
                task_schema.file_sha256(run_dir / "fill_spec.yaml"),
            "execution_plan_sha256":
                task_schema.file_sha256(run_dir / "execution_plan.json"),
            "draft_sha256":
                task_schema.file_sha256(run_dir / "validated_draft.xlsx"),
        }
        (run_dir / ".gate3_pending").write_text(json.dumps(
            {"presented_at": "2026-08-23T00:00:00Z", "hashes": trio},
            ensure_ascii=False, indent=2), encoding="utf-8")

    if level == "confirmed":
        trio = {
            "fill_spec_sha256":
                task_schema.file_sha256(run_dir / "fill_spec.yaml"),
            "execution_plan_sha256":
                task_schema.file_sha256(run_dir / "execution_plan.json"),
            "draft_sha256":
                task_schema.file_sha256(run_dir / "validated_draft.xlsx"),
        }
        (run_dir / ".gate3_confirmed").write_text(json.dumps(
            {"confirmed_at": "2026-08-23T00:01:00Z", "hashes": trio},
            ensure_ascii=False, indent=2), encoding="utf-8")

    if level == "promoted":
        (run_dir / "final_receipt.json").write_text(
            json.dumps({"schema_version": 2}), encoding="utf-8")

    if level == "materialized_modified":
        # 物化 CSV 被改动 → manifest 登记的 sha256 不再匹配
        csv = run_dir / "parameter_book_R32_flat.csv"
        csv.write_bytes(b"tampered-bytes")

    return run_dir


def make_status(task_ids, states: dict):
    """合成 task_status.json 形态（runs: {id: {state, superseded_by}}）。"""
    return {
        "schema_version": 1,
        "task": {"id": "egypt-params-2026a", "yaml": "task.yaml",
                 "yaml_sha256": "ab" * 32},
        "runs": {rid: {"state": states.get(rid, "planned"),
                       "superseded_by": None} for rid in sorted(task_ids)},
        "updated_at": "2026-08-23T00:00:00Z",
    }


class TestClassifyRunFacts(unittest.TestCase):
    """spec S7 断点判定场景矩阵（验收：矩阵全部通过，含 execute crash
    window 用例）。status 只提供 superseded 标记，其余全部由产物证据裁决 ——
    status 不是真值源。"""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="task_classify_"))

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def classify(self, rid, level, *, status_state=None, rebuild=False):
        run_dir = write_run_at(self.root, rid, level)
        facts = task_resume.gather_run_facts(run_dir)
        decision = task_resume.classify_run_facts(
            facts, status_state=status_state, rebuild=rebuild)
        return decision, run_dir, facts

    def test_no_artifacts_planned_stage1(self):
        """无产物 → planned → 阶段 1 起."""
        decision, _, _ = self.classify("r1", "planned")
        self.assertEqual(decision["status"], "planned")
        self.assertEqual(decision["needs"], ["source_prepare", "run_prepare",
                                             "compile", "execute", "gate"])

    def test_manifest_valid_prepared(self):
        """manifest 有效 + 物化产物 hash 匹配 → prepared → 跳过阶段 2."""
        decision, _, _ = self.classify("r1", "prepared")
        self.assertEqual(decision["status"], "prepared")
        self.assertEqual(decision["needs"], ["compile", "execute", "gate"])

    def test_drifted_materialization_planned(self):
        """物化产物 hash 与 manifest 不符 → 降级 planned（重新物化）。"""
        decision, _, _ = self.classify("r1", "materialized_modified")
        self.assertEqual(decision["status"], "planned")

    def test_plan_valid_compiled(self):
        """plan 的 fill_spec_sha256 匹配 + input_hashes 绑定有效 → compiled
        → 跳过阶段 3."""
        decision, _, _ = self.classify("r1", "compiled")
        self.assertEqual(decision["status"], "compiled")
        self.assertEqual(decision["needs"], ["execute", "gate"])

    def test_spec_changed_before_compile_prepared(self):
        """fill_spec 在 compile 前被改 → plan 绑定失效 → 降级 prepared
        （重新 compile，输入事实此时仍可重试）。"""
        root = Path(tempfile.mkdtemp(prefix="task_classify2_"))
        try:
            run_dir = write_run_at(root, "r1", "compiled")
            spec = run_dir / "fill_spec.yaml"
            spec.write_text("fill_spec: edited\n", encoding="utf-8")
            decision = task_resume.classify_run_facts(
                task_resume.gather_run_facts(run_dir))
            self.assertEqual(decision["status"], "prepared")
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_draft_with_receipt_drafted(self):
        """draft 存在 + receipt.draft_sha256 匹配 → drafted → 直接进 gate."""
        decision, _, _ = self.classify("r1", "drafted")
        self.assertEqual(decision["status"], "drafted")
        self.assertEqual(decision["needs"], ["gate"])

    def test_crash_window_no_receipt_execute_retry(self):
        """**execute crash window**：draft 存在但 receipt 缺失 → 重跑 execute."""
        decision, _, _ = self.classify("r1", "crash_noreceipt")
        self.assertEqual(decision["status"], "execute_retry")
        self.assertEqual(decision["needs"], ["execute", "gate"])

    def test_crash_window_receipt_mismatch_execute_retry(self):
        """**execute crash window**：draft 存在但 receipt.draft_sha256 不匹配
        → 重跑 execute."""
        decision, _, _ = self.classify("r1", "crash_receipt_mismatch")
        self.assertEqual(decision["status"], "execute_retry")
        self.assertEqual(decision["needs"], ["execute", "gate"])

    def test_gated_pending_valid_waits_no_bypass(self):
        """.gate3_pending 有效（hash 三元组匹配）→ gated → 等待确认，不绕过
        （needs 为空，不重跑任何阶段）。"""
        decision, _, _ = self.classify("r1", "gated")
        self.assertEqual(decision["status"], "gated")
        self.assertEqual(decision["needs"], [])
        self.assertIn("等待人工确认", decision["reason"])

    def test_pending_drifted_reexecutes(self):
        """pending 失效（三元组不匹配：draft 在呈现后被改动）→ receipt 证据
        失效 → execute_retry 重跑（不绕过、不把未验证的 draft 重新呈现）。"""
        root = Path(tempfile.mkdtemp(prefix="task_classify3_"))
        try:
            run_dir = write_run_at(root, "r1", "gated")
            # draft 在呈现后被改动 → 三元组漂移 + receipt.draft_sha256 失效
            draft = run_dir / "validated_draft.xlsx"
            draft.write_bytes(b"changed-draft")
            decision = task_resume.classify_run_facts(
                task_resume.gather_run_facts(run_dir))
            self.assertEqual(decision["status"], "execute_retry")
            self.assertEqual(decision["needs"], ["execute", "gate"])
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_pending_without_draft_reexecutes(self):
        """pending 存在但 draft 缺失（三元组无法成立）→ 不视为 gated，回落到
        compiled → 重跑 execute + gate。"""
        root = Path(tempfile.mkdtemp(prefix="task_classify3b_"))
        try:
            run_dir = write_run_at(root, "gated", "gated")
            (run_dir / "validated_draft.xlsx").unlink()
            decision = task_resume.classify_run_facts(
                task_resume.gather_run_facts(run_dir))
            self.assertEqual(decision["status"], "compiled")
            self.assertEqual(decision["needs"], ["execute", "gate"])
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_confirmed_valid_waits_promote(self):
        """.gate3_confirmed 有效且未 promote → confirmed（promote 由 gate_task
        展开；resume 不自动 promote）。"""
        decision, _, _ = self.classify("r1", "confirmed")
        self.assertEqual(decision["status"], "confirmed")
        self.assertEqual(decision["needs"], [])
        self.assertIn("gate_task", decision["reason"])

    def test_final_receipt_promoted(self):
        """final_receipt 存在 → promoted → 跳过."""
        decision, _, _ = self.classify("r1", "promoted")
        self.assertEqual(decision["status"], "promoted")
        self.assertEqual(decision["needs"], [])
        self.assertIn("跳过", decision["reason"])

    def test_superseded_skipped_unless_rebuild(self):
        """superseded → 跳过；显式 rebuild → 按产物证据重入主路径."""
        run_dir = write_run_at(self.root, "r1", "planned")
        decision = task_resume.classify_run_facts(
            task_resume.gather_run_facts(run_dir), status_state="superseded")
        self.assertEqual(decision["status"], "superseded")
        self.assertEqual(decision["needs"], [])
        decision_rb = task_resume.classify_run_facts(
            task_resume.gather_run_facts(run_dir), status_state="superseded",
            rebuild=True)
        self.assertEqual(decision_rb["status"], "planned")

    def test_receipt_binding_drift_blocked_supersede(self):
        """execute 后 spec 被改（receipt 绑定失效）→ 阻塞并建议 supersede,
        不继续旧 run."""
        for level in ("binding_spec_changed", "binding_plan_changed"):
            with self.subTest(level=level):
                decision, _, _ = self.classify("r1", level)
                self.assertEqual(decision["status"], "blocked")
                self.assertEqual(decision["needs"], [])
                self.assertEqual(decision["blocked"]["code"],
                                 "RUN_INPUT_CHANGED")
                self.assertIn("supersede",
                              decision["blocked"]["corrective_action"])

    def test_status_only_superseded_flag_not_truth(self):
        """status 只是生命周期索引：artifacts 说 drafted 而 status 说 planned
        时，判定必须由产物裁决（drafted），status 字段不参与判定。"""
        root = Path(tempfile.mkdtemp(prefix="task_classify4_"))
        try:
            run_dir = write_run_at(root, "r1", "drafted")
            decision = task_resume.classify_run_facts(
                task_resume.gather_run_facts(run_dir),
                status_state="planned")
            self.assertEqual(decision["status"], "drafted")
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_promoted_wins_over_pending(self):
        """final_receipt 优先于 gate marker（已交付的 run 不再被 gate
        等待态吸附）。"""
        root = Path(tempfile.mkdtemp(prefix="task_classify5_"))
        try:
            run_dir = write_run_at(root, "r1", "gated")
            (run_dir / "final_receipt.json").write_text(
                json.dumps({"schema_version": 2}), encoding="utf-8")
            decision = task_resume.classify_run_facts(
                task_resume.gather_run_facts(run_dir))
            self.assertEqual(decision["status"], "promoted")
        finally:
            shutil.rmtree(root, ignore_errors=True)


class TestScheduleResume(unittest.TestCase):
    """判定集合 → 阶段计划（纯函数）：STAGES 顺序、空阶段不执行、终态/
    等待态/阻塞态永不调度。"""

    def decision(self, status):
        return {"status": status, "needs": [],
                "reason": "", "evidence": {}}

    def test_planned_covers_all_stages_in_order(self):
        """planned run 覆盖 resume 主路径全部阶段（source_prepare →
        run_prepare → compile → execute → gate；resume 不含 promote ——
        promote 由 gate_task 展开）。"""
        schedule = task_resume.schedule_resume({"a": self.decision("planned")})
        self.assertEqual(list(schedule), list(task_prepare.RUN_STAGES))
        self.assertEqual(schedule["source_prepare"], ["a"])
        self.assertEqual(schedule["gate"], ["a"])

    def test_terminal_states_never_scheduled(self):
        decisions = {rid: self.decision(s)
                     for rid, s in [("g", "gated"), ("c", "confirmed"),
                                    ("p", "promoted"), ("s", "superseded"),
                                    ("b", "blocked")]}
        self.assertEqual(task_resume.schedule_resume(decisions), {})

    def test_prepared_skips_stage1_2(self):
        schedule = task_resume.schedule_resume({"a": self.decision("prepared")})
        self.assertEqual(list(schedule), ["compile", "execute", "gate"])

    def test_drafted_only_gate(self):
        schedule = task_resume.schedule_resume({"a": self.decision("drafted")})
        self.assertEqual(list(schedule), ["gate"])
        self.assertEqual(schedule["gate"], ["a"])

    def test_execute_retry_reschedules_execute_and_gate(self):
        schedule = task_resume.schedule_resume(
            {"a": self.decision("execute_retry")})
        self.assertEqual(list(schedule), ["execute", "gate"])

    def test_mixed_schedule_preserves_run_order(self):
        decisions = {"r1": self.decision("planned"),
                     "r2": self.decision("drafted")}
        schedule = task_resume.schedule_resume(decisions)
        self.assertEqual(schedule["source_prepare"], ["r1"])
        self.assertEqual(schedule["gate"], ["r1", "r2"])


class TestResumePipeline(unittest.TestCase):
    """resume_with_ctx 恢复编排（fake worker，无 Office）：模拟各阶段中断后
    从实际断点继续；跳过 promoted/superseded；gated 不绕过；阶段失败 run
    不进后续阶段（失败隔离）；status 阶段边界推进。"""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="task_resume_pipe_"))
        (self.root / "runs").mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def run_pipeline(self, levels: dict, states: dict, *, rebuild=False):
        """levels: rid → checkpoint level（None = 不生成产物目录）；
        states: rid → status state。返回 (report, log)；log 是 worker 调用
        (stage, item) 记录，多 item 阶段的到达顺序不定（比较时自行排序）。"""
        for rid, level in levels.items():
            if level is not None:
                write_run_at(self.root, rid, level)
        status = make_status(list(levels), states)
        ctx = {"root": self.root,
               "runs_dir": self.root / "runs",
               "status_runs": status["runs"],
               # 阶段 1 的 item 域是缓存键（cache_build_worker 契约）；单键
               # 即可驱动 planned run 的 source_prepare 阶段（fake worker）
               "unique_keys": ["ck"]}
        log: list = []

        def worker_of(stage):
            def worker(item):
                log.append((stage, item))
                return {"run": item, "status": "ok", "artifacts": {}}
            return worker

        with mock.patch.object(task_prepare, "finalize_cache_facts",
                               return_value=None):
            report = task_resume.resume_with_ctx(ctx, status,
                                                 rebuild=rebuild,
                                                 worker_of=worker_of)
        return report, log

    def test_planned_run_runs_all_stages_and_advances_status(self):
        """无产物 run：5 阶段全部执行，status 推进到 gated（不自动越过
        gate —— 编排止于 gate 呈现，不进入 promote）。"""
        report, log = self.run_pipeline({"r1": "planned"},
                                        {"r1": "planned"})
        # 阶段 1 的 worker 收到的是缓存键（"ck"），不是 run id
        self.assertEqual(log, [("source_prepare", "ck"),
                               ("run_prepare", "r1"), ("compile", "r1"),
                               ("execute", "r1"), ("gate", "r1")])
        self.assertEqual(report["checkpoints"]["r1"]["status"], "planned")
        status_file = json.loads(
            (self.root / "task_status.json").read_text(encoding="utf-8"))
        self.assertEqual(status_file["runs"]["r1"]["state"], "gated")
        self.assertTrue(report["gate_presented"])

    def test_each_checkpoint_resumes_from_actual_breakpoint(self):
        """模拟中断：prepared/compiled/drafted/crash window 各层级中断后，
        恢复只执行断点之后的阶段（验收：resume 均从实际断点继续）。"""
        cases = [
            ("prepared", "prepared",
             [("compile", "r1"), ("execute", "r1"), ("gate", "r1")]),
            ("compiled", "compiled",
             [("execute", "r1"), ("gate", "r1")]),
            ("crash_noreceipt", "compiled",
             [("execute", "r1"), ("gate", "r1")]),
            ("crash_receipt_mismatch", "compiled",
             [("execute", "r1"), ("gate", "r1")]),
            ("drafted", "drafted", [("gate", "r1")]),
        ]
        for level, state, expected in cases:
            with self.subTest(level=level):
                report, log = self.run_pipeline({"r1": level},
                                                {"r1": state})
                self.assertEqual(log, expected)
                self.assertEqual(report["checkpoints"]["r1"]["needs"],
                                 [s for s, _ in expected])

    def test_promoted_and_superseded_skipped(self):
        """promoted / superseded run 跳过（不执行任何阶段）。"""
        report, log = self.run_pipeline(
            {"r1": "promoted", "r2": "planned"},
            {"r1": "promoted", "r2": "superseded"})
        # r1 有产物但 status 标 superseded → 跳过；r2 promoted → 跳过
        self.assertEqual(report["skipped"]["promoted"], ["r1"])
        self.assertEqual(report["skipped"]["superseded"], ["r2"])
        self.assertEqual(log, [])

    def test_gated_pending_not_bypassed(self):
        """.gate3_pending 有效 → 等待确认：不执行任何阶段、不重新 --set、
        不自动 promote（fail-closed）。"""
        report, log = self.run_pipeline({"r1": "gated"}, {"r1": "gated"})
        self.assertEqual(log, [])
        self.assertEqual(report["gated_pending"], ["r1"])
        self.assertFalse(report["gate_presented"])

    def test_confirmed_run_left_for_gate_task(self):
        """.gate3_confirmed 有效 → confirmed：resume 不触碰（promote 由
        gate_task 展开）。"""
        report, log = self.run_pipeline({"r1": "confirmed"},
                                        {"r1": "drafted"})
        self.assertEqual(log, [])
        self.assertEqual(report["confirmed"], ["r1"])

    def test_blocked_run_not_continued_supersede_suggested(self):
        """.blocked (输入事实改变) → 不调度任何阶段，整体报告 supersede 建议."""
        report, log = self.run_pipeline({"r1": "binding_spec_changed"},
                                        {"r1": "compiled"})
        self.assertEqual(log, [])
        self.assertEqual(len(report["blocked"]), 1)
        self.assertEqual(report["blocked"][0]["code"], "RUN_INPUT_CHANGED")
        self.assertIn("supersede", report["blocked"][0]["corrective_action"])

    def test_mixed_task_skips_and_continues(self):
        """.混合任务：planned/prepared/compiled/crash 只跑所需阶段；gated 等
        待；promoted/superseded 跳过。"""
        levels = {"r-p": "planned", "r-pr": "prepared", "r-c": "compiled",
                  "r-d": "drafted", "r-crash": "crash_noreceipt",
                  "r-g": "gated", "r-f": "confirmed", "r-m": "promoted",
                  "r-s": None, "r-b": "binding_plan_changed"}
        states = {"r-p": "planned", "r-pr": "prepared", "r-c": "compiled",
                  "r-d": "drafted", "r-crash": "compiled", "r-g": "gated",
                  "r-f": "drafted", "r-m": "promoted", "r-s": "superseded",
                  "r-b": "compiled"}
        report, log = self.run_pipeline(levels, states)
        expected = [
            ("source_prepare", "ck"),
            ("run_prepare", "r-p"),
            ("compile", "r-p"), ("compile", "r-pr"),
            ("execute", "r-p"), ("execute", "r-pr"), ("execute", "r-c"),
            ("execute", "r-crash"),
            ("gate", "r-p"), ("gate", "r-pr"), ("gate", "r-c"),
            ("gate", "r-crash"), ("gate", "r-d"),
        ]
        self.assertEqual(sorted(log), sorted(expected))
        self.assertEqual(set(report["skipped"]["promoted"]), {"r-m"})
        self.assertEqual(set(report["skipped"]["superseded"]), {"r-s"})
        self.assertEqual(report["gated_pending"], ["r-g"])
        self.assertEqual(report["confirmed"], ["r-f"])
        self.assertEqual([b["run"] for b in report["blocked"]], ["r-b"])
        status_file = json.loads(
            (self.root / "task_status.json").read_text(encoding="utf-8"))
        for rid in ("r-p", "r-pr", "r-c", "r-crash", "r-d"):
            self.assertEqual(status_file["runs"][rid]["state"], "gated")
        self.assertEqual(status_file["runs"]["r-g"]["state"], "gated")
        self.assertEqual(status_file["runs"]["r-f"]["state"], "drafted")
        self.assertEqual(status_file["runs"]["r-m"]["state"], "promoted")
        self.assertEqual(status_file["runs"]["r-s"]["state"], "superseded")
        self.assertEqual(status_file["runs"]["r-b"]["state"], "compiled")

    def test_rebuild_reactivates_superseded_run(self):
        """验收：superseded → 默认跳过，显式 --rebuild 重新进入主路径且
        状态索引能随阶段推进（不会因 superseded 索引卡在 apply 前驱集外）。"""
        # 默认：跳过
        report_skip, log_skip = self.run_pipeline(
            {"r1": "planned"}, {"r1": "superseded"})
        self.assertEqual(log_skip, [])
        self.assertEqual(report_skip["skipped"]["superseded"], ["r1"])
        # --rebuild：状态索引复位 planned → 五阶段全部执行 → 推进到 gated
        report_rb, log_rb = self.run_pipeline(
            {"r1": "planned"}, {"r1": "superseded"}, rebuild=True)
        self.assertEqual(log_rb, [("source_prepare", "ck"),
                                   ("run_prepare", "r1"), ("compile", "r1"),
                                   ("execute", "r1"), ("gate", "r1")])
        self.assertEqual(report_rb["skipped"]["superseded"], [])
        status_file = json.loads(
            (self.root / "task_status.json").read_text(encoding="utf-8"))
        self.assertEqual(status_file["runs"]["r1"]["state"], "gated")
        self.assertIsNone(status_file["runs"]["r1"]["superseded_by"])

    def test_rebuild_resumes_from_artifact_evidence(self):
        """--rebuild 的 superseded run 按产物证据接续（如已 drafted → 只补
        gate 呈现，不重跑前序阶段），索引复位到 drafted 后可正常推进。"""
        report, log = self.run_pipeline(
            {"r1": "drafted"}, {"r1": "superseded"}, rebuild=True)
        self.assertEqual(log, [("gate", "r1")])
        self.assertEqual(report["checkpoints"]["r1"]["status"], "drafted")
        status_file = json.loads(
            (self.root / "task_status.json").read_text(encoding="utf-8"))
        self.assertEqual(status_file["runs"]["r1"]["state"], "gated")

    def test_gated_index_does_not_suppress_crash_window(self):
        """status 索引停在 gated 但产物证据是 crash window（pending 漂移后
        draft 无 receipt）→ execute + gate 仍被调度（证据裁决，不因索引
        等待）；gate 重新呈现后索引保持 gated（等待确认，不绕过）。"""
        report, log = self.run_pipeline(
            {"r1": "crash_noreceipt"}, {"r1": "gated"})
        self.assertEqual(log, [("execute", "r1"), ("gate", "r1")])
        status_file = json.loads(
            (self.root / "task_status.json").read_text(encoding="utf-8"))
        self.assertEqual(status_file["runs"]["r1"]["state"], "gated")

    def test_failed_execute_run_excluded_from_gate(self):
        """阶段失败隔离：execute 失败的 run 不进 gate 阶段（不会对无有效
        draft 的 run 执行 --set），同阶段其他 run 不受影响。"""
        status = make_status(["r1", "r2"], {"r1": "compiled",
                                            "r2": "compiled"})
        write_run_at(self.root, "r1", "compiled")
        write_run_at(self.root, "r2", "compiled")
        ctx = {"root": self.root, "runs_dir": self.root / "runs",
               "status_runs": status["runs"]}
        log: list = []

        def worker_of(stage):
            def worker(item):
                log.append((stage, item))
                if stage == "execute" and item == "r2":
                    raise task_scheduler.StageError(
                        "EXECUTE_FAILED", "模拟执行失败", "修复后重试")
                return {"run": item, "status": "ok", "artifacts": {}}
            return worker

        with mock.patch.object(task_prepare, "finalize_cache_facts",
                               return_value=None):
            report = task_resume.resume_with_ctx(ctx, status,
                                                 worker_of=worker_of)
        self.assertEqual(sorted(log), [("execute", "r1"), ("execute", "r2"),
                                       ("gate", "r1")])
        codes = [f["code"] for f in report["failures"]]
        self.assertIn("EXECUTE_FAILED", codes)
        self.assertEqual([f["run"] for f in report["failures"]], ["r2"])
        status_file = json.loads(
            (self.root / "task_status.json").read_text(encoding="utf-8"))
        self.assertEqual(status_file["runs"]["r1"]["state"], "gated")
        self.assertEqual(status_file["runs"]["r2"]["state"], "compiled")


class TestSupersedePure(unittest.TestCase):
    """supersede 的纯函数层：mapping 校验矩阵 + 状态演进（不重置未涉 run）。"""

    def setUp(self):
        task, defect = task_schema.parse_task_yaml(
            (FIX / "task.yaml").read_text(encoding="utf-8"))
        assert defect is None
        self.task = task
        # 新版本 run 声明（supersede 前置：先入 task.yaml 再 mapping）
        self.task_with_v2 = json.loads(json.dumps(task))
        self.task_with_v2["runs"].append({
            "id": "r32-heating_v2",
            "source": {"file": "sources/parameter_book.xlsx",
                       "sheets": ["R32参数"]},
            "target": {"template": "templates/filling_template.xlsx",
                       "sheet": "Sheet1",
                       "output": "out_r32_heating_v2.xlsx"},
        })
        self.status = task_schema.derive_task_status(
            self.task, "ab" * 32, updated_at="2026-08-23T00:00:00Z")
        self.manifest = task_schema.derive_task_manifest(
            self.task, "ab" * 32, frozen_at="2026-08-23T00:00:00Z")
        # 模拟推进：r32-cooling 已 gated（不得被 supersede 重置）
        self.status["runs"]["r32-cooling"] = {"state": "gated",
                                              "superseded_by": None}

    def codes(self, mappings, task=None):
        return [d["code"] for d in task_resume.validate_supersede(
            task or self.task_with_v2, self.manifest, self.status, mappings)]

    def test_valid_mapping_passes(self):
        codes = self.codes([("r32-heating", "r32-heating_v2")])
        self.assertEqual(codes, [])

    def test_self_link_rejected(self):
        self.assertIn("MAPPING_SELF_LINK",
                      self.codes([("r32-heating", "r32-heating")]))

    def test_unknown_old_rejected(self):
        self.assertIn("RUN_NOT_FOUND",
                      self.codes([("ghost", "r32-heating_v2")]))

    def test_undeclared_new_rejected(self):
        """new 未在 task.yaml 声明 → 拒绝（新版本必须先入声明）。"""
        self.assertIn("RUN_NOT_FOUND",
                      self.codes([("r32-heating", "not-declared-v2")]))

    def test_already_superseded_rejected(self):
        self.status["runs"]["r32-heating"] = {"state": "superseded",
                                              "superseded_by": "old-v2"}
        self.assertIn("RUN_ALREADY_SUPERSEDED",
                      self.codes([("r32-heating", "r32-heating_v2")]))

    def test_chain_and_duplicates_rejected(self):
        self.assertIn("MAPPING_CHAIN", self.codes(
            [("r32-heating", "r32-cooling"),
             ("r32-cooling", "r410a-cooling")]))
        self.assertIn("MAPPING_DUPLICATE_NEW", self.codes(
            [("r32-heating", "r32-heating_v2"),
             ("r410a-cooling", "r32-heating_v2")]))
        self.assertIn("MAPPING_DUPLICATE_OLD", self.codes(
            [("r32-heating", "r32-heating_v2"),
             ("r32-heating", "r32-heating_v3")]))

    def test_removed_old_run_rejected(self):
        """旧 run 从 task.yaml 删除 → 拒绝（superseded 状态在 status 延续，
        旧声明必须保留）。"""
        task = json.loads(json.dumps(self.task_with_v2))
        task["runs"] = [r for r in task["runs"] if r["id"] != "r32-cooling"]
        defects = task_resume.validate_supersede(
            task, self.manifest, self.status,
            [("r32-heating", "r32-heating_v2")])
        self.assertIn("RUN_REMOVED_FROM_TASK_YAML",
                      [d["code"] for d in defects])
        # 同时声明被改动的 r32-heating 已 mapping → 无 UNMAPPED
        self.assertNotIn("UNMAPPED_RUN_CHANGED",
                         [d["code"] for d in defects])

    def test_unmapped_changed_run_rejected(self):
        """声明被改动（含源 sheet）且未 mapping 的 run → 拒绝
        （防“改了声明但不标记废弃”的静默错误）。"""
        task = json.loads(json.dumps(self.task_with_v2))
        for run in task["runs"]:
            if run["id"] == "r32-cooling":
                run["source"]["sheets"] = ["R32参数"]
        defects = task_resume.validate_supersede(
            task, self.manifest, self.status,
            [("r32-heating", "r32-heating_v2")])
        self.assertIn("UNMAPPED_RUN_CHANGED", [d["code"] for d in defects])
        self.assertEqual(defects[0]["at"], "task.yaml/runs/r32-cooling")

    def test_cosmetic_template_family_change_not_blocking(self):
        """template_family（仅记录，D6 不实现）改动不视为业务声明变化。"""
        task = json.loads(json.dumps(self.task_with_v2))
        for run in task["runs"]:
            if run["id"] == "r32-cooling":
                run["template_family"] = "changed 记录"
        codes = [d["code"] for d in task_resume.validate_supersede(
            task, self.manifest, self.status,
            [("r32-heating", "r32-heating_v2")])]
        self.assertEqual(codes, [])

    def test_supersede_status_preserves_untouched_runs(self):
        """状态演进：旧 run superseded + superseded_by 链接新版本；task.yaml
        新增 run 初始化 planned；未涉 run 状态原样（gated 不被重置）；task
        绑定指纹刷新。"""
        task = json.loads(json.dumps(self.task))
        task["runs"].append({
            "id": "r32-heating_v2",
            "source": {"file": "sources/parameter_book.xlsx",
                       "sheets": ["R32参数"]},
            "target": {"template": "templates/filling_template.xlsx",
                       "sheet": "Sheet1",
                       "output": "out_r32_heating_v2.xlsx"},
        })
        new_status = task_resume.supersede_status(
            self.status, task, "cd" * 32,
            [("r32-heating", "r32-heating_v2")])
        self.assertEqual(new_status["runs"]["r32-heating"]["state"],
                         "superseded")
        self.assertEqual(new_status["runs"]["r32-heating"]["superseded_by"],
                         "r32-heating_v2")
        self.assertEqual(new_status["runs"]["r32-heating_v2"]["state"],
                         "planned")
        self.assertEqual(new_status["runs"]["r32-cooling"]["state"], "gated")
        self.assertEqual(
            new_status["task"]["yaml_sha256"], "cd" * 32)
        # 与 task.yaml 的一一对应契约保持（check_status 应通过）
        problems = task_schema.check_status(task, new_status, "cd" * 32)
        self.assertEqual(problems, [])


class TestResumeTaskCLI(unittest.TestCase):
    """resume_task.py 公共 CLI seam（无 Office）：前置守卫 + --supersede
    全流程 + 前置缺陷矩阵。"""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="task_resume_cli_"))
        shutil.copytree(FIX, self.root, dirs_exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def yaml_hash(self):
        return task_schema.file_sha256(self.root / "task.yaml")

    def add_v2_run_to_yaml(self, rid="r32-heating",
                           new_id="r32-heating_v2"):
        """在 task.yaml 追加一个新版本 run 声明（旧 run 保留）。"""
        with (self.root / "task.yaml").open("a", encoding="utf-8") as fh:
            fh.write(f"\n  - id: {new_id}\n"
                     "    source:\n"
                     "      file: sources/parameter_book.xlsx\n"
                     "      sheets: [R32参数]\n"
                     "    target:\n"
                     "      template: templates/filling_template.xlsx\n"
                     "      sheet: Sheet1\n"
                     f"      output: out_{new_id}.xlsx\n")

    def test_resume_without_init_exit3(self):
        proc = run_resume_cli(self.root, "--resume")
        self.assertEqual(proc.returncode, 3, proc.stderr)
        self.assertIn("TASK_MANIFEST_MISSING", proc.stderr)

    def test_resume_stale_manifest_blocks_with_supersede_suggestion(self):
        """task.yaml 修改 → 冻结快照不一致 → resume 阻塞（fail-closed，不静默
        重派生）；corrective 指向 supersede。"""
        run_cli(self.root, "--init")
        with (self.root / "task.yaml").open("a", encoding="utf-8") as fh:
            fh.write("\n# 输入事实变化 — 快照已冻结\n")
        proc = run_resume_cli(self.root, "--resume")
        self.assertEqual(proc.returncode, 3, proc.stderr)
        self.assertIn("MANIFEST_STALE", proc.stderr)
        err = json.loads(proc.stderr)
        action = err["defects"][0]["corrective_action"]
        self.assertIn("supersede", action)

    def test_resume_without_task_yaml_exit1(self):
        empty = Path(tempfile.mkdtemp(prefix="task_resume_empty_"))
        try:
            proc = run_resume_cli(empty, "--resume")
        finally:
            shutil.rmtree(empty, ignore_errors=True)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("TASK_YAML_NOT_FOUND", proc.stderr)

    def test_resume_requires_init_derived_files(self):
        """--init 未跑（只有 task.yaml）→ exit 3 指向 --init。"""
        proc = run_resume_cli(self.root, "--resume")
        self.assertEqual(proc.returncode, 3, proc.stderr)
        self.assertIn("TASK_MANIFEST_MISSING", proc.stderr)

    def test_supersede_full_flow(self):
        """验收 ✓ 全流程：声明新版本 → --supersede --map → 重派生快照 +
        状态演进；随后 --init/--validate 一致通过。"""
        run_cli(self.root, "--init")
        self.add_v2_run_to_yaml()
        proc = run_resume_cli(
            self.root, "--supersede", "--map", "r32-heating=r32-heating_v2")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = json.loads(proc.stdout)
        self.assertEqual(out["status"], "PASS")
        self.assertEqual(out["code"], "TASK_SUPERSEDED")
        self.assertEqual(out["superseded"][0]["old"], "r32-heating")
        self.assertEqual(out["superseded"][0]["new"], "r32-heating_v2")

        manifest = json.loads((self.root / "task_manifest.json")
                              .read_text(encoding="utf-8"))
        self.assertEqual(manifest["task"]["yaml_sha256"], self.yaml_hash())
        self.assertIn("r32-heating_v2", manifest["runs"])
        self.assertIn("r32-heating", manifest["runs"])  # 旧声明保留
        self.assertEqual(manifest["staged_files"], [])  # 新快照等 prepare 补全
        self.assertGreater(manifest["frozen_at"], "2026-08-23")  # 重新封存

        status = json.loads((self.root / "task_status.json")
                            .read_text(encoding="utf-8"))
        self.assertEqual(status["runs"]["r32-heating"]["state"],
                         "superseded")
        self.assertEqual(status["runs"]["r32-heating"]["superseded_by"],
                         "r32-heating_v2")
        self.assertEqual(status["runs"]["r32-heating_v2"]["state"],
                         "planned")
        self.assertEqual(status["runs"]["r32-cooling"]["state"], "planned")
        self.assertEqual(status["task"]["yaml_sha256"], self.yaml_hash())

        # 一致性保持：--init / --validate 均通过（run id 一一对应 + 合法状态）
        proc = run_cli(self.root, "--init")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        proc = run_cli(self.root, "--validate")
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_supersede_twice_rejected(self):
        run_cli(self.root, "--init")
        self.add_v2_run_to_yaml()
        ok = run_resume_cli(self.root, "--supersede", "--map",
                            "r32-heating=r32-heating_v2")
        self.assertEqual(ok.returncode, 0, ok.stderr)
        again = run_resume_cli(self.root, "--supersede", "--map",
                               "r32-heating=r32-heating_v2")
        self.assertEqual(again.returncode, 3, again.stderr)
        self.assertIn("RUN_ALREADY_SUPERSEDED", again.stderr)

    def test_supersede_precondition_defects(self):
        run_cli(self.root, "--init")
        cases = [
            (["--map", "ghost=ghost_v2"], "RUN_NOT_FOUND"),      # old 不存在
            (["--map", "r32-heating=nope"], "RUN_NOT_FOUND"),    # new 未声明
            (["--map", "r32-heating=r32-heating"], "MAPPING_SELF_LINK"),
        ]
        for args, code in cases:
            with self.subTest(code=code):
                proc = run_resume_cli(self.root, "--supersede", *args)
                self.assertEqual(proc.returncode, 3, proc.stderr)
                self.assertIn(code, proc.stderr)

    def test_supersede_unmapped_changed_run_rejected(self):
        """声明被改动且未 mapping 的 run → 整体拒绝（先改声明后 supersede
        其他 run 时，被改 run 必须一并 mapping）；拒绝时不写任何文件。"""
        run_cli(self.root, "--init")
        original_hash = self.yaml_hash()  # --init 时封存的绑定指纹
        self.add_v2_run_to_yaml()
        yaml_path = self.root / "task.yaml"
        text = yaml_path.read_text(encoding="utf-8")
        yaml_path.write_text(text.replace("sheets: [R32参数, R410A参数]",
                                          "sheets: [R32参数]"),
                             encoding="utf-8")
        proc = run_resume_cli(self.root, "--supersede", "--map",
                              "r32-heating=r32-heating_v2")
        self.assertEqual(proc.returncode, 3, proc.stderr)
        self.assertIn("UNMAPPED_RUN_CHANGED", proc.stderr)
        # 拒绝时不得写入任何文件：manifest 指纹仍绑定旧 task.yaml，status 原样
        manifest = json.loads((self.root / "task_manifest.json")
                              .read_text(encoding="utf-8"))
        self.assertEqual(manifest["task"]["yaml_sha256"], original_hash)
        status = json.loads((self.root / "task_status.json")
                            .read_text(encoding="utf-8"))
        self.assertEqual(status["runs"]["r32-heating"]["state"], "planned")

    def test_supersede_removed_run_rejected(self):
        """旧 run 从 task.yaml 删除 → 拒绝（保留旧声明是 superseded 契约）。"""
        run_cli(self.root, "--init")
        self.add_v2_run_to_yaml()
        yaml_path = self.root / "task.yaml"
        lines = yaml_path.read_text(encoding="utf-8").splitlines()
        # 删除 r32-cooling 声明块（固定行距，见 fixture task.yaml）
        start = next(i for i, ln in enumerate(lines)
                     if "id: r32-cooling" in ln)
        end = next(i for i, ln in enumerate(lines)
                   if "id: r32-heating" in ln)
        del lines[start:end]
        yaml_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        proc = run_resume_cli(self.root, "--supersede", "--map",
                              "r32-heating=r32-heating_v2")
        self.assertEqual(proc.returncode, 3, proc.stderr)
        self.assertIn("RUN_REMOVED_FROM_TASK_YAML", proc.stderr)

    def test_supersede_without_map_exit3(self):
        run_cli(self.root, "--init")
        proc = run_resume_cli(self.root, "--supersede")
        self.assertEqual(proc.returncode, 3, proc.stderr)
        self.assertIn("MAPPING_REQUIRED", proc.stderr)

    def test_supersede_bad_map_format_exit3(self):
        run_cli(self.root, "--init")
        proc = run_resume_cli(self.root, "--supersede", "--map", "noequals")
        self.assertEqual(proc.returncode, 3, proc.stderr)
        self.assertIn("MAPPING_FORMAT", proc.stderr)

    def test_supersede_without_init_exit3(self):
        proc = run_resume_cli(self.root, "--supersede", "--map",
                              "r32-heating=r32-heating_v2")
        self.assertEqual(proc.returncode, 3, proc.stderr)
        self.assertIn("TASK_DERIVED_MISSING", proc.stderr)


class TestResumeDriftBlocked(unittest.TestCase):
    """验收：源文件 hash 变化 → resume 前置阻塞（SOURCE_HASH_DRIFT）并给出
    supersede 建议，不继续旧 run（in-process seam，officecli 探测打桩）。"""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="task_resume_drift_"))
        shutil.copytree(FIX, self.root, dirs_exist_ok=True)
        run_cli(self.root, "--init")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_source_hash_drift_blocks_and_suggests_supersede(self):
        from prepare_task import _load_derived

        task, _y, manifest, status = _load_derived(
            self.root, require_existing=True)
        with _Patches(_probe_patches()):
            ctx = task_prepare.prepare_task_level(
                self.root, task, manifest, status,
                allowed_states=task_schema.RUN_STATES)
        # 模拟 finalize_cache_facts 的 staged_files 封存（漂移检查只依赖
        # staged_files；cache refs/指纹补全属于 prepare 阶段，此处不涉及）
        manifest["staged_files"] = ctx["staged_files"]
        (self.root / task_schema.TASK_MANIFEST_NAME).write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8")

        # 源文件内容变化（模拟用户更新数据源）
        source = self.root / "sources" / "parameter_book.xlsx"
        with source.open("ab") as fh:
            fh.write(b"source-changed")

        err = io.StringIO()
        with _Patches(_probe_patches()), redirect_stderr(err):
            with self.assertRaises(SystemExit) as cm:
                task_prepare.prepare_task_level(
                    self.root, task, manifest, status,
                    allowed_states=task_schema.RUN_STATES)
        self.assertEqual(cm.exception.code, 3)
        self.assertIn("SOURCE_HASH_DRIFT", err.getvalue())
        self.assertIn("supersede", err.getvalue())

    def test_unchanged_inputs_resume_prelude_passes(self):
        """输入未变时 resume 前置通过（无漂移），无 SystemExit。"""
        from prepare_task import _load_derived

        task, _y, manifest, status = _load_derived(
            self.root, require_existing=True)
        with _Patches(_probe_patches()):
            ctx = task_prepare.prepare_task_level(
                self.root, task, manifest, status,
                allowed_states=task_schema.RUN_STATES)
        manifest["staged_files"] = ctx["staged_files"]
        (self.root / task_schema.TASK_MANIFEST_NAME).write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8")
        with _Patches(_probe_patches()):
            ctx2 = task_prepare.prepare_task_level(
                self.root, task, manifest, status,
                allowed_states=task_schema.RUN_STATES)
        self.assertEqual(ctx2["task_id"], "egypt-params-2026a")


class _Patches:
    """批量 mock patch 上下文（无依赖第三方 mock 库）。"""

    def __init__(self, patches):
        self._patches = patches

    def __enter__(self):
        for p in self._patches:
            p.start()
        return self

    def __exit__(self, *exc):
        for p in reversed(self._patches):
            p.stop()
        return False


def _probe_patches():
    """officecli 探测打桩（prelude 的 check_resident/版本探测/outline 探测）：
    让 prepare_task_level 在无 officecli 环境跑通（真实 staging/hash，只打桩
    officecli 触点）。fake outline 按 staged 文件名给出 fixture 的真实 sheet。"""

    def fake_outline(path):
        name = Path(path).name
        if name.startswith("parameter_book"):
            sheets = [{"name": "R32参数"}, {"name": "R410A参数"}]
        else:
            sheets = [{"name": "Sheet1"}]
        return {"data": {"sheets": sheets}}

    return [
        mock.patch.object(task_prepare.preflight, "check_resident_cleanup"),
        mock.patch.object(task_prepare.flatten_cache, "officecli_version",
                          return_value="1.0.144-test"),
        mock.patch.object(task_prepare, "officecli_outline",
                          side_effect=fake_outline),
    ]


def _seed_cache_hits(root: Path, ctx: dict) -> None:
    """为 ctx 的全部唯一缓存键预建白名单三产物（cache_hit 只查存在性）：
    阶段 1 全命中、零 officecli（顶层契约：物化产物由测试确定）。meta 带一
    个非空列，使 classify_columns 产出非空 column_classifications（空列 section
    会被 PyYAML 解析为 None，触发 structure_digest 既有的空节遍历缺失）。"""
    for key in ctx["unique_keys"]:
        entry = flatten_cache.cache_entry_dir(root, key)
        entry.mkdir(parents=True, exist_ok=True)
        (entry / "flat.csv").write_text("a,b\n1,2\n", encoding="utf-8")
        (entry / "meta.json").write_text(json.dumps({
            "schema_version": 1, "file": "seeded.xlsx", "sheet": "seeded",
            "dimensions": {"rows": 2, "cols": 2, "data_rows": 1},
            "columns": [{"col": "A", "nonempty": 1, "numeric_ratio": 1.0,
                         "unique": 1, "samples": ["1"]}],
            "row_gaps": [], "style_granularity": {}},
            ensure_ascii=False), encoding="utf-8")
        (entry / "digest.md").write_text("seeded digest\n", encoding="utf-8")


class TestResumeRealWorkers(unittest.TestCase):
    """真实默认 worker 路径（无 Office）：阶段 1 的 item 域是缓存键而非 run
    id（cache_build_worker 契约）；命中路径零 officecli。验收 #2 的生产路径
    段 —— 恢复编排在真实 worker 下从断点继续（阶段 1/2 真跑，compile 因缺
    fill_spec 聚合 FILL_SPEC_MISSING，绝不出现 WORKER_RAISED 的键寻址崩溃）。"""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="task_resume_real_"))
        shutil.copytree(FIX, self.root, dirs_exist_ok=True)
        proc = run_cli(self.root, "--init")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        from prepare_task import _load_derived
        self._load_derived = _load_derived

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_stage1_items_are_cache_keys_not_run_ids(self):
        """planned run（无产物）走真实 worker：阶段 1 用缓存键调度（全命中），
        阶段 2 真实物化成功；compile 缺 fill_spec 聚合 FILL_SPEC_MISSING，
        没有任何 WORKER_RAISED（run id 误入缓存键域会在这里崩溃）。"""
        task, _y, manifest, status = self._load_derived(
            self.root, require_existing=True)
        with _Patches(_probe_patches()):
            ctx = task_prepare.prepare_task_level(
                self.root, task, manifest, status,
                allowed_states=task_schema.RUN_STATES)
            _seed_cache_hits(self.root, ctx)
            report = task_resume.resume_with_ctx(ctx, status,
                                                 progress=lambda _: None)

        by_stage = {sr["stage"]: sr for sr in report["stages"]}
        # 阶段 1：item 域 = 唯一缓存键（3 个），全命中 → 零 officecli
        self.assertEqual(report["checkpoints"]["r32-cooling"]["status"],
                         "planned")
        sr1 = by_stage["source_prepare"]
        self.assertEqual(sr1["items"], 3)
        self.assertEqual(len(sr1["ok"]), 3)
        self.assertEqual(sum(1 for r in sr1["ok"]
                             if r["artifacts"].get("hit")), 3)
        # 阶段 2：真实 run_prepare_worker 成功（物化 = 纯文本 subprocess）
        sr2 = by_stage["run_prepare"]
        self.assertEqual(len(sr2["ok"]), 3)
        self.assertTrue(all(
            (self.root / "runs" / rid / "prepare_manifest.json").is_file()
            for rid in VALID_RUN_IDS))
        # 阶段 3：缺 fill_spec → FILL_SPEC_MISSING（不是键寻址崩溃）
        codes = {f["code"] for f in report["failures"]}
        self.assertEqual(codes, {"FILL_SPEC_MISSING"})
        self.assertNotIn("WORKER_RAISED", codes)
        # 阶段边界状态推进：prepared（compile 失败前）
        status_file = json.loads(
            (self.root / "task_status.json").read_text(encoding="utf-8"))
        self.assertEqual(status_file["runs"]["r32-cooling"]["state"],
                         "prepared")


# ── issue 05: 聚合 Gate（gate_summary 生成 + 逐 run 确认展开） ─────────────

def write_timing(run_dir: Path, machine_ms: list | None = None,
                 agent_ms: list | None = None) -> None:
    """合成 run_timing.json（机器 + agent 双栏条目；duration_ms 非数值条目
    用于计时解析防御）。"""
    entries = []
    for i, ms in enumerate(machine_ms or []):
        entries.append({"kind": "machine", "phase": f"m{i}", "duration_ms": ms})
    for i, ms in enumerate(agent_ms or []):
        entries.append({"kind": "agent", "phase": f"a{i}", "duration_ms": ms})
    (run_dir / "run_timing.json").write_text(
        json.dumps(entries, ensure_ascii=False), encoding="utf-8")


def write_mod(run_dir: Path, status: str = "resolved",
              names: tuple = ("cost_reply",)) -> None:
    """合成 mod_resolution.json（MOD 裁决表面）。"""
    (run_dir / "mod_resolution.json").write_text(json.dumps({
        "status": status,
        "candidates": [{"name": n, "display_name": n,
                        "hits": ["scope::value"], "pending": [],
                        "missed": []} for n in names],
        "why": "exactly one candidate",
    }, ensure_ascii=False), encoding="utf-8")


def write_promotable_run(root: Path, rid: str, run_decl: dict,
                         spec_text: str = "fill_spec: synthetic",
                         ) -> tuple[Path, dict]:
    """完整可 promote 的合成 run（无 Office；draft 是合法 zip）。

    覆盖 promote_output 的全部既有校验前置：staged 输入 + plan 的
    input_hashes 绑定 + 合法 zip draft + 完整 receipt（source_hashes /
    template_sha256 / 哈希三元组 / readback / coverage / structural /
    validate）+ run_timing + mod_resolution。返回 (run_dir, hashes)。"""
    run_dir = Path(root) / "runs" / rid
    run_dir.mkdir(parents=True, exist_ok=True)
    spec = run_dir / "fill_spec.yaml"
    spec.write_text(spec_text + "\n", encoding="utf-8")
    staged_src = run_dir / "parameter_book.xlsx"
    staged_src.write_bytes(f"source-bytes-{rid}".encode("utf-8"))
    tpl = run_dir / "filling_template.xlsx"
    tpl.write_bytes(f"template-bytes-{rid}".encode("utf-8"))
    draft = run_dir / "validated_draft.xlsx"
    with zipfile.ZipFile(draft, "w") as z:
        z.writestr("xl/workbook.xml", "<workbook/>")  # promote 的最小 zip 检查
    input_hashes = {
        "parameter_book.xlsx": task_schema.file_sha256(staged_src),
        "filling_template.xlsx": task_schema.file_sha256(tpl),
    }
    plan = {"schema_version": "2.5",
            "fill_spec_sha256": task_schema.file_sha256(spec),
            "target": "filling_template.xlsx", "input_hashes": input_hashes}
    (run_dir / "execution_plan.json").write_text(
        json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    hashes = {
        "fill_spec_sha256": task_schema.file_sha256(spec),
        "execution_plan_sha256": task_schema.file_sha256(
            run_dir / "execution_plan.json"),
        "draft_sha256": task_schema.file_sha256(draft),
    }
    receipt = {
        "schema_version": "2.5",
        "source_hashes": {"parameter_book.xlsx":
                          input_hashes["parameter_book.xlsx"]},
        "template_sha256": input_hashes["filling_template.xlsx"],
        "input_hash_check": {"drifted": []},
        **hashes, "draft_path": str(draft),
        "operation_counts": {},
        "source_coverage": {"entries": [], "result": "pass"},
        "readback": {"total": 10, "passed": 10},
        "structural": {"pass": True, "actual_final_row_count": 24},
        "render_qa": {"status": "ok"},
        "issue_delta": {"supported": True, "new_issues": 0},
        "validate": "ok",
    }
    (run_dir / "draft_receipt.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
    write_timing(run_dir, machine_ms=[100, 200], agent_ms=[50])
    write_mod(run_dir)
    return run_dir, hashes


def add_pending(run_dir: Path, hashes: dict) -> None:
    """写入有效 pending 呈现（呈现态 = 当前产物哈希三元组）。"""
    (run_dir / ".gate3_pending").write_text(json.dumps(
        {"presented_at": "2026-08-25T00:00:00Z", "hashes": hashes},
        ensure_ascii=False, indent=2), encoding="utf-8")


def make_synthetic_task(n: int, prefix: str = "r") -> dict:
    """n-run 合成 task 定义（共享源/模板，与 fixture 同构）。"""
    runs = [{
        "id": f"{prefix}{i:02d}",
        "source": {"file": "sources/parameter_book.xlsx",
                   "sheets": ["R32参数"]},
        "target": {"template": "templates/filling_template.xlsx",
                   "sheet": "Sheet1", "output": f"out_{prefix}{i:02d}.xlsx"},
    } for i in range(1, n + 1)]
    return {"task": {"id": f"synthetic-{prefix}-{n}"}, "runs": runs}


class TestGateSummaryPure(unittest.TestCase):
    """gate_summary 生成（纯函数 seam，无 Office）：timing 双栏 / MOD /
    selected_mod / 校验结果摘要 / 聚合呈现（13 run 合成：只含 drafted/gated，
    缺口列全，终态 excluded）。"""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="task_gate_sum_"))
        (self.root / "runs").mkdir(parents=True, exist_ok=True)
        self.task, self.defect = parse_fixture("task.yaml")
        assert self.defect is None
        self.by_id = {r["id"]: r for r in self.task["runs"]}

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def status_of(self, states: dict) -> dict:
        return make_status([r["id"] for r in self.task["runs"]], states)

    def test_timing_totals_two_columns(self):
        """run_timing.json → 机器 + agent 双栏合计（缺失/非数值条目忽略）。"""
        run_dir = self.root / "runs" / "r32-cooling"
        run_dir.mkdir(parents=True, exist_ok=True)
        write_timing(run_dir, machine_ms=[100, 200, "bad"], agent_ms=[50])
        self.assertEqual(task_gate.timing_totals(run_dir),
                         {"machine_ms": 300, "agent_ms": 50})
        empty = self.root / "runs" / "r32-heating"
        empty.mkdir(parents=True, exist_ok=True)
        self.assertEqual(task_gate.timing_totals(empty),
                         {"machine_ms": 0, "agent_ms": 0})
        (empty / "run_timing.json").write_text("not-a-list", encoding="utf-8")
        self.assertEqual(task_gate.timing_totals(empty),
                         {"machine_ms": 0, "agent_ms": 0})

    def test_mod_summary_compact_and_absent(self):
        """MOD 裁决摘要（status/candidates 命中等/why）；缺失 → None。"""
        run_dir = self.root / "runs" / "r32-cooling"
        run_dir.mkdir(parents=True, exist_ok=True)
        write_mod(run_dir)
        mod = task_gate.mod_summary(run_dir)
        self.assertEqual(mod["status"], "resolved")
        self.assertEqual(mod["candidates"][0]["name"], "cost_reply")
        self.assertIn("why", mod)
        other = self.root / "runs" / "r32-heating"
        other.mkdir(parents=True, exist_ok=True)
        self.assertIsNone(task_gate.mod_summary(other))

    def test_spec_mod_reads_selected_mod(self):
        """fill_spec 的 selected_mod（关键 mapping 与业务决策）；缺失 → None。"""
        run_dir = self.root / "runs" / "r32-cooling"
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "fill_spec.yaml").write_text(
            "selected_mod: cost_reply\n", encoding="utf-8")
        self.assertEqual(task_gate.spec_mod(run_dir), "cost_reply")
        (run_dir / "fill_spec.yaml").write_text(
            "columns:\n  - source: A\n", encoding="utf-8")
        self.assertIsNone(task_gate.spec_mod(run_dir))  # 无 selected_mod

    def test_receipt_validation_extract(self):
        """关键校验结果摘要（readback/coverage/issue delta/structural/
        render_qa/validate）；非 mapping → 空。"""
        receipt = {
            "readback": {"total": 10, "passed": 10},
            "source_coverage": {"entries": [{"name": "s"}], "result": "pass"},
            "issue_delta": {"supported": True, "new_issues": 0},
            "structural": {"pass": True, "actual_final_row_count": 24},
            "render_qa": {"status": "ok"},
            "validate": "ok",
        }
        v = task_gate.receipt_validation(receipt)
        self.assertEqual(v["readback"], {"total": 10, "passed": 10})
        self.assertEqual(v["source_coverage"]["result"], "pass")
        self.assertEqual(v["issue_delta"]["new_issues"], 0)
        self.assertEqual(v["structural"]["final_row_count"], 24)
        self.assertEqual(v["render_qa"], {"status": "ok"})
        self.assertEqual(v["validate"], "ok")
        self.assertEqual(task_gate.receipt_validation(None), {})
        self.assertEqual(task_gate.receipt_validation([]), {})

    def test_collect_gate_summary_only_drafted_gated_plus_gaps(self):
        """呈现集合 = drafted/gated（产物证据）；planned 不入呈现、缺口列全。"""
        write_run_at(self.root, "r32-cooling", "drafted", spec_text="spec c")
        write_run_at(self.root, "r32-heating", "gated", spec_text="spec h")
        write_run_at(self.root, "r410a-cooling", "planned")
        summary = task_gate.collect_gate_summary(
            self.task, self.status_of({"r32-cooling": "drafted",
                                       "r32-heating": "gated",
                                       "r410a-cooling": "planned"}),
            self.root)
        self.assertEqual(set(summary["runs"]),
                         {"r32-cooling", "r32-heating"})
        self.assertEqual(len(summary["gaps"]), 1)
        self.assertEqual(summary["gaps"][0]["run"], "r410a-cooling")
        self.assertEqual(summary["gaps"][0]["state"], "planned")
        self.assertEqual(summary["excluded"], [])

    def test_summary_per_run_fields_align_gate_content(self):
        """每 run: id/输出名/行数/校验结果/哈希三元组/MOD/timing 双栏
        （呈现形态对齐 Execution Gate 内容要求）。"""
        write_run_at(self.root, "r32-cooling", "drafted", spec_text="spec c")
        run_dir = self.root / "runs" / "r32-cooling"
        receipt = json.loads(
            (run_dir / "draft_receipt.json").read_text(encoding="utf-8"))
        receipt.update({
            "readback": {"total": 10, "passed": 10},
            "source_coverage": {"entries": [], "result": "pass"},
            "issue_delta": {"supported": True, "new_issues": 0},
            "structural": {"pass": True, "actual_final_row_count": 24},
            "render_qa": {"status": "ok"},
            "validate": "ok",
        })
        (run_dir / "draft_receipt.json").write_text(
            json.dumps(receipt, ensure_ascii=False, indent=2),
            encoding="utf-8")
        write_timing(run_dir, machine_ms=[300], agent_ms=[70])
        write_mod(run_dir)
        (run_dir / "fill_spec.yaml").write_text(
            "selected_mod: cost_reply\n", encoding="utf-8")
        facts = task_resume.gather_run_facts(run_dir)
        entry = task_gate.summarize_run(
            "r32-cooling", self.by_id["r32-cooling"], run_dir, facts)
        self.assertEqual(entry["id"], "r32-cooling")
        self.assertEqual(entry["output"], "out_r32_cooling.xlsx")
        self.assertEqual(entry["rows"], 24)
        self.assertEqual(entry["hashes"], task_resume.gate_hashes(run_dir))
        self.assertEqual(entry["validation"]["readback"],
                         {"total": 10, "passed": 10})
        self.assertEqual(entry["validation"]["issue_delta"]["new_issues"], 0)
        self.assertEqual(entry["validation"]["source_coverage"]["result"],
                         "pass")
        self.assertEqual(entry["validation"]["validate"], "ok")
        self.assertEqual(entry["mod"]["status"], "resolved")
        self.assertEqual(entry["spec"]["selected_mod"], "cost_reply")
        self.assertEqual(entry["timing"], {"machine_ms": 300, "agent_ms": 70})

    def test_13_run_synthetic_only_drafted_gaps_complete(self):
        """验收：13 run 合成任务 —— gate_summary 只含 drafted run，缺口列
        完整（state + reason），每 run 哈希三元组独立。"""
        task = make_synthetic_task(13)
        ids = [r["id"] for r in task["runs"]]
        levels = {f"r{i:02d}": ("drafted" if i % 2 == 1 else "gated")
                  for i in range(1, 11)}
        levels.update({"r11": "planned", "r12": "compiled",
                       "r13": "crash_noreceipt"})
        states = {rid: ("drafted" if levels[rid] in ("drafted", "gated")
                        else "planned") for rid in ids}
        for rid, level in levels.items():
            write_run_at(self.root, rid, level, spec_text=f"spec {rid}")
        summary = task_gate.collect_gate_summary(
            task, make_status(ids, states), self.root)
        self.assertEqual(len(summary["runs"]), 10)  # 只含 drafted/gated
        self.assertEqual(set(summary["runs"]),
                         {f"r{i:02d}" for i in range(1, 11)})
        gaps = {g["run"]: g for g in summary["gaps"]}
        self.assertEqual(set(gaps), {"r11", "r12", "r13"})
        self.assertEqual(gaps["r11"]["state"], "planned")
        self.assertEqual(gaps["r12"]["state"], "compiled")
        self.assertEqual(gaps["r13"]["state"], "execute_retry")
        self.assertTrue(all(g["reason"] for g in summary["gaps"]))
        # 每 run 的哈希三元组绑定自己的呈现内容（spec 各异 → 三元组互异）
        trios = {rid: tuple(sorted(summary["runs"][rid]["hashes"].items()))
                 for rid in summary["runs"]}
        self.assertEqual(len(set(trios.values())), 10)

    def test_terminal_runs_excluded_not_gaps(self):
        """promoted/superseded 不入呈现、不入缺口（excluded 记录）；已确认
        未交付（confirmed）作为未决项在 summary["confirmed"] 呈现。"""
        write_run_at(self.root, "r32-cooling", "promoted")
        write_run_at(self.root, "r32-heating", "confirmed")
        write_run_at(self.root, "r410a-cooling", "planned")
        summary = task_gate.collect_gate_summary(
            self.task, self.status_of({"r32-cooling": "promoted",
                                       "r32-heating": "drafted",
                                       "r410a-cooling": "planned"}),
            self.root)
        self.assertEqual(summary["runs"], {})
        ex = {e["run"]: e for e in summary["excluded"]}
        self.assertEqual(set(ex), {"r32-cooling"})
        self.assertEqual(ex["r32-cooling"]["state"], "promoted")
        self.assertEqual([c["run"] for c in summary["confirmed"]],
                         ["r32-heating"])
        self.assertEqual(summary["confirmed"][0]["state"], "confirmed")
        self.assertEqual([g["run"] for g in summary["gaps"]],
                         ["r410a-cooling"])


class TestConfirmPlanPure(unittest.TestCase):
    """confirm_plan：呈现集合 vs 当前证据判定（stale / not_presented /
    skipped_terminal 守卫 + task.yaml 声明顺序的授权顺序）。"""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="task_gate_plan_"))
        (self.root / "runs").mkdir(parents=True, exist_ok=True)
        self.task, self.defect = parse_fixture("task.yaml")
        assert self.defect is None
        self.ids = [r["id"] for r in self.task["runs"]]

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def decisions_for(self, levels: dict, states: dict) -> dict:
        out = {}
        for rid, level in levels.items():
            write_run_at(self.root, rid, level, spec_text=f"spec {rid}")
            facts = task_resume.gather_run_facts(self.root / "runs" / rid)
            out[rid] = task_resume.classify_run_facts(
                facts, status_state=states.get(rid))
        return out

    def test_matching_partition_and_task_order(self):
        """全匹配：confirm/already_confirmed/promote 按 task.yaml 声明顺序；
        promoted 跳过且不阻塞。"""
        decisions = self.decisions_for(
            {"r32-cooling": "gated", "r32-heating": "gated",
             "r410a-cooling": "confirmed"},
            {"r32-cooling": "gated", "r32-heating": "gated",
             "r410a-cooling": "drafted"})
        summary = {"runs": {"r32-cooling": {}, "r32-heating": {},
                            "r410a-cooling": {}}}
        plan = task_gate.confirm_plan(summary, decisions)
        self.assertEqual(plan["confirm"], ["r32-cooling", "r32-heating"])
        self.assertEqual(plan["already_confirmed"], ["r410a-cooling"])
        self.assertEqual(plan["promote"],
                         ["r32-cooling", "r32-heating", "r410a-cooling"])
        self.assertEqual(plan["stale"], [])
        self.assertEqual(plan["not_presented"], [])

    def test_stale_presented_but_no_longer_confirmable(self):
        """呈现过的 run 已不可确认（产物回退）→ stale 阻塞；promoted 终态
        跳过不阻塞。"""
        decisions = self.decisions_for(
            {"r32-cooling": "planned", "r32-heating": "gated",
             "r410a-cooling": "promoted"},
            {"r32-cooling": "drafted", "r32-heating": "gated",
             "r410a-cooling": "promoted"})
        summary = {"runs": {"r32-cooling": {}, "r32-heating": {},
                            "r410a-cooling": {}}}
        plan = task_gate.confirm_plan(summary, decisions)
        self.assertEqual([e["run"] for e in plan["stale"]],
                         ["r32-cooling"])
        self.assertEqual(plan["confirm"], ["r32-heating"])
        self.assertEqual([e["run"] for e in plan["skipped_terminal"]],
                         ["r410a-cooling"])

    def test_not_presented_blocks_unseen_content(self):
        """确认时可确认但不在呈现集合（新出现）→ 未呈现的内容不能被授权。"""
        decisions = self.decisions_for(
            {"r32-cooling": "gated", "r32-heating": "gated",
             "r410a-cooling": "planned"},
            {"r32-cooling": "gated", "r32-heating": "gated",
             "r410a-cooling": "planned"})
        summary = {"runs": {"r32-cooling": {}}}  # r32-heating 未被呈现
        plan = task_gate.confirm_plan(summary, decisions)
        self.assertEqual([e["run"] for e in plan["not_presented"]],
                         ["r32-heating"])
        self.assertEqual(plan["confirm"], ["r32-cooling"])

    def test_confirmed_not_presented_still_promotable(self):
        """已 confirmed 但不在呈现集合 → 不阻塞：授权已落账（.gate3_confirmed
        绑定呈现三元组），重试幂等跳过确认仍进 promote（呈现守卫只约束待
        确认的 gated run —— 未呈现的新授权不被放行）。"""
        decisions = self.decisions_for(
            {"r32-cooling": "confirmed"}, {"r32-cooling": "drafted"})
        plan = task_gate.confirm_plan({"runs": {}}, decisions)
        self.assertEqual(plan["already_confirmed"], ["r32-cooling"])
        self.assertEqual(plan["promote"], ["r32-cooling"])
        self.assertEqual(plan["not_presented"], [])

    def test_terminal_skipped_terminal_named(self):
        """superseded 标记 → skipped_terminal（跳过且不阻塞）。"""
        decisions = self.decisions_for(
            {"r32-cooling": "drafted"}, {"r32-cooling": "superseded"})
        plan = task_gate.confirm_plan({"runs": {"r32-cooling": {}}},
                                      decisions)
        self.assertEqual([e["run"] for e in plan["skipped_terminal"]],
                         ["r32-cooling"])
        self.assertEqual(plan["confirm"], [])
        self.assertEqual(plan["stale"], [])

    def test_refresh_gate_summary_state_evolution(self):
        """refresh_gate_summary：gate.state 演进（promoted / confirm_failed /
        promote_failed / noop），per-run 呈现快照不动（被呈现内容的封存）。
        blocked 不落账：调用方在 refresh 前 fail（GATE_PRESENTATION_
        MISMATCH）。"""
        summary = {"schema_version": 1, "runs": {"r1": {"id": "r1"}},
                   "confirmed": [], "gaps": [], "excluded": []}
        base = {"plan": {"confirm": [], "already_confirmed": []},
                "blocked": [], "confirm_failures": [],
                "promote_failures": [], "confirmed": [],
                "already_confirmed": [], "promoted": []}

        r = task_gate.refresh_gate_summary(
            summary, dict(base, confirmed=["r1"], promoted=["r1"]))
        self.assertEqual(r["gate"]["state"], "promoted")
        self.assertEqual(r["gate"]["confirmed"], ["r1"])
        self.assertEqual(r["gate"]["promoted"], ["r1"])

        r = task_gate.refresh_gate_summary(
            summary, dict(base, confirm_failures=[{"run": "r1"}]))
        self.assertEqual(r["gate"]["state"], "confirm_failed")

        r = task_gate.refresh_gate_summary(
            summary, dict(base, promote_failures=[{"run": "r1"}]))
        self.assertEqual(r["gate"]["state"], "promote_failed")

        r = task_gate.refresh_gate_summary(summary, dict(base))
        self.assertEqual(r["gate"]["state"], "noop")
        # per-run 呈现快照不被演进触碰
        self.assertEqual(r["runs"]["r1"], {"id": "r1"})


class TestGateExpand(unittest.TestCase):
    """run_confirm_expansion（worker 可注入，无 Office）：逐 run 确认独立
    绑定自己的哈希三元组（真实 execution_gate --confirm subprocess）+
    promote 阶段（并发契约经 task_scheduler）+ 确认失败整体停止（不
    promote）+ promote 失败隔离与部分状态推进。"""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="task_gate_exp_"))
        self.task, self.defect = parse_fixture("task.yaml")
        assert self.defect is None
        self.ids = [r["id"] for r in self.task["runs"]]
        (self.root / "runs").mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def gated_ctx(self):
        """3 run 全部 gated 证据（有效 pending 三元组，spec 各异）+
        索引 gated + 展开 ctx。"""
        for run in self.task["runs"]:
            write_run_at(self.root, run["id"], "gated",
                         spec_text=f"spec {run['id']}")
        status = make_status(self.ids, {rid: "gated" for rid in self.ids})
        ctx = {"root": self.root, "runs_dir": self.root / "runs",
               "final_paths": {run["id"]: task_gate.final_output_path(
                   self.root, run) for run in self.task["runs"]}}
        return ctx, status

    def summary_and_plan(self, status) -> tuple[dict, dict]:
        decisions = {rid: task_resume.classify_run_facts(
            task_resume.gather_run_facts(self.root / "runs" / rid),
            status_state=status["runs"][rid]["state"]) for rid in self.ids}
        summary = task_gate.collect_gate_summary(self.task, status, self.root)
        return summary, task_gate.confirm_plan(summary, decisions)

    def spy_promote(self):
        calls: list = []

        def worker_of(_ctx):
            def worker(rid):
                calls.append(rid)
                return {"run": rid, "status": "ok", "artifacts": {}}
            return worker
        return calls, worker_of

    def test_success_each_confirmed_trio_bound_independently(self):
        """逐 run .gate3_confirmed 绑定自己的哈希三元组（3 run 三元组互不
        相同 —— 聚合不削弱逐 run 授权粒度）；promote 全部执行、状态推进到
        promoted（单一写者：阶段边界恰写一次）。"""
        ctx, status = self.gated_ctx()
        summary, plan = self.summary_and_plan(status)
        calls, worker_of = self.spy_promote()
        report = task_gate.run_confirm_expansion(
            self.root, self.task, status, plan,
            confirmer=lambda rid: task_gate.default_confirm_worker(ctx, rid),
            promote_worker_of=worker_of, progress=lambda _: None)
        self.assertEqual(report["confirm_failures"], [])
        self.assertEqual(report["promote_failures"], [])
        self.assertEqual(report["confirmed"], self.ids)
        self.assertEqual(sorted(calls), sorted(self.ids))  # promote 全部
        for rid in self.ids:
            run_dir = self.root / "runs" / rid
            confirmed = json.loads(
                (run_dir / ".gate3_confirmed").read_text(encoding="utf-8"))
            self.assertEqual(confirmed["hashes"],
                             task_resume.gate_hashes(run_dir))
            self.assertFalse((run_dir / ".gate3_pending").exists())
        trios = {rid: tuple(sorted(task_resume.gate_hashes(
            self.root / "runs" / rid).items())) for rid in self.ids}
        self.assertEqual(len(set(trios.values())), 3)  # 独立绑定
        status_file = json.loads(
            (self.root / "task_status.json").read_text(encoding="utf-8"))
        for rid in self.ids:
            self.assertEqual(status_file["runs"][rid]["state"], "promoted")

    def test_confirm_failure_stops_and_reports_no_promote(self):
        """模拟某 run 确认失败：整体停止并报告该 run，不继续确认、不进入
        promote（验收 #3：任一 run 确认失败即停止并报告，不静默跳过）。"""
        ctx, status = self.gated_ctx()
        summary, plan = self.summary_and_plan(status)
        calls, worker_of = self.spy_promote()

        def flaky_confirmer(rid):
            if rid == "r32-heating":
                raise task_scheduler.StageError(
                    "GATE_CONFIRM_FAILED", "模拟确认失败（哈希漂移）",
                    "重新 --set 呈现后确认")
            return task_gate.default_confirm_worker(ctx, rid)

        report = task_gate.run_confirm_expansion(
            self.root, self.task, status, plan,
            confirmer=flaky_confirmer, promote_worker_of=worker_of,
            progress=lambda _: None)
        self.assertEqual([f["run"] for f in report["confirm_failures"]],
                         ["r32-heating"])
        self.assertEqual(report["confirmed"], ["r32-cooling"])
        self.assertEqual(calls, [])  # 不进入 promote
        self.assertEqual(report["promoted"], [])
        self.assertFalse(any(
            (self.root / "runs" / rid / "final_receipt.json").exists()
            for rid in self.ids))
        # r32-cooling 已确认（授权落账），r32-heating 未被触碰
        self.assertTrue(
            (self.root / "runs" / "r32-cooling" / ".gate3_confirmed").is_file())
        self.assertTrue(
            (self.root / "runs" / "r32-heating" / ".gate3_pending").is_file())

    def test_promote_failure_isolated_partial_status(self):
        """promote 失败 run 不推进状态，同阶段其他 run 不受影响（失败清单
        汇总；重试幂等 —— 已确认 run 跳过确认仍可重跑 promote）。"""
        ctx, status = self.gated_ctx()
        summary, plan = self.summary_and_plan(status)
        calls: list = []

        def worker_of(_ctx):
            def worker(rid):
                calls.append(rid)
                if rid == "r32-heating":
                    raise task_scheduler.StageError(
                        "PROMOTE_FAILED", "模拟 promote 失败",
                        "修复后重试该 run 的确认展开")
                return {"run": rid, "status": "ok", "artifacts": {}}
            return worker

        report = task_gate.run_confirm_expansion(
            self.root, self.task, status, plan,
            confirmer=lambda rid: task_gate.default_confirm_worker(ctx, rid),
            promote_worker_of=worker_of, progress=lambda _: None)
        self.assertEqual(report["promote_failures"][0]["run"],
                         "r32-heating")
        self.assertEqual(sorted(report["promoted"]),
                         ["r32-cooling", "r410a-cooling"])
        status_file = json.loads(
            (self.root / "task_status.json").read_text(encoding="utf-8"))
        self.assertEqual(status_file["runs"]["r32-heating"]["state"],
                         "gated")
        self.assertEqual(status_file["runs"]["r32-cooling"]["state"],
                         "promoted")

    def test_blocked_no_expansion(self):
        """stale/not_presented → blocked，不展开（confirmer/promote 零调用）。"""
        ctx, status = self.gated_ctx()
        decision = task_resume.classify_run_facts(
            task_resume.gather_run_facts(self.root / "runs" / "r32-cooling"),
            status_state="gated")
        plan = task_gate.confirm_plan({"runs": {}},
                                      {"r32-cooling": decision})  # 未呈现
        called: list = []
        promote_called: list = []

        def confirmer(rid):
            called.append(rid)
            return {"run": rid, "status": "ok", "artifacts": {}}

        def worker_of(_ctx):
            def worker(rid):
                promote_called.append(rid)
                return {"run": rid, "status": "ok", "artifacts": {}}
            return worker

        report = task_gate.run_confirm_expansion(
            self.root, self.task, status, plan, confirmer=confirmer,
            promote_worker_of=worker_of, progress=lambda _: None)
        self.assertEqual(len(report["blocked"]), 1)
        self.assertEqual(report["blocked"][0]["code"], "GATE_NOT_PRESENTED")
        self.assertEqual(called, [])
        self.assertEqual(promote_called, [])


class TestGateTaskCLI(unittest.TestCase):
    """gate_task.py 公共 CLI seam（无 Office）：聚合呈现 + 确认展开全链路
    （真实 execution_gate / promote_output subprocess —— 纯 Python +
    zipfile）。"""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="task_gate_cli_"))
        shutil.copytree(FIX, self.root, dirs_exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def fixture_runs(self) -> dict:
        task, _ = parse_fixture("task.yaml")
        return {r["id"]: r for r in task["runs"]}

    def init_and_stage_gated(self) -> dict:
        """--init + 构造 3 个 gated evidence 的完整可 promote run（pending
        绑定各自三元组）+ 索引推进 gated（模拟 pipeline 阶段边界推进）。"""
        proc = run_cli(self.root, "--init")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        by_id = self.fixture_runs()
        for rid, decl in by_id.items():
            _, hashes = write_promotable_run(self.root, rid, decl,
                                             spec_text=f"spec {rid}")
            add_pending(self.root / "runs" / rid, hashes)
        status_path = self.root / "task_status.json"
        status = json.loads(status_path.read_text(encoding="utf-8"))
        for rid in by_id:
            status["runs"][rid]["state"] = "gated"
        status_path.write_text(json.dumps(status, ensure_ascii=False,
                                          indent=2), encoding="utf-8")
        return by_id

    def test_set_without_init_exit3(self):
        proc = run_gate_cli(self.root, "--set")
        self.assertEqual(proc.returncode, 3, proc.stderr)
        self.assertIn("TASK_MANIFEST_MISSING", proc.stderr)

    def test_set_aggregates_drafted_only_and_presents(self):
        """--set：只聚合 Draft 就绪 run；evidence-drafted run 先 --set 呈现
        （pending 落账 + 索引推进 gated）；planned 入缺口。"""
        proc = run_cli(self.root, "--init")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        write_run_at(self.root, "r32-cooling", "drafted", spec_text="spec c")
        write_run_at(self.root, "r32-heating", "gated", spec_text="spec h")
        write_run_at(self.root, "r410a-cooling", "planned")
        proc = run_gate_cli(self.root, "--set")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        out = json.loads(proc.stdout)
        self.assertEqual(out["code"], "GATE_SUMMARY_WRITTEN")
        summary = json.loads((self.root / "gate_summary.json")
                             .read_text(encoding="utf-8"))
        self.assertEqual(set(summary["runs"]),
                         {"r32-cooling", "r32-heating"})
        self.assertEqual([g["run"] for g in summary["gaps"]],
                         ["r410a-cooling"])
        # evidence-drafted run 已被呈现（pending 落账 + 索引推进 gated）
        self.assertTrue((self.root / "runs" / "r32-cooling"
                         / ".gate3_pending").is_file())
        status = json.loads((self.root / "task_status.json")
                            .read_text(encoding="utf-8"))
        self.assertEqual(status["runs"]["r32-cooling"]["state"], "gated")

    def test_set_nothing_to_present_exit3(self):
        proc = run_cli(self.root, "--init")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        proc = run_gate_cli(self.root, "--set")
        self.assertEqual(proc.returncode, 3, proc.stdout + proc.stderr)
        err = json.loads(proc.stdout)  # _fail_json 结构化 JSON 走 stdout
        self.assertEqual(err["code"], "GATE_NOTHING_TO_PRESENT")
        self.assertTrue((self.root / "gate_summary.json").is_file())

    def test_confirm_without_set_exit3(self):
        proc = run_cli(self.root, "--init")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        proc = run_gate_cli(self.root, "--confirm")
        self.assertEqual(proc.returncode, 3, proc.stdout + proc.stderr)
        self.assertIn("GATE_SUMMARY_MISSING", proc.stdout)

    def test_full_flow_set_confirm_promote(self):
        """全链路：--set 呈现（只有 drafted/gated）→ --confirm 逐 run 确认
        （各自三元组独立绑定）+ promote（outputs/ 落盘，final hash == 已确认
        draft hash）→ 状态全 promoted；重复 --confirm 是 noop（幂等）。"""
        by_id = self.init_and_stage_gated()
        proc = run_gate_cli(self.root, "--set")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        summary = json.loads((self.root / "gate_summary.json")
                             .read_text(encoding="utf-8"))
        self.assertEqual(set(summary["runs"]), set(by_id))
        self.assertEqual(summary["gaps"], [])
        self.assertEqual(summary["gate"]["state"], "presented")

        proc = run_gate_cli(self.root, "--confirm")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        out = json.loads(proc.stdout)
        self.assertEqual(out["code"], "GATE_CONFIRMED_AND_PROMOTED")
        self.assertEqual(out["confirmed"], list(by_id))
        self.assertEqual(out["promoted"], list(by_id))
        self.assertEqual(out["gate"]["state"], "promoted")
        for rid, decl in by_id.items():
            run_dir = self.root / "runs" / rid
            confirmed = json.loads(
                (run_dir / ".gate3_confirmed").read_text(encoding="utf-8"))
            self.assertEqual(confirmed["hashes"],
                             task_resume.gate_hashes(run_dir))
            final = self.root / "outputs" / decl["target"]["output"]
            self.assertTrue(final.is_file())
            final_receipt = json.loads(
                (run_dir / "final_receipt.json").read_text(encoding="utf-8"))
            self.assertEqual(final_receipt["final_sha256"],
                             task_schema.file_sha256(final))
            self.assertEqual(
                final_receipt["draft_sha256"],
                task_schema.file_sha256(run_dir / "validated_draft.xlsx"))
        status = json.loads((self.root / "task_status.json")
                            .read_text(encoding="utf-8"))
        for rid in by_id:
            self.assertEqual(status["runs"][rid]["state"], "promoted")

        # 幂等重跑：全部终态 → GATE_NOOP（不重复确认/交付）
        proc = run_gate_cli(self.root, "--confirm")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(json.loads(proc.stdout)["code"], "GATE_NOOP")

    def test_stale_presentation_blocks_without_promote(self):
        """呈现后 draft 被改动 → --confirm 阻塞（GATE_PRESENTATION_MISMATCH），
        不做任何 promote（fail-closed：改动即重新授权）。"""
        by_id = self.init_and_stage_gated()
        proc = run_gate_cli(self.root, "--set")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        draft = (self.root / "runs" / "r32-heating" / "validated_draft.xlsx")
        draft.write_bytes(b"tampered-after-presentation")
        proc = run_gate_cli(self.root, "--confirm")
        self.assertEqual(proc.returncode, 3, proc.stdout + proc.stderr)
        err = json.loads(proc.stdout)
        self.assertEqual(err["code"], "GATE_PRESENTATION_MISMATCH")
        self.assertEqual([d["run"] for d in err["defects"]],
                         ["r32-heating"])
        self.assertFalse((self.root / "outputs").exists())  # 未做任何 promote
        self.assertTrue((self.root / "runs" / "r32-cooling"
                         / ".gate3_pending").is_file())  # 未被确认

    def test_inflight_confirm_failure_stops_and_reports(self):
        """模拟确认展开中某 run 失败（in-process patch confirmer）：整体
        停止并报告该 run，不继续确认、不做 promote（验收 #3）。"""
        self.init_and_stage_gated()
        proc = run_gate_cli(self.root, "--set")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

        real_confirm = task_gate.default_confirm_worker

        def flaky(ctx, rid):
            if rid == "r32-heating":
                raise task_scheduler.StageError(
                    "GATE_CONFIRM_FAILED", "模拟确认失败",
                    "重新 --set 呈现后确认")
            return real_confirm(ctx, rid)

        out = io.StringIO()
        with mock.patch.object(task_gate, "default_confirm_worker",
                               side_effect=flaky), redirect_stdout(out):
            with self.assertRaises(SystemExit) as cm:
                gate_task.run_gate_confirm(self.root)
        self.assertEqual(cm.exception.code, 3)
        err = json.loads(out.getvalue())
        self.assertEqual(err["code"], "GATE_CONFIRM_FAILED")
        self.assertEqual([d["run"] for d in err["defects"]],
                         ["r32-heating"])
        self.assertFalse((self.root / "outputs").exists())  # 不继续 promote
        self.assertTrue((self.root / "runs" / "r32-cooling"
                         / ".gate3_confirmed").is_file())
        self.assertTrue((self.root / "runs" / "r32-heating"
                         / ".gate3_pending").is_file())

    def test_promote_before_confirm_rejected_fail_closed(self):
        """确认前 promote 被拒（fail-closed 保持）：promote_output.py 零
        改动 —— 缺 .gate3_confirmed → GATE_NOT_CONFIRMED（exit 3），
        no final_receipt / no final output。"""
        by_id = self.fixture_runs()
        rid, decl = next(iter(by_id.items()))
        run_dir, _hashes = write_promotable_run(self.root, rid, decl)
        self.assertFalse((run_dir / ".gate3_confirmed").exists())
        proc = subprocess.run(
            [sys.executable, str(_SCRIPTS_DIR / "promote_output.py"),
             "--workdir", str(run_dir),
             "--final", str(task_gate.final_output_path(self.root, decl))],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=60)
        self.assertEqual(proc.returncode, 3, proc.stdout)
        self.assertIn("GATE_NOT_CONFIRMED", proc.stderr)
        self.assertFalse((run_dir / "final_receipt.json").exists())
        self.assertFalse(
            (self.root / "outputs" / decl["target"]["output"]).exists())


if __name__ == "__main__":
    unittest.main()
