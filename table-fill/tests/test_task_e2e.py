"""Task Orchestration 性能验收 — 三层验证的第三层（有 Office；spec S8 /
ticket 08 / issue 08）。skipIf officecli 缺失时整体优雅跳过（层次 1/2 契约与
恢复测试在 tests/test_task_orchestration.py，无 Office 必须全绿）。

验收（全部为结构性断言，不用墙钟）：
  断言 1 — 第一次 prepare_task 后 cache/ 目录数 == 唯一 (file, sheet)
     需求数（4 = 3 个源 sheet + 1 个共享目标模板；而非 4 run × 2 sheet
     = 8 次重复展平 —— 埃及 13 条产品线共享源书复盘语义的合成版）。
  断言 2 — 第二次 prepare_task 缓存零新增（cache/ 目录数不变、阶段 1
     报告 hits=4/misses=0，命中路径零 officecli 展平）、物化 CSV hash 不变。
  断言 3 — 结果等价：物化 CSV 与单 run CSV 逐字节一致（hash 相等）；
     task run manifest 与单 run manifest 的 compile-facing 字段同构
     （仅多 cache_key/sha256 元数据）；同一 fill_spec 经 public CLI
     compile_fill.py 编译 → plan 的 input_hashes / fingerprints /
     operations 一致。
  完整流程 — prepare_task --init/--prepare → 逐 run fill_spec →
     --run（compile → execute → gate 呈现）→ gate_task --set（聚合呈现）
     → --confirm（逐 run 确认 + promote）→ outputs/ 全部落盘 +
     task_status 全 promoted + 幂等重跑 --confirm noop。
  crash window 恢复 — execute 在「写 draft 与写 receipt 之间」崩溃 →
     resume_task --resume 按证据重跑 execute + 重呈现 gate（不自动越过
     Gate、不自动 promote；status 只是索引，恢复以 artifact 真值驱动）。

已知环境事实（KNOWN_TRAPS「Office 并发 = 2」）：execute 阶段并发 2 下
officecli batch 偶发 BATCH_CHUNK_FAILED（rc=1 空 stderr；本机实测约 1/4
轮次）。本测试按产品恢复路径处理：失败 run 的产物证据判定为 execute crash
window → resume_task.py --resume 重跑 execute → gate，这正是 spec S7
「恢复由 artifact 驱动，status 不是真值源」的实证路径；断言不依赖零竞态。

fixture：tests/_fixtures/task_orchestration/e2e/（预生成合成工作簿，
1 源书 3 sheets × ~30 行 + 4 run 共享模板；测试运行时绝不现场生成）。

Run with:
  python -m pytest table-fill/tests/test_task_e2e.py -q
  python -m unittest tests.test_task_e2e -v
"""

from __future__ import annotations

import hashlib
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
FIX_E2E = (Path(__file__).resolve().parent / "_fixtures"
           / "task_orchestration" / "e2e")

RUN_IDS = ["r32-cooling", "r32-heating", "r410a-cooling", "r22-cooling"]
# run id → (源 sheet, ascii slug — 与 task_prepare.entry_name 同约定)
RUN_SHEETS = {"r32-cooling": ("R32参数", "R32"),
              "r32-heating": ("R32参数", "R32"),
              "r410a-cooling": ("R410A参数", "R410A"),
              "r22-cooling": ("R22参数", "R22")}
# run id → target.output（从 fixture task.yaml 读取 —— 不重复声明，杜绝漂移）
OUTPUTS = {r["id"]: r["target"]["output"] for r in
           yaml.safe_load((FIX_E2E / "task.yaml").read_text(encoding="utf-8"))
           ["runs"]}


def strip_cache_metadata(entry: dict) -> dict:
    """去掉任务物化条目的 provenance 元数据 → 单 run 条目形态。"""
    return {k: v for k, v in entry.items() if k not in ("sha256", "cache_key")}


