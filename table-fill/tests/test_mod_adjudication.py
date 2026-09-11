"""Ticket 03 — mod_nominate.py `--mod <NAME|NONE>` 裁决记录契约。

只测 CLI 输出 JSON 契约 (exit codes / 缺陷码 / 磁盘产物), 不 import 内部
实现结构。与 test_optimization.py 提名测试同风格: 临时目录内联写 MOD_INDEX
+ MOD 文件, 经 subprocess CLI seam 跑 mod_nominate.py, 断言写入的 JSON。

两条硬性契约保持不破:
  - 两段加载: 候选卡永远不含完整 `rules` 字段。
  - rule_evidence (id+description) 只在 ambiguous 时附到候选卡。
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

NOMINATE = SKILL_ROOT / "scripts" / "mod_nominate.py"

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
                 mod: str | None = None) -> subprocess.CompletedProcess:
    """跑一次 CLI, 返回 CompletedProcess (digest 文本按需写成临时文件传入)。"""
    argv = [sys.executable, str(NOMINATE), "--task", task,
            "--workdir", str(workdir), "--index", str(idx),
            "--mods-dir", str(mods), "--out", out]
    if digest_text is not None:
        dpath = workdir / "digest.md"
        dpath.write_text(digest_text, encoding="utf-8")
        argv += ["--digest", str(dpath)]
    if mod is not None:
        argv += ["--mod", mod]
    return subprocess.run(argv, capture_output=True, text=True, encoding="utf-8")


def two_candidate_index() -> list[str]:
    """ambiguous fixture: 2 个候选都命中 semantic_type::quotation。"""
    return [
        "| mod_a | a | semantic_type::quotation |  | MOD_a.md | 7 | private |\n",
        "| mod_b | b | semantic_type::quotation |  | MOD_b.md | 9 | private |\n",
    ]


def write_two_candidate_mods(mods: Path) -> None:
    mods.mkdir(exist_ok=True)
    (mods / "MOD_a.md").write_text(_mod_file(rules=(
        "| FLD-006 | business_transformation | mod_gate | 描述FLD-006。 | X | n |\n")),
        encoding="utf-8")
    (mods / "MOD_b.md").write_text(_mod_file(rules=(
        "| FRM-002 | business_transformation | mod_gate | 描述FRM-002。 | X | n |\n")),
        encoding="utf-8")


class ModAdjudicationTests(unittest.TestCase):
    def _workdir(self) -> tuple[Path, Path, Path, Path]:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        workdir = Path(tmp.name)
        idx = workdir / "MOD_INDEX.md"
        mods = workdir / "MODS"
        mods.mkdir(exist_ok=True)
        return workdir, idx, mods, workdir / "mod_resolution.json"

    # ── 1. --mod NAME on ambiguous fixture ─────────────────────────────
    def test_mod_name_ambiguous_records_resolved(self):
        workdir, idx, mods, out = self._workdir()
        _write_index(idx, two_candidate_index())
        write_two_candidate_mods(mods)
        r = run_nominate(workdir, idx, mods, str(out), mod="mod_a")
        self.assertEqual(r.returncode, 0, r.stderr)
        data = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(data["status"], "resolved")
        self.assertEqual(data["selected"], "mod_a")
        self.assertEqual(data["selected_revision"], 7)
        self.assertEqual(len(data["candidates"]), 2)
        # 候选卡完整保留 (含 rule_evidence)
        for cand in data["candidates"]:
            self.assertNotIn("rules", cand)
            self.assertIn("rule_evidence", cand)
        self.assertEqual(data["adjudicated_from"], "ambiguous")

    # ── 2. --mod NAME not in candidate set → fail-closed ────────────────
    def test_mod_name_not_in_candidates_fails_closed(self):
        workdir, idx, mods, out = self._workdir()
        # 3 个注册 MOD, 但只有 2 个命中 (third 的 scope 不匹配)
        _write_index(idx, two_candidate_index() + [
            "| mod_c | c | semantic_type::kpi_scorecard |  | MOD_c.md | 1 | private |\n",
        ])
        write_two_candidate_mods(mods)
        (mods / "MOD_c.md").write_text(_mod_file(), encoding="utf-8")
        r = run_nominate(workdir, idx, mods, str(out), mod="mod_c")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("MOD_NOT_IN_CANDIDATES", r.stderr)
        self.assertFalse(out.exists())  # --out 未写 (或未新建)

    # ── 3. --mod NONE from ambiguous ───────────────────────────────────
    def test_mod_none_from_ambiguous(self):
        workdir, idx, mods, out = self._workdir()
        _write_index(idx, two_candidate_index())
        write_two_candidate_mods(mods)
        r = run_nominate(workdir, idx, mods, str(out), mod="NONE")
        self.assertEqual(r.returncode, 0, r.stderr)
        data = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(data["status"], "resolved")
        self.assertEqual(data["selected"], "NONE")
        self.assertNotIn("selected_revision", data)  # NONE 短路 revision
        self.assertEqual(data["adjudicated_from"], "ambiguous")

    # ── 4. status none (no --mod) vs --mod NONE → resolved ─────────────
    def test_none_status_vs_mod_none(self):
        workdir, idx, mods, out = self._workdir()
        # 单一候选, 但 scope 是 sheet_marker (无 outline → pending, 不成候选?)
        # 用只含 missed 信号的 MOD → status none (fail-closed by absence)
        _write_index(idx, [
            "| tauto | t | sheet_marker::三三三\\|333 |  | MOD_test.md | 1 | private |\n",
        ])
        (mods / "MOD_test.md").write_text(_mod_file(), encoding="utf-8")
        digest = "- 表头: Z码 | 数量 | 报价 | 原型机成本\n"
        # 无 --mod: status none
        r = run_nominate(workdir, idx, mods, str(out), digest_text=digest)
        self.assertEqual(r.returncode, 0, r.stderr)
        data = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(data["status"], "none")
        # --mod NONE: status resolved + selected NONE (即便 fresh 评估是 none)
        r = run_nominate(workdir, idx, mods, str(out), digest_text=digest, mod="NONE")
        self.assertEqual(r.returncode, 0, r.stderr)
        data = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(data["status"], "resolved")
        self.assertEqual(data["selected"], "NONE")

    # ── 5. exclusion override → overridden_exclusions ──────────────────
    def test_mod_name_overrides_exclusion(self):
        workdir, idx, mods, out = self._workdir()
        # 排除「目标缺少24角色表头指纹」在无 24 列 digest 证据时触发
        _write_index(idx, [
            "| tauto | t | semantic_type::quotation | 目标缺少24角色表头指纹 | MOD_test.md | 3 | private |\n",
        ])
        (mods / "MOD_test.md").write_text(_mod_file(), encoding="utf-8")
        # digest 无 24 列, 无 outline → 排除触发 → fresh status conflict
        digest = "- 表头: 数量 | 报价 | 毛利\n- 标题型数据块: 无自动候选（不代表不存在重复记录区）\n"
        r = run_nominate(workdir, idx, mods, str(out), digest_text=digest)
        self.assertEqual(r.returncode, 0, r.stderr)
        fresh = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(fresh["status"], "conflict")
        # --mod NAME → resolved + overridden_exclusions + adjudicated_from conflict
        r = run_nominate(workdir, idx, mods, str(out), digest_text=digest, mod="tauto")
        self.assertEqual(r.returncode, 0, r.stderr)
        data = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(data["status"], "resolved")
        self.assertEqual(data["selected"], "tauto")
        self.assertTrue(data["overridden_exclusions"])  # 非空
        self.assertEqual(data["adjudicated_from"], "conflict")

    # ── 6. auto-adopt single clean candidate → selected (regression) ───
    def test_auto_adopt_single_candidate_has_selected(self):
        workdir, idx, mods, out = self._workdir()
        _write_index(idx, [
            "| tauto | t | semantic_type::quotation |  | MOD_test.md | 2 | private |\n",
        ])
        (mods / "MOD_test.md").write_text(_mod_file(), encoding="utf-8")
        r = run_nominate(workdir, idx, mods, str(out))
        self.assertEqual(r.returncode, 0, r.stderr)
        data = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(data["status"], "resolved")
        self.assertEqual(data["selected"], "tauto")
        self.assertEqual(data["selected_revision"], 2)

    # ── 7. explicit mention → selected (regression) ────────────────────
    def test_explicit_mention_has_selected(self):
        workdir, idx, mods, out = self._workdir()
        _write_index(idx, two_candidate_index())
        write_two_candidate_mods(mods)
        task = "使用 MOD mod_a 读取并回写报价汇总"
        r = run_nominate(workdir, idx, mods, str(out), task=task)
        self.assertEqual(r.returncode, 0, r.stderr)
        data = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(data["status"], "resolved")
        self.assertEqual(data["selected"], "mod_a")

    # ── adudrecord: NAME via alias resolves to card name ───────────────
    def test_mod_alias_resolves_to_card_name(self):
        workdir, idx, mods, out = self._workdir()
        _write_index(idx, two_candidate_index())
        write_two_candidate_mods(mods)
        r = run_nominate(workdir, idx, mods, str(out), mod="b")
        self.assertEqual(r.returncode, 0, r.stderr)
        data = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(data["status"], "resolved")
        self.assertEqual(data["selected"], "mod_b")  # alias → 卡名
        self.assertEqual(data["selected_revision"], 9)


if __name__ == "__main__":
    unittest.main()
