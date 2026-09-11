"""Prepare flatten 产出 role-neutral Pre-MOD Evidence、延迟 digest 的磁盘契约测试。

Business Reasoning Barrier — ticket 02 (.scratch/table-fill-mod-barrier/) +
ADR 0018/0019 (workspace role neutrality + target routing view):

  workspace_init 每 xlsx sheet 生成 {name}_premod_evidence.md（最小结构标签
  视图，role-neutral：只保留任务形状/MOD 适用性事实，**无 target 视角的
  占位/克隆段**），**不再写 {name}_digest.md**；完整 digest 延后到 MOD
  Resolution 解锁后由 Agent 单独补生成。workspace_manifest flattened 条目
  记录 evidence 路径、digest 字段标记为 deferred（非文件名）。

  target 视角（占位行/克隆源样式段）由 materialize_run 渲染的 run-local
  target routing view (`<target>_target_view.md`) 提供 — 与 workspace
  role-neutral evidence 不同名、不覆盖 (ADR 0018 Q3)。

只测磁盘产物与 manifest（spec Testing Decision: external behavior only），
不 import 内部实现。复用 checked-in fixture
tests/_fixtures/task_orchestration/e2e/（带 Office，officecli 缺失时跳过）。

Run with:
  python -m pytest table-fill/tests/test_prepare_evidence_output.py -q
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
SCRIPTS = SKILL_ROOT / "scripts"
FIX_E2E = (Path(__file__).resolve().parent / "_fixtures"
           / "task_orchestration" / "e2e")
# Ticket 01 evidence E2E fixture (data-neutral, 共享字符串工作簿):
# 见 tests/_fixtures/generate_prepare_evidence_fixture.py
FIX_EVID = (Path(__file__).resolve().parent / "_fixtures" / "prepare_evidence")


def run_py(workdir: Path, script: str, *args) -> subprocess.CompletedProcess:
    """套件 e2e 的 subprocess seam（cwd=workdir）。"""
    return subprocess.run(
        [sys.executable, "-X", "utf8", str(SCRIPTS / script), *args],
        cwd=str(workdir), capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=1500,
    )


@unittest.skipIf(shutil.which("officecli") is None, "officecli not on PATH")
class PrepareEvidenceOutputTests(unittest.TestCase):
    """ticket 02 flatten 阶段产物契约（磁盘 + manifest，带 Office e2e）。"""

    def setUp(self):
        sys.path.insert(0, str(SCRIPTS))
        from _officecli import clean_residents  # noqa: PLC0415
        clean_residents()
        self.workdir = Path(tempfile.mkdtemp(prefix="premod_evidence_e2e_"))
        shutil.copy2(FIX_E2E / "sources" / "parameter_book.xlsx",
                     self.workdir / "parameter_book.xlsx")
        shutil.copy2(FIX_E2E / "templates" / "filling_template.xlsx",
                     self.workdir / "filling_template.xlsx")

    def tearDown(self):
        from _officecli import clean_residents, unlink_retry  # noqa: PLC0415
        import time
        clean_residents()
        time.sleep(1.0)
        for p in sorted(self.workdir.rglob("*"), reverse=True):
            try:
                if p.is_file():
                    unlink_retry(p)
                else:
                    p.rmdir()
            except OSError:
                pass
        try:
            self.workdir.rmdir()
        except OSError:
            pass

    def _workspace_init(self, sheets: str) -> subprocess.CompletedProcess:
        return run_py(
            self.workdir, "workspace_init.py", "--workdir", ".",
            "--init", "--files",
            "parameter_book.xlsx|parameter_book.xlsx,"
            "filling_template.xlsx|filling_template.xlsx",
            "--sheets", sheets, "--task", "premod evidence e2e")

    def _single_materialize(self) -> dict:
        """单 run materialize (source=R32, target=模板 Sheet1), 返回 prepare_manifest."""
        from _fixtures.run_driver import materialize, target_entry_name
        materialize(self.workdir,
                    sources=target_entry_name("parameter_book", "R32参数"),
                    target=target_entry_name("filling_template", "Sheet1"))
        return json.loads(
            (self.workdir / "prepare_manifest.json").read_text(encoding="utf-8"))

    def _manifest(self) -> dict:
        """role-neutral workspace manifest（无 fingerprints/target — 那是
        run-local 派生视图 prepare_manifest 的内容，见 ADR 0018）。"""
        return json.loads(
            (self.workdir / "workspace_manifest.json").read_text(encoding="utf-8"))

    def test_premod_evidence_written_digest_deferred(self):
        """init 产出 role-neutral evidence, 不产出 digest; manifest 条目契约。"""
        proc = self._workspace_init(
            "parameter_book.xlsx:R32参数;filling_template.xlsx:Sheet1")
        self.assertEqual(proc.returncode, 0, proc.stderr[-800:])
        wm = self._manifest()

        # 每条目: evidence 落盘、digest 缺席、manifest 条目契约 (evidence +
        # digest == "deferred")
        self.assertEqual(len(wm["flattened"]), 2)
        for e in wm["flattened"]:
            n = e["name"]
            self.assertEqual(e["evidence"], f"{n}_premod_evidence.md")
            self.assertEqual(e["digest"], "deferred",
                             "digest 必须是 deferred 标记, 不是文件名")
            self.assertTrue((self.workdir / e["evidence"]).is_file(),
                            f"{e['evidence']} 缺失")
            self.assertFalse((self.workdir / f"{n}_digest.md").exists(),
                             f"{n}_digest.md 不应被产出 (digest 延后至 MOD 解锁)")
            # classify 列候选不受屏障影响 (Post-MOD digest 生成输入)
            self.assertTrue((self.workdir / e["candidates"]).is_file())
            # entry-level structure 指纹在场 (role-neutral, ADR 0018)
            self.assertTrue(e.get("structure_sha256"))

        # workspace manifest 角色中立: 无 target / 无二元 fingerprints
        self.assertNotIn("target", wm, "workspace_manifest 不得含 target 角色")
        self.assertNotIn("fingerprints", wm,
                         "workspace_manifest 不得含二元指纹 (run-local 派生)")

        # run-local 派生视图: fingerprints/target 由 materialize 产出
        pm = self._single_materialize()
        fp = pm["fingerprints"]
        self.assertTrue(fp.get("source_structure"))
        self.assertTrue(fp.get("target_structure"))
        self.assertEqual(pm["target"]["digest"], "deferred")

    def test_evidence_content_complies_with_premod_contract(self):
        """evidence 保留 dims/表头/块(无标题)/合并/行洞/样式事实 (无样例),
        剔除公式链模板/非空列画像/合并锚点/numFmt。role-neutral: 目标样式段
        只在 run-local target view (materialize 渲染) 出现。"""
        proc = self._workspace_init(
            "parameter_book.xlsx:R32参数;filling_template.xlsx:Sheet1")
        self.assertEqual(proc.returncode, 0, proc.stderr[-800:])
        wm = self._manifest()
        bm = {e["name"]: e for e in wm["flattened"]}

        # 源 sheet evidence (role-neutral): dims 行 + 剔除项 + block 无标题
        src = (self.workdir / bm["parameter_book_R32"]["evidence"]).read_text(
            encoding="utf-8")
        self.assertRegex(src, r"\d+行 × \d+列")
        # 剔除项 (字符串缺席)
        for forbidden in ("公式链模板", "非空列画像", "合并锚点", "numFmt"):
            self.assertNotIn(forbidden, src,
                             f"Pre-MOD evidence 泄露出禁读信息: {forbidden}")
        # block 保留但无标题 (block 行只含 B{id}/行范围/score, 不含引号标题)
        if "- 数据块:" in src:
            for line in src.splitlines():
                if line.strip().startswith("- B"):
                    self.assertNotIn('"', line,
                                     "Pre-MOD evidence 的 block 行不得携带块标题")

        # target entry 的 ROLE-NEUTRAL evidence: 不含 target 视角样式段
        # (占位行/克隆源样式 — 那是 run-local target view 的内容, ADR 0018)
        tgt = (self.workdir / bm["filling_template_Sheet1"]["evidence"]).read_text(
            encoding="utf-8")
        self.assertRegex(tgt, r"\d+行 × \d+列")
        self.assertNotIn("占位行样式", tgt,
                         "role-neutral evidence 不得含 target 视角样式段")
        for forbidden in ("公式链模板", "非空列画像", "合并锚点", "numFmt"):
            self.assertNotIn(forbidden, tgt,
                             f"target Pre-MOD evidence 泄露出禁读信息: {forbidden}")

        # run-local target view: target 视角样式段由 materialize 渲染, 且不
        # 覆盖 workspace 产物 (不同名)
        pm = self._single_materialize()
        tv = pm["target"]["evidence"]
        self.assertEqual(tv, f"{pm['target']['name']}_target_view.md")
        self.assertTrue((self.workdir / tv).is_file(), f"{tv} 缺失")
        # 不得有样例值泄露 (无论哪份 evidence)
        view_text = (self.workdir / tv).read_text(encoding="utf-8")
        self.assertNotIn("样例:", view_text)
        self.assertNotIn("(样例:", view_text)

    def test_evidence_reports_data_start_and_bbox_and_axis_facts(self):
        """Ticket 01 Prepare E2E evidence 契约: premod_evidence.md 必须报告
        数据起始行 (表头带) 与有效值列信息, 且 value_bbox / style_bbox 分开
        报告 — 样式/列宽延伸超出值域 (值域 A1:F12 vs 样式域 A1:Z12) 即
        Case D 的合成工作簿替换验收 (真实模板 replay 本票显式排除)。"""
        book = "evidence_bbox_book.xlsx"
        shutil.copy2(FIX_EVID / book, self.workdir / book)
        proc = run_py(self.workdir, "workspace_init.py", "--workdir", ".",
                      "--init", "--files", f"{book}|{book}",
                      "--sheets", f"{book}:Sheet1", "--task", "bbox evidence e2e")
        self.assertEqual(proc.returncode, 0, proc.stderr[-800:])
        wm = self._manifest()
        bm = {e["name"]: e for e in wm["flattened"]}
        name = "evidence_bbox_book_Sheet1"
        self.assertIn(name, bm)
        ev = (self.workdir / bm[name]["evidence"]).read_text(encoding="utf-8")

        # (1) 数据起始行: 表头带行 (共享字符串 fixture → header band 检测可达)
        self.assertIn("- 表头带: 行 [2] 数据起始行 3", ev)
        # (2) 有效值列信息
        self.assertIn("- 有效值列: A B C D E F", ev)
        # (3) value_bbox vs style_bbox 分离 (格式/列宽延伸超值域)
        self.assertIn("- value_bbox: A1:F12", ev)
        self.assertIn("- style_bbox: A1:Z12", ev)
        # (4) 列头名与表头带并存 (Case A) + 轻量 axis evidence
        self.assertIn("- 表头: 参数 | 单位 | 12K | 18K | 24K | 备注", ev)
        self.assertIn("- 候选列头: A=参数 B=单位 C=12K D=18K E=24K F=备注", ev)

    def test_workspace_scope_is_sheet_union(self):
        """Selective Flatten (ADR 0018): --sheets 一次声明业务 sheet 并集 →
        workspace 只含这些 entry; 增量 flatten 已退役 (缺失 sheet = 重新 init)。"""
        proc = self._workspace_init(
            "parameter_book.xlsx:R32参数,R410A参数;filling_template.xlsx:Sheet1")
        self.assertEqual(proc.returncode, 0, proc.stderr[-800:])
        wm = self._manifest()
        names = {e["name"] for e in wm["flattened"]}
        self.assertEqual(
            names, {"parameter_book_R32", "parameter_book_R410A",
                    "filling_template_Sheet1"},
            "--sheets 业务并集: 每 sheet 恰好一个 entry, 无重复")
        self.assertEqual(len(wm["flattened"]), 3)
        # role-neutral: 每条目 evidence 落盘 + digest deferred + entry 指纹
        for e in wm["flattened"]:
            self.assertTrue((self.workdir / e["evidence"]).is_file())
            self.assertEqual(e["digest"], "deferred")
            self.assertTrue(e.get("structure_sha256"))
        # 全簿展平被禁: workspace 不含未声明的 sheet
        self.assertNotIn("parameter_book_R22", names,
                         "未声明的业务 sheet 不得进入事实空间")


if __name__ == "__main__":
    unittest.main()
