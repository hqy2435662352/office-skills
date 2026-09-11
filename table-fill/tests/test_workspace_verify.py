"""Trust boundary tests — workspace fact-space integrity.

Trust-boundary 契约 (Sprint A, workspace consumption boundary):
  workspace_init --init 建立事实 → 任何消费入口 (materialize_run /
  workspace_init --verify) 在消费前必须证明「manifest 声明 == 物理文件」:

    1. inputs[].sha256 与 staged 文件一致 (input drift)
    2. derived[].sha256 与 outline/产物文件一致 (derived drift)
    3. flattened 引用 (csv/meta/evidence/candidates/digest) 是 workdir 内
       相对路径且文件在场 (reference / containment)
    4. schema_version 必须等于 4 (shape)

失败语义:
    --verify           → exit 3 + INPUT_DRIFT (既有 CLI 契约, 覆盖面扩展)
    materialize_run    → exit 3 + WORKSPACE_DRIFT (load_workspace fail-closed)
    形状/版本错误       → WORKSPACE_MANIFEST_INVALID (exit 1)

合成 workspace, 零 officecli (materialize 只消费 manifest 条目与
meta/csv/candidates; structure_digest 是纯文本派生)。

Run with:
  python -m pytest table-fill/tests/test_workspace_verify.py -q
"""

from __future__ import annotations

import hashlib
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


def _run_py(workdir: Path, script: str, *args) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-X", "utf8", str(_SCRIPTS_DIR / script), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=str(workdir), timeout=180)


