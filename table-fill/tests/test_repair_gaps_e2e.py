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


# ══════════════════════════════════════════════════════════════════════════
# One-pass init + namespace-robust readers (recorded 2026-09)
#
# 两个既存缺陷: ① 行号空洞检测前缀脆性 (`<row\b` 在 officecli 写回的
# `<x:row>` 上命中 0 行 → 对仍有空洞的 sheet 答 NO_ROW_GAPS) 且 `--sheet`
# 被无视 ("第一个 sheet 通吃"); ② init → repair → 再 init 的两趟形状纯属
# 开销 — 真不变量是 "manifest 哈希 == 磁盘字节" 且 "既有事实空间不被回改",
# 不是 "修复必须是第二个进程"。修复放在 staging 之后、任何哈希/outline/
# 展平/指纹被记录之前, 三个不变量同时成立, 一趟出事实空间。
# 安全闸门: 源路径 == 暂存路径时拒绝原地修复 (那等于改用户原件)。
# ══════════════════════════════════════════════════════════════════════════


def build_two_sheet_gap_fixture(path: Path) -> None:
    """Two sheets, gaps in BOTH, in sheets whose names differ in length so a
    "first sheet wins" bug cannot masquerade as working."""
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "AAA"
    for i in range(1, 6):
        ws.cell(row=i, column=1, value=f"a{i}")
    ws2 = wb.create_sheet("BBBB")
    for i in range(1, 6):
        ws2.cell(row=i, column=1, value=f"b{i}")
    wb.save(path)

    # Remove <row r=3> from AAA and <row r=4> from BBB.
    zf = zipfile.ZipFile(path)
    names = zf.namelist()
    entries = {n: zf.read(n) for n in names}
    zf.close()
    parts = sorted(n for n in names
                   if re.match(r"xl/worksheets/sheet\d+\.xml$", n))
    aaa_xml = entries[parts[0]].decode("utf-8")
    bbb_xml = entries[parts[1]].decode("utf-8")
    new_aaa = re.sub(r"<row r=\"3\"[^>]*>.*?</row>", "", aaa_xml, flags=re.DOTALL)
    new_bbb = re.sub(r"<row r=\"4\"[^>]*>.*?</row>", "", bbb_xml, flags=re.DOTALL)
    assert new_aaa != aaa_xml and new_bbb != bbb_xml, "fixture row removal failed"
    entries[parts[0]] = new_aaa.encode("utf-8")
    entries[parts[1]] = new_bbb.encode("utf-8")
    tmp = path.with_suffix(".tmp.xlsx")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zo:
        for n in names:
            zo.writestr(n, entries[n])
    shutil.move(tmp, path)


