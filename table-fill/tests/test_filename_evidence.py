"""Ticket A — mod_nominate.py 文件名证据 (原始业务文件名 + staged 暂存名)。

走查修复 (2026-08-27): `source_pattern` / `target_pattern` 信号曾只匹配
`--files` 传入的 staged ASCII 名 (source_maoli.xlsx), 中文业务文件名模式
(如 source_pattern::毛利表*) 永远 miss。修复后 mod_nominate 从
prepare_manifest.json 载入 files[].source 原始 basename 与 files[].staged
暂存名进入证据 — MOD 规则不再对暂存命名敏感。

只测 CLI 输出 JSON 契约 (exit codes / 磁盘产物), 不 import 内部实现结构。
与 test_mod_adjudication.py 同风格: 临时目录内联写 MOD_INDEX + MOD 文件,
经 subprocess CLI seam 跑 mod_nominate.py, 断言写入的 JSON。
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


def _mod_file(summary: str = "- x\n") -> str:
    return ("## Applicability\n- semantic_type: quotation\n\n"
            "## 业务逻辑摘要\n" + summary + "\n")


def run_nominate(workdir: Path, idx: Path, mods: Path, out: str,
                 task: str = "报价汇总 迁移", files: str = "",
                 ) -> subprocess.CompletedProcess:
    """跑一次 CLI, 返回 CompletedProcess。"""
    argv = [sys.executable, str(NOMINATE), "--task", task,
            "--workdir", str(workdir), "--index", str(idx),
            "--mods-dir", str(mods), "--out", out]
    if files:
        argv += ["--files", files]
    return subprocess.run(argv, capture_output=True, text=True, encoding="utf-8")


def _write_manifest(workdir: Path, files: list[dict]) -> None:
    (workdir / "prepare_manifest.json").write_text(
        json.dumps({"files": files}, ensure_ascii=False), encoding="utf-8")


class FilenameEvidenceTests(unittest.TestCase):
    def _workdir(self) -> tuple[Path, Path, Path, Path]:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        workdir = Path(tmp.name)
        idx = workdir / "MOD_INDEX.md"
        mods = workdir / "MODS"
        mods.mkdir(exist_ok=True)
        return workdir, idx, mods, workdir / "mod_resolution.json"

    def _index(self, idx: Path, scope: str) -> None:
        idx.write_text(INDEX_HEADER + (
            f"| tfile | t | {scope} |  | MOD_test.md | 1 | private |\n"),
            encoding="utf-8")
        (idx.parent / "MODS" / "MOD_test.md").write_text(_mod_file(),
                                                         encoding="utf-8")

    # ── 1. 原名中文 + staged ASCII → hit ────────────────────────────────
    def test_chinese_source_basename_matches_pattern(self):
        workdir, idx, mods, out = self._workdir()
        self._index(idx, "source_pattern::毛利表*")
        _write_manifest(workdir, [{
            "source": r"D:\some\毛利表-FRESH订单机核价105000-6.7 0728 final.xlsx",
            "staged": "source_maoli.xlsx",
        }])
        r = run_nominate(workdir, idx, mods, str(out), files="source_maoli.xlsx")
        self.assertEqual(r.returncode, 0, r.stderr)
        data = json.loads(out.read_text(encoding="utf-8"))
        # 命中 → 单候选 clean → resolved (selected), 信号在 hits 而非 missed
        self.assertEqual(data["status"], "resolved")
        cand = data["candidates"][0]
        self.assertIn("source_pattern::毛利表*", cand["hits"])
        self.assertNotIn("source_pattern::毛利表*", cand["missed"])

    # ── 2. 改变 staged alias → 结果不变 ────────────────────────────────
    def test_staging_alias_invariant(self):
        workdir, idx, mods, out = self._workdir()
        self._index(idx, "source_pattern::毛利表*")
        src = r"D:\some\毛利表-FRESH订单机核价105000-6.7 0728 final.xlsx"

        # run 1: staged s1.xlsx
        _write_manifest(workdir, [{"source": src, "staged": "s1.xlsx"}])
        r1 = run_nominate(workdir, idx, mods, str(out), files="s1.xlsx")
        d1 = json.loads(out.read_text(encoding="utf-8"))

        # run 2: staged s2.xlsx (同 source)
        _write_manifest(workdir, [{"source": src, "staged": "s2.xlsx"}])
        r2 = run_nominate(workdir, idx, mods, str(out), files="s2.xlsx")
        d2 = json.loads(out.read_text(encoding="utf-8"))

        self.assertEqual(r1.returncode, 0, r1.stderr)
        self.assertEqual(r2.returncode, 0, r2.stderr)
        # 信号判定一致: 都命中 (不依赖 staged alias)
        self.assertIn("source_pattern::毛利表*", d1["candidates"][0]["hits"])
        self.assertIn("source_pattern::毛利表*", d2["candidates"][0]["hits"])
        self.assertEqual(d1["status"], d2["status"])
        self.assertEqual(d1["candidates"][0]["hits"],
                         d2["candidates"][0]["hits"])

    # ── 3. target_pattern 同理 ─────────────────────────────────────────
    def test_chinese_target_basename_matches_pattern(self):
        workdir, idx, mods, out = self._workdir()
        self._index(idx, "target_pattern::报价汇总*")
        _write_manifest(workdir, [{
            "source": r"D:\src\source.xlsx",
            "staged": "s1.xlsx",
        }, {
            "source": r"D:\tgt\报价汇总-FRESH.xlsx",
            "staged": "target_baojia.xlsx",
        }])
        r = run_nominate(workdir, idx, mods, str(out),
                         files="s1.xlsx,target_baojia.xlsx")
        self.assertEqual(r.returncode, 0, r.stderr)
        data = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(data["status"], "resolved")
        self.assertIn("target_pattern::报价汇总*", data["candidates"][0]["hits"])

    # ── 4. manifest 缺失 → 旧行为 + WARNING ─────────────────────────────
    def test_missing_manifest_degrades_with_warning(self):
        workdir, idx, mods, out = self._workdir()
        self._index(idx, "source_pattern::毛利表*")
        # 无 prepare_manifest.json
        r = run_nominate(workdir, idx, mods, str(out), files="source_maoli.xlsx")
        self.assertEqual(r.returncode, 0, r.stderr)
        # WARNING 打到 stderr
        self.assertIn("未发现 prepare_manifest.json", r.stderr)
        # 旧行为: 中文模式无法命中 → 无 hit 信号 → status none (fail-closed
        # by absence); 输出形状仍是 status/why/candidates
        data = json.loads(out.read_text(encoding="utf-8"))
        self.assertIn("status", data)
        self.assertIn("why", data)
        self.assertIn("candidates", data)
        self.assertEqual(data["status"], "none")

    # ── 5. sanity: --files staged 名仍匹配英文模式 ──────────────────────
    def test_english_pattern_still_matches_with_manifest(self):
        workdir, idx, mods, out = self._workdir()
        self._index(idx, "source_pattern::*maoli*")
        _write_manifest(workdir, [{
            "source": r"D:\some\毛利表.xlsx",
            "staged": "source_maoli.xlsx",
        }])
        r = run_nominate(workdir, idx, mods, str(out), files="source_maoli.xlsx")
        self.assertEqual(r.returncode, 0, r.stderr)
        data = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(data["status"], "resolved")
        self.assertIn("source_pattern::*maoli*", data["candidates"][0]["hits"])


if __name__ == "__main__":
    unittest.main()