# Business Reasoning Barrier（ticket 02）: 单 run xlsx 条目的 digest 是
# "deferred" 标记（非文件名）、另带 evidence 字段；task 物化条目是真实
# {name}_digest.md、无 evidence。compile 只读这六个 compile-facing 键，故
# 同构对账只覆盖它们，evidence/digest 的有意发散单独断言。
COMPILE_KEYS = ("file", "sheet", "name", "csv", "meta", "candidates")


def compile_keys(entry: dict) -> dict:
    return {k: entry[k] for k in COMPILE_KEYS if k in entry}


def matched_source_rows(run_dir: Path, slug: str) -> int:
    """按 fill_spec 同一 selector 口径数源数据行（不含表头）—— 期望行数
    从产物真值推导，不硬编码生成器行数。"""
    import csv as _csv
    n = 0
    with open(run_dir / f"parameter_book_{slug}_flat.csv", encoding="utf-8-sig",
              newline="") as fh:
        for line in _csv.reader(fh):
            if not line:
                continue
            a = (line[0] or "").strip()
            if a and a != "产品线":
                n += 1
    return n
# 唯一 (file, sheet) 需求 = 3 源 sheet + 1 共享目标模板（ticket 08: U_source=3）
UNIQUE_DEMANDS = 4
NAIVE_FILLS = len(RUN_IDS) * 2  # 4 run × (1 源 + 1 目标) = 8 次重复展平


def run_py(workdir: Path, script: str, *args) -> subprocess.CompletedProcess:
    """套件 e2e 的 subprocess seam（cwd=workdir，超时放宽到 Office 阶段）。"""
    return subprocess.run(
        [sys.executable, "-X", "utf8", str(SCRIPTS / script), *args],
        cwd=str(workdir), capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=1500,
    )


def make_task_root() -> Path:
    """整目录复制预生成 e2e fixture 为临时任务根（ASCII 路径）。"""
    root = Path(tempfile.mkdtemp(prefix="task_e2e_"))
    shutil.copytree(FIX_E2E, root, dirs_exist_ok=True)
    return root


def run_prepare_task(root: Path, mode: str) -> subprocess.CompletedProcess:
    return run_py(root, "prepare_task.py", "--task-root", str(root), mode)