class SheetXmlReaderContractTests(unittest.TestCase):
    """The shared reader must be prefix-agnostic and name-addressed."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="sx_"))
        self.book = self.tmp / "book.xlsx"
        build_two_sheet_gap_fixture(self.book)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_resolves_sheet_by_name_not_first_sheet(self):
        import flatten_table as ft
        self.assertEqual(ft.sheet_names(self.book), ["AAA", "BBBB"])
        p1 = ft.sheet_rel_path(self.book, "AAA")
        p2 = ft.sheet_rel_path(self.book, "BBBB")
        self.assertNotEqual(p1, p2, "按名解析: 两个 sheet 必须解析到不同部件")

    def test_unknown_sheet_fails_closed(self):
        import flatten_table as ft
        with self.assertRaises(ft.SheetNotFound):
            ft.sheet_rel_path(self.book, "nope")

    def test_gaps_detected_on_prefixed_and_bare_xml(self):
        """Bare <row> AND officecli-rewritten <x:row> must both be read.

        This is the regression: a `<row\\b` regex reads 0 rows from a prefixed
        part and silently reports "no gaps".
        """
        import flatten_table as ft
        self.assertEqual(ft.row_gaps(self.book, "AAA"), [3])
        self.assertEqual(ft.row_gaps(self.book, "BBBB"), [4])

        bare = ('<worksheet><sheetData>'
                '<row r="1"/><row r="2"/><row r="4"/>'
                '</sheetData></worksheet>')
        prefixed = ('<x:worksheet><x:sheetData>'
                    '<x:row r="1"/><x:row r="2"/><x:row r="4"/>'
                    '</x:sheetData></x:worksheet>')
        self.assertEqual(ft.row_gaps_in_xml(bare), [3])
        self.assertEqual(ft.row_gaps_in_xml(prefixed), [3],
                         "officecli 写回的 x: 前缀部件必须同样能读出行洞")


@unittest.skipIf(shutil.which("officecli") is None, "officecli not on PATH")
class OnePassInitRepairTests(unittest.TestCase):
    """init repairs gaps inside staging — one pass, original untouched."""

    def setUp(self):
        from _officecli import clean_residents
        clean_residents()
        self.tmp = Path(tempfile.mkdtemp(prefix="onepass_"))
        self.wd = self.tmp / "wd"
        self.wd.mkdir()
        # Source lives OUTSIDE the workdir (the ordinary, real-world layout).
        self.src = self.tmp / "gapped.xlsx"
        build_two_sheet_gap_fixture(self.src)
        self.src_bytes = self.src.read_bytes()

    def tearDown(self):
        from _officecli import clean_residents, unlink_retry
        import time
        clean_residents()
        time.sleep(1.0)
        for p in sorted(self.tmp.rglob("*"), reverse=True):
            try:
                unlink_retry(p) if p.is_file() else p.rmdir()
            except OSError:
                pass
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _init(self, *extra):
        return run_py(self.wd, "workspace_init.py", "--workdir", ".",
                      "--init", "--files", f"{self.src}|gz.xlsx",
                      "--sheets", "gz.xlsx:AAA,BBBB", *extra)

    def test_init_repairs_in_one_pass_original_untouched(self):
        import flatten_table as ft
        proc = self._init()
        self.assertEqual(proc.returncode, 0, (proc.stderr or proc.stdout)[-900:])
        out = json.loads(proc.stdout)

        # One pass: the gaps are already gone, and reported as repaired.
        rep = out["row_gaps_repaired"]
        self.assertTrue(rep, "init 必须报告已修复的行洞")
        self.assertEqual(out["row_gaps_deferred"], [])
        staged = self.wd / "gz.xlsx"
        self.assertEqual(ft.all_row_gaps(staged), {},
                         "staging 内一次性修复后不应再有行洞")

        # The caller's original file is NOT touched.
        self.assertEqual(self.src.read_bytes(), self.src_bytes,
                         "自动修复只允许改暂存副本, 原件必须逐字节不变")

        # manifest records POST-repair bytes (hash == disk).
        m = json.loads((self.wd / "workspace_manifest.json").read_text("utf-8"))
        self.assertEqual(m["repairs"][0]["staged"], "gz.xlsx")
        self.assertEqual(
            sorted(r["sheet"] for r in m["repairs"][0]["repaired"]),
            ["AAA", "BBBB"], "两个 sheet 的行洞都必须在一次 init 内修完")
        import hashlib
        self.assertEqual(m["inputs"][0]["sha256"],
                         hashlib.sha256(staged.read_bytes()).hexdigest())

        # --verify agrees (fact space coherent).
        v = run_py(self.wd, "workspace_init.py", "--workdir", ".", "--verify")
        self.assertEqual(v.returncode, 0, (v.stderr or v.stdout)[-600:])

    def test_no_repair_flag_defers_and_records(self):
        proc = self._init("--no-repair")
        self.assertEqual(proc.returncode, 0, (proc.stderr or proc.stdout)[-900:])
        out = json.loads(proc.stdout)
        self.assertEqual(out["row_gaps_repaired"], [])
        deferred = out["row_gaps_deferred"]
        self.assertTrue(deferred, "--no-repair 必须把行洞记录为 deferred 而非静默")
        self.assertEqual(
            sorted(x["sheet"] for x in deferred[0]["sheets"]), ["AAA", "BBBB"])
        self.assertEqual({x["reason"] for x in deferred[0]["sheets"]},
                         {"repair-disabled"})

    def test_source_is_staged_file_is_refused(self):
        """When the source path IS the staged path, repairing in place would
        mutate the caller's file — must be deferred, never done."""
        import flatten_table as ft
        # Stage by pointing --files at a file already inside the workdir.
        local = self.wd / "local.xlsx"
        shutil.copy2(self.src, local)
        before = local.read_bytes()
        proc = run_py(self.wd, "workspace_init.py", "--workdir", ".",
                      "--init", "--files", "local.xlsx|local.xlsx",
                      "--sheets", "local.xlsx:AAA,BBBB")
        self.assertEqual(proc.returncode, 0, (proc.stderr or proc.stdout)[-900:])
        out = json.loads(proc.stdout)
        self.assertEqual(out["row_gaps_repaired"], [],
                         "源路径 == 暂存路径时不得原地修复")
        reasons = {x["reason"] for d in out["row_gaps_deferred"]
                   for x in d["sheets"]}
        self.assertIn("source-is-staged-file", reasons)
        self.assertEqual(local.read_bytes(), before,
                         "源==暂存时原件必须逐字节不变")
        self.assertNotEqual(ft.all_row_gaps(local), {},
                            "拒绝修复后行洞应保持原样 (未被静默改动)")


