"""Execute-period backing for Q7 (precision: keep vs round4).

Compile-time contract tests prove `precision: keep` compiles; this test
proves the CROSS-LAYER trap the 2026-08-12 retrospective documented: a
15-digit cost value written with `precision: keep` into a normal-width
column fails at EXECUTE with text_overflow, while the documented
`transform: round4` pattern passes. The contract chapter's Q7 recommendation
order (round4 first, keep only for wide columns) is thus locked against
future regression at the layer where it actually broke.

Skips cleanly when officecli is unavailable (unit-only environments).
Run with:
  python -m unittest tests.test_precision_e2e -v
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"

LONG_DECIMAL = "168.715100569657"


def run_py(workdir: Path, script: str, *args) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-X", "utf8", str(SCRIPTS / script), *args],
        cwd=str(workdir), capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=900)


def build_fixtures(dirpath: Path) -> None:
    """Tiny template + source: 4-row template (col A default width, data row
    wrapText + fixed height like the real quote template), 3 source rows
    carrying a 15-digit cost value."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment
    tpl = Workbook()
    ws = tpl.active
    ws.title = "S"
    ws["A1"] = "Quotation"       # title row
    ws["A2"] = "Type | Model"    # header row
    ws["A3"] = ""                # data template row (empty — no residue)
    ws["A4"] = "Total"           # base_last_row
    ws.row_dimensions[3].height = 20   # 固定行高 (真实模板 customHeight)
    ws["A3"].alignment = Alignment(wrap_text=True)  # 换行 → 长数值触发 text overflow
    ws.column_dimensions["A"].width = 12  # round4 值 (8 字符) 可容纳; keep 值 (16 字符) 换行溢出
    tpl.save(dirpath / "template.xlsx")

    src = Workbook()
    ws = src.active
    ws.title = "SRC"
    for i in range(3):
        ws.cell(row=i + 1, column=1, value=float(LONG_DECIMAL))
    src.save(dirpath / "source.xlsx")


@unittest.skipIf(shutil.which("officecli") is None, "officecli not on PATH")
class PrecisionExecutionContractTests(unittest.TestCase):
    """Q7 执行期背书: keep → execute text_overflow 失败; round4 → 通过."""

    def setUp(self):
        sys.path.insert(0, str(SCRIPTS))
        from _officecli import clean_residents  # noqa: PLC0415
        self.workdir = Path(tempfile.mkdtemp(prefix="prec_e2e_"))
        build_fixtures(self.workdir)

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

    def _prepare_and_compile(self, column_extra: dict | None) -> Path:
        wd = self.workdir
        proc = run_py(wd, "prepare_run.py", "--workdir", ".",
                      "--files", "source.xlsx|source.xlsx,template.xlsx|template.xlsx",
                      "--outline")
        self.assertEqual(proc.returncode, 0, proc.stderr[-800:])
        proc = run_py(wd, "prepare_run.py", "--workdir", ".",
                      "--flatten", "--sheets", "source.xlsx:SRC;template.xlsx:S",
                      "--target", "template.xlsx")
        self.assertEqual(proc.returncode, 0, proc.stderr[-800:])
        manifest = json.loads((wd / "prepare_manifest.json").read_text(encoding="utf-8"))
        fp = manifest["fingerprints"]
        mapping = {"source": "A", "target": "A"}
        if column_extra:
            mapping.update(column_extra)
        spec = {
            "task": {"intent": "precision 执行期契约", "selected_mod": "NONE",
                     "selected_mod_revision": None},
            "inputs": {"sources": ["source.xlsx"], "target": "template.xlsx",
                       "source_sheets": [{"source": "source.xlsx", "sheets": ["SRC"]}],
                       "target_sheet": "S"},
            "fingerprints": {"source_structure": fp["source_structure"],
                             "target_structure": fp["target_structure"]},
            "mapping": {"targets": [{
                "sheet": "S", "base_last_row": 4,
                "clone_roles": [{"role": "data", "template_row": 3}],
                "rows": {"source": "source_SRC"},
                "columns": [mapping],
            }]},
            "decisions": [], "gaps": [],
            "lineage": [{"source": "source_SRC_flat.csv", "role": "primary",
                         "note": "精度契约 fixture"}],
            "validation": {"required_coverage": [], "required_empty": [],
                           "key_outputs": ["A5"]},
        }
        (wd / "fill_spec.yaml").write_text(
            yaml.safe_dump(spec, allow_unicode=True, sort_keys=False),
            encoding="utf-8")
        proc = run_py(wd, "compile_fill.py", "--spec", "fill_spec.yaml",
                      "--workdir", ".")
        self.assertEqual(proc.returncode, 0, proc.stderr[-1500:])
        return wd / "execution_plan.json"

    def _execute(self) -> subprocess.CompletedProcess:
        return run_py(self.workdir, "execute_batch.py", "--plan",
                      "execution_plan.json", "--template", "template.xlsx",
                      "--workdir", ".", "--round", "1", "--render", "html")

    def test_precision_keep_fails_at_execute_with_text_overflow(self):
        """跨层陷阱: keep 编译通过, 但 15 位值在常规列宽下 execute 期
        text_overflow → DRAFT_VERIFY_FAILED (defect_class=text_overflow)."""
        self._prepare_and_compile({"precision": "keep"})
        proc = self._execute()
        self.assertEqual(proc.returncode, 3, proc.stdout[-800:] + proc.stderr[-800:])
        failure = json.loads(
            (self.workdir / "_draft_failure.json").read_text(encoding="utf-8"))
        self.assertEqual(failure["code"], "DRAFT_VERIFY_FAILED")
        self.assertIn("text_overflow", failure["defect_classes"])

    def test_precision_round4_passes_at_execute(self):
        """文档推荐模式: transform: round4 → 编译 + 执行全链路通过, 无新增 issue."""
        self._prepare_and_compile({"transform": "round4"})
        proc = self._execute()
        self.assertEqual(proc.returncode, 0, proc.stdout[-800:] + proc.stderr[-800:])
        receipt = json.loads(
            (self.workdir / "draft_receipt.json").read_text(encoding="utf-8"))
        self.assertEqual(receipt["issue_delta"]["new_issues"], 0)
        self.assertEqual(receipt["readback"]["passed"], receipt["readback"]["total"])


if __name__ == "__main__":
    unittest.main()
