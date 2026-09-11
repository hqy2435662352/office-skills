"""workspace_init 统一入口 (ticket 01) 的 CLIs E2E 契约测试。

验收清单 (issues/01-workspace-init-unified-entry.md 4 条 checkbox):

  [ ] 中文名文件零人工干预完成初始化，全程 0 次手工复制/改名
  [ ] 工作区清单记录全部输入与派生产物的哈希身份
  [ ] 输入内容变更后执行前被哈希核对拒绝，提示重新初始化
  [ ] 既有准备/展平/继承测试全绿

测试层 (spec Testing Decisions「好测试 = 只测外部行为」):

  只测 CLI 出口状态码 + 结构化输出 + 磁盘产物事实 (workspace_manifest.json 的
  输入/派生产物哈希身份、staged 文件、flatten 产物、--verify 的拒绝行为),
  不 import 内部实现、不测实现细节。fixture 沿用 e2e 三件套 (源/模板/任务清单)
  的源/模板工作簿; 中文名源文件由测试在临时目录生成 (把既有 ASCII fixture 的字
  节复制为中文名 —— 复制发生在测试 harness 侧, 不发生在被测管线侧; 被测管线
  (workspace_init) 只做「零手工痕迹」的 ASCII 命名 staging)。

无 Office 时整体跳过 (flatten 依赖 officecli); 复用 checked-in fixture
tests/_fixtures/task_orchestration/e2e/ 的 parameter_book.xlsx / filling_template.xlsx。

Run with:
  python -m pytest table-fill/tests/test_workspace_init.py -q
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
FIX_E2E = (Path(__file__).resolve().parent / "_fixtures"
           / "task_orchestration" / "e2e")
WS_INIT = SCRIPTS / "workspace_init.py"

# 中文名源文件 (fixture 字节, 测试 harness 侧复制为中文名; 管线侧负责 ASCII staging)
CN_SOURCE = "参数手册.xlsx"


def run_py(workdir: Path, *args) -> subprocess.CompletedProcess:
    """CLI subprocess seam (与 test_prepare_evidence_output 同风格)。"""
    return subprocess.run(
        [sys.executable, "-X", "utf8", str(WS_INIT), "--workdir", ".", *args],
        cwd=str(workdir), capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=1500,
    )


@unittest.skipIf(shutil.which("officecli") is None, "officecli not on PATH")
class WorkspaceInitTests(unittest.TestCase):
    """--init / --verify / --inherit-from 的 CLIs 外显行为契约。"""

    def setUp(self):
        sys.path.insert(0, str(SCRIPTS))
        from _officecli import clean_residents  # noqa: PLC0415
        clean_residents()
        self.workdir = Path(tempfile.mkdtemp(prefix="ws_init_e2e_"))

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

    # ── helpers ────────────────────────────────────────────────────────

    def _copy_chinese_source(self, name: str = CN_SOURCE) -> Path:
        """把 e2e fixture 源书复制为中文名 (测试 harness 侧, 非管线侧)。"""
        dst = self.workdir / name
        shutil.copy2(FIX_E2E / "sources" / "parameter_book.xlsx", dst)
        return dst

    def _copy_template(self) -> Path:
        dst = self.workdir / "filling_template.xlsx"
        shutil.copy2(FIX_E2E / "templates" / "filling_template.xlsx", dst)
        return dst

    def _manifest(self) -> dict:
        return json.loads(
            (self.workdir / "workspace_manifest.json").read_text(encoding="utf-8"))

    def _init(self, *, chinese_source: bool = True) -> subprocess.CompletedProcess:
        """一次 --init: 中文源 → ASCII staged 名 + ASCII 模板 (role-neutral,
        无 --target — ADR 0018)。"""
        if chinese_source:
            self._copy_chinese_source()
            files_arg = f"{CN_SOURCE}|parameter_book.xlsx,filling_template.xlsx|filling_template.xlsx"
        else:
            shutil.copy2(FIX_E2E / "sources" / "parameter_book.xlsx",
                         self.workdir / "parameter_book.xlsx")
            files_arg = ("parameter_book.xlsx|parameter_book.xlsx,"
                         "filling_template.xlsx|filling_template.xlsx")
        self._copy_template()
        return run_py(
            self.workdir, "--init", "--files", files_arg,
            "--sheets", "parameter_book.xlsx:R32参数;filling_template.xlsx:Sheet1",
            "--task", "合成验收任务")

    # ── 验收 1: 中文名文件零手工复制/改名 → 一次 --init 完成 ──────────

    def test_chinese_source_init_zero_manual_intervention(self):
        """中文名源文件经一次 --init 完成: staged 为 ASCII 名、outline/flatten/
        digest/清单全落盘, 全程无手工复制/改名 (管线侧只做 ASCII staging)。"""
        proc = self._init(chinese_source=True)
        self.assertEqual(proc.returncode, 0, proc.stderr[-800:])
        out = json.loads(proc.stdout)
        self.assertEqual(out["status"], "PASS")
        self.assertEqual(out["code"], "WORKSPACE_INIT_DONE")

        # 中文源名被映射为 ASCII staged 名 (不进入文件系统路径)
        self.assertTrue((self.workdir / "parameter_book.xlsx").is_file(),
                        "中文源应被 staging 为 ASCII 名 parameter_book.xlsx")
        self.assertTrue((self.workdir / "filling_template.xlsx").is_file())

        manifest = self._manifest()
        # 清单记录的 input 保留原始中文源路径 + ASCII staged 身份 (可追溯)
        staged_map = {f["staged"]: f for f in manifest["inputs"]}
        self.assertIn("parameter_book.xlsx", staged_map)
        self.assertIn(CN_SOURCE, staged_map["parameter_book.xlsx"]["source"],
                      "清单应保留中文源文件的可追溯路径")
        # flatten 产物落盘
        self.assertTrue((self.workdir / "parameter_book_R32_flat.csv").is_file())
        self.assertTrue((self.workdir / "filling_template_Sheet1_flat.csv").is_file())
        # role-neutral: workspace manifest 无 target 角色 (ADR 0018 — 角色由
        # materialize_run 投影)
        self.assertNotIn("target", manifest,
                         "workspace_manifest 不得含 target 角色")

    # ── 验收 2: 清单记录全部输入与派生产物的哈希身份 ──────────────────

    def test_manifest_records_input_and_derived_hash_identity(self):
        """workspace_manifest.json 记录 inputs[].sha256 与 derived[].sha256
        (outline + 全部 flatten 产物), 且逐条与磁盘文件实际哈希一致。"""
        from _officecli import sha256_file  # noqa: PLC0415
        proc = self._init(chinese_source=True)
        self.assertEqual(proc.returncode, 0, proc.stderr[-800:])
        manifest = self._manifest()

        # 输入哈希身份 (全部输入)
        self.assertTrue(manifest["inputs"], "清单必须记录全部输入")
        for it in manifest["inputs"]:
            self.assertTrue(it.get("sha256"))
            self.assertEqual(it["sha256"], sha256_file(self.workdir / it["staged"]))

        # 派生产物哈希身份 (outline + flatten 产物)
        self.assertTrue(manifest["derived"], "清单必须记录派生产物哈希")
        kinds = {d["kind"] for d in manifest["derived"]}
        self.assertIn("outline", kinds)
        self.assertTrue(any(k.startswith("flatten/") for k in kinds))
        for d in manifest["derived"]:
            p = self.workdir / d["path"]
            self.assertTrue(p.is_file(), f"派生文件缺失: {d['path']}")
            self.assertEqual(d["sha256"], sha256_file(p),
                             f"派生哈希不一致: {d['path']}")

    # ── ADR 0018: 角色中立 — init 不产出 compile 视图, materialize 产出 ──

    def test_compile_facing_view_produced_by_materialize_not_init(self):
        """--init 产出 role-neutral canonical (无 prepare_manifest/target/
        二元指纹); materialize_run 投影 run-local prepare_manifest (schema v2,
        含 target + 派生 fingerprints); --inherit-from 同样只继承 role-neutral
        事实空间。"""
        proc = self._init(chinese_source=True)
        self.assertEqual(proc.returncode, 0, proc.stderr[-800:])
        ws = self._manifest()

        # canonical 角色中立: init 不写 prepare_manifest, 无 target/指纹
        self.assertFalse((self.workdir / "prepare_manifest.json").exists(),
                         "init 不产出 run-local prepare_manifest (ADR 0018)")
        self.assertNotIn("target", ws)
        self.assertNotIn("fingerprints", ws)
        self.assertTrue(all("kind" not in e for e in ws["flattened"]))
        # entry-level structure 指纹在场 (role-neutral)
        self.assertTrue(all(e.get("structure_sha256") for e in ws["flattened"]))

        # materialize 投影 run-local compile view (schema v2)
        sys.path.insert(0, str(SCRIPTS))
        from _fixtures.run_driver import materialize, target_entry_name
        materialize(self.workdir,
                    sources=target_entry_name("parameter_book", "R32参数"),
                    target=target_entry_name("filling_template", "Sheet1"))
        view = json.loads((self.workdir / "prepare_manifest.json").read_text(
            encoding="utf-8"))
        self.assertEqual(view["schema_version"], 2)
        self.assertEqual({f["staged"] for f in view["files"]},
                         {i["staged"] for i in ws["inputs"]})
        self.assertEqual(view["outlines"], ws["outlines"])
        self.assertEqual([e["name"] for e in view["flattened"]],
                         [e["name"] for e in ws["flattened"]])
        self.assertTrue(all("kind" not in e for e in view["flattened"]))
        # target 为完整展平条目 (run-local projection)
        self.assertEqual(view["target"]["name"], "filling_template_Sheet1")
        self.assertEqual(view["target"]["meta"],
                         "filling_template_Sheet1_meta.json")
        self.assertTrue(view["fingerprints"]["source_structure"])
        self.assertTrue(view["fingerprints"]["target_structure"])
        self.assertIn("row_gaps", view)
        self.assertIn("style_granularity", view)

    # ── 验收 3: 输入内容变更 → --verify 拒绝 + 重新初始化提示 ─────────

    def test_verify_rejects_content_change_with_reinit_hint(self):
        """staged 输入内容被改 → --verify exit 3 + '重新初始化' 提示
        (fail-closed, 绝不静默沿用旧结果)。"""
        proc = self._init(chinese_source=True)
        self.assertEqual(proc.returncode, 0, proc.stderr[-800:])
        # 先验证一致 (基线)
        proc = run_py(self.workdir, "--verify")
        self.assertEqual(proc.returncode, 0, proc.stderr[-800:])

        # 篡改一个 staged 输入 (内容漂移)
        p = self.workdir / "parameter_book.xlsx"
        data = bytearray(p.read_bytes())
        data[0] ^= 0xFF  # 翻转首字节, 改变哈希
        p.write_bytes(bytes(data))

        proc = run_py(self.workdir, "--verify")
        self.assertEqual(proc.returncode, 3, proc.stdout)
        err = json.loads(proc.stderr)
        self.assertEqual(err["status"], "ERROR")
        self.assertEqual(err["code"], "INPUT_DRIFT")
        self.assertIn("重新初始化", err["corrective_action"])
        self.assertIn("parameter_book.xlsx", err["message"])

    def test_verify_passes_when_unchanged(self):
        """输入未变 → --verify exit 0 + WORKSPACE_VERIFIED。"""
        proc = self._init(chinese_source=True)
        self.assertEqual(proc.returncode, 0, proc.stderr[-800:])
        proc = run_py(self.workdir, "--verify")
        self.assertEqual(proc.returncode, 0, proc.stderr[-800:])
        out = json.loads(proc.stdout)
        self.assertEqual(out["code"], "WORKSPACE_VERIFIED")

    # ── 验收 4 (继承语法): --inherit-from 显式继承, 无手工快照推演 ────

    def test_inherit_from_copies_fact_space_explicitly(self):
        """--inherit-from 从已 init 工作区显式继承 (staged/outline/flatten), 记
        inherited_from; 继承产物与来源逐字节一致 (哈希身份不变)。"""
        # 源工作区: 完整 --init
        proc = self._init(chinese_source=True)
        self.assertEqual(proc.returncode, 0, proc.stderr[-800:])
        src_manifest = self._manifest()

        # 目标工作区: 显式继承
        dst = Path(tempfile.mkdtemp(prefix="ws_inherit_dst_"))
        self.addCleanup(lambda: shutil.rmtree(dst, ignore_errors=True))
        try:
            proc = run_py(dst, "--inherit-from", str(self.workdir))
            self.assertEqual(proc.returncode, 0, proc.stderr[-800:])
            out = json.loads(proc.stdout)
            self.assertEqual(out["code"], "WORKSPACE_INHERITED")
            self.assertEqual(out["inherited_from"], str(self.workdir))

            dst_manifest = json.loads(
                (dst / "workspace_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(dst_manifest["inherited_from"], str(self.workdir))
            self.assertEqual(dst_manifest["inputs"], src_manifest["inputs"])
            self.assertEqual(dst_manifest["flattened"], src_manifest["flattened"])
            # role-neutral: 双方均无 target/二元指纹 (ADR 0018)
            self.assertNotIn("fingerprints", dst_manifest)
            self.assertNotIn("fingerprints", src_manifest)
            # staged 与 flatten 产物已复制且哈希一致
            for it in dst_manifest["inputs"]:
                self.assertTrue((dst / it["staged"]).is_file())
            self.assertTrue((dst / "parameter_book_R32_flat.csv").is_file())
            # 继承后 --verify 通过 (事实空间一致)
            proc = run_py(dst, "--verify")
            self.assertEqual(proc.returncode, 0, proc.stderr[-800:])
        finally:
            from _officecli import clean_residents, unlink_retry  # noqa: PLC0415
            import time
            clean_residents()
            time.sleep(1.0)
            for p in sorted(dst.rglob("*"), reverse=True):
                try:
                    if p.is_file():
                        unlink_retry(p)
                    else:
                        p.rmdir()
                except OSError:
                    pass

    def test_inherit_from_rejects_drifted_source(self):
        """来源工作区输入漂移 → --inherit-from exit 3 (fail-closed, 不静默继承)。"""
        proc = self._init(chinese_source=True)
        self.assertEqual(proc.returncode, 0, proc.stderr[-800:])
        # 篡改来源工作区 staged 输入
        p = self.workdir / "parameter_book.xlsx"
        data = bytearray(p.read_bytes())
        data[0] ^= 0xFF
        p.write_bytes(bytes(data))

        dst = Path(tempfile.mkdtemp(prefix="ws_inherit_drift_"))
        self.addCleanup(lambda: shutil.rmtree(dst, ignore_errors=True))
        proc = run_py(dst, "--inherit-from", str(self.workdir))
        self.assertEqual(proc.returncode, 3, proc.stdout)
        err = json.loads(proc.stderr)
        self.assertIn("INHERIT_INPUT_DRIFT", err["code"])




# ── T12: PPTX 源/目标经 workspace_init 入口 (role-neutral, ADR 0018) ──

PPTX_TEMPLATE = (Path(r"C:\\Users\\Administrator\\Desktop\\Shirley冷年汇报")
                 / "海外2027冷年推演模板-V3.pptx")
PPTX_TABLE = "slide[11]/table[@id=9]"


@unittest.skipIf(not PPTX_TEMPLATE.is_file(),
                 "海外2027冷年推演模板-V3.pptx not present")
@unittest.skipIf(shutil.which("officecli") is None, "officecli not on PATH")
class WorkspaceInitPptxTests(unittest.TestCase):
    """PPTX 经 role-neutral workspace_init 入口 (T12): pptx 表格直展, 条目
    形态与 prepare_run xlsx=False 对齐 (真实 digest 文件, 无 evidence);
    materialize_run 投影 run-local compiler view。"""

    def test_init_supports_pptx_flat_and_view(self):
        sys.path.insert(0, str(SCRIPTS))
        from _officecli import clean_residents, unlink_retry  # noqa: PLC0415
        workdir = Path(tempfile.mkdtemp(prefix="ws_pptx_"))
        try:
            proc = run_py(
                workdir, "--init", "--files", f"{PPTX_TEMPLATE}|template.pptx",
                "--sheets", f"template.pptx:{PPTX_TABLE}")
            self.assertEqual(proc.returncode, 0, proc.stderr[-800:])
            out = json.loads(proc.stdout)
            self.assertEqual(out["code"], "WORKSPACE_INIT_DONE")
            ws = json.loads((workdir / "workspace_manifest.json").read_text(
                encoding="utf-8"))
            entry = ws["flattened"][0]
            self.assertEqual(entry["sheet"], PPTX_TABLE)
            # pptx 条目形态: 真实 digest 文件 (非 deferred), 无 evidence
            self.assertNotEqual(entry["digest"], "deferred")
            self.assertTrue((workdir / entry["digest"]).is_file())
            self.assertNotIn("evidence", entry)
            self.assertTrue((workdir / entry["csv"]).is_file())
            # role-neutral: workspace 无 target / 二元指纹 (ADR 0018)
            self.assertNotIn("target", ws)
            self.assertNotIn("fingerprints", ws)
            # entry-level structure 指纹在场
            self.assertTrue(entry.get("structure_sha256"))
            # init 不写 prepare_manifest (run-local 视图由 materialize 投影)
            self.assertFalse((workdir / "prepare_manifest.json").exists())
        finally:
            clean_residents()
            time.sleep(1.0)
            for p in sorted(workdir.rglob("*"), reverse=True):
                try:
                    if p.is_file():
                        unlink_retry(p)
                    else:
                        p.rmdir()
                except OSError:
                    pass
            try:
                workdir.rmdir()
            except OSError:
                pass


if __name__ == "__main__":
    unittest.main()