@unittest.skipIf(shutil.which("officecli") is None, "officecli not on PATH")
class RepairCliContractTests(unittest.TestCase):
    """--sheet / --all / unknown-sheet semantics of the standalone utility."""

    def setUp(self):
        from _officecli import clean_residents
        clean_residents()
        self.tmp = Path(tempfile.mkdtemp(prefix="repair_cli_"))
        self.book = self.tmp / "gapped.xlsx"
        build_two_sheet_gap_fixture(self.book)

    def tearDown(self):
        from _officecli import clean_residents, unlink_retry
        import time
        clean_residents()
        time.sleep(1.0)
        for p in sorted(self.tmp.rglob("*"), reverse=True):
            try:
                unlink_retry(p) if p.is_file() else p.rmdir()
            except OSError:
                pass
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _repair(self, *args):
        return run_py(self.tmp, "repair_row_gaps.py", "--input", "gapped.xlsx", *args)

    def test_sheet_argument_targets_that_sheet(self):
        """Repairing the SECOND sheet must actually repair the second sheet."""
        import flatten_table as ft
        proc = self._repair("--sheet", "BBBB", "--out", "out.xlsx")
        self.assertEqual(proc.returncode, 0, proc.stderr[-600:])
        out = json.loads(proc.stdout)
        self.assertEqual(out["code"], "ROW_GAPS_REPAIRED")
        self.assertEqual([s["sheet"] for s in out["sheets"]], ["BBBB"])
        gaps = ft.all_row_gaps(self.tmp / "out.xlsx")
        self.assertNotIn("BBBB", gaps, "--sheet BBBB 必须真的修 BBBB")
        self.assertEqual(gaps.get("AAA"), [3], "未指定的 sheet 不应被改动")

    def test_all_repairs_every_sheet_in_one_snapshot(self):
        import flatten_table as ft
        proc = self._repair("--all", "--out", "out.xlsx")
        self.assertEqual(proc.returncode, 0, proc.stderr[-600:])
        out = json.loads(proc.stdout)
        self.assertEqual(sorted(s["sheet"] for s in out["sheets"]), ["AAA", "BBBB"])
        self.assertEqual(ft.all_row_gaps(self.tmp / "out.xlsx"), {})

    def test_unknown_sheet_fails_closed(self):
        proc = self._repair("--sheet", "nope", "--out", "out.xlsx")
        self.assertNotEqual(proc.returncode, 0,
                            "未知 sheet 名必须 fail-closed, 不得静默产出无改动副本")

    def test_original_input_untouched(self):
        before = self.book.read_bytes()
        self._repair("--all", "--out", "out.xlsx")
        self.assertEqual(self.book.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
