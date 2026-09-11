from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

import structure_digest  # noqa: E402


def _make_fixture(tmp: Path) -> tuple[Path, Path]:
    """最小 meta.json + 展平 CSV fixture (不依赖 office 二进制).

    meta 覆盖 pre-mod 视图所有 KEEP/DROP 分支:
      - dimensions: 24 列 (命中 _digest_has_24col 正则)
      - row_gaps: 存在空洞 → 触发行号空洞行
      - style_granularity: 带样式占位段 + 克隆源行 (带样例值, 应被剥离)
      - header_band: 表头带 (CSV 提供表头名)
      - blocks: 带 title 的数据块 (块标题应被剔除)
      - merged_ranges: 合并区
      - merge_anchors / formulas / column_numfmt / columns: 全量 digest 才有的内容"""
    meta = {
        "file": "unused/fixture.xlsx",
        "sheet": "Sheet1",
        "dimensions": {"rows": 30, "cols": 24, "formulas": 5, "errorCells": 0,
                       "oleObjects": 0, "charts": 0, "tables": 0},
        "row_gaps": [12, 13],
        "style_granularity": {
            "placeholder_segments": [
                {"start": 25, "end": 30, "styled": True, "sample": "A25"},
            ],
            "clone_source_rows": [
                {"block": 1, "title": {"row": 1, "styled": True},
                 "header": {"row": 2, "styled": True},
                 "data": {"row": 3, "styled": False}},
            ],
        },
        "header_band": {"header_rows": [1, 2], "data_start_row": 3},
        "blocks": [
            {"id": 1, "start": 3, "end": 10, "title": "绝密块标题-不应出现", "score": 0.9},
            {"id": 2, "start": 14, "end": 20, "title": "第二块标题", "score": 0.7},
        ],
        "merged_ranges": ["A1:B2", "C3:D4"],
        "merge_anchors": [{"anchor": "H", "range": "H3:H10", "formula": "=SUM(H3:H10)"}],
        "formulas": {"A1": "=SUM(A3:A10)", "B1": "=B3"},
        "column_numfmt": {"A": "#,##0.00", "B": "0.00%"},
        "columns": [
            {"col": "A", "nonempty": 20, "numeric_ratio": 1.0, "unique": 18,
             "min": 1, "max": 99, "samples": ["s1", "s2"]},
        ],
    }
    meta_path = tmp / "meta.json"
    meta_path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")

    # 展平 CSV 每行末列是原始行号 (parse_csv_headers 依赖); 表头带内行号 1,2
    # parse_csv_headers 取每列最后非空值 → 表头 = "名称 | 角色"
    csv_path = tmp / "flat.csv"
    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        f.write("名称,角色,_,r\n")
        f.write("姓名,角色A,,1\n")
        f.write("名称,角色,,2\n")
        f.write("张三,甲,,3\n")
    return meta_path, csv_path


def _run_cli(args: list[str], tmp: Path) -> tuple[int, dict, str]:
    """以 CLI 方式调用 main(): 捕获 stdout + SystemExit 退出码 + 输出文件内容."""
    out = tmp / "digest.md"
    argv = ["structure_digest", *args, "--out", str(out)]
    old_argv, old_stdout = sys.argv, sys.stdout
    import io
    buf = io.StringIO()
    sys.stdout = buf
    try:
        sys.argv = argv
        code = 0
        try:
            structure_digest.main()
        except SystemExit as e:
            code = e.code if e.code is not None else 0
    finally:
        sys.argv = old_argv
        sys.stdout = old_stdout
    status = json.loads(buf.getvalue())
    text = out.read_text(encoding="utf-8") if out.exists() else ""
    return code, status, text


