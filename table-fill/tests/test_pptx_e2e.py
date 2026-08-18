"""Real PPTX end-to-end test (audit issue 06): compile → execute → readback.

The first true pptx E2E in the suite: a REAL pptx template (海外2027冷年推演
模板-V3.pptx, slide 11's normal pptx table `/slide[11]/table[@id=9]` — the
OLE tables on S13 are embedded Excel objects, NOT targetable tables) is
staged, flattened, filled through the public Compiler CLI + executor, and
read back with officecli.

Data semantics are mechanism-only: the table is filled from its OWN flattened
rows (self-fill with a 序号 selector), because the E2E's job is to prove the
pptx compile→execute→readback machinery on a real file, not a business
scenario. The unsupported-declaration fail-closed behavior is covered by
compile-level tests (CapabilityMappingContractTests); this test covers the
supported core: column value fills into pre-built tr rows.

Skips cleanly when the template or officecli is unavailable (unit-only
environments) — never fakes a pass. Run with:
  python -m unittest tests.test_pptx_e2e -v
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

import yaml

SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"

TEMPLATE = (Path(r"C:\Users\Administrator\Desktop\Shirley冷年汇报")
            / "海外2027冷年推演模板-V3.pptx")
TABLE = "slide[11]/table[@id=9]"
TABLE_ROWS = 25  # tr[1] header / tr[2] sub-header / tr[3..25] data+totals

# Selector: 序号 column (A) holds digits on the two data blocks
# (tr[3..12] 增量重要客户 1-10, tr[14..23] 减量重要客户 1-10); header rows,
# 小计/总计 rows and blanks never match.
MATCHED_ORIGS = list(range(3, 13)) + list(range(14, 24))  # 20 rows


def run_py(workdir: Path, script: str, *args) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-X", "utf8", str(SCRIPTS / script), *args],
        cwd=str(workdir), capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=900)


def officecli_get(path: str, table: str) -> dict:
    proc = subprocess.run(
        ["officecli", "get", path, f"/{table}", "--depth", "2", "--json"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=300)
    if proc.returncode != 0:
        raise RuntimeError(f"officecli get failed: {proc.stderr[-500:]}")
    return json.loads(proc.stdout)


def table_texts(data: dict) -> dict[str, str]:
    """{/slide[N]/table[@id=M]/tr[X]/tc[Y]: text} from a depth-2 get."""
    out = {}
    for res in data.get("data", {}).get("results", []):
        for tr in res.get("children", []) or []:
            for tc in tr.get("children", []) or []:
                out[tc["path"]] = tc.get("text") or ""
    return out


@unittest.skipIf(not TEMPLATE.is_file(),
                 "海外2027冷年推演模板-V3.pptx not present")
@unittest.skipIf(shutil.which("officecli") is None, "officecli not on PATH")
class PptxEndToEndTests(unittest.TestCase):
    """Real-pptx compile → execute → readback through the public CLI seam."""

    def test_pptx_value_fill_compile_execute_readback(self):
        sys.path.insert(0, str(SCRIPTS))
        from _officecli import clean_residents, unlink_retry
        workdir = Path(tempfile.mkdtemp(prefix="pptx_e2e_"))
        try:
            self._run_pipeline(workdir)
        finally:
            clean_residents()  # release officecli file locks before cleanup
            time.sleep(1.0)    # Windows releases handles asynchronously
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

    def _run_pipeline(self, workdir: Path):
        # 1. Prepare: outline + flatten the real table.
        proc = run_py(workdir, "prepare_run.py", "--workdir", ".",
                      "--files", f"{TEMPLATE}|template.pptx", "--outline")
        self.assertEqual(proc.returncode, 0, proc.stderr[-800:])
        proc = run_py(workdir, "prepare_run.py", "--workdir", ".",
                      "--flatten", "--sheets", f"template.pptx:{TABLE}",
                      "--target", "template.pptx")
        self.assertEqual(proc.returncode, 0, proc.stderr[-800:])

        manifest = json.loads((workdir / "prepare_manifest.json").read_text(
            encoding="utf-8"))
        fp = manifest["fingerprints"]
        src_name = manifest["target"]["name"]
        self.assertEqual(manifest["target"]["sheet"], TABLE)

        # 2. FillSpec — mechanism-only self-fill of the same table.
        spec = {
            "task": {"intent": "pptx E2E 机制验证: 真实模板表自填 (数据无语义)",
                     "selected_mod": "NONE", "selected_mod_revision": None},
            "inputs": {"sources": ["template.pptx"], "target": "template.pptx",
                       "source_sheets": [{"source": "template.pptx",
                                          "sheets": [TABLE]}],
                       "target_sheet": TABLE, "platform": "pptx"},
            "fingerprints": {"source_structure": fp["source_structure"],
                             "target_structure": fp["target_structure"]},
            "mapping": {"targets": [{
                "sheet": TABLE,
                "first_data_row": 3,
                "rows": {"source": src_name, "selectors": [
                    {"column": "A", "pattern": "[0-9]*"}]},
                "columns": [
                    {"source": "B", "target": "C"},   # 模块 → 客户名称
                    {"source": "A", "target": "D"},   # 序号 → 客户类型
                ],
            }]},
            "decisions": ["E2E 机制验证: 源=模板自身表格 (自填), 数据无语义"],
            "gaps": [],
            "lineage": [{"source": f"{src_name}_flat.csv", "role": "primary",
                         "note": "机制测试: 匹配行按序填入预建 tr 行"}],
            "validation": {"required_coverage": [
                {"source": src_name, "rows": MATCHED_ORIGS}],
                "required_empty": [],
                "key_outputs": [f"/{TABLE}/tr[3]/tc[3]",
                                f"/{TABLE}/tr[22]/tc[4]"]},
        }
        (workdir / "fill_spec.yaml").write_text(
            yaml.safe_dump(spec, allow_unicode=True, sort_keys=False),
            encoding="utf-8")

        # 3. Compile.
        proc = run_py(workdir, "compile_fill.py", "--spec", "fill_spec.yaml",
                      "--workdir", ".")
        self.assertEqual(proc.returncode, 0, proc.stderr[-1500:])
        plan = json.loads((workdir / "execution_plan.json").read_text(
            encoding="utf-8"))
        self.assertEqual(plan["platform"], "pptx")
        self.assertEqual(plan["source_coverage"][0]["matched"], 20)
        self.assertEqual(len(plan["readback"]), 40)  # 20 行 × 2 列映射
        for op in plan["operations"]:
            self.assertEqual(op["command"], "set")
            self.assertIn(f"/{TABLE}/tr[", op["path"])
            self.assertEqual(set(op["props"]), {"text"})

        # 4. Execute (pptx: no render QA — mechanism is value fills; readback
        #    is the machine gate).
        proc = run_py(workdir, "execute_batch.py", "--plan", "execution_plan.json",
                      "--template", "template.pptx", "--workdir", ".",
                      "--round", "1", "--render", "none")
        self.assertEqual(proc.returncode, 0, proc.stderr[-1500:])
        receipt = json.loads((workdir / "draft_receipt.json").read_text(
            encoding="utf-8"))
        self.assertEqual(receipt["readback"]["passed"],
                         receipt["readback"]["total"])
        self.assertEqual(receipt["readback"]["total"], 40)

        # 5. Read back the REAL draft with officecli (independent of the
        #    executor's own readback) — values landed in the right tr/tc.
        draft = str(workdir / "validated_draft.pptx")
        cells = table_texts(officecli_get(draft, TABLE))
        P = f"/{TABLE}"
        self.assertEqual(cells[f"{P}/tr[3]/tc[3]"], "增量重要客户")  # 源 B
        self.assertEqual(cells[f"{P}/tr[3]/tc[4]"], "1")            # 源 A
        self.assertEqual(cells[f"{P}/tr[12]/tc[4]"], "10")          # 增量块末行
        self.assertEqual(cells[f"{P}/tr[13]/tc[3]"], "减量重要客户")  # 第 11 匹配行
        self.assertEqual(cells[f"{P}/tr[22]/tc[4]"], "10")          # 减量块末行
        # Non-data rows are untouched (no clobbering outside the fill range).
        self.assertEqual(cells[f"{P}/tr[1]/tc[1]"], "序号")
        self.assertEqual(cells[f"{P}/tr[13]/tc[1]"], "\u3000")      # 小计行
        self.assertEqual(cells[f"{P}/tr[25]/tc[7]"], "xx")          # 总计行


if __name__ == "__main__":
    unittest.main()
