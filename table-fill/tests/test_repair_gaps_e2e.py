"""ADR 0021 — repair_row_gaps.py 是 input-repair utility (canonical re-init).

2026-08-13 埃及 FRESH 运行复盘: 目标 sheet 行号空洞 (row 22 缺失) →
TEMPLATE_ROW_GAP 编译拒绝 → 物化行 → staged 文件变 → 指纹变。旧实现:
repair 原地改 staged + 自动重跑 prepare_run --flatten 同步指纹 +
--patch-spec 改 spec → 破坏 immutable workspace 契约。

本契约 (ADR 0021 — Row-gap repair is input-version repair):
- repair 在**副本**上物化缺失行元素, 产出 repaired input snapshot;
- 原输入/当前 workspace 不被修改 (immutable);
- repaired snapshot 必须经 canonical workspace_init --init 重新进入
  (re-init), 然后 materialize_run 重投影 run view → 指纹必然变化;
- 无空洞时 NO_ROW_GAPS (no-op, 幂等);
- repair 不触碰 manifest/spec/编译 (零 resync/patch/next-state 推导)。

Smoke (脚本集成): 构造带行洞 fixture → repair → snapshot 无洞且原文件
未变 → re-init + materialize → 新指纹 != 旧指纹。officecli 不可用时跳过。

Run with:
  python -m unittest tests.test_repair_gaps_e2e -v
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"


def run_py(workdir: Path, script: str, *args) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-X", "utf8", str(SCRIPTS / script), *args],
        cwd=str(workdir), capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=900)


def build_gap_fixture(dirpath: Path) -> None:
    """4-row template (sheet TPL) with <row r=3> element removed from the
    sheet XML + 2-row source (sheet SRC). Sheet names are >1 char so officecli
    path parsing is unambiguous."""
    from openpyxl import Workbook
    src = Workbook()
    ws = src.active
    ws.title = "SRC"
    for i in range(2):
        ws.cell(row=i + 1, column=1, value=f"v{i}")
    src.save(dirpath / "source.xlsx")

    tpl = Workbook()
    ws = tpl.active
    ws.title = "TPL"
    ws["A1"] = "Title"
    ws["A2"] = "Header"
    ws["A3"] = "old data"
    ws["A4"] = "Total"
    p = dirpath / "template.xlsx"
    tpl.save(p)

    zf = zipfile.ZipFile(p)
    names = zf.namelist()
    sheet = next(n for n in names if re.match(r"xl/worksheets/sheet\d+\.xml$", n))
    xml = zf.read(sheet).decode("utf-8")
    new_xml = re.sub(r"<row r=\"3\"[^>]*>.*?</row>", "", xml, flags=re.DOTALL)
    assert new_xml != xml, "row r=3 element not found in fixture XML"
    entries = {n: zf.read(n) for n in names}
    zf.close()
    tmp = p.with_suffix(".tmp.xlsx")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zo:
        for n in names:
            zo.writestr(n, new_xml if n == sheet else entries[n])
    shutil.move(tmp, p)


@unittest.skipIf(shutil.which("officecli") is None, "officecli not on PATH")
class RepairGapsCanonicalReinitTests(unittest.TestCase):
    """ADR 0021 冒烟: repair 副本产出 snapshot; 原输入不变; re-init +
    materialize 后指纹必然变化; 无空洞幂等 no-op。"""

    def setUp(self):
        sys.path.insert(0, str(SCRIPTS))
        from _officecli import clean_residents  # noqa: PLC0415
        clean_residents()
        self.workdir = Path(tempfile.mkdtemp(prefix="gap_e2e_"))
        build_gap_fixture(self.workdir)

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

    def _workspace_init(self, files: str = "template.xlsx|template.xlsx") -> None:
        wd = self.workdir
        proc = run_py(wd, "workspace_init.py", "--workdir", ".",
                      "--init", "--files",
                      "source.xlsx|source.xlsx,template.xlsx|template.xlsx"
                      if "source.xlsx" not in files else files,
                      "--sheets", "source.xlsx:SRC;template.xlsx:TPL",
                      "--task", "row-gap canonical re-init e2e")
        self.assertEqual(proc.returncode, 0, proc.stderr[-800:])

    def _materialize(self) -> dict:
        from _fixtures.run_driver import materialize, target_entry_name
        materialize(self.workdir,
                    sources=target_entry_name("source", "SRC"),
                    target=target_entry_name("template", "TPL"))
        return json.loads(
            (self.workdir / "prepare_manifest.json").read_text(encoding="utf-8"))

    def _meta(self) -> dict:
        return json.loads(
            (self.workdir / "template_TPL_meta.json").read_text(encoding="utf-8"))

    def test_repair_produces_snapshot_input_untouched(self):
        """repair 在副本上修复: 产出 repaired snapshot, 原始 template.xlsx
        一个字都不改 (immutable workspace, ADR 0021)。"""
        self._manifest_before = None
        wd = self.workdir
        original_bytes = (wd / "template.xlsx").read_bytes()

        proc = run_py(wd, "repair_row_gaps.py", "--input", "template.xlsx",
                      "--sheet", "TPL", "--out", "template.repaired.xlsx")
        self.assertEqual(proc.returncode, 0, proc.stderr[-800:])
        out = json.loads(proc.stdout)
        self.assertEqual(out["code"], "ROW_GAPS_REPAIRED")
        self.assertEqual(out["repaired"], [3])

        # 原输入字节不变 (修复发生在副本上)
        self.assertEqual((wd / "template.xlsx").read_bytes(), original_bytes,
                         "repair 不得修改原输入 (immutable workspace)")
        # snapshot 存在
        self.assertTrue((wd / "template.repaired.xlsx").is_file())

        # snapshot 无空洞 (机械验证)
        import sys as _sys
        _sys.path.insert(0, str(SCRIPTS))
        import repair_row_gaps as rg  # noqa: PLC0415
        sheet_path = rg.sheet_target_path(wd / "template.repaired.xlsx", "TPL")
        self.assertEqual(rg.row_gaps_in(wd / "template.repaired.xlsx", sheet_path),
                         [])

    def test_canonical_reinit_yields_new_fingerprint(self):
        """repaired snapshot 经 canonical re-init: 行洞修复 = 物理结构变化 =
        target 指纹必然变化; re-materialize 投影出新指纹。"""
        from _fixtures.run_driver import (
            materialize, target_entry_name, workspace_init)
        wd = self.workdir

        # 1. 初始 workspace (带洞模板) → 指纹 A
        workspace_init(wd,
                       files="source.xlsx|source.xlsx,template.xlsx|template.xlsx",
                       sheets="source.xlsx:SRC;template.xlsx:TPL",
                       task="row-gap e2e round 1")
        materialize(wd,
                    sources=target_entry_name("source", "SRC"),
                    target=target_entry_name("template", "TPL"))
        fp_before = json.loads((wd / "prepare_manifest.json").read_text(
            encoding="utf-8"))["fingerprints"]["target_structure"]
        self.assertEqual(self._meta()["row_gaps"], [3])

        # 2. repair 副本 → snapshot
        proc = run_py(wd, "repair_row_gaps.py", "--input", "template.xlsx",
                      "--sheet", "TPL", "--out", "template.repaired.xlsx")
        self.assertEqual(proc.returncode, 0, proc.stderr[-800:])
        self.assertEqual(json.loads(proc.stdout)["code"], "ROW_GAPS_REPAIRED")

        # 3. canonical re-init: 以 repaired snapshot 为新输入 (ADR 0021:
        #    re-init 的 --files 换用 snapshot, 绝不原地改 staged 后局部续跑)
        wd2 = wd / "round2"
        wd2.mkdir()
        src2 = wd2 / "source.xlsx"
        shutil.copy2(wd / "source.xlsx", src2)
        repaired = wd2 / "template.xlsx"
        shutil.copy2(wd / "template.repaired.xlsx", repaired)
        proc = run_py(wd2, "workspace_init.py", "--workdir", ".",
                      "--init", "--files",
                      "source.xlsx|source.xlsx,template.xlsx|template.xlsx",
                      "--sheets", "source.xlsx:SRC;template.xlsx:TPL",
                      "--task", "row-gap e2e round 2 (repaired snapshot)")
        self.assertEqual(proc.returncode, 0, proc.stderr[-800:])
        materialize(wd2,
                    sources=target_entry_name("source", "SRC"),
                    target=target_entry_name("template", "TPL"))
        fp_after = json.loads((wd2 / "prepare_manifest.json").read_text(
            encoding="utf-8"))["fingerprints"]["target_structure"]

        # 物理结构变化 → 指纹必然变化 (机械事实); 无空洞
        self.assertNotEqual(fp_after, fp_before,
                            "行洞修复 = 物理结构变化 = 指纹必然变化")
        meta2 = json.loads((wd2 / "template_TPL_meta.json").read_text(
            encoding="utf-8"))
        self.assertEqual(meta2["row_gaps"], [])

    def test_repair_no_gaps_is_idempotent_noop(self):
        """无空洞时 NO_ROW_GAPS (no-op, 幂等)。先修复带洞 fixture 产出
        repaired snapshot, 再对 repaired snapshot 跑 → NO_ROW_GAPS。"""
        wd = self.workdir
        proc = run_py(wd, "repair_row_gaps.py", "--input", "template.xlsx",
                      "--sheet", "TPL", "--out", "template.repaired.xlsx")
        self.assertEqual(proc.returncode, 0, proc.stderr[-800:])
        self.assertEqual(json.loads(proc.stdout)["code"], "ROW_GAPS_REPAIRED")
        # 对已修复的 snapshot 再跑 → 无洞 no-op (幂等)
        proc = run_py(wd, "repair_row_gaps.py", "--input",
                      "template.repaired.xlsx", "--sheet", "TPL",
                      "--out", "template.repaired2.xlsx")
        self.assertEqual(proc.returncode, 0, proc.stderr[-800:])
        out = json.loads(proc.stdout)
        self.assertEqual(out["code"], "NO_ROW_GAPS")


if __name__ == "__main__":
    unittest.main()