class DigestPremodEvidenceTest(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmpdir.name)
        self.meta, self.csv = _make_fixture(self.tmp)

    def tearDown(self):
        self._tmpdir.cleanup()

    # ── KEEP ────────────────────────────────────────────────────────

    def test_premod_keeps_shape_facts(self):
        code, status, text = _run_cli(
            ["--meta", str(self.meta), "--csv", str(self.csv), "--pre-mod"], self.tmp)
        self.assertEqual(code, 0)
        self.assertEqual(status["status"], "SUCCESS")
        # 维度行 (24 列指纹正则必须仍命中)
        self.assertIn("30行 × 24列", text)
        # 表头行 (角色名原样 — parse_csv_headers 取每列最后非空值)
        self.assertIn("- 表头: 名称 | 角色", text)
        # 数据块行 (带 score, 不带块标题文本)
        self.assertIn("- B1 行3-10 (score 0.9)", text)
        self.assertIn("- B2 行14-20 (score 0.7)", text)
        # 合并区行
        self.assertIn("- 合并区(2): A1:B2, C3:D4", text)
        # 行号空洞行
        self.assertIn("- 行号空洞: [12, 13]", text)

    def test_premod_block_lines_have_no_title_text(self):
        _, _, text = _run_cli(
            ["--meta", str(self.meta), "--csv", str(self.csv), "--pre-mod"], self.tmp)
        # 块标题文本物理缺席
        self.assertNotIn("绝密块标题", text)
        self.assertNotIn("第二块标题", text)
        self.assertNotIn('"', text)  # 块标题在 full 模式用引号包裹

    def test_premod_target_keeps_verdict_without_sample(self):
        code, _, text = _run_cli(
            ["--meta", str(self.meta), "--csv", str(self.csv), "--pre-mod",
             "--target"], self.tmp)
        self.assertEqual(code, 0)
        # 占位行 verdict 无样例值; 带样式时给出带样式段范围 (不泄露值)
        self.assertIn("- 占位行样式: 带样式 (段: 25-30)", text)
        self.assertNotIn("样例:", text)
        self.assertNotIn("A25", text)
        # 克隆源行样式行保留 (role=row 带样式/裸行)
        self.assertIn("- 克隆源行样式: B1(title=1 带样式 | header=2 带样式 | data=3 裸行)", text)

    # ── DROP ────────────────────────────────────────────────────────

    def test_premod_drops_all_solution_content(self):
        _, _, text = _run_cli(
            ["--meta", str(self.meta), "--csv", str(self.csv), "--pre-mod"], self.tmp)
        for banned in ("公式链模板", "合并锚点", "numFmt", "非空列画像",
                       "列分类", "min~max", "样例", "=SUM"):
            self.assertNotIn(banned, text, f"pre-mod 不应包含: {banned}")

    # ── full-digest 未变 ────────────────────────────────────────────

    def test_full_digest_still_emits_kept_and_dropped_content(self):
        code, _, text = _run_cli(
            ["--meta", str(self.meta), "--csv", str(self.csv)], self.tmp)
        self.assertEqual(code, 0)
        # full 模式仍含保留项
        self.assertIn("30行 × 24列", text)
        self.assertIn("- 表头: 名称 | 角色", text)
        self.assertIn("- 合并区(2): A1:B2, C3:D4", text)
        # full 模式仍含 (被 pre-mod 剔除的) 内容 — 自洽性检查
        self.assertIn('"绝密块标题-不应出现" (score 0.9)', text)
        self.assertIn("公式链模板", text)
        self.assertIn("合并锚点", text)
        self.assertIn("numFmt", text)
        self.assertIn("非空列画像", text)

    def test_full_digest_target_keeps_sample(self):
        _, _, text = _run_cli(
            ["--meta", str(self.meta), "--csv", str(self.csv), "--target"], self.tmp)
        self.assertIn("- 占位行样式: 带样式 (样例: A25)", text)

    # ── fail-fast ───────────────────────────────────────────────────

    def test_missing_meta_fails(self):
        with self.assertRaises(SystemExit) as ctx:
            sys.argv = ["structure_digest", "--meta", str(self.tmp / "nope.json"),
                        "--out", str(self.tmp / "o.md")]
            structure_digest.main()
        self.assertNotEqual(ctx.exception.code, 0)


