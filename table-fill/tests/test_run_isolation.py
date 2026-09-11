"""Ticket 08 — Run Isolation / Task Artifact Boundary（spec D8，v3 收敛）。

验收（与 issues/08-run-isolation-task-artifact-boundary.md 对齐，v3 收敛）：
  1. 污染目录 prepare fail-closed（WORKDIR_POLLUTED + corrective action）：
     workdir 已含本 run 生命周期产物（spec/plan/receipt/compile 输出）→
     拒绝；全新 workdir / flatten 产物-only / 合法 prepare 产物（manifest）
     绝不误伤（增量 flatten 合法）；
  2. 连续 Run A/B：B 的 run root 无 A 的 spec/plan/receipt 等任何产物，
     A 完整可追溯（run 级计时机制已整体退役 —— T11）；
  3. 分层边界：task 级 artifact 只有 task.yaml / task_manifest.json /
     task_status.json + 共享 prepare 产物（staged/outlines/cache）；
     run 级产物位于 runs/<id>/，二者绝不互当、互不穿透（assembly 已退役，
     无 assembly/ 共享产物）；
  4. （T11 退役）run 级计时（run_timing.json / note_phase.py /
     record_timing）已删除 —— 不再产生/消费，残留文件为惰性文件；
  5. 契约文字 pin（Layer 3 风格）：SKILL.md 的 run-isolation / boundary
     措辞存在且稳定；
  6. 既有套件全绿（不对其它测试的守卫误伤）。

分层：
  - PollutionGuardPureTests       纯函数矩阵（无 Office，prepare_run 接缝）；
  - PollutionGuardCliTests        prepare_run.py CLI 拒绝（守卫先行，零 officecli）；
  - TaskLayerGuardTests           task_prepare.run_prepare_worker 对污染 run
                                   目录 fail-closed；prepare 产物目录放行；
  - RunABIsolationTests           连续 Run A/B 隔离（纯逻辑 + 真实 --init，
                                   无 officecli 探测）；
  - RunIsolationContractTextTests 契约文字存在性。

Run with:
  python -m pytest tests/test_run_isolation.py -q
"""

from __future__ import annotations

import hashlib
import io
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
FIX_ORT = (Path(__file__).resolve().parent / "_fixtures"
           / "task_orchestration")
FIX_E2E = (Path(__file__).resolve().parent / "_fixtures"
           / "task_orchestration" / "e2e")
SYS_PATH_SCRIPTS = str(SCRIPTS)

if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

# ── 通用 helpers ──────────────────────────────────────────────────────

def run_py(workdir: Path, script: str, *args) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-X", "utf8", str(SCRIPTS / script), *args],
        cwd=str(workdir), capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=300,
    )