def _h(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _synthetic_meta(sheet: str) -> dict:
    return {
        "file": "book_a.xlsx", "sheet": sheet,
        "dimensions": {"rows": 2, "cols": 1, "data_rows": 1},
        "header_band": None, "merged_ranges": [], "blocks": [],
        "columns": [{"col": "A", "nonempty": 1, "numeric_ratio": 1.0}],
        "formulas": {}, "column_numfmt": {}, "merge_anchors": [],
        "row_gaps": [], "style_granularity": {},
    }


class WorkspaceTrustBoundaryTests(unittest.TestCase):
    """合成自洽 workspace → 篡改 (staged/derived/reference/schema) →
    --verify 与 materialize_run 双双 fail-closed。"""

    SHEET = "Sheet1"
    NAME = "book_a_Sheet1"

    def setUp(self):
        self.wd = Path(tempfile.mkdtemp(prefix="ws_verify_"))
        staged = b"staged-content-001"
        (self.wd / "book_a.xlsx").write_bytes(staged)
        meta = _synthetic_meta(self.SHEET)
        (self.wd / f"{self.NAME}_meta.json").write_text(
            json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        (self.wd / f"{self.NAME}_flat.csv").write_text("a\n", encoding="utf-8")
        (self.wd / f"{self.NAME}_candidates.yaml").write_text(
            "column_classifications: []\n", encoding="utf-8")
        (self.wd / f"{self.NAME}_premod_evidence.md").write_text(
            f"# {self.NAME} synthetic evidence\n", encoding="utf-8")
        self.inputs = [{"staged": "book_a.xlsx", "source": "orig/book_a.xlsx",
                        "sha256": _h(staged)}]
        self.flattened = [{
            "file": "book_a.xlsx", "sheet": self.SHEET, "name": self.NAME,
            "csv": f"{self.NAME}_flat.csv",
            "meta": f"{self.NAME}_meta.json",
            "evidence": f"{self.NAME}_premod_evidence.md",
            "digest": "deferred",
            "candidates": f"{self.NAME}_candidates.yaml",
            "structure_sha256": "ee" * 32,
        }]
        self.derived = []
        for key in ("csv", "meta", "evidence", "candidates"):
            rel = self.flattened[0][key]
            self.derived.append({
                "path": rel, "kind": f"flatten/{self.NAME}/{key}",
                "sha256": _h((self.wd / rel).read_bytes())})
        self._write_manifest()

    def _write_manifest(self) -> None:
        (self.wd / "workspace_manifest.json").write_text(json.dumps({
            "schema_version": 4, "kind": "workspace_init",
            "workdir": str(self.wd), "task": "trust boundary",
            "inputs": self.inputs, "outlines": {},
            "flattened": self.flattened, "derived": self.derived,
            "inherited_from": None,
        }, ensure_ascii=False, indent=2), encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.wd, ignore_errors=True)

    # ── 正向: 自洽 workspace 全部放行 ─────────────────────────────────

    def test_verify_passes_on_consistent_workspace(self):
        proc = _run_py(self.wd, "workspace_init.py", "--workdir", ".",
                       "--verify")
        self.assertEqual(proc.returncode, 0, proc.stderr[-800:])
        out = json.loads(proc.stdout)
        self.assertEqual(out["code"], "WORKSPACE_VERIFIED")

    def test_materialize_passes_on_consistent_workspace(self):
        proc = _run_py(self.wd, "materialize_run.py", "--workdir", ".",
                       "--sources", self.NAME, "--target", self.NAME)
        self.assertEqual(proc.returncode, 0, proc.stderr[-800:])
        self.assertEqual(json.loads(proc.stdout)["code"], "MATERIALIZED_RUN")
        self.assertTrue((self.wd / "prepare_manifest.json").is_file())

    # ── derived 漂移: meta 被篡改 (内容级) ─────────────────────────────

    def test_meta_drift_rejected_by_verify(self):
        (self.wd / f"{self.NAME}_meta.json").write_text(
            (self.wd / f"{self.NAME}_meta.json").read_text(encoding="utf-8")
            + "\n", encoding="utf-8")
        proc = _run_py(self.wd, "workspace_init.py", "--workdir", ".",
                       "--verify")
        self.assertEqual(proc.returncode, 3, proc.stdout)
        err = json.loads(proc.stderr)
        self.assertEqual(err["code"], "INPUT_DRIFT")
        self.assertIn("meta.json", err["message"])
        self.assertIn("重新初始化", err["corrective_action"])

    def test_meta_drift_rejected_by_materialize(self):
        (self.wd / f"{self.NAME}_meta.json").write_text(
            (self.wd / f"{self.NAME}_meta.json").read_text(encoding="utf-8")
            + "\n", encoding="utf-8")
        proc = _run_py(self.wd, "materialize_run.py", "--workdir", ".",
                       "--sources", self.NAME, "--target", self.NAME)
        self.assertEqual(proc.returncode, 3, proc.stdout)
        err = json.loads(proc.stderr)
        self.assertEqual(err["code"], "WORKSPACE_DRIFT")
        kinds = {d["kind"] for d in err["defects"]}
        self.assertIn("derived", kinds)
        self.assertIn("重新 workspace_init", err["corrective_action"])
        # fail-closed: 不产生任何 run 级产物
        self.assertFalse((self.wd / "prepare_manifest.json").exists())

    # ── input 漂移: staged 输入被篡改 ─────────────────────────────────

    def test_staged_drift_rejected_by_materialize(self):
        p = self.wd / "book_a.xlsx"
        p.write_bytes(p.read_bytes() + b"\x00")
        proc = _run_py(self.wd, "materialize_run.py", "--workdir", ".",
                       "--sources", self.NAME, "--target", self.NAME)
        self.assertEqual(proc.returncode, 3, proc.stdout)
        err = json.loads(proc.stderr)
        self.assertEqual(err["code"], "WORKSPACE_DRIFT")
        self.assertIn("input", {d["kind"] for d in err["defects"]})

    # ── reference / containment: 路径逃逸 ─────────────────────────────

    def test_reference_escape_rejected(self):
        self.flattened[0]["csv"] = "../escape.csv"
        self._write_manifest()
        proc = _run_py(self.wd, "materialize_run.py", "--workdir", ".",
                       "--sources", self.NAME, "--target", self.NAME)
        self.assertEqual(proc.returncode, 3, proc.stdout)
        err = json.loads(proc.stderr)
        self.assertEqual(err["code"], "WORKSPACE_DRIFT")
        refs = [d for d in err["defects"] if d["kind"] == "reference"]
        self.assertTrue(refs)
        self.assertIn("escape.csv", refs[0]["path"])

    # ── shape: schema_version 不匹配 ──────────────────────────────────

    def test_schema_version_mismatch_rejected(self):
        m = json.loads((self.wd / "workspace_manifest.json")
                       .read_text(encoding="utf-8"))
        m["schema_version"] = 3
        (self.wd / "workspace_manifest.json").write_text(
            json.dumps(m, ensure_ascii=False), encoding="utf-8")
        proc = _run_py(self.wd, "materialize_run.py", "--workdir", ".",
                       "--sources", self.NAME, "--target", self.NAME)
        self.assertEqual(proc.returncode, 1, proc.stdout)
        self.assertEqual(json.loads(proc.stderr)["code"],
                         "WORKSPACE_MANIFEST_INVALID")


if __name__ == "__main__":
    unittest.main()
