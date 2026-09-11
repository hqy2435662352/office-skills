"""Ticket 01 (spec T04) — Manifest Input Fact Boundary.

T04 把 task manifest 的冻结维度从「task.yaml 全文件 yaml_sha256」收敛为
「输入事实状态」（source file / template input / sheet 引用），使非输入修改
（输出命名、notes、package metadata）不再触发 MANIFEST_STALE，同时
source/template/sheet 引用变化仍 fail-closed 阻塞（supersede / 显式 re-init
提示不变）。

测试层（与 spec Testing Decisions「只测外显行为」+ test_task_orchestration.py
风格一致）：
  1. task_schema.check_frozen / check_status 纯函数 seam —— 冻结边界逐字段
     断言（输入事实维度全部仍 MANIFEST_STALE；非输入维度全部放行）；
  2. prepare_task.py --init CLI 外显 —— 验收 4（改输出命名 → 不 stale、
     manifest 继续有效）；验收 3 的声明维度（改 sheet 引用 → MANIFEST_STALE）。

验收 3 的内容维度（修改 source.xlsx 内容 → SOURCE_HASH_DRIFT 阻塞 + supersede
建议）由既有 test_task_orchestration.py::TestResumeDriftBlocked
::test_source_hash_drift_blocks_and_suggests_supersede 覆盖 —— prepare
prelude 的 staged 文件 SHA-256 比对，本票不重复该 officecli 打桩机制。

Run with:
  python -m pytest table-fill/tests/test_manifest_input_fact_boundary.py -q
"""

from __future__ import annotations

import copy
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
_SHA = "ab" * 32


def _fixture_task() -> dict:
    """解析合法 fixture task.yaml（3 run 共享源/模板）。"""
    data, defect = task_schema.parse_task_yaml(
        (FIX / "task.yaml").read_text(encoding="utf-8"))
    assert defect is None
    assert task_schema.validate_task_yaml(data, FIX) == []
    return data