def zip_parts(parts: dict) -> bytes:
    """{(part 名, bytes)} → xlsx bytes（固定时间戳 → 确定性 zip）。"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name in sorted(parts):
            info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, parts[name])
    return buf.getvalue()


def unzip(data: bytes) -> dict:
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        return {i.filename: z.read(i.filename) for i in z.infolist()}


# 最小工作簿构造（仅 stdlib zip + 手写 XML；与合成 run 产物同构）
_CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
    '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
    "</Types>"
)
_RELS_ROOT = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
    "</Relationships>"
)
_WORKBOOK_XML = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    ' xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
    '<sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets>'
    "</workbook>"
)
_WORKBOOK_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
    "</Relationships>"
)
_STYLES_XML = (
    '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
    '<fonts count="1"><font><sz val="11"/><name val="Calibri"/></font></fonts>'
    '<fills count="1"><fill><patternFill patternType="none"/></fill></fills>'
    '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
    '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
    '<cellXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/></cellXfs>'
    '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
    "</styleSheet>"
).encode("utf-8")
_THEME_XML = (
    '<a:theme xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" name="theme1">'
    "<a:themeElements><a:clrScheme name=\"Office\">"
    '<a:dk1><a:sysClr val="windowText" lastClr="000000"/></a:dk1>'
    '<a:lt1><a:sysClr val="window" lastClr="FFFFFF"/></a:lt1>'
    '<a:dk2><a:srgbClr val="1F497D"/></a:dk2><a:lt2><a:srgbClr val="EEECE1"/></a:lt2>'
    '<a:accent1><a:srgbClr val="4F81BD"/></a:accent1><a:accent2><a:srgbClr val="C0504D"/></a:accent2>'
    '<a:accent3><a:srgbClr val="77933C"/></a:accent3><a:accent4><a:srgbClr val="8064A2"/></a:accent4>'
    '<a:accent5><a:srgbClr val="4BACC6"/></a:accent5><a:accent6><a:srgbClr val="F79646"/></a:accent6>'
    '<a:hlink><a:srgbClr val="0000FF"/></a:hlink><a:folHlink><a:srgbClr val="800080"/></a:folHlink>'
    "</a:clrScheme>"
    '<a:fontScheme name="Office"><a:majorFont><a:latin typeface="Cambria"/></a:majorFont>'
    '<a:minorFont><a:latin typeface="Calibri"/></a:minorFont></a:fontScheme>'
    "<a:fmtScheme name=\"Office\"><a:fillStyleLst>"
    '<a:solidFill><a:schemeClr val="phClr"/></a:solidFill>'
    '<a:gradFill rotWithShape="1"><a:gsLst><a:gs pos="0"><a:schemeClr val="phClr"/>'
    '<a:gs pos="100000"><a:schemeClr val="phClr"/></a:gsLst><a:lin ang="16200000" scaled="1"/></a:gradFill>'
    "</a:fillStyleLst>"
    '<a:lnStyleLst><a:ln w="9525" cap="flat" cmpd="sng" algn="ctr">'
    '<a:solidFill><a:schemeClr val="phClr"/></a:solidFill><a:prstDash val="solid"/></a:ln></a:lnStyleLst>'
    '<a:effectStyleLst><a:effectStyle><a:effectLst/></a:effectStyle></a:effectStyleLst>'
    '<a:bgFillStyleLst><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:bgFillStyleLst>'
    "</a:fmtScheme></a:themeElements></a:theme>"
).encode("utf-8")


def _sheet_xml(marker: str) -> bytes:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        "<sheetData>"
        f'<row r="1"><c r="A1" t="inlineStr"><is><t>{marker}-TITLE</t></is></c></row>'
        f'<row r="2"><c r="A2" t="inlineStr"><is><t>{marker}-VAL</t></is></c></row>'
        "</sheetData></worksheet>"
    ).encode("utf-8")


def minimal_parts(sheet1: bytes) -> dict:
    return {
        "[Content_Types].xml": _CONTENT_TYPES.encode("utf-8"),
        "_rels/.rels": _RELS_ROOT.encode("utf-8"),
        "xl/workbook.xml": _WORKBOOK_XML.encode("utf-8"),
        "xl/_rels/workbook.xml.rels": _WORKBOOK_RELS.encode("utf-8"),
        "xl/styles.xml": _STYLES_XML,
        "xl/theme/theme1.xml": _THEME_XML,
        "xl/worksheets/sheet1.xml": sheet1,
    }


def make_draft_bytes(marker: str) -> bytes:
    return zip_parts(minimal_parts(_sheet_xml(marker)))


# ── 合成 task root（--init 前的 task.yaml 与占位输入；无 Office） ─────────

TASK_YAML_1RUN = """\
task:
  id: iso-ab-fixture
  customer: fixture
  notes: ticket 08 A/B isolation（data-neutral 合成任务）
runs:
  - id: r32-cooling
    source:
      file: sources/parameter_book.xlsx
      sheets: [R32参数]
    target:
      template: templates/filling_template.xlsx
      sheet: Sheet1
      output: out_r32_cooling.xlsx
