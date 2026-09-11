"""Ticket 02 — MOD 提名客户文件名信号强化 (Client 大小写变体命中客户参数表 MOD)。

验收清单 (issues/02-mod-nomination-client-alias.md):
  [ ] 客户参数表类文件名信号 (含 Client 大小写变体) 在提名阶段命中对应 MOD (fixture)
  [ ] 提名证据完全来自初始化产物, 无需任何额外探测
  [ ] 命中/未命中两条路径的提名行为都有测试覆盖
  [ ] MOD 相关测试全绿

只测 CLI 输出 JSON 契约 (exit codes / 磁盘产物), 不 import 内部实现结构 —
与 test_filename_evidence.py / test_mod_adjudication.py 同风格: 临时目录内联
写 MOD_INDEX + MOD 文件 + workspace_manifest.json, 经 subprocess CLI seam 跑
mod_nominate.py, 断言写入的 JSON。

覆盖两条机制改动:
  1. 命名层大小写不敏感 (signal_matched 的 source/target_pattern fnmatch 显式
     lower 双侧): 单个 `*client*` 即命中 *CLIENT*/*Client*/*client* 变体。
  2. mod_nominate 消费 workspace_manifest.json (优先) — 文件名证据来自
     inputs[].source/staged, outline/digest 从 outlines/flattened[].evidence
     派生, 无需显式 --outline/--digest (提名证据完全来自初始化产物)。
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
REFS = SKILL_ROOT / "references"

INDEX_HEADER = (
    "## Registered MODs\n\n"
    "| MOD Name | Aliases | Scope Signals | Exclusion Signals | Path | Revision | Visibility |\n"
    "|---|---|---|---|---|---|---|\n"
)


def _mod_file(summary: str = "- x\n") -> str:
    return ("## Applicability\n- semantic_type: quotation\n\n"
            "## 业务逻辑摘要\n" + summary + "\n")


def run_nominate(workdir: Path, idx: Path, mods: Path, out: str,
                 task: str = "客户参数表 外发", files: str = "",
                 outline: str = "", digest: str = "",
                 ) -> subprocess.CompletedProcess:
    argv = [sys.executable, str(NOMINATE), "--task", task,
            "--workdir", str(workdir), "--index", str(idx),
            "--mods-dir", str(mods), "--out", out]
    if files:
        argv += ["--files", files]
    if outline:
        argv += ["--outline", outline]
    if digest:
        argv += ["--digest", digest]
    return subprocess.run(argv, capture_output=True, text=True, encoding="utf-8")


def _write_prepare_manifest(workdir: Path, files: list[dict],
                            outlines: dict[str, str] | None = None,
                            flattened: list[dict] | None = None) -> None:
    """compile-facing manifest fixture (T12 收敛后 mod_nominate 唯一消费面)。"""
    manifest = {
        "schema_version": 2,
        "files": files,
        "outlines": outlines or {},
        "flattened": flattened or [],
    }
    (workdir / "prepare_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8")


def _index_param_only(idx: Path, mods: Path, scope: str) -> None:
    """单候选 fixture: 客户参数表 MOD (无排除, 便于断言 clean resolved)。"""
    idx.write_text(INDEX_HEADER + (
        f"| param_sheet | tcl-param-sheet | {scope} |  | MOD_param.md | 1 | private |\n"),
        encoding="utf-8")
    (mods / "MOD_param.md").write_text(
        "## Applicability\n- semantic_type: internal_parameter_to_customer_parameter_sheet\n\n"
        "## 业务逻辑摘要\n- 参数表转换\n",
        encoding="utf-8")


class ClientFilenameSignalTests(unittest.TestCase):
    """命中/未命中两条路径 + 大小写变体 + workspace_manifest 消费。"""

    def _workdir(self) -> tuple[Path, Path, Path, Path]:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        workdir = Path(tmp.name)
        idx = workdir / "MOD_INDEX.md"
        mods = workdir / "MODS"
        mods.mkdir(exist_ok=True)
        return workdir, idx, mods, workdir / "mod_resolution.json"

    # ── 1. 命中: *client* 变体命中对应 MOD (fixture 验证) ─────────────────
    def test_client_filename_hits_param_mod(self):
        workdir, idx, mods, out = self._workdir()
        scope = ("semantic_type::internal_parameter_to_customer_parameter_sheet,"
                 "target_pattern::*client*")
        _index_param_only(idx, mods, scope)
        # 客户模板文件名 (英文 Client 变体) 进入初始化产物 inputs[].source
        _write_prepare_manifest(workdir, [
            {"source": r"D:\src\spectrum.xlsx", "staged": "spectrum_src.xlsx"},
            {"source": r"D:\tgt\TCL Client Parameter Sheet.xlsx",
             "staged": "target_client.xlsx"},
        ])
        r = run_nominate(workdir, idx, mods, str(out))
        self.assertEqual(r.returncode, 0, r.stderr)
        data = json.loads(out.read_text(encoding="utf-8"))
        # 唯一强候选 + 全部命中 → fail-closed 语义下 auto-adopt (resolved + selected)
        self.assertEqual(data["status"], "resolved")
        cand = data["candidates"][0]
        self.assertIn("target_pattern::*client*", cand["hits"])
        self.assertNotIn("target_pattern::*client*", cand["missed"])

    # ── 2. 大小写变体: 单个 *client* 覆盖 *CLIENT*/*Client*/*client* ─────
    def test_client_pattern_case_insensitive_variants(self):
        for filename in ("TCL CLIENT Parameter.xlsx",
                         "TCL Client Parameter.xlsx",
                         "tcl client parameter.xlsx",
                         "TCL ClIeNt Parameter.xlsx"):
            with self.subTest(filename=filename):
                workdir, idx, mods, out = self._workdir()
                scope = ("semantic_type::internal_parameter_to_customer_parameter_sheet,"
                         "target_pattern::*client*")
                _index_param_only(idx, mods, scope)
                _write_prepare_manifest(workdir, [
                    {"source": rf"D:\tgt\{filename}", "staged": "target_client.xlsx"},
                ])
                r = run_nominate(workdir, idx, mods, str(out))
                self.assertEqual(r.returncode, 0, r.stderr)
                data = json.loads(out.read_text(encoding="utf-8"))
                cand = data["candidates"][0]
                self.assertIn("target_pattern::*client*", cand["hits"],
                              f"{filename!r} 应命中 *client* 模式")

    # ── 3. 未命中: 无 client/客户版 文件名 → 不提名参数表 MOD ────────────
    def test_no_client_filename_misses_param_mod(self):
        workdir, idx, mods, out = self._workdir()
        scope = ("semantic_type::internal_parameter_to_customer_parameter_sheet,"
                 "target_pattern::*client*")
        _index_param_only(idx, mods, scope)
        # 目标文件名不含 client (语义信号仍经任务文本命中, 但 target_pattern miss)
        _write_prepare_manifest(workdir, [
            {"source": r"D:\tgt\Internal Price List.xlsx", "staged": "target_price.xlsx"},
        ])
        r = run_nominate(workdir, idx, mods, str(out))
        self.assertEqual(r.returncode, 0, r.stderr)
        data = json.loads(out.read_text(encoding="utf-8"))
        # target_pattern miss → 该信号在 missed (fail-closed by absence, 不自动 resolved)
        cand = data["candidates"][0]
        self.assertIn("target_pattern::*client*", cand["missed"])
        self.assertNotIn("target_pattern::*client*", cand["hits"])

    # ── 4. 完全无匹配 → status none (无 MOD 流程) ────────────────────────
    def test_no_match_at_all_status_none(self):
        workdir, idx, mods, out = self._workdir()
        scope = "target_pattern::*client*"
        _index_param_only(idx, mods, scope)
        # 纯文件名信号, 文件名不含 client, 任务文本也不含语义关键词
        _write_prepare_manifest(workdir, [
            {"source": r"D\tgt\bill.xlsx", "staged": "bill.xlsx"},
        ])
        r = run_nominate(workdir, idx, mods, str(out), task="普通任务")
        self.assertEqual(r.returncode, 0, r.stderr)
        data = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(data["status"], "none")


class WorkspaceManifestEvidenceTests(unittest.TestCase):
    """验收 2 + T12: 提名证据完全来自初始化产物 (compile-facing manifest —收敛后唯一消费面), 无需探测。"""

    def _workdir(self) -> tuple[Path, Path, Path, Path]:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        workdir = Path(tmp.name)
        idx = workdir / "MOD_INDEX.md"
        mods = workdir / "MODS"
        mods.mkdir(exist_ok=True)
        return workdir, idx, mods, workdir / "mod_resolution.json"

    def test_nominate_consumes_manifest_outline_and_digest(self):
        """不传 --outline/--digest: 从 prepare_manifest.json 派生 outline +
        flatten evidence, 结构信号经 digest 事实核对 (dimension_set hit)。"""
        workdir, idx, mods, out = self._workdir()
        # 参数表 MOD: dimension_set 信号经 digest 表头角色核对
        idx.write_text(INDEX_HEADER + (
            "| param_sheet | tcl-param-sheet | "
            "semantic_type::internal_parameter_to_customer_parameter_sheet,"
            "target_pattern::*client*,"
            "dimension_set::product_line_capacity_zcode |  | MOD_param.md | 1 | private |\n"
        ), encoding="utf-8")
        (mods / "MOD_param.md").write_text(
            "## Applicability\n- semantic_type: internal_parameter_to_customer_parameter_sheet\n\n"
            "## 业务逻辑摘要\n- 参数表转换\n",
            encoding="utf-8")

        # 初始化产物: inputs + outlines + flattened evidence (磁盘上写实际文件)
        outline_name = "target_client_outline.txt"
        outline_data = {"data": {"sheets": [
            {"name": "customer parameter", "rows": 30, "cols": 24}]}}
        (workdir / outline_name).write_text(
            json.dumps(outline_data, ensure_ascii=False), encoding="utf-8")

        evidence_name = "target_client_premod_evidence.md"
        (workdir / evidence_name).write_text(
            "- 表头: 产品线 | 容量 | Z码 | 9K | 12K\n"
            "- 30行 × 24列\n", encoding="utf-8")

        _write_prepare_manifest(workdir, [
            {"source": r"D:\src\spectrum.xlsx", "staged": "spectrum_src.xlsx"},
            {"source": r"D:\tgt\TCL Client Parameter.xlsx", "staged": "target_client.xlsx"},
        ], outlines={"target_client.xlsx": outline_name},
            flattened=[{
                "file": "target_client.xlsx", "sheet": "customer parameter",
                "name": "target_client_customer_parameter",
                "evidence": evidence_name,
            }])

        # 只给 --task, 不传 --files/--outline/--digest (证据全来自 manifest)
        r = run_nominate(workdir, idx, mods, str(out))
        self.assertEqual(r.returncode, 0, r.stderr)
        data = json.loads(out.read_text(encoding="utf-8"))
        cand = data["candidates"][0]
        # 文件名信号命中 (来自 files[].source basename)
        self.assertIn("target_pattern::*client*", cand["hits"])
        # dimension_set 经 digest 表头角色 (产品线/容量/Z码) 核对 → hit
        self.assertIn("dimension_set::product_line_capacity_zcode", cand["hits"])

    def test_manifest_missing_degrades_with_warning(self):
        """无 prepare_manifest.json (init 派生视图 / prepare 产物均缺失) → 降级 WARNING。"""
        workdir, idx, mods, out = self._workdir()
        _index_param_only(idx, mods, "target_pattern::*client*")
        r = run_nominate(workdir, idx, mods, str(out), files="target_client.xlsx")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("WARNING", r.stderr)
        self.assertIn("prepare_manifest.json", r.stderr)


class RealParamModIntegrationTests(unittest.TestCase):
    """真实 MOD_INDEX + 真实客户参数表 MOD 命中 *Client* 文件名 (integration)。

    验证生产修复意图: references/ 下真实 MOD 的 target_pattern::*client* 信号
    在 *Client* 客户模板文件名到齐时命中该 MOD (埃及 log 根因之一)。
    """

    def _workdir(self) -> tuple[Path, Path]:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        return Path(tmp.name), Path(tmp.name) / "mod_resolution.json"

    def test_real_param_mod_hits_client_filename(self):
        workdir, out = self._workdir()
        if not (REFS / "MOD_INDEX.md").is_file():
            self.skipTest("references/MOD_INDEX.md 缺失")
        _write_prepare_manifest(workdir, [
            {"source": r"D:\src\型谱.xlsx", "staged": "spectrum.xlsx"},
            {"source": r"D:\src\参数表.xlsx", "staged": "param.xlsx"},
            {"source": r"D:\tgt\TCL Client Parameter Sheet.xlsx",
             "staged": "target_client.xlsx"},
        ])
        r = run_nominate(workdir, REFS / "MOD_INDEX.md", REFS, str(out))
        self.assertEqual(r.returncode, 0, r.stderr)
        data = json.loads(out.read_text(encoding="utf-8"))
        param_cand = [c for c in data["candidates"]
                      if c["name"] ==
                      "MOD_tcl_internal_parameter_to_customer_parameter_sheet"]
        self.assertTrue(param_cand, "客户参数表 MOD 应被提名")
        cand = param_cand[0]
        self.assertIn("target_pattern::*client*", cand["hits"],
                      "*Client* 客户模板文件名应命中客户参数表 MOD 的 target_pattern")
        # 无 digest 证据 → 排除/结构信号不可验证 → fail-closed (ambiguous, 不冒充
        # 自动 resolved) — 这是正确语义, 不是缺陷。
        self.assertNotEqual(data["status"], "conflict")


if __name__ == "__main__":
    unittest.main()