class TestCheckFrozenInputFactBoundary(unittest.TestCase):
    """check_frozen 纯函数 seam：冻结维度 = 输入事实状态（T04）。

    输入事实（source.file / source.sheets / target.template / target.sheet）
    变化 → MANIFEST_STALE；非输入（target.output / notes / customer /
    template_family / yaml_sha256 指纹漂移）变化 → 不 stale。"""

    @classmethod
    def setUpClass(cls):
        cls.task = _fixture_task()
        cls.manifest = task_schema.derive_task_manifest(
            cls.task, _SHA, frozen_at="2026-08-22T00:00:00")

    def _task_with(self, mutate) -> dict:
        t = copy.deepcopy(self.task)
        mutate(t)
        return t

    def _codes(self, task) -> list[str]:
        return [d["code"] for d in task_schema.check_frozen(task, self.manifest)]

    # ── 一致基线 ──────────────────────────────────────────────────────

    def test_identical_inputs_no_defects(self):
        self.assertEqual(task_schema.check_frozen(self.task, self.manifest), [])

    # ── 不冻结：非输入字段 ─────────────────────────────────────────────

    def test_output_rename_not_stale(self):
        """验收 4：改 target.output（输出命名）→ 不再 MANIFEST_STALE。"""
        changed = self._task_with(
            lambda t: t["runs"][0]["target"].__setitem__(
                "output", "out_r32_cooling_v2.xlsx"))
        self.assertEqual(self._codes(changed), [])

    def test_notes_and_customer_not_stale(self):
        """notes / customer（元数据）修改 → 不 stale。"""
        changed = self._task_with(lambda t: t["task"].update(
            {"notes": "改说明（非输入）", "customer": "另一客户名"}))
        self.assertEqual(self._codes(changed), [])

    def test_template_family_record_not_stale(self):
        """template_family（仅记录，D6 不实现）修改 → 不 stale。"""
        changed = self._task_with(
            lambda t: t["runs"][2].__setitem__("template_family", "改了记录"))
        self.assertEqual(self._codes(changed), [])

    def test_yaml_fingerprint_drift_not_stale(self):
        """manifest 记录的 yaml_sha256 不再是冻结判据：仅该指纹漂移（如
        非输入修改后的 task.yaml 全文件 hash 变化）→ 不 MANIFEST_STALE。"""
        m = copy.deepcopy(self.manifest)
        m["task"]["yaml_sha256"] = "ff" * 32
        self.assertEqual(task_schema.check_frozen(self.task, m), [])

    # ── 冻结：输入事实维度全部仍阻塞 ──────────────────────────────────

    def test_source_file_change_stale(self):
        changed = self._task_with(
            lambda t: t["runs"][0]["source"].__setitem__(
                "file", "sources/other_book.xlsx"))
        cases = task_schema.check_frozen(changed, self.manifest)
        self.assertIn("MANIFEST_STALE", [d["code"] for d in cases])
        self.assertIn("r32-cooling", cases[0]["message"])

    def test_source_sheets_change_stale(self):
        """验收 3 的声明维度：sheet 引用变化 → 仍然 MANIFEST_STALE
        （输入事实边界绝不因 T04 放宽）。"""
        changed = self._task_with(
            lambda t: t["runs"][0]["source"].__setitem__("sheets", ["R32参数"]))
        codes = self._codes(changed)
        self.assertIn("MANIFEST_STALE", codes)

    def test_target_template_change_stale(self):
        changed = self._task_with(
            lambda t: t["runs"][1]["target"].__setitem__(
                "template", "templates/other_template.xlsx"))
        self.assertIn("MANIFEST_STALE", self._codes(changed))

    def test_target_sheet_change_stale(self):
        changed = self._task_with(
            lambda t: t["runs"][1]["target"].__setitem__("sheet", "Sheet2"))
        self.assertIn("MANIFEST_STALE", self._codes(changed))

    # ── run 清单（输入事实集合）与 task 身份 ───────────────────────────

    def test_run_removed_stale(self):
        m = copy.deepcopy(self.manifest)
        del m["runs"]["r32-heating"]
        codes = [d["code"] for d in task_schema.check_frozen(
            self.task, m)]
        self.assertIn("RUN_ID_MISMATCH", codes)

    def test_run_added_stale(self):
        m = copy.deepcopy(self.manifest)
        m["runs"]["ghost-run"] = {
            "source": {"file": "x.xlsx", "sheets": []},
            "target": {"template": "t.xlsx", "sheet": "S",
                       "output": "o.xlsx"}}
        codes = [d["code"] for d in task_schema.check_frozen(self.task, m)]
        self.assertIn("RUN_ID_MISMATCH", codes)

    def test_task_id_mismatch_stale(self):
        """manifest 绑定另一个 task id → MANIFEST_STALE（快照属于别的任务）。"""
        m = copy.deepcopy(self.manifest)
        m["task"]["id"] = "other-task"
        codes = [d["code"] for d in task_schema.check_frozen(self.task, m)]
        self.assertIn("MANIFEST_STALE", codes)

    def test_stale_defect_carries_reinit_corrective(self):
        changed = self._task_with(
            lambda t: t["runs"][0]["source"].__setitem__("sheets", ["R32参数"]))
        d = task_schema.check_frozen(changed, self.manifest)[0]
        self.assertEqual(d["code"], "MANIFEST_STALE")
        self.assertIn("重新 --init", d["corrective_action"])