class DigestTicket01EvidenceContractTest(unittest.TestCase):
    """Ticket 01: 列头名与 header band 并存 / value_bbox vs style_bbox 分离 /
    「无自动候选」措辞 / 轻量 axis evidence — 纯函数接缝 (Case A/B/C/D)。

    只测结构摘要两条输出路径 (full digest 与 pre-mod evidence) 的外部行,
    不触及 detect_header_rows 等检测算法 (Case 010 契约不动)。
    """

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmpdir.name)

    def tearDown(self):
        self._tmpdir.cleanup()

    def _base_meta(self) -> dict:
        return {
            "file": "unused/both.xlsx",
            "sheet": "Sheet1",
            "dimensions": {"rows": 30, "cols": 24, "formulas": 0, "errorCells": 0,
                           "oleObjects": 0, "charts": 0, "tables": 0},
            "header_band": {"header_rows": [1, 2], "data_start_row": 3},
            "blocks": [],
            "merged_ranges": [],
            "columns": [
                {"col": "A", "nonempty": 20}, {"col": "B", "nonempty": 5},
                {"col": "C", "nonempty": 0},
            ],
        }

    def _write(self, meta: dict, csv_lines: list[str]) -> tuple[Path, Path]:
        mp = self.tmp / "meta.json"
        mp.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        cp = self.tmp / "flat.csv"
        cp.write_text("\n".join(csv_lines) + "\n", encoding="utf-8")
        return mp, cp

    # ── Case A: 列头 + header band 并存 (full 与 pre-mod 各验一次) ─────

    def test_case_a_headers_coexist_full_view(self):
        meta = self._base_meta()
        mp, cp = self._write(meta, ["名称,角色,,1", "名称,角色,,2", "张三,甲,,3"])
        lines = structure_digest.build_digest(meta, cp, None)
        text = "\n".join(lines)
        # 两行并存: 列头名 + header band, 顺序确定 (列头在前, 表头带在后)
        self.assertIn("- 表头: 名称 | 角色", text)
        self.assertIn("- 表头带: 行 [1, 2] 数据起始行 3", text)
        self.assertLess(text.index("- 表头:"), text.index("- 表头带:"))

    def test_case_a_headers_coexist_premod_view(self):
        meta = self._base_meta()
        mp, cp = self._write(meta, ["名称,角色,,1", "名称,角色,,2", "张三,甲,,3"])
        lines = structure_digest.build_premod_evidence(meta, cp, None)
        text = "\n".join(lines)
        self.assertIn("- 表头: 名称 | 角色", text)
        self.assertIn("- 表头带: 行 [1, 2] 数据起始行 3", text)
        self.assertLess(text.index("- 表头:"), text.index("- 表头带:"))

    # ── Case B: 无列头但有 header band → 原行为不变 ───────────────────

    def test_case_b_only_header_band_unchanged(self):
        # CSV 无表头带内的行 (带=行5) → parse_csv_headers 无列头 → 只输出表头带行
        meta = self._base_meta()
        meta["header_band"] = {"header_rows": [5], "data_start_row": 6}
        mp, cp = self._write(meta, ["数据,行,,1", "数据,行,,2"])
        for build in (structure_digest.build_digest,
                      structure_digest.build_premod_evidence):
            text = "\n".join(build(meta, cp, None))
            self.assertNotIn("- 表头:", text)
            self.assertIn("- 表头带: 行 [5] 数据起始行 6", text)

    # ── Case C: 无候选措辞不再暗示没有数据区 ──────────────────────────

    def test_case_c_wording_no_longer_implies_no_data_region(self):
        meta = self._base_meta()
        meta["blocks"] = []
        mp, cp = self._write(meta, [])
        for build in (structure_digest.build_digest,
                      structure_digest.build_premod_evidence):
            text = "\n".join(build(meta, cp, None))
            self.assertIn("- 标题型数据块: 无自动候选（不代表不存在重复记录区）", text)
            # 旧措辞 (暗示无数据区/依赖 LLM 兜底) 物理缺席
            self.assertNotIn("无自动候选 (LLM", text)
            self.assertNotIn("- 数据块: 无自动候选", text)

    # ── Case D: value_bbox 与 style_bbox 分离输出 ─────────────────────

    def test_case_d_bbox_separated_full_and_premod(self):
        meta = self._base_meta()
        meta["value_bbox"] = "A1:F18"
        meta["style_bbox"] = "A1:Z18"
        mp, cp = self._write(meta, [])
        for build in (structure_digest.build_digest,
                      structure_digest.build_premod_evidence):
            lines = build(meta, cp, None)
            text = "\n".join(lines)
            self.assertIn("- value_bbox: A1:F18", text)
            self.assertIn("- style_bbox: A1:Z18", text)
            self.assertNotEqual(
                [l for l in lines if l.startswith("- value_bbox:")],
                [l for l in lines if l.startswith("- style_bbox:")],
                "value_bbox 与 style_bbox 必须是两条独立行")

    def test_case_d_bbox_absent_in_handbuilt_meta_still_graceful(self):
        # 既有单测手写 meta 无 bbox 键 → 无崩溃、行缺席 (向后兼容)
        meta = self._base_meta()
        mp, cp = self._write(meta, [])
        for build in (structure_digest.build_digest,
                      structure_digest.build_premod_evidence):
            text = "\n".join(build(meta, cp, None))
            self.assertNotIn("bbox", text)

    # ── 轻量 axis evidence: 有效值列 + 候选列头 (两视图一致) ───────────

    def test_axis_evidence_value_cols_and_candidate_headers(self):
        meta = self._base_meta()
        # 列头行含产品系列占位词 12K/18K/24K (与 ticket 措辞示例一致)
        meta["columns"] = [
            {"col": "A", "nonempty": 12}, {"col": "B", "nonempty": 12},
            {"col": "C", "nonempty": 0}, {"col": "D", "nonempty": 12},
            {"col": "E", "nonempty": 12}, {"col": "F", "nonempty": 12},
            {"col": "G", "nonempty": 0},
        ]
        csv_lines = [
            "参数,单位,,12K,18K,24K,备注,1",
            "参数,单位,,12K,18K,24K,备注,2",
            "字段甲,kW,,120,180,240,,3",
        ]
        mp, cp = self._write(meta, csv_lines)
        for build in (structure_digest.build_digest,
                      structure_digest.build_premod_evidence):
            text = "\n".join(build(meta, cp, None))
            # 有效值列: 非空列字母清单 (含 F, 不含 C/G)
            self.assertIn("- 有效值列: A B D E F", text)
            # 候选列头: header band 最后一行 (数据起始行前一行) 非空值 per column
            self.assertIn("- 候选列头: A=参数 B=单位 D=12K E=18K F=24K G=备注", text)


if __name__ == "__main__":
    unittest.main()