"""


def make_task_root(task_yaml: str) -> Path:
    """占位输入 + task.yaml 的临时任务根（静态校验可过；不打开工作簿）。"""
    root = Path(tempfile.mkdtemp(prefix="iso_root_"))
    (root / "sources").mkdir(parents=True)
    (root / "templates").mkdir(parents=True)
    shutil.copy2(FIX_ORT / "sources" / "parameter_book.xlsx",
                 root / "sources" / "parameter_book.xlsx")
    shutil.copy2(FIX_ORT / "templates" / "filling_template.xlsx",
                 root / "templates" / "filling_template.xlsx")
    (root / "task.yaml").write_text(task_yaml, encoding="utf-8")
    return root


def _run_trio(run_dir: Path) -> dict:
    h = lambda name: hashlib.sha256(  # noqa: E731
        (run_dir / name).read_bytes()).hexdigest()
    return {"fill_spec_sha256": h("fill_spec.yaml"),
            "execution_plan_sha256": h("execution_plan.json"),
            "draft_sha256": h("validated_draft.xlsx")}


def fabricate_progressed_run(root: Path, rid: str, marker: str) -> Path:
    """装配一条「已推进」的 run（spec/plan/draft/receipt 证据齐全；无 Office）。
    v3 收敛：无 gate marker（gate 已退役）；deliver 阶段产物为
    final_receipt.json（validated draft 的哈希核对副本收据）。"""
    run_dir = root / "runs" / rid
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "fill_spec.yaml").write_text(
        f"# {marker} spec for {rid}\n", encoding="utf-8")
    (run_dir / "execution_plan.json").write_text(
        json.dumps({"fake": True, "rid": rid, "marker": marker}),
        encoding="utf-8")
    (run_dir / "mapping.md").write_text(
        f"# {marker} mapping for {rid}\n", encoding="utf-8")
    (run_dir / "source_trace.json").write_text(
        json.dumps({"rid": rid, "marker": marker}), encoding="utf-8")
    (run_dir / "validated_draft.xlsx").write_bytes(make_draft_bytes(marker))
    trio = _run_trio(run_dir)
    (run_dir / "draft_receipt.json").write_text(json.dumps(
        {"draft_sha256": trio["draft_sha256"]}, ensure_ascii=False),
        encoding="utf-8")
    (run_dir / "final_receipt.json").write_text(json.dumps(
        {"draft_sha256": trio["draft_sha256"],
         "final_sha256": trio["draft_sha256"],  # 哈希一致 = 已验证副本
         "output": f"out_{rid}.xlsx"},
        ensure_ascii=False), encoding="utf-8")
    return run_dir


def fabricate_prepare_products_dir(root: Path, rid: str) -> Path:
    """装配一条「仅 prepare 完成」的 run：manifest + staged + flatten 产物
    无任何 post-prepare 产物。"""
    run_dir = root / "runs" / rid
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "prepare_manifest.json").write_text(json.dumps({
        "schema_version": 2, "workdir": str(run_dir), "task": "iso-fixture",
        "files": [{"staged": "parameter_book.xlsx", "source": "s",
                   "sha256": "a" * 64}],
        "outlines": {}, "flattened": [], "target": None,
        "fingerprints": {}, "row_gaps": {}, "style_granularity": {},
    }, ensure_ascii=False), encoding="utf-8")
    (run_dir / "parameter_book.xlsx").write_bytes(b"staged-bytes")
    (run_dir / "parameter_book_R32_flat.csv").write_text("a\n", encoding="utf-8")
    (run_dir / "parameter_book_R32_meta.json").write_text(
        '{"schema_version": 1}', encoding="utf-8")
    return run_dir


def init_task_root(root: Path) -> None:
    """初始化 task root 目录结构（runs/ + workspace_manifest 占位；零
    officecli）。Task runtime 已退役（ADR 0020）：无 task_manifest/
    task_status/派生快照 —— task root 只有 task.yaml + sources/ + templates/
    + runs/。make_task_root 已复制 fixture，这里只补 runs/ 目录。"""
    (root / "runs").mkdir(parents=True, exist_ok=True)


# ── 1. 污染守卫纯函数矩阵（无 Office） ───────────────────────────────────

class PollutionGuardPureTests(unittest.TestCase):
    """workdir_pollution_defect：触发器/allowlist/阶段语义。"""

    def setUp(self):
        self.wd = Path(tempfile.mkdtemp(prefix="iso_poll_"))

    def tearDown(self):
        shutil.rmtree(self.wd, ignore_errors=True)

    def _seed(self, names: list[str]) -> None:
        for n in names:
            (self.wd / n).write_bytes(b"seed")

    def _defect(self, strict: bool = True) -> dict | None:
        import prepare_run  # noqa: PLC0415
        return prepare_run.workdir_pollution_defect(self.wd, strict=strict)

    def test_clean_workdir_not_rejected(self):
        self.assertIsNone(self._defect(), "全新 workdir 不得拒绝")

    def test_each_post_prepare_trigger_rejected(self):
        """workdir 已含任一 post-prepare 生命周期产物 → WORKDIR_POLLUTED +
        corrective action（新 run root / 显式 run-id）。"""
        import prepare_run  # noqa: PLC0415
        for name in prepare_run.POST_PREPARE_TRIGGERS:
            with self.subTest(artifact=name):
                wd = Path(tempfile.mkdtemp(prefix="iso_trig_"))
                try:
                    (wd / name).write_bytes(b"seed")
                    d = prepare_run.workdir_pollution_defect(wd)
                    self.assertIsNotNone(d)
                    self.assertEqual(d["code"], "WORKDIR_POLLUTED")
                    self.assertIn(name, d["message"])
                    self.assertIn("全新 run root", d["corrective_action"])
                    self.assertIn("run-id", d["corrective_action"])
                finally:
                    shutil.rmtree(wd, ignore_errors=True)

    def test_trigger_list_matches_v3_lifecycle(self):
        """v3 收敛：WORKDIR_POLLUTED 触发器恰为 compile/execute/deliver 五个
        生命周期产物（gate marker 已删除）。"""
        import prepare_run  # noqa: PLC0415
        self.assertEqual(
            set(prepare_run.POST_PREPARE_TRIGGERS),
            {"execution_plan.json", "mapping.md", "source_trace.json",
             "draft_receipt.json", "final_receipt.json"},
        )

    def test_validated_draft_glob_rejected(self):
        d = self._defect()
        self.assertIsNone(d, "前置断言：干净目录")
        (self.wd / "validated_draft.xlsx").write_bytes(b"draft")
        d = self._defect()
        self.assertIsNotNone(d)
        self.assertIn("validated_draft.*", d["message"])

    def test_fill_spec_rejected_at_outline_strict_only(self):
        """fill_spec.yaml：strict（outline / task 物化入口）触发；flatten
        续跑豁免（repair_row_gaps 重 flatten 接缝，spec 存在时 plan/receipt
        尚不存在）。"""
        (self.wd / "fill_spec.yaml").write_bytes(b"spec")
        self.assertIsNotNone(self._defect(strict=True))
        self.assertIsNone(self._defect(strict=False),
                          "flatten 阶段豁免 fill_spec.yaml（repair 接缝）")

    def test_stale_run_timing_ignored(self):
        """T11 退役：run_timing.json 已无生产者/消费者 —— 目录残留（无论
        prepare_manifest 并存与否）一律降级忽略，不再触发 WORKDIR_POLLUTED。"""
        (self.wd / "run_timing.json").write_text("[]", encoding="utf-8")
        self.assertIsNone(self._defect(), "孤立残留 run_timing.json 不得拒绝")
        self.assertIsNone(self._defect(strict=False))
        self._seed(["run_timing.json", "prepare_manifest.json"])
        self.assertIsNone(self._defect(), "与 manifest 并存的残留亦不得拒绝")
        self.assertIsNone(self._defect(strict=False))

    def test_flatten_products_only_not_rejected(self):
        """增量 flatten 合法：flatten 产物（flat/meta/digest/evidence/
        candidates）不是生命周期产物，任何阶段都不拒。"""
        self._seed([
            "parameter_book_R32_flat.csv", "parameter_book_R32_meta.json",
            "parameter_book_R32_digest.md", "parameter_book_R32_premod_evidence.md",
            "parameter_book_R32_candidates.yaml",
        ])
        self._seed(["prepare_manifest.json"])
        self.assertIsNone(self._defect(), "增量 flatten 不得误伤")
        self.assertIsNone(self._defect(strict=False))

    def test_prepare_side_artifacts_allowed(self):
        """prepare / 环境侧产物豁免：manifest、staged、outline、_plan_*、
        preflight cache、mod_resolution、task_shape —— 不触发。"""
        self._seed([
            "prepare_manifest.json",
            "parameter_book.xlsx", "parameter_book_outline.txt",
            "_plan_parameter_book.json", ".preflight_cache.json",
            "mod_resolution.json", "task_shape.json",
        ])
        self.assertIsNone(self._defect(), "prepare 侧产物不得误伤")
        self.assertIsNone(self._defect(strict=False))

    def test_plan_receipt_rejected_at_flatten_stage(self):
        """flatten 续跑仍拒绝 compile+ 产物（plan/receipt/trace/mapping）——
        已推进的 run 不得被重 flatten 覆盖。"""
        for name in ("execution_plan.json", "mapping.md", "source_trace.json",
                     "final_receipt.json", "draft_receipt.json"):
            with self.subTest(artifact=name):
                d = self._defect(strict=False)
                self.assertIsNone(d, "前置断言：干净目录")
                (self.wd / name).write_bytes(b"seed")
                d = self._defect(strict=False)
                self.assertIsNotNone(d)
                self.assertEqual(d["code"], "WORKDIR_POLLUTED")
                (self.wd / name).unlink()


# ── 2. 污染守卫 CLI（materialize_run.py；守卫先行，零 officecli） ────────

class PollutionGuardCliTests(unittest.TestCase):
    """public CLI 接缝：materialize 对污染 workdir 拒绝并输出结构化 defect
    （stderr JSON + exit 3），不含「先读旧文件再覆写」路径。守卫在
    officecli/preflight 探测之前触发 → 测试零 officecli 依赖
    （ADR 0020: 污染守卫从退役的 prepare_run CLI 迁至 materialize_run）。"""

    def tearDown(self):
        for p in sorted(getattr(self, "wd", Path(".")).rglob("*"),
                        reverse=True):
            try:
                if p.is_file():
                    p.unlink(missing_ok=True)
                else:
                    p.rmdir()
            except OSError:
                pass
        try:
            self.wd.rmdir()
        except OSError:
            pass

    def _seed_workspace(self) -> None:
        """合成 role-neutral workspace manifest（守卫在读取后触发前需要
        manifest 在场 —— materialize 先读 workspace 再查污染）。"""
        (self.wd / "workspace_manifest.json").write_text(json.dumps({
            "schema_version": 4, "kind": "workspace_init",
            "workdir": str(self.wd), "task": "pollution cli",
            "inputs": [{"staged": "a.xlsx", "source": "a.xlsx",
                        "sha256": "ab" * 32}],
            "outlines": {}, "flattened": [], "derived": [],
            "inherited_from": None,
        }, ensure_ascii=False), encoding="utf-8")

    def test_materialize_rejects_workdir_with_spec(self):
        """workdir 已含 fill_spec.yaml 且已推进到 compile+？strict=False 豁免
        spec —— 但 plan/receipt 必须拒绝。此处验证 compile 产物触发守卫。"""
        self.wd = Path(tempfile.mkdtemp(prefix="iso_cli_"))
        self._seed_workspace()
        (self.wd / "execution_plan.json").write_text("{}", encoding="utf-8")
        proc = run_py(self.wd, "materialize_run.py", "--workdir", ".",
                      "--sources", "a", "--target", "a")
        self.assertEqual(proc.returncode, 3, proc.stderr[-800:])
        defect = json.loads(proc.stderr)
        self.assertEqual(defect["code"], "WORKDIR_POLLUTED")
        self.assertIn("全新 run root", defect["corrective_action"])
        self.assertIn("run-id", defect["corrective_action"])
        # 旧产物未被读取/覆写（不产生新生命周期产物）
        self.assertTrue((self.wd / "execution_plan.json").is_file())
        self.assertFalse((self.wd / "prepare_manifest.json").exists())

    def test_materialize_rejects_workdir_with_plan(self):
        """materialize 拒绝 compile 产物（plan 已存在 = run 已推进）。"""
        self.wd = Path(tempfile.mkdtemp(prefix="iso_cli_"))
        self._seed_workspace()
        (self.wd / "execution_plan.json").write_text("{}", encoding="utf-8")
        proc = run_py(self.wd, "materialize_run.py", "--workdir", ".",
                      "--sources", "a", "--target", "a")
        self.assertEqual(proc.returncode, 3, proc.stderr[-800:])
        self.assertEqual(json.loads(proc.stderr)["code"], "WORKDIR_POLLUTED")


# ── 4. 连续 Run A/B 隔离（纯逻辑；零 officecli） ─────────────────────────

class RunABIsolationTests(unittest.TestCase):
    """连续 Run A/B：B 的 run root 无 A 的 spec/plan/receipt 等任何产物；
    A 完整可追溯（run 级计时已随 T11 整体退役，隔离由 run 目录物理边界 +
    哈希引用承担）。"""

    def setUp(self):
        self.root_a = make_task_root(TASK_YAML_1RUN)
        self.root_b = make_task_root(TASK_YAML_1RUN)
        init_task_root(self.root_a)
        init_task_root(self.root_b)

    def tearDown(self):
        shutil.rmtree(self.root_a, ignore_errors=True)
        shutil.rmtree(self.root_b, ignore_errors=True)

    def _a_dir(self) -> Path:
        return self.root_a / "runs" / "r32-cooling"

    def _b_dir(self) -> Path:
        return self.root_b / "runs" / "r32-cooling"

    def test_b_root_free_of_a_artifacts_from_zero(self):
        """Run A 推进到 delivered（spec/plan/receipt/draft），Run B 独立
        run root：B 无 A 的任何产物；A 完整可追溯。"""
        a_dir = fabricate_progressed_run(self.root_a, "r32-cooling", "ALPHA")
        # 追加 execute 期产物（receipt 由 fabrication 补 final_receipt）
        (a_dir / "draft_receipt.json").write_text(json.dumps(
            {"draft_sha256": "d" * 64}), encoding="utf-8")

        # B 仅 prepare 完成（manifest + flatten 产物）
        fabricate_prepare_products_dir(self.root_b, "r32-cooling")
        b_dir = self._b_dir()

        # ── B 无 A 产物（spec/plan/receipt/draft/trace） ──
        for name in ("fill_spec.yaml", "execution_plan.json", "mapping.md",
                     "source_trace.json", "draft_receipt.json",
                     "final_receipt.json", "validated_draft.xlsx"):
            self.assertFalse((b_dir / name).exists(),
                             f"B 泄漏了 A 的产物: {name}")

        # ── A 完整可追溯（内容原样保留） ──
        self.assertEqual((a_dir / "fill_spec.yaml").read_text(
            encoding="utf-8"), "# ALPHA spec for r32-cooling\n")
        self.assertTrue((a_dir / "draft_receipt.json").is_file())
        self.assertTrue((a_dir / "final_receipt.json").is_file())
        self.assertTrue((a_dir / "validated_draft.xlsx").is_file())
        plan = json.loads((a_dir / "execution_plan.json").read_text(
            encoding="utf-8"))
        self.assertEqual(plan["marker"], "ALPHA")


# ── 5. 分层边界：task 级 vs runs/<id>/（run 级） ─────────────────────────

class BoundaryLayeringTests(unittest.TestCase):
    """ADR 0020：assembly / task_manifest / task_status 已退役；task 级
    artifact 只有 task.yaml + workspace_manifest + runs/；run 级产物全部位于
    runs/<id>/，二者绝不互当。"""

    def setUp(self):
        self.root = make_task_root(TASK_YAML_1RUN)
        init_task_root(self.root)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_task_level_artifacts_and_run_level_scoping(self):
        """task root 顶层只有 task.yaml + runs/；task 状态机产物
        （task_manifest/task_status/cache）不存在；run 级产物在 runs/<id>/。"""
        # task 级文件: task.yaml 在根; 退役的 task 状态机产物不出现
        self.assertTrue((self.root / "task.yaml").is_file(),
                        "task root 顶层缺 task.yaml")
        for retired in ("task_manifest.json", "task_status.json", "cache"):
            self.assertFalse((self.root / retired).exists(),
                             f"退役的 task 状态机产物不应存在: {retired} (ADR 0020)")

        # 推进一条 run：run 级产物全部位于 runs/<id>/，根顶层绝不泄漏
        run_dir = fabricate_progressed_run(self.root, "r32-cooling", "ALPHA")
        self.assertTrue((self.root / "runs").is_dir(),
                        "物化后应有 runs/ 目录")
        for name in ("fill_spec.yaml", "execution_plan.json", "mapping.md",
                     "source_trace.json", "draft_receipt.json",
                     "final_receipt.json", "validated_draft.xlsx"):
            self.assertTrue((run_dir / name).is_file(),
                            f"{name} 应在 runs/<id>/ 内")
            self.assertFalse((self.root / name).exists(),
                             f"task root 顶层泄漏 run 级产物: {name}")
        # assembly 已退役：不产生 assembly/ 目录，也无 final.xlsx / final_gate.json
        self.assertFalse((self.root / "assembly").exists(),
                         "无 assembly/ 共享产物")
        self.assertFalse((run_dir / "final.xlsx").exists())
        self.assertFalse((run_dir / "final_gate.json").exists())


# ── 7. 契约文字 pin（Layer 3 风格） ─────────────────────────────────────

class RunIsolationContractTextTests(unittest.TestCase):
    """SKILL.md 的 run-isolation / boundary 措辞存在且稳定 —— 段落被删/被改/
    顺序被破时变红。"""

    def _skill(self) -> str:
        return (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")

    @staticmethod
    def _strip(text: str) -> str:
        return re.sub(r"[\s`*]", "", text)

    def test_skill_run_isolation_bullets_present(self):
        skill = self._skill()
        stripped = self._strip(skill)
        self.assertIn("RunIsolation/TaskArtifactBoundary（ticket08）", stripped)
        for phrase in ("每次run独立runroot", "WORKDIR_POLLUTED",
                       "全新runroot", "run-id",
                       "task/run边界", "绝不互当"):
            self.assertIn(phrase, stripped, f"SKILL.md 缺 run-isolation 措辞 {phrase!r}")

    def test_skill_run_root_products_contract(self):
        stripped = self._strip(self._skill())
        for phrase in ("spec/plan/draft/receipt/scratch", "runs/<id>",
                       "历史run完整可追溯"):
            self.assertIn(phrase, stripped, f"SKILL.md 缺边界措辞 {phrase!r}")


if __name__ == "__main__":
    unittest.main()