class TestCheckStatusSnapshotBinding(unittest.TestCase):
    """check_status 纯函数 seam：T04 后 status 绑定冻结快照（manifest），
    不再与 task.yaml 全文件指纹直接比对。"""

    @classmethod
    def setUpClass(cls):
        cls.task = _fixture_task()
        cls.manifest = task_schema.derive_task_manifest(
            cls.task, _SHA, frozen_at="2026-08-22T00:00:00")
        cls.status = task_schema.derive_task_status(
            cls.task, _SHA, updated_at="2026-08-22T00:00:00")

    def _codes(self, status, manifest=None) -> list[str]:
        return [d["code"] for d in task_schema.check_status(
            self.task, status, manifest)]

    def test_binding_ok(self):
        self.assertEqual(
            task_schema.check_status(self.task, self.status, self.manifest), [])

    def test_metadata_yaml_change_no_status_stale(self):
        """非输入 task.yaml 修改后（输出改名），manifest/status 原样 →
        check_status 通过 —— 不按旧语义拦截（避免半截状态）。"""
        t = copy.deepcopy(self.task)
        t["runs"][0]["target"]["output"] = "out_r32_cooling_v2.xlsx"
        self.assertEqual(
            task_schema.check_status(t, self.status, self.manifest), [])

    def test_tampered_yaml_sha256_rejected(self):
        """status 的 task 绑定指纹被手改（与冻结快照不同代）→ STATUS_STALE。"""
        s = copy.deepcopy(self.status)
        s["task"]["yaml_sha256"] = "0" * 64
        self.assertIn("STATUS_STALE", self._codes(s, self.manifest))

    def test_foreign_task_id_rejected(self):
        """status 绑定另一个 task → STATUS_STALE。"""
        s = copy.deepcopy(self.status)
        s["task"]["id"] = "other-task"
        self.assertIn("STATUS_STALE", self._codes(s, self.manifest))

    def test_no_manifest_skips_snapshot_binding(self):
        """manifest 缺失（未来 --init 首跑路径）→ 只做 run 清单/状态校验。"""
        s = copy.deepcopy(self.status)
        s["task"]["yaml_sha256"] = "0" * 64
        self.assertEqual(self._codes(s, None), [])

    def test_ghost_run_rejected(self):
        s = copy.deepcopy(self.status)
        s["runs"]["ghost-run"] = {"state": "planned", "superseded_by": None}
        self.assertIn("RUN_ID_MISMATCH", self._codes(s, self.manifest))

    def test_bogus_state_rejected(self):
        s = copy.deepcopy(self.status)
        s["runs"]["r32-cooling"] = {"state": "bogus", "superseded_by": None}
        self.assertIn("STATUS_INVALID_STATE", self._codes(s, self.manifest))