def file_sha256_hex(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_fill_spec(run_dir: Path, sheet: str, slug: str,
                    intent_note: str) -> Path:
    """按 workdir 自己的 prepare_manifest.json 指纹撰写 fill_spec.yaml
    （映射永远在 runs/<id>/fill_spec.yaml：MOD 规则指导撰写的消费侧）。
    task run 与单 run 对照组共用同一构建器 —— 指纹一致 → spec 文本一致。
    同时写 mod_resolution.json（ticket 04 C1：compile 需最终裁决记录，
    spec selected_mod=NONE 与 resolved/NONE 对齐）。"""
    manifest = json.loads(
        (run_dir / "prepare_manifest.json").read_text(encoding="utf-8"))
    (run_dir / "mod_resolution.json").write_text(
        json.dumps({"status": "resolved", "selected": "NONE",
                    "candidates": []}, ensure_ascii=False),
        encoding="utf-8")
    fp = manifest["fingerprints"]
    spec = {
        "task": {"intent": f"合成参数表填充（{sheet}）— {intent_note}",
                 "selected_mod": "NONE", "selected_mod_revision": None},
        "inputs": {"sources": ["parameter_book.xlsx"],
                   "target": "filling_template.xlsx",
                   "source_sheets": [{"source": "parameter_book.xlsx",
                                      "sheets": [sheet]}],
                   "target_sheet": "Sheet1"},
        "fingerprints": {"source_structure": fp["source_structure"],
                         "target_structure": fp["target_structure"]},
        "mapping": {"targets": [{
            "sheet": "Sheet1", "base_last_row": 4,
            "clone_roles": [{"role": "data", "template_row": 3}],
            "rows": {"source": f"parameter_book_{slug}",
                     "selectors": [{"column": "A", "not_value": ""},
                                   {"column": "A", "not_value": "产品线"}]},
            "columns": [{"source": "A", "target": "A"},
                        {"source": "B", "target": "B"},
                        {"source": "C", "target": "C"},
                        {"source": "D", "target": "D"}],
        }]},
        "decisions": ["仅纳入产品线资料行（表头行排除）"],
        "gaps": [],
        "lineage": [{"source": f"parameter_book_{slug}_flat.csv",
                     "role": "primary",
                     "note": "每个匹配源行写入一个追加行（模板行 3 克隆）"}],
        "validation": {"required_coverage": [], "required_empty": [],
                       "key_outputs": ["A5", "B5", "C5", "D5",
                                      f"A{4 + matched_source_rows(run_dir, slug)}"]},
    }
    spec_path = run_dir / "fill_spec.yaml"
    spec_path.write_text(
        yaml.safe_dump(spec, allow_unicode=True, sort_keys=False),
        encoding="utf-8")
    return spec_path


def write_all_run_specs(root: Path) -> None:
    for rid in RUN_IDS:
        sheet, slug = RUN_SHEETS[rid]
        build_fill_spec(root / "runs" / rid, sheet, slug,
                        "issue 08 e2e")


def materialized_csv_hashes(root: Path) -> dict[str, dict[str, str]]:
    """{run id: {csv 名: 逐字节 hex}}（断言 2 的物化产物真值快照）。"""
    out = {}
    for rid in RUN_IDS:
        run_dir = root / "runs" / rid
        out[rid] = {p.name: p.read_bytes().hex()
                    for p in run_dir.glob("*_flat.csv")}
    return out


def drive_to_gate(root: Path) -> tuple[subprocess.CompletedProcess, bool]:
    """--run → gate 呈现；execute 偶发 Office 竞态（BATCH_CHUNK_FAILED）
    时按产品恢复路径走 resume_task --resume（产物证据判定 crash window →
    重跑 execute）—— 失败二分：仅 EXECUTE_FAILED 允许纯重试恢复。
    返回 (最终进程结果, 是否经 resume 恢复)。"""
    attempts = 0
    via_resume = False
    proc = run_prepare_task(root, "--run")
    while proc.returncode != 0:
        attempts += 1
        via_resume = True
        if attempts > 6:
            raise AssertionError(
                f"--run 六轮仍未恢复（Office 竞态超限）: {proc.stdout[-600:]}")
        raw = proc.stdout or "{}"
        try:
            err = json.loads(raw)
        except ValueError:
            raise AssertionError(
                f"--run 失败但 stdout 非结构化 JSON（无法判定失败类别）: "
                f"{raw[-600:]}") from None
        codes = {d.get("code") for d in err.get("defects", [])}
        if not codes or not codes <= {"EXECUTE_FAILED"}:
            raise AssertionError(
                f"非 execute 阶段失败不可用 resume 恢复: {err.get('code')} "
                f"{sorted(codes)} {proc.stdout[-500:]}")
        proc = run_py(root, "resume_task.py", "--task-root", str(root),
                      "--resume")
    return proc, via_resume


@unittest.skipIf(shutil.which("officecli") is None, "officecli not on PATH")
class TaskPerformanceAcceptanceTests(unittest.TestCase):
    """三层验证第三层（有 Office）：结构性断言 + 完整 task 流程走通。"""

    def setUp(self):
        self.root = make_task_root()

    def tearDown(self):
        sys.path.insert(0, str(SCRIPTS))
        from _officecli import clean_residents, unlink_retry  # noqa: PLC0415
        clean_residents()          # 释放 officecli 文件锁
        time.sleep(1.0)            # Windows 异步释放句柄
        for p in sorted(self.root.rglob("*"), reverse=True):
            try:
                if p.is_file():
                    unlink_retry(p)
                else:
                    p.rmdir()
            except OSError:
                pass
        try:
            self.root.rmdir()
        except OSError:
            pass

    def _init_and_prepare(self) -> dict:
        proc = run_prepare_task(self.root, "--init")
        self.assertEqual(proc.returncode, 0, proc.stderr[-800:])
        proc = run_prepare_task(self.root, "--prepare")
        self.assertEqual(proc.returncode, 0, proc.stderr[-800:])
        return json.loads(proc.stdout)

    def test_prepare_cache_structural_assertions(self):
        """断言 1 + 断言 2：cache/ 目录数 == 唯一需求数（非 4×2=8）；
        第二次 prepare 缓存零新增 + 物化 CSV hash 不变（命中路径零展平）。"""
        report = self._init_and_prepare()

        # 断言 1：第一次 prepare 后 cache/ 目录数 == 唯一需求数
        cache_dirs = sorted((self.root / "cache").iterdir())
        self.assertEqual(len(cache_dirs), UNIQUE_DEMANDS,
                         "cache/ 目录数 != 唯一 (file,sheet) 需求数 "
                         "(4 = 3 源 sheet + 1 共享目标模板)")
        self.assertEqual(report["cache"], {"unique_keys": UNIQUE_DEMANDS,
                                           "hits": 0, "misses": UNIQUE_DEMANDS})
        self.assertLess(UNIQUE_DEMANDS, NAIVE_FILLS,
                        "唯一需求数必须严格小于 4 run × 2 sheet 的朴素重复数")
        # 每缓存条目恰好三个白名单产物（无 run 产物入缓存 — spec S3）
        for entry_dir in cache_dirs:
            self.assertEqual(sorted(p.name for p in entry_dir.iterdir()),
                             ["digest.md", "flat.csv", "meta.json"])
        # 4 个 run 全部物化出 manifest（compile-facing 契约在断言 3 对账）
        for rid in RUN_IDS:
            self.assertTrue(
                (self.root / "runs" / rid / "prepare_manifest.json").is_file())

        # 断言 2：第二次 prepare 零新增 + 物化 CSV hash 不变
        before = materialized_csv_hashes(self.root)
        proc = run_prepare_task(self.root, "--prepare")
        self.assertEqual(proc.returncode, 0, proc.stderr[-800:])
        report2 = json.loads(proc.stdout)
        self.assertEqual(report2["cache"], {"unique_keys": UNIQUE_DEMANDS,
                                            "hits": UNIQUE_DEMANDS,
                                            "misses": 0},
                         "第二次 prepare 必须全命中（命中路径零 officecli 展平）")
        self.assertEqual(len(list((self.root / "cache").iterdir())),
                         UNIQUE_DEMANDS, "第二次 prepare 缓存零新增")
        after = materialized_csv_hashes(self.root)
        self.assertEqual(after, before, "物化 CSV 在缓存命中路径下 hash 不变")
        # 物化 = 逐字节复制：全部 run 的展平 CSV 集合 == 全部缓存条目集合
        run_csv_set = {p.read_bytes()
                       for rid in RUN_IDS
                       for p in (self.root / "runs" / rid).glob("*_flat.csv")}
        cache_csv_set = {(entry_dir / "flat.csv").read_bytes()
                         for entry_dir in cache_dirs}
        self.assertEqual(len(run_csv_set), UNIQUE_DEMANDS)
        self.assertEqual(run_csv_set, cache_csv_set)

    def test_materialized_csv_and_plan_equivalent_to_single_run(self):
        """断言 3（结果等价）：物化 CSV 与单 run CSV 逐字节一致；manifest
        compile-facing 同构；同一 spec 经 public CLI 编译 → plan 等价。"""
        self._init_and_prepare()
        run_dir = self.root / "runs" / "r32-cooling"

        # ── 单 run 对照组：同一源书 + 同一模板，prepare_run 全流程 ──
        single = self.root / "_single_run"
        single.mkdir()
        shutil.copy2(self.root / "sources/parameter_book.xlsx",
                     single / "parameter_book.xlsx")
        shutil.copy2(self.root / "templates/filling_template.xlsx",
                     single / "filling_template.xlsx")
        proc = run_py(single, "prepare_run.py", "--workdir", ".",
                      "--files",
                      "parameter_book.xlsx|parameter_book.xlsx,"
                      "filling_template.xlsx|filling_template.xlsx",
                      "--outline")
        self.assertEqual(proc.returncode, 0, proc.stderr[-800:])
        proc = run_py(single, "prepare_run.py", "--workdir", ".",
                      "--flatten",
                      "--sheets",
                      "parameter_book.xlsx:R32参数;"
                      "filling_template.xlsx:Sheet1",
                      "--target", "filling_template.xlsx")
        self.assertEqual(proc.returncode, 0, proc.stderr[-800:])

        # 物化 CSV 与单 run CSV 逐字节一致（hash 相等；源 + 目标各一）
        for csv_name in ("parameter_book_R32_flat.csv",
                         "filling_template_Sheet1_flat.csv"):
            task_bytes = (run_dir / csv_name).read_bytes()
            single_bytes = (single / csv_name).read_bytes()
            self.assertEqual(task_bytes, single_bytes,
                             f"{csv_name}: 任务物化产物与单 run 产物不一致")
            self.assertEqual(file_sha256_hex(run_dir / csv_name),
                             file_sha256_hex(single / csv_name),
                             f"{csv_name}: 物化 hash 与单 run hash 不一致")

        # manifest 同构：compile-facing 字段一致，仅多 cache_key/sha256 元数据
        # （files[].source 是 provenance：task 记录解析后的绝对路径、单 run
        # 记录 CLI 实参 — staged/sha256 才是 compile 绑定对象，必须一致）
        task_manifest = json.loads(
            (run_dir / "prepare_manifest.json").read_text(encoding="utf-8"))
        single_manifest = json.loads(
            (single / "prepare_manifest.json").read_text(encoding="utf-8"))
        for key in ("outlines", "fingerprints"):
            self.assertEqual(task_manifest[key], single_manifest[key],
                             f"manifest.{key} 与单 run 不一致")
        self.assertEqual(
            [{"staged": f["staged"], "sha256": f["sha256"]}
             for f in task_manifest["files"]],
            [{"staged": f["staged"], "sha256": f["sha256"]}
             for f in single_manifest["files"]],
            "manifest.files 的 staged/sha256 与单 run 不一致")
        # 展平条目双向对账：单 run 无多余条目、同名条目 compile-facing 键全等。
        # Business Reasoning Barrier（ticket 02）有意发散：单 run xlsx 条目是
        # Pre-MOD 形态（evidence + digest=="deferred"）；task 物化条目是真实
        # digest 文件、无 evidence —— 各自显式断言，不做整字典相等。
        single_by_name = {e["name"]: e for e in single_manifest["flattened"]}
        task_by_name = {e["name"]: e for e in task_manifest["flattened"]}
        self.assertEqual(set(single_by_name), set(task_by_name),
                         "task 与单 run 的展平条目名集合不一致")
        for name, entry in task_by_name.items():
            self.assertEqual(compile_keys(entry), compile_keys(single_by_name[name]),
                             f"展平条目 {name} compile-facing 键与单 run 不一致")
            self.assertEqual(set(entry) - set(single_by_name[name]),
                             {"sha256", "cache_key"})
        for name, entry in single_by_name.items():
            self.assertEqual(entry["evidence"], f"{name}_premod_evidence.md")
            self.assertEqual(entry["digest"], "deferred")
        for name, entry in task_by_name.items():
            self.assertNotIn("evidence", entry)
            self.assertEqual(entry["digest"], f"{name}_digest.md")
        self.assertEqual(task_manifest["fingerprints"]["source_structure"],
                         single_manifest["fingerprints"]["source_structure"])

        # plan 等价：同一构建器产出的 fill_spec（两 workdir 指纹一致 →
        # spec 文本一致）经 public CLI compile_fill.py 编译
        spec_task = build_fill_spec(run_dir, "R32参数", "R32",
                                    "issue 08 e2e")
        spec_single = build_fill_spec(single, "R32参数", "R32",
                                      "issue 08 e2e 单 run 对照")
        proc = run_py(run_dir, "compile_fill.py", "--spec", str(spec_task),
                      "--workdir", ".")
        self.assertEqual(proc.returncode, 0,
                         proc.stdout[-800:] + proc.stderr[-800:])
        proc = run_py(single, "compile_fill.py", "--spec", str(spec_single),
                      "--workdir", ".")
        self.assertEqual(proc.returncode, 0,
                         proc.stdout[-800:] + proc.stderr[-800:])
        plan_task = json.loads(
            (run_dir / "execution_plan.json").read_text(encoding="utf-8"))
        plan_single = json.loads(
            (single / "execution_plan.json").read_text(encoding="utf-8"))
        for key in ("input_hashes", "fingerprints", "operations",
                    "operation_count", "warnings", "key_outputs",
                    "expected_final_row_count", "structural_deltas"):
            self.assertEqual(plan_task[key], plan_single[key],
                             f"plan.{key} 在 task 与单 run 编译间发散")
        self.assertGreater(plan_task["operation_count"], 0)
        # 期望行数 = base_last_row(4) + 源数据行数（从产物真值推导，非硬编码）
        self.assertEqual(plan_task["expected_final_row_count"],
                         4 + matched_source_rows(run_dir, "R32"))

    def test_full_task_flow_to_promoted(self):
        """完整 task 流程走通：prepare_task → compile+execute+gate（--run，
        execute 竞态经 resume 恢复）→ gate_task --set → --confirm → promote
        （outputs/ 落盘、status 全 promoted、幂等重跑 noop）。"""
        report = self._init_and_prepare()
        self.assertEqual(report["cache"]["misses"], UNIQUE_DEMANDS)
        write_all_run_specs(self.root)

        proc, via_resume = drive_to_gate(self.root)
        if via_resume:
            # 经 resume 恢复：报告形态是 resume 的（阶段信息在 checkpoints/
            # stages 且 semantics 相同）；结构性断言改由状态 + 证据承担
            out = json.loads(proc.stdout)
            self.assertIn(out["code"], ("TASK_RESUME_GATE_PENDING",
                                        "TASK_RESUMED"))
            self.assertEqual(out["failures"], [])
        else:
            out = json.loads(proc.stdout)
            self.assertEqual(out["code"], "TASK_RUN_GATE_PRESENTED")
            # 全流程结构性断言：唯一需求数展平（--run 阶段 1 全命中 + 零新增）
            self.assertEqual(out["cache"], {"unique_keys": UNIQUE_DEMANDS,
                                            "hits": UNIQUE_DEMANDS,
                                            "misses": 0})
            self.assertEqual(len(list((self.root / "cache").iterdir())),
                             UNIQUE_DEMANDS)
            by_stage = {s["stage"]: s for s in out["stages"]}
            self.assertEqual([by_stage[s]["ok"] for s in
                              ("run_prepare", "compile", "execute", "gate")],
                             [4, 4, 4, 4])
        # 每 run：draft + receipt + pending 呈现证据齐全（无 crash window 残余）
        for rid in RUN_IDS:
            run_dir = self.root / "runs" / rid
            self.assertTrue((run_dir / "validated_draft.xlsx").is_file())
            self.assertTrue((run_dir / "draft_receipt.json").is_file())
            self.assertTrue((run_dir / ".gate3_pending").is_file())

        # 聚合呈现（一次人机交互）
        proc = run_py(self.root, "gate_task.py", "--task-root", str(self.root),
                      "--set")
        self.assertEqual(proc.returncode, 0,
                         proc.stdout[-800:] + proc.stderr[-800:])
        summary = json.loads(
            (self.root / "gate_summary.json").read_text(encoding="utf-8"))
        self.assertEqual(set(summary["runs"]), set(RUN_IDS),
                         "呈现集合应覆盖全部 4 个 drafted run")
        self.assertEqual(summary["gaps"], [])

        # 确认展开 → promote（final hash == 已确认 draft hash，逐 run）
        proc = run_py(self.root, "gate_task.py", "--task-root", str(self.root),
                      "--confirm")
        self.assertEqual(proc.returncode, 0,
                         proc.stdout[-800:] + proc.stderr[-800:])
        out = json.loads(proc.stdout)
        self.assertEqual(out["code"], "GATE_CONFIRMED_AND_PROMOTED")
        self.assertEqual(sorted(out["confirmed"]), sorted(RUN_IDS))
        self.assertEqual(sorted(out["promoted"]), sorted(RUN_IDS))
        self.assertEqual(out["gate"]["state"], "promoted")
        for rid in RUN_IDS:
            run_dir = self.root / "runs" / rid
            final = self.root / "outputs" / OUTPUTS[rid]
            self.assertTrue(final.is_file(), f"{rid} 最终输出缺失")
            final_receipt = json.loads(
                (run_dir / "final_receipt.json").read_text(encoding="utf-8"))
            self.assertEqual(final_receipt["final_sha256"],
                             file_sha256_hex(final))
            self.assertEqual(final_receipt["draft_sha256"],
                             file_sha256_hex(run_dir / "validated_draft.xlsx"))
        status = json.loads(
            (self.root / "task_status.json").read_text(encoding="utf-8"))
        for rid in RUN_IDS:
            self.assertEqual(status["runs"][rid]["state"], "promoted")
            self.assertIsNone(status["runs"][rid]["superseded_by"])

        # 幂等重跑 --confirm：全部终态 → noop（不重复确认/交付）
        proc = run_py(self.root, "gate_task.py", "--task-root", str(self.root),
                      "--confirm")
        self.assertEqual(proc.returncode, 0,
                         proc.stdout[-800:] + proc.stderr[-800:])
        self.assertEqual(json.loads(proc.stdout)["code"], "GATE_NOOP")

    def test_execute_crash_window_resume_recovers(self):
        """恢复由 artifact 驱动（spec S7 实证路径，Office 级）：execute
        crash window（draft 存在但 receipt 缺失）→ resume_task --resume
        按证据重跑 execute + 重呈现 gate，不靠 status 猜测、不自动越过
        Gate。"""
        self._init_and_prepare()
        write_all_run_specs(self.root)
        drive_to_gate(self.root)  # 失败竞态同样由 resume 恢复

        # 模拟 execute 在「写 draft 与写 receipt 之间」崩溃：
        # 删除任一 run 的 receipt（证据链断裂，呈现随之失效）
        rid = "r22-cooling"
        run_dir = self.root / "runs" / rid
        (run_dir / "draft_receipt.json").unlink()
        (run_dir / ".gate3_pending").unlink()

        proc = run_py(self.root, "resume_task.py", "--task-root", str(self.root),
                      "--resume")
        self.assertEqual(proc.returncode, 0,
                         proc.stdout[-800:] + proc.stderr[-800:])
        report = json.loads(proc.stdout)
        self.assertEqual(report["failures"], [])
        # 证据真值：receipt 重建（execute 重跑）、pending 重建（gate 重呈现）、
        # 状态推进到 gated —— 未自动确认、未自动 promote（fail-closed）
        self.assertTrue((run_dir / "draft_receipt.json").is_file())
        self.assertTrue((run_dir / ".gate3_pending").is_file())
        self.assertTrue((run_dir / "validated_draft.xlsx").is_file())
        status = json.loads(
            (self.root / "task_status.json").read_text(encoding="utf-8"))
        self.assertEqual(status["runs"][rid]["state"], "gated")
        other = json.loads(
            (run_dir.parent / "r32-cooling" / "draft_receipt.json")
            .read_text(encoding="utf-8"))
        self.assertIn("draft_sha256", other)  # 未受影响的 run 证据原样


if __name__ == "__main__":
    unittest.main()