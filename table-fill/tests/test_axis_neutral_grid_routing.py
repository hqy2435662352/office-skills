"""Ticket 02 — Axis-Neutral Grid Routing: 三层回归测试 (fixture R / C).

Layer 1 — detector contract (Case 010 样式, 纯函数): detect_header_rows 对
  fixture R (Grouped Row Grid) 与 C (Column Record Matrix) 的行画像给出
  正确的 header band (表头行 + 数据起始行)。不改动 Case 010 自身断言。
Layer 2 — Pre-MOD Evidence 契约 (E2E, prepare_run --outline/--flatten 驱动
  checked-in 工作簿): `{name}_premod_evidence.md` 含路由判定所需的全部元素 —
  表头/候选列头 (schema axis)、表头带 + 数据起始行 (record 区起点)、合并区
  (fixture R 的纵向组 merge 必须被 flatten 报告)、value_bbox (+style_bbox)。
Layer 3 — routing contract: 期望判定 (expected_verdicts.json 的
  grid_record / fillspec / obvious_grid + record_axis) 仅凭 prepare 产物
  (evidence 文件) 即可判定 (probe_free, 0 探测)；SKILL.md 契约文字含
  axis-neutral 定义 (record_axis ∈ rows/columns)、cardinality 锚点、form
  对照；task_shape 值域不变 (matrix/column-grid/grouped-grid 不是 shape 值)；
  无记录数量阈值 / 无 Routing V3 / 无新分类器语言。

真实业务文件 replay (客户参数表 / ATLAS 报价单 / Fast Path 0 探测 replay
记录) 本轮明确排除 (用户禁令)；fixture R/C 是其 canonical 替代回归对。

Run: python -m pytest tests/test_axis_neutral_grid_routing.py -q
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))  # flatten_table / _officecli 可导入 (Layer 1 纯函数接缝)
FIX_ROUTING = (Path(__file__).resolve().parent
               / "_fixtures" / "routing")
FIX_R = FIX_ROUTING / "fixture_r_grouped_row_grid.xlsx"
FIX_C = FIX_ROUTING / "fixture_c_column_record_matrix.xlsx"
VERDICTS_PATH = FIX_ROUTING / "expected_verdicts.json"
SKILL_MD = SKILL_ROOT / "SKILL.md"

# prepare_run flatten 生成的条目名 (file_stem_ascii(sheet))
ENTRY_NAMES = {
    "fixture_r_grouped_row_grid": "fixture_r_grouped_row_grid_Sheet1",
    "fixture_c_column_record_matrix": "fixture_c_column_record_matrix_Sheet1",
}


def _load_verdicts() -> dict:
    return json.loads(VERDICTS_PATH.read_text(encoding="utf-8"))


def run_py(workdir: Path, script: str, *args) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-X", "utf8", str(SCRIPTS / script), *args],
        cwd=str(workdir), capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=1500,
    )


def prepare_fixtures(workdir: Path) -> None:
    """把 checked-in fixture 复制进 workdir 并跑 canonical init + materialize。

    role-neutral init 展平两本书的业务 sheet 并集; 每条 fixture 作为对方
    fixture 的 run 各自 materialize 一次 (evidence 文件名由 manifest 条目
    给出 — 断言读 manifest, 不猜名; target 视角的 evidence 由 run-local
    target view 提供 (ADR 0018/0019))。
    """
    files = []
    for fix in (FIX_R, FIX_C):
        workdir.joinpath(fix.name).write_bytes(fix.read_bytes())
        files.append(f"{workdir / fix.name}|{fix.name}")
    files_arg = ",".join(files)
    sheets_arg = (
        "fixture_r_grouped_row_grid.xlsx:Sheet1;"
        "fixture_c_column_record_matrix.xlsx:Sheet1")
    from _fixtures.run_driver import workspace_init, materialize, target_entry_name
    workspace_init(workdir, files_arg, sheets_arg,
                   task="axis-neutral routing fixtures")
    # 两个 run, 每条 fixture 作一次 target (另一方作 source)
    materialize(workdir,
                sources=target_entry_name("fixture_c_column_record_matrix", "Sheet1"),
                target=target_entry_name("fixture_r_grouped_row_grid", "Sheet1"),
                run_id="run_r_as_target")
    materialize(workdir,
                sources=target_entry_name("fixture_r_grouped_row_grid", "Sheet1"),
                target=target_entry_name("fixture_c_column_record_matrix", "Sheet1"),
                run_id="run_c_as_target")


def evidence_of(workdir: Path, fix_key: str) -> str:
    manifest = json.loads(
        (workdir / "prepare_manifest.json").read_text(encoding="utf-8"))
    by_name = {e["name"]: e for e in manifest["flattened"]}
    entry = by_name.get(ENTRY_NAMES[fix_key])
    assert entry, f"manifest 缺条目 {ENTRY_NAMES[fix_key]}"
    return (workdir / entry["evidence"]).read_text(encoding="utf-8")


class RoutingE2EBase(unittest.TestCase):
    """officecli E2E 基类: 每类一次 prepare + 类尾锁清理 (沿用
    test_prepare_evidence_output.py 的 resident 清理纪律)。"""

    @classmethod
    def setUpClass(cls):
        if shutil.which("officecli") is None:
            raise unittest.SkipTest("officecli not on PATH")
        cls._tmpdir = tempfile.TemporaryDirectory(prefix="axis_neutral_routing_")
        cls.workdir = Path(cls._tmpdir.name)
        sys.path.insert(0, str(SCRIPTS))
        from _officecli import clean_residents  # noqa: PLC0415
        clean_residents()
        prepare_fixtures(cls.workdir)

    @classmethod
    def tearDownClass(cls):
        from _officecli import clean_residents, unlink_retry  # noqa: PLC0415
        import time
        clean_residents()
        time.sleep(1.0)
        try:
            for p in sorted(cls.workdir.rglob("*"), reverse=True):
                try:
                    if p.is_file():
                        unlink_retry(p)
                    else:
                        p.rmdir()
                except OSError:
                    pass
            cls.workdir.rmdir()
        except OSError:
            pass


# ─────────────────────────────────────────────────────────────────────
# Layer 1 — detector contract (Case 010 样式, 纯函数, 不依赖 office)
# ─────────────────────────────────────────────────────────────────────

class Layer1DetectorContractTests(unittest.TestCase):
    """detect_header_rows 对 fixture R/C 行画像的契约 (Case 010 同款接缝)。

    profiles 与 checked-in 工作簿逐行同构 (含纵向 merge 非锚点格为空、
    Unit Price 记录行留空 → 数据行密度低于表头带最高密度)。"""

    def _cells(self, rows_profile):
        cells = []
        for r, entries in rows_profile.items():
            for col, ctype, text in entries:
                cells.append({
                    "path": f"/S/{col}{r}",
                    "format": {"type": ctype},
                    "text": text,
                })
        return cells

    def test_fixture_r_grouped_row_grid_band(self):
        """R: 稀疏标题行(1 格)跳过 → 六列表头带 [2] → 数据行密度 5<6 断开
        → 数据起始行 3 (record 区从 row 3 开始; A/F 组 merge 不吸入表头带)。"""
        from flatten_table import detect_header_rows
        profile = {
            1: [("A", "SharedString", "Product Quotation List")],
            2: [("A", "SharedString", "Type"),
                ("B", "SharedString", "Model"),
                ("C", "SharedString", "Capacity"),
                ("D", "SharedString", "Qty"),
                ("E", "SharedString", "Unit Price"),
                ("F", "SharedString", "Panel looking")],
            3: [("A", "SharedString", "Wall"),
                ("B", "SharedString", "Model L1"),
                ("C", "SharedString", "9000Btu"),
                ("D", "Number", "2"),
                ("F", "SharedString", "Classic")],
            4: [("B", "SharedString", "Model L2"),
                ("C", "SharedString", "12000Btu"),
                ("D", "Number", "3")],
            5: [("B", "SharedString", "Model L3"),
                ("C", "SharedString", "18000Btu"),
                ("D", "Number", "4")],
            6: [("A", "SharedString", "Portable"),
                ("B", "SharedString", "Model M1"),
                ("C", "SharedString", "7000Btu"),
                ("D", "Number", "1"),
                ("F", "SharedString", "Modern")],
            7: [("B", "SharedString", "Model M2"),
                ("C", "SharedString", "9000Btu"),
                ("D", "Number", "2")],
            8: [("B", "SharedString", "Model M3"),
                ("C", "SharedString", "12000Btu"),
                ("D", "Number", "2")],
            9: [("A", "SharedString", "Total")],
            10: [("A", "SharedString", "Features")],
            11: [("A", "SharedString", "Notes")],
        }
        out = detect_header_rows(self._cells(profile), 6)
        self.assertEqual(out, {"header_rows": [2], "data_start_row": 3})

    def test_fixture_c_column_record_matrix_band(self):
        """C: 合并标题行(1 格)跳过 → 表头示意行 [2] (Parameter|12K|18K|24K)
        → 首个字段行 (Capacity + 3 数值) 多数数值 → 断开 → 数据起始行 3。"""
        from flatten_table import detect_header_rows
        profile = {
            1: [("A", "SharedString", "Product Parameter Matrix")],
            2: [("A", "SharedString", "Parameter"),
                ("B", "SharedString", "12K"),
                ("C", "SharedString", "18K"),
                ("D", "SharedString", "24K")],
            3: [("A", "SharedString", "Capacity"),
                ("B", "Number", "12000"),
                ("C", "Number", "18000"),
                ("D", "Number", "24000")],
            4: [("A", "SharedString", "EER"),
                ("B", "Number", "11"),
                ("C", "Number", "10.8"),
                ("D", "Number", "11.6")],
            5: [("A", "SharedString", "Sound"),
                ("B", "Number", "42"),
                ("C", "Number", "45"),
                ("D", "Number", "48")],
            6: [("A", "SharedString", "Type"),
                ("B", "SharedString", "Wall"),
                ("C", "SharedString", "Wall"),
                ("D", "SharedString", "Wall")],
            7: [("A", "SharedString", "Compressor"),
                ("B", "SharedString", "VS1"),
                ("C", "SharedString", "VS2"),
                ("D", "SharedString", "VS3")],
            8: [("A", "SharedString", "Refrigerant"),
                ("B", "SharedString", "R32"),
                ("C", "SharedString", "R32"),
                ("D", "SharedString", "R32")],
        }
        out = detect_header_rows(self._cells(profile), 4)
        self.assertEqual(out, {"header_rows": [2], "data_start_row": 3})


# ─────────────────────────────────────────────────────────────────────
# Layer 2 — Pre-MOD Evidence 四要素 (E2E, officecli)
# ─────────────────────────────────────────────────────────────────────

class Layer2PremodEvidenceTests(RoutingE2EBase):
    """prepare 产出 {name}_premod_evidence.md 含路由判定所需全部元素。

    四要素: ① 表头/候选列头 (schema axis 可读); ② 表头带行含数据起始行
    (record 区起点); ③ 合并区 (fixture R 的纵向组 merge 被 flatten 报告,
    真实 merged ranges); ④ value_bbox (+style_bbox)。"""

    def test_fixture_r_evidence_four_elements(self):
        ev = evidence_of(self.workdir, "fixture_r_grouped_row_grid")
        # ① 列头 (schema axis) — 并存 view, 候选列头齐全
        self.assertIn("- 表头: Type | Model | Capacity | Qty | Unit Price"
                      " | Panel looking", ev)
        self.assertIn("- 候选列头: A=Type B=Model C=Capacity D=Qty"
                      " E=Unit Price F=Panel looking", ev)
        # ② 表头带行 + 数据起始行 (record axis 起点)
        self.assertIn("- 表头带: 行 [2] 数据起始行 3", ev)
        # ③ 合并区: A/F 纵向组 merge 必须被 flatten 报告 (组展示, 非 form 证据)
        self.assertIn("- 合并区(8): ", ev)
        for merge in ("A3:A5", "F3:F5", "A6:A8", "F6:F8"):
            self.assertIn(merge, ev, f"fixture R 缺合并区 {merge}")
        # ④ bbox: value_bbox 必在, style_bbox 并存
        self.assertIn("- value_bbox: A1:F11", ev)
        self.assertIn("- style_bbox: A1:F11", ev)

    def test_fixture_c_evidence_four_elements(self):
        ev = evidence_of(self.workdir, "fixture_c_column_record_matrix")
        # ① 字段标签列头 + 产品列头 (schema axis + record axis 候选)
        self.assertIn("- 表头: Parameter | 12K | 18K | 24K", ev)
        self.assertIn("- 候选列头: A=Parameter B=12K C=18K D=24K", ev)
        # ② 表头带行 + 数据起始行
        self.assertIn("- 表头带: 行 [2] 数据起始行 3", ev)
        # ③ 候选列头中的产品 token (12K/18K/24K) 即 record axis 候选
        self.assertIn("- 合并区(1): A1:D1", ev)
        # ④ bbox
        self.assertIn("- value_bbox: A1:D8", ev)
        self.assertIn("- style_bbox: A1:D8", ev)


# ─────────────────────────────────────────────────────────────────────
# Layer 3 — routing contract (SKILL 契约文字 + 期望判定可判定性)
# ─────────────────────────────────────────────────────────────────────

class Layer3ContractTextTests(unittest.TestCase):
    """SKILL.md Task Shape Check 契约文字 (axis-neutral 定义 + 值域不变 +
    禁止语言)。契约措辞稳定, ticket 09 的 contract-text 测试将 pin 此行。"""

    def _skill(self) -> str:
        return SKILL_MD.read_text(encoding="utf-8")

    def _routing_section(self) -> str:
        text = self._skill()
        m = re.search(r"^### 3\. Task Shape Check.*?(?=^### 4\. )",
                      text, re.MULTILINE | re.DOTALL)
        self.assertIsNotNone(m, "SKILL.md 缺 §3 Task Shape Check 段")
        return m.group(0)

    def test_axis_neutral_definition_present(self):
        """grid_record = 稳定 schema axis + 重复 record axis, record_axis
        ∈ {rows, columns}; Row-Record 与 Column-Record Matrix 同为 grid_record。"""
        section = self._routing_section()
        self.assertIn("record_axis ∈ {rows, columns}", section)
        self.assertIn("schema axis + 重复 record axis", section)
        self.assertIn("Row-Record Grid", section)
        self.assertIn("Column-Record Matrix", section)
        self.assertIn("同为 `grid_record`", section)
        # cardinality 锚点 + 外围结构不改变 Grid 身份 (上一轮契约吸收);
        # 契约行跨行折行 → 用去空白形式断言 (行内空格/换行不影响)
        stripped = re.sub(r"[\s`]", "", section)
        self.assertIn("Nrecords→Ntargetrows", stripped)
        self.assertIn("Ntargetcolumns", stripped)
        self.assertIn("固定行数本身不构成 form 证据", section)
        self.assertIn("纵向groupmerge", stripped)
        self.assertIn("merge数量本身不得作为Non-Grid依据", stripped)
        # form 对照 (slot cardinality 固定 vs record cardinality 数据驱动)
        self.assertIn("slot cardinality 固定", section)
        self.assertIn("record cardinality 数据驱动", section)
        # task_shape.json 兼容 record_axis 字段 (evidence/diagnostic, 不改三字段)
        self.assertIn("record_axis", self._skill())
        self.assertIn("不改变三字段契约", self._skill())

    def test_value_domain_unchanged(self):
        """task_shape 值域仍恰为四值; matrix/column-grid/grouped-grid 只是
        结构属性/evidence 标签, 不是新 shape 值。"""
        skill = self._skill()
        # 值域声明 (权威模型 + axis-neutral 契约段)
        self.assertIn(
            "值域 `grid_record`/`form_content`/`mixed`/`uncertain`", skill)
        stripped = re.sub(r"[\s`]", "", self._routing_section())
        self.assertIn("task_shape值域不变", stripped)
        self.assertIn("不是新shape值", stripped)
        self.assertIn("仍是grid_record", stripped)
        # §1.5 值域表 shape 维度恰好四值 (matrix 等不占行)
        section = self._routing_section()
        m = re.search(
            r"^\|\s*task_shape\s*\|\s*含义\s*\|\s*合法 route\s*\|"
            r"\s*典型 evidence\s*\|",
            section, re.MULTILINE)
        self.assertIsNotNone(m, "§1.5 缺 task_shape 值域表")
        rows = []
        for line in section[m.end():].splitlines():
            line = line.rstrip()
            if not line:
                continue  # 表头行后首个换行产生的空串
            if not line.startswith("|"):
                break
            if re.fullmatch(r"\|\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|", line):
                continue
            cells = [c.strip().strip("`") for c in
                     line.strip().strip("|").split("|")]
            rows.append(cells)
        shapes = {r[0] for r in rows if r}
        self.assertEqual(
            shapes, {"grid_record", "form_content", "mixed", "uncertain"},
            "task_shape 值域表出现新 shape 值")
        # 标签词不得以 shape 值身份出现在值域表
        for label in ("matrix", "column-grid", "grouped-grid"):
            self.assertNotIn(label, shapes,
                             f"{label} 不是 task_shape 值")

    def test_forbidden_language_absent(self):
        """禁止语言: 无 Routing V3 / scoring classifier / 第二层分类器
        字面; 禁句以语义方式声明 (记录数量阈值不作为路由依据, 不新增
        分类器/评分层/第二层路由判定)。"""
        skill = self._skill()
        section = self._routing_section()
        for banned in ("Routing V3", "scoring classifier", "第二层分类器"):
            self.assertNotIn(banned, skill,
                             f"SKILL.md 禁止语言漏入: {banned}")
        # 禁句语义在契约中显式声明 (证明未把阈值/新分类器当路由依据)
        self.assertIn("以记录数量阈值作为路由依据", section)
        self.assertIn("禁止新增分类器/评分层/第二层路由判定", section)
        self.assertIn("0 新增动作", section)
        # Grid 定义不以数量阈值比较器作判定 (≥ 为阈值比较典型形态)
        self.assertNotIn("≥", section, "§1.5 不得出现数量阈值比较器 ≥")

    def test_record_axis_documented_in_task_shape_json_contract(self):
        """task_shape.json 的 record_axis 是 evidence/diagnostic 兼容字段,
        不重定义三字段 schema 权威 (artifact 是 Agent 撰写的)。"""
        skill = self._skill()
        self.assertIn("`record_axis`", skill)
        section = self._routing_section()
        self.assertIn("evidence/diagnostic 层", section)
        self.assertIn("schema 权威性", section)
        self.assertIn("Agent 撰写的 artifact", section)


@unittest.skipIf(shutil.which("officecli") is None, "officecli not on PATH")
class Layer3RoutingVerdictTests(RoutingE2EBase):
    """期望判定 = expected_verdicts.json 记录; 判定仅凭 prepare 产物
    (evidence 文件) 可判定 (probe_free): 每个 evidence_fact 都是 evidence
    里的行, 不需要任何 view/render/query/get/临时脚本探测。"""

    def test_fixture_verdicts_match_expected_records(self):
        verdicts = _load_verdicts()["fixtures"]
        for fix_key, verdict in verdicts.items():
            ev = evidence_of(self.workdir, fix_key)
            # ① canonical verdict 字段 (SKILL 契约值, 无新 shape/route/evidence)
            self.assertEqual(verdict["task_shape"], "grid_record")
            self.assertEqual(verdict["route"], "fillspec")
            self.assertEqual(verdict["evidence"], ["obvious_grid"])
            self.assertEqual(verdict["record_axis"], "rows" if "fixture_r" in
                             fix_key else "columns")
            # ② record_axis 是 evidence/diagnostic 字段, 不影响三字段契约
            self.assertIn(verdict["record_axis"], ("rows", "columns"))
            # ③ 判定可判定性: 每条 routing 事实都出自 premod evidence 文件
            for fact in verdict["evidence_facts"]:
                self.assertIn(fact, ev,
                              f"{fix_key} evidence 缺路由判定事实: {fact!r}")
            # ④ 0 探测断言: 无 view/render/query/get 探测需要 (documented)
            self.assertTrue(verdict["probe_free"],
                            f"{fix_key} 期望判定必须是 probe-free")

    def test_verdict_decidable_from_evidence_alone(self):
        """显式 0 探测断言 (真实 replay 的 Fast Path 0 探测记录替代):
        expected verdict 的全部事实来自 evidence + manifest (prepare 产物),
        测试只读这两个文件即可复现判定。"""
        workdir_files = {p.name for p in self.workdir.iterdir()}
        evidence_names = {f"{n}_premod_evidence.md" for n in
                          ENTRY_NAMES.values()}
        self.assertTrue(
            evidence_names.issubset(workdir_files),
            "prepare 必须产出 premod_evidence (唯一判定输入)")
        # 判定输入白名单: evidence + manifest (prepare 产物) — 无 officecli
        # view/render/query/get / HTML inspect / 临时脚本参与
        verdicts = _load_verdicts()["fixtures"]
        for fix_key, verdict in verdicts.items():
            ev = evidence_of(self.workdir, fix_key)
            facts_ok = all(f in ev for f in verdict["evidence_facts"])
            self.assertTrue(
                facts_ok,
                f"{fix_key}: 期望判定不能仅凭 evidence 复现 → 需要额外探测")


if __name__ == "__main__":
    unittest.main()