class TestManifestBoundaryCLI(unittest.TestCase):
    """task.yaml 输入事实边界（ADR 0018/0020 视角）：
    materialize_run 调用时 task.yaml 输入事实由 workspace_manifest 校验；
    非输入变化（输出命名/notes）不触发任何重新初始化。CLI 生命周期
    （task_manifest 冻结快照 / yaml_sha256）已随 Task runtime 退役。"""

    SHEETS = "parameter_book.xlsx:R32参数,R410A参数;filling_template.xlsx:Sheet1"

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="manifest_fact_boundary_"))
        shutil.copytree(FIX, self.root, dirs_exist_ok=True)
        # 合成 role-neutral workspace（materialize 消费面；无 Office）。
        # Trust boundary (verify_workspace_facts): staged 文件必须在场且
        # inputs[].sha256 为真实哈希, 否则 materialize 在消费前拒绝。
        import hashlib
        (self.root / "parameter_book.xlsx").write_bytes(b"staged-db")
        (self.root / "filling_template.xlsx").write_bytes(b"staged-tb")
        self._inputs = [
            {"staged": "parameter_book.xlsx",
             "source": str(FIX / "sources" / "parameter_book.xlsx"),
             "sha256": hashlib.sha256(b"staged-db").hexdigest()},
            {"staged": "filling_template.xlsx",
             "source": str(FIX / "templates" / "filling_template.xlsx"),
             "sha256": hashlib.sha256(b"staged-tb").hexdigest()},
        ]
        (self.root / "workspace_manifest.json").write_text(json.dumps({
            "schema_version": 4, "kind": "workspace_init",
            "workdir": str(self.root), "task": "manifest boundary",
            "inputs": self._inputs,
            "outlines": {}, "flattened": [], "derived": [],
            "inherited_from": None,
        }, ensure_ascii=False), encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _edit_yaml(self, old: str, new: str) -> None:
        p = self.root / "task.yaml"
        text = p.read_text(encoding="utf-8")
        assert old in text, f"替换目标缺失: {old!r}"
        p.write_text(text.replace(old, new), encoding="utf-8")

    def _run_materialize(self) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-X", "utf8",
             str(_SCRIPTS_DIR / "materialize_run.py"),
             "--workdir", str(self.root), "--task", "task.yaml"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=120)

    def _seed_flatten(self) -> None:
        """合成 flatten 条目使 materialize 可消费（校验工作区 entry 引用）。"""
        wm = json.loads((self.root / "workspace_manifest.json").read_text(
            encoding="utf-8"))
        wm["flattened"] = [
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
             "structure_sha256": "c1" * 32},
            {"file": "filling_template.xlsx", "sheet": "Sheet1",
             "name": "filling_template_Sheet1",
             "csv": "filling_template_Sheet1_flat.csv",
             "meta": "filling_template_Sheet1_meta.json",
             "evidence": "filling_template_Sheet1_premod_evidence.md",
             "digest": "deferred",
             "candidates": "filling_template_Sheet1_candidates.yaml",
             "structure_sha256": "dd" * 32},
        ]
        (self.root / "workspace_manifest.json").write_text(
            json.dumps(wm, ensure_ascii=False, indent=2), encoding="utf-8")
        for n, sheet in (("parameter_book_R32", "R32参数"),
                         ("parameter_book_R410A", "R410A参数"),
                         ("filling_template_Sheet1", "Sheet1")):
            (self.root / f"{n}_meta.json").write_text(json.dumps({
                "file": "x.xlsx", "sheet": sheet,
                "dimensions": {"rows": 2, "cols": 1, "data_rows": 1},
                "header_band": None, "merged_ranges": [], "blocks": [],
                "columns": [{"col": "A", "nonempty": 1, "numeric_ratio": 1.0}],
                "formulas": {}, "column_numfmt": {}, "merge_anchors": [],
                "row_gaps": [], "style_granularity": {}},
                ensure_ascii=False), encoding="utf-8")
            (self.root / f"{n}_flat.csv").write_text("a\n", encoding="utf-8")
            (self.root / f"{n}_candidates.yaml").write_text(
                "column_classifications: []\n", encoding="utf-8")
            # evidence 是 xlsx 条目声明产物 (reference 校验要求在场)
            (self.root / f"{n}_premod_evidence.md").write_text(
                f"# {n} synthetic evidence\n", encoding="utf-8")

    def test_acceptance4_output_rename_keeps_materialize_valid(self):
        """非输入修改（输出命名）→ materialize 正常（不触发任何失效）。"""
        self._seed_flatten()
        self._edit_yaml("out_r32_cooling.xlsx", "out_r32_cooling_v2.xlsx")
        proc = self._run_materialize()
        self.assertEqual(proc.returncode, 0, proc.stderr[-1200:])
        self.assertEqual(json.loads(proc.stdout)["code"], "MATERIALIZED_TASK")

    def test_acceptance3_sheet_reference_change_fails_closed(self):
        """输入事实（sheet 引用）变化但 workspace scope 未包含该 sheet →
        materialize fail-closed（RUN_ENTRY_NOT_IN_WORKSPACE）—— Agent 必须
        重新 workspace_init 纳入新业务 sheet（绝不增量 flatten）。"""
        self._seed_flatten()
        self._edit_yaml("sheets: [R32参数, R410A参数]", "sheets: [R32参数, R22参数]")
        # R22 不在合成 workspace 的 flattened 里 → fail-closed
        proc = self._run_materialize()
        self.assertEqual(proc.returncode, 3, proc.stdout[-600:])
        err = json.loads(proc.stderr)
        self.assertEqual(err["code"], "RUN_ENTRY_NOT_IN_WORKSPACE")
        self.assertIn("重新 workspace_init", err["corrective_action"])

    def test_notes_change_keeps_materialize_valid(self):
        """notes（非输入）修改 → materialize 正常。"""
        self._seed_flatten()
        self._edit_yaml("notes: 合成验收示例", "notes: 改成另一种说明（非输入）")
        proc = self._run_materialize()
        self.assertEqual(proc.returncode, 0, proc.stderr[-1200:])
        self.assertEqual(json.loads(proc.stdout)["code"], "MATERIALIZED_TASK")


class TestSupersedeDeclarationBoundary(unittest.TestCase):
    """supersede 机制已退役（ticket 07）：validate_supersede 删除，输入事实
    边界由 check_frozen / SOURCE_HASH_DRIFT 承担（版本历史 = 文件版本命名）。"""

    def test_supersede_mechanism_retired(self):
        """validate_supersede 不再存在（无 supersede 抽象）。"""
        self.assertFalse(hasattr(task_schema, "validate_supersede"))


if __name__ == "__main__":
    unittest.main()