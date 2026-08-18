"""MXP quotation end-to-end acceptance test (ADR 0007 position-model boundary).

Replays the real customer scenario the manual `run_mxp_quote.py` was built
for, entirely through the v2.5 Compiler: inplace placeholder region (18 rows,
13 products → trim 5), data-driven group merges rebuilt (A/F by product
family), absolute sets (A4/F4/A36→final A31), E-column numberformat, and
price consistency vs the v4 verified snapshot.

Skips cleanly when the reproduction package or officecli is unavailable
(unit-only environments). Run with:
  python -m unittest tests.test_mxp_e2e -v
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

REPRO_DIR = (Path(r"C:\Users\Administrator\Desktop\Shirley冷年汇报")
             / "MXP报价单_20260806_可观测复现包_20260807"
             / "MXP_Quotation_20260806_Handoff_20260807")
TEMPLATE_SNAPSHOT = REPRO_DIR / "inputs" / "quotation_template_snapshot_used.xlsx"
SOURCE_SNAPSHOT = REPRO_DIR / "inputs" / "source_snapshot_used.xlsx"
V4_SNAPSHOT = REPRO_DIR / "process" / "temp_snapshot" / "mxp_quote_output_v4.xlsx"

EXPECTED_PRICES = [158, 172, 272, 378, 790, 170, 265, 370, 775, 240, 340, 510, 595]
EXPECTED_MERGES = {"A7:A10", "A12:A14", "A16:A19",
                   "F7:F10", "F12:F14", "F16:F19"}
EXPECTED_NF = "$#,##0.00"

# Source 类别 → customer-facing Type (from the reproduction mapping table).
TYPE_LOOKUP = {
    "Xpro/R410A 3.2变频": {"type": "Xpro Series\nR410A Inverter Split AC\nCooling & Heating"},
    "R410A变频柜机冷暖": {"type": "R410A Inverter Floor-Standing AC\nCooling & Heating"},
    "TPRO/R32/3.22（白朗原机型降本机型）": {"type": "TPRO Series\nR32 Inverter Split AC\nCooling & Heating"},
    "R32变频柜机冷暖": {"type": "R32 Inverter Floor-Standing AC\nCooling & Heating"},
    "MXP一拖多ODU": {"type": "R410A Inverter Multi-Split ODU\nCooling & Heating\nDrives 2-5 IDUs"},
}
# 产品类别 → Btu capacity (ODU Drive 5 = 42000Btu per confirmed decision).
CAPACITY_LOOKUP = {
    "9KCH": {"capacity": "9000Btu"}, "12KCH": {"capacity": "12000Btu"},
    "18KCH": {"capacity": "18000Btu"}, "24KCH": {"capacity": "24000Btu"},
    "48KFH": {"capacity": "48000Btu"}, "T1 45KFH": {"capacity": "45000Btu"},
    "ODU 1 Drive 2": {"capacity": "18000Btu"},
    "ODU 1 Drive 3": {"capacity": "27000Btu"},
    "ODU 1 Drive 4": {"capacity": "32000Btu"},
    "新 ODU 1 Drive 5": {"capacity": "42000Btu"},
}


def run_py(workdir: Path, script: str, *args) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-X", "utf8", str(SCRIPTS / script), *args],
        cwd=str(workdir), capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=900)


def officecli_get(path: str, rng: str) -> dict:
    proc = subprocess.run(
        ["officecli", "get", path, rng, "--depth", "0", "--json"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=300)
    if proc.returncode != 0:
        raise RuntimeError(f"officecli get failed: {proc.stderr[-500:]}")
    return json.loads(proc.stdout)


def flatten_cells(data: dict) -> dict[str, dict]:
    out = {}
    for res in data.get("data", {}).get("results", []):
        for ch in res.get("children", []):
            out[ch["path"]] = ch
    return out


@unittest.skipIf(not (TEMPLATE_SNAPSHOT.is_file() and SOURCE_SNAPSHOT.is_file()),
                 "MXP reproduction package not present")
@unittest.skipIf(shutil.which("officecli") is None, "officecli not on PATH")
class MxpEndToEndTests(unittest.TestCase):
    """The ADR 0007 acceptance: the whole scenario the manual script expressed
    must be expressible as fill_spec.yaml and deliver a draft structurally
    equivalent to the v4 verified snapshot."""

    def test_full_compiler_pipeline_vs_v4_snapshot(self):
        sys.path.insert(0, str(SCRIPTS))
        from _officecli import clean_residents, unlink_retry
        import time
        workdir = Path(tempfile.mkdtemp(prefix="mxp_e2e_"))
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
        shutil.copy2(TEMPLATE_SNAPSHOT, workdir / "template.xlsx")
        shutil.copy2(SOURCE_SNAPSHOT, workdir / "source.xlsx")

        # Prepare: outline + flatten.
        proc = run_py(workdir, "prepare_run.py", "--workdir", ".",
                      "--files", "source.xlsx|source.xlsx,template.xlsx|template.xlsx",
                      "--outline")
        self.assertEqual(proc.returncode, 0, proc.stderr[-800:])
        proc = run_py(workdir, "prepare_run.py", "--workdir", ".",
                      "--flatten",
                      "--sheets", "source.xlsx:17_MXP;template.xlsx:ATLAS Quotation",
                      "--target", "template.xlsx")
        self.assertEqual(proc.returncode, 0, proc.stderr[-800:])

        manifest = json.loads((workdir / "prepare_manifest.json").read_text(encoding="utf-8"))
        fp = manifest["fingerprints"]

        # Lookup fixtures (business decisions from the mapping table).
        json.dump(TYPE_LOOKUP, open(workdir / "lookups_type.json", "w", encoding="utf-8"),
                  ensure_ascii=False)
        json.dump(CAPACITY_LOOKUP, open(workdir / "lookups_capacity.json", "w",
                                        encoding="utf-8"), ensure_ascii=False)

        spec = {
            "task": {"intent": "MXP 13-机报价单：inplace 占位区 + 分组合并 + 绝对写",
                     "selected_mod": "NONE", "selected_mod_revision": None},
            "inputs": {"sources": ["source.xlsx"], "target": "template.xlsx",
                       "source_sheets": [{"source": "source.xlsx",
                                          "sheets": ["17_MXP"]}],
                       "target_sheet": "ATLAS Quotation"},
            "fingerprints": {"source_structure": fp["source_structure"],
                             "target_structure": fp["target_structure"]},
            "mapping": {
                "transforms": [
                    {"name": "rename_drive5", "function": "regex_replace",
                     "pattern": "^新 ODU 1 Drive 5$",
                     "replacement": "ODU 1 Drive 5 (New Design)"},
                    {"name": "add_voltage", "function": "regex_replace",
                     "pattern": "^(.*)$",
                     "replacement": r"\1\n(220-240V,1N,50Hz)"},
                ],
                "targets": [{
                    "sheet": "ATLAS Quotation",
                    "base_last_row": 40,
                    "clone_roles": [
                        {"role": "data", "mode": "inplace", "start_row": 7,
                         "capacity": 18, "template_row": 8},
                    ],
                    "rows": {"source": "source_17_MXP", "selectors": [
                        {"column": "A", "not_value": ""},
                        {"column": "A", "not_value": "类别"},
                        {"column": "L", "not_value": "0"},
                        {"column": "L", "not_value": ""},
                    ]},
                    "columns": [
                        {"target": "A",
                         "lookup": {"name": "type_translation", "field": "type",
                                    "missing": "error"}},
                        {"source": "D", "target": "B", "fallback": "B",
                         "transforms": ["rename_drive5", "add_voltage"]},
                        {"target": "C",
                         "lookup": {"name": "capacity", "field": "capacity",
                                    "missing": "error"}},
                        {"source": "Z", "target": "D"},
                        {"source": "L", "target": "E",
                         "props": {"numberformat": "$#,##0.00"}},
                    ],
                    "lookups": [
                        {"name": "type_translation", "from": "lookups_type.json",
                         "key_column": "A", "fields": ["type"], "missing": "error"},
                        {"name": "capacity", "from": "lookups_capacity.json",
                         "key_column": "B", "fields": ["capacity"], "missing": "error"},
                    ],
                    "group_merges": [
                        {"col": "A", "group_by": "A", "style": "label"},
                        {"col": "F", "group_by": "A", "label": ""},
                    ],
                    "sets": [
                        {"path": "A4", "value": "To Messrs: MXP"},
                        {"path": "F4", "value": "Date of issue: 2026-08-06"},
                        {"path": "A36",
                         "value": "* The above goods will be shipped to any port of Algeria."},
                    ],
                }],
            },
            "decisions": ["仅纳入报价 > 0 的 13 个机型；排除 TPRO 9K/12K/9KCH（源行 18-20）",
                          "Model 优先工厂型号（D），空则回退产品描述（B）；ODU 1 Drive 5 重命名"],
            "gaps": ["新 ODU 1 Drive 5 容量 42000Btu 为按系列推定（复现包确认口径）"],
            "lineage": [{"source": "source_17_MXP_flat.csv", "role": "primary",
                         "note": "每个匹配源行恰好写入一个占位行"}],
            "validation": {"required_coverage": [
                {"source": "source_17_MXP_flat.csv",
                 "rows": [13, 14, 15, 16, 17, 21, 22, 23, 24, 25, 26, 27, 28]}],
                "required_empty": [],
                "key_outputs": ["A4", "A7", "A19", "A36", "E7", "E19"]},
        }
        spec_path = workdir / "fill_spec.yaml"
        spec_path.write_text(yaml.safe_dump(spec, allow_unicode=True, sort_keys=False),
                             encoding="utf-8")

        # Compile.
        proc = run_py(workdir, "compile_fill.py", "--spec", "fill_spec.yaml",
                      "--workdir", ".")
        self.assertEqual(proc.returncode, 0, proc.stderr[-1500:])
        plan = json.loads((workdir / "execution_plan.json").read_text(encoding="utf-8"))
        self.assertEqual(plan["schema_version"], "2.5")
        self.assertEqual(plan["expected_final_row_count"], 35)
        self.assertEqual(plan["structural_deltas"]["inplace_trim"], 5)
        self.assertEqual([g["expected_merges"] for g in plan["group_boundaries"]],
                         [["A7:A10", "A12:A14", "A16:A19"],
                          ["F7:F10", "F12:F14", "F16:F19"]])
        self.assertEqual([s["path"] for s in plan["sets"]],
                         ["/ATLAS Quotation/A4", "/ATLAS Quotation/F4",
                          "/ATLAS Quotation/A31"])

        # Execute (html render QA — text-only model fallback; the visual
        # verdict is the agent's, the artifact + structural receipt is the
        # machine's).
        proc = run_py(workdir, "execute_batch.py", "--plan", "execution_plan.json",
                      "--template", "template.xlsx", "--workdir", ".",
                      "--round", "1", "--render", "html")
        self.assertEqual(proc.returncode, 0, proc.stderr[-1500:])
        receipt = json.loads((workdir / "draft_receipt.json").read_text(encoding="utf-8"))
        self.assertEqual(receipt["structural"]["pass"], True)
        self.assertEqual(receipt["structural"]["actual_final_row_count"], 35)
        self.assertEqual(receipt["readback"]["passed"], receipt["readback"]["total"])
        self.assertEqual(receipt["render_qa"]["status"], "produced")
        self.assertTrue((workdir / "render_qa.html").is_file())

        # Structural equivalence vs the v4 verified snapshot.
        draft = str(workdir / "validated_draft.xlsx")
        cells = flatten_cells(officecli_get(draft, "/ATLAS Quotation/A1:F35"))
        import re as _re
        merged = set()
        for p, c in cells.items():
            if not _re.search(r"/[A-F]\d+$", p):
                continue
            mg = (c.get("format") or {}).get("merge")
            if mg:
                merged.add(mg)
        self.assertTrue(EXPECTED_MERGES <= merged, f"missing {EXPECTED_MERGES - merged}")
        # no residue merges inside the data region (rows 7-19)
        region = {m for m in merged
                  if _re.fullmatch(r"[AF](?:7|8|9|1[0-9])", m.split(":")[0])}
        self.assertEqual(region, EXPECTED_MERGES)

        def text(col, row):
            c = cells.get(f"/ATLAS Quotation/{col}{row}")
            return (c or {}).get("text", "")

        self.assertEqual(text("A", 4), "To Messrs: MXP")
        self.assertEqual(text("F", 4), "Date of issue: 2026-08-06")
        self.assertEqual(text("A", 31), "* The above goods will be shipped to any port of Algeria.")
        self.assertEqual(text("A", 20), "Total")  # Total shifted up by the trim

        # Price consistency (sum 5035 USD cross-check from the mapping table).
        prices = [int(text("E", r)) for r in range(7, 20)]
        self.assertEqual(prices, EXPECTED_PRICES)
        self.assertEqual(sum(prices), 5035)

        # E-column numberformat on every price cell.
        for r in range(7, 20):
            c = cells.get(f"/ATLAS Quotation/E{r}") or {}
            self.assertEqual((c.get("format") or {}).get("numberformat"),
                             EXPECTED_NF, f"E{r}")

        # Source-derived model strings (the snapshot is the data truth;
        # rows 11/15 differ from v4's hard-coded strings because the source
        # file was modified after the original task — documented in the
        # reproduction package HANDOFF §4).
        self.assertEqual(text("B", 7), "KFR-25GW/YXABp(E)(082203)\n(220-240V,1N,50Hz)")
        self.assertEqual(text("B", 19), "ODU 1 Drive 5 (New Design)\n(220-240V,1N,50Hz)")
        self.assertEqual(text("C", 19), "42000Btu")


if __name__ == "__main__":
    unittest.main()
