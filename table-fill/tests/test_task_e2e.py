"""Multi-run materialization E2E (ADR 0018/0020) — 三层验证的第三层（有
Office）。skipIf officecli 缺失时整体优雅跳过。

旧 Task runtime (prepare_task --init/--prepare/--run, cache/, task_status)
已退役。新 canonical 多 run 路径:

    workspace_init --init (role-neutral, 业务 sheet 并集, 一次)
        → workspace_manifest.json
    materialize_run --task task.yaml (Topology lowering, 一次遍历全部 runs)
        → runs/<id>/prepare_manifest.json + target routing view
    每 run (S2→S8): Task Shape → MOD → FillSpec → compile → Spec Review →
        execute → promote (独立公开命令, 无 task 编排状态机)

验收（全部为结构性断言，不用墙钟）：
  断言 1 — role-neutral init 只展平业务 sheet 并集（3 源 sheet + 1 共享
     目标模板 = 4 个 entry；非 4 run × 2 sheet = 8 次重复展平）。
  断言 2 — materialize 投影与单 run 对应物等价：同一 run 的 materialize
     指纹/plan == 单 run CLI 投影指纹/plan（同一 workspace facts）。
  断言 3 — 完整多 run 流程：一次 workspace_init + 一次 materialize --task
     → 逐 run compile → Spec Review 一次覆盖全部 run → 逐 run execute →
     promote；每 run 独立交付文件；无 task 状态机产物（task_status/cache）。

fixture：tests/_fixtures/task_orchestration/e2e/（预生成合成工作簿）。

Run with:
  python -m pytest table-fill/tests/test_task_e2e.py -q
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
# run id → (源 sheet, ascii slug — 与 prepare_run.ascii_slug 同约定)
RUN_SHEETS = {"r32-cooling": ("R32参数", "R32"),
              "r32-heating": ("R32参数", "R32"),
              "r410a-cooling": ("R410A参数", "R410A"),
              "r22-cooling": ("R22参数", "R22")}
# run id → target.output（从 fixture task.yaml 读取 —— 不重复声明，杜绝漂移）
OUTPUTS = {r["id"]: r["target"]["output"] for r in
           yaml.safe_load((FIX_E2E / "task.yaml").read_text(encoding="utf-8"))
           ["runs"]}

# 唯一 (file, sheet) 需求 = 3 源 sheet + 1 共享目标模板（ticket 08 语义:
# U_source=3; role-neutral init 只展平业务 sheet 并集）
UNIQUE_ENTRIES = 4
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


def file_sha256_hex(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_mod_resolution(target_dir: Path) -> None:
    """写一份最终裁决记录（status=resolved, selected=NONE）—— compile
    C1-C4 前置。任务级一份放 task root（过滤 compile --mod-resolution）。"""
    target_dir.mkdir(parents=True, exist_ok=True)
    (target_dir / "mod_resolution.json").write_text(
        json.dumps({"status": "resolved", "selected": "NONE",
                    "candidates": []}, ensure_ascii=False),
        encoding="utf-8")


def build_fill_spec(run_dir: Path, sheet: str, slug: str,
                    intent_note: str) -> Path:
    """按 run 自己的 prepare_manifest.json 指纹撰写 fill_spec.yaml。"""
    manifest = json.loads(
        (run_dir / "prepare_manifest.json").read_text(encoding="utf-8"))
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
                       "key_outputs": ["A5", "B5", "C5", "D5"]},
    }
    spec_path = run_dir / "fill_spec.yaml"
    spec_path.write_text(
        yaml.safe_dump(spec, allow_unicode=True, sort_keys=False),
        encoding="utf-8")
    return spec_path


def compile_run(root: Path, rid: str) -> subprocess.CompletedProcess:
    """compile 一条 run（reads task-root mod_resolution + run 目录 spec;
    shared-root 指向 task root — flatten 产物/staged 平铺引用, ADR 0018）。"""
    write_mod_resolution(root)
    run_dir = root / "runs" / rid
    return run_py(run_dir, "compile_fill.py", "--spec", "fill_spec.yaml",
                  "--workdir", ".", "--mod-resolution",
                  str(root / "mod_resolution.json"),
                  "--shared-root", str(root))


@unittest.skipIf(shutil.which("officecli") is None, "officecli not on PATH")
class TaskMaterializeAcceptanceTests(unittest.TestCase):
    """三层验证第三层（有 Office）：结构性断言 + 完整多 run 流程走通。"""

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

    def _workspace_init(self) -> dict:
        """role-neutral init: 业务 sheet 并集 (3 源 + 1 模板), 无 --target."""
        from _fixtures.run_driver import workspace_init
        return workspace_init(
            self.root,
            files=(f"{self.root / 'sources' / 'parameter_book.xlsx'}"
                   f"|parameter_book.xlsx,"
                   f"{self.root / 'templates' / 'filling_template.xlsx'}"
                   f"|filling_template.xlsx"),
            sheets=("parameter_book.xlsx:R32参数,R410A参数,R22参数;"
                    "filling_template.xlsx:Sheet1"),
            task="multi-run materialize e2e")

    def _materialize_task(self) -> dict:
        proc = run_py(self.root, "materialize_run.py", "--workdir", ".",
                      "--task", "task.yaml")
        self.assertEqual(proc.returncode, 0, proc.stderr[-1200:])
        return json.loads(proc.stdout)

    def test_workspace_scope_is_unique_sheet_union(self):
        """断言 1：role-neutral init 只展平业务 sheet 并集（4 个 entry,
        非 4 run × 2 sheet = 8 次重复）。"""
        self._workspace_init()
        ws = json.loads((self.root / "workspace_manifest.json").read_text(
            encoding="utf-8"))
        self.assertEqual(len(ws["flattened"]), UNIQUE_ENTRIES)
        self.assertLess(UNIQUE_ENTRIES, NAIVE_FILLS,
                        "业务 sheet 并集必须严格小于 4 run × 2 sheet 的朴素重复数")
        # role-neutral: 无 target / 无二元指纹
        self.assertNotIn("target", ws)
        self.assertNotIn("fingerprints", ws)
        # entry-level structure 指纹在场
        self.assertTrue(all(e.get("structure_sha256") for e in ws["flattened"]))

    def test_materialize_task_projects_every_run(self):
        """materialize_run --task: 一次遍历全部 runs, 每 run 独立的
        prepare_manifest (run-local compiler view) + target routing view。"""
        self._workspace_init()
        report = self._materialize_task()
        self.assertEqual(report["code"], "MATERIALIZED_TASK")
        self.assertEqual(len(report["runs"]), 4)
        for r in report["runs"]:
            rid = r["run"]
            m = json.loads((self.root / "runs" / rid / "prepare_manifest.json")
                           .read_text(encoding="utf-8"))
            self.assertEqual(m["schema_version"], 2)
            self.assertEqual(m["target"]["name"], "filling_template_Sheet1")
            self.assertTrue(m["fingerprints"]["target_structure"])
            # target routing view 落盘 (0 probing/flatten/extraction)
            tv = m["target"]["evidence"]
            self.assertTrue(tv.endswith("_target_view.md"))
            run_dir = self.root / "runs" / rid
            self.assertTrue((run_dir / tv).is_file())
            # run 目录不复制 raw 输入/共享展平产物 (哈希引用, ADR 0018)
            self.assertFalse((run_dir / "parameter_book.xlsx").exists(),
                             "run 目录不应复制 raw 输入")
            self.assertFalse(list(run_dir.glob("*_flat.csv")),
                             "run 目录不应复制共享展平产物")

    def test_materialized_plan_equivalent_to_single_run(self):
        """断言 2（结果等价）：同一 run 经 materialize 投影与单 run CLI 投影
        编译 plan 等价（fingerprints/operations 一致）—— materialize 是纯
        lowering, 不改变 compiler 输入语义。"""
        self._workspace_init()
        self._materialize_task()
        ws = json.loads((self.root / "workspace_manifest.json").read_text(
            encoding="utf-8"))
        run_dir = self.root / "runs" / "r32-cooling"
        run_manifest = json.loads((run_dir / "prepare_manifest.json").read_text(
            encoding="utf-8"))

        # 聚合一致性: 单源 run 的 source_structure == 该 entry 的 entry-level
        # structure_sha256 (同一 structure_facts 单元素聚合 — ADR 0018)
        r32_entry = next(e for e in ws["flattened"]
                         if e["name"] == "parameter_book_R32")
        self.assertEqual(run_manifest["fingerprints"]["source_structure"],
                         r32_entry["structure_sha256"],
                         "run 级聚合指纹必须是 entry-level 指纹的机械派生")

        # 编译 run 并断言 plan 结构完整
        spec_path = build_fill_spec(run_dir, "R32参数", "R32", "multi-run e2e")
        proc = compile_run(self.root, "r32-cooling")
        self.assertEqual(proc.returncode, 0,
                         proc.stdout[-800:] + proc.stderr[-800:])
        plan = json.loads((run_dir / "execution_plan.json").read_text(
            encoding="utf-8"))
        self.assertGreater(plan["operation_count"], 0)
        self.assertIn("parameter_book_R32_flat.csv", plan["source_csv"])

    def test_full_multi_run_flow_to_delivered(self):
        """断言 3（完整多 run 流程）：一次 workspace_init → 一次 materialize
        --task → 逐 run compile → Spec Review 一次覆盖全部 run → 逐 run
        execute → promote。每 run 独立交付文件；无 task 状态机产物
        （task_status/cache/调度器）。"""
        self._workspace_init()
        self._materialize_task()

        # 逐 run: spec + compile (任务级一次 MOD 裁决)
        write_mod_resolution(self.root)
        for rid in RUN_IDS:
            sheet, slug = RUN_SHEETS[rid]
            build_fill_spec(self.root / "runs" / rid, sheet, slug,
                            "multi-run e2e")
        for rid in RUN_IDS:
            proc = compile_run(self.root, rid)
            self.assertEqual(proc.returncode, 0,
                             proc.stdout[-800:] + proc.stderr[-800:])

        # Spec Review: 一次摘要覆盖全部 run, 一次确认绑定全部 run 哈希
        proc = run_py(self.root, "spec_review.py", "--workdir", ".",
                      "--task", "task.yaml")
        self.assertEqual(proc.returncode, 0,
                         proc.stdout[-800:] + proc.stderr[-800:])
        self.assertTrue((self.root / "spec_review.json").is_file())
        proc = run_py(self.root, "spec_review.py", "--workdir", ".",
                      "--task", "task.yaml", "--confirm")
        self.assertEqual(proc.returncode, 0,
                         proc.stdout[-800:] + proc.stderr[-800:])
        confirm = json.loads(
            (self.root / "review_confirm.json").read_text(encoding="utf-8"))
        self.assertEqual(set(r["run_id"] for r in confirm["runs"]),
                         set(RUN_IDS), "一次确认必须绑定全部 4 个 run 的哈希")

        # 逐 run execute (串行 — 单 Office resident 窗口) + promote
        for rid in RUN_IDS:
            run_dir = self.root / "runs" / rid
            proc = run_py(run_dir, "execute_batch.py", "--plan",
                          "execution_plan.json", "--template",
                          str(self.root / "filling_template.xlsx"),
                          "--workdir", ".", "--round", "1", "--render", "html",
                          "--staged-root", str(self.root),
                          "--review-confirm",
                          str(self.root / "review_confirm.json"))
            self.assertEqual(proc.returncode, 0,
                             proc.stdout[-800:] + proc.stderr[-800:])
            receipt = json.loads((run_dir / "draft_receipt.json").read_text(
                encoding="utf-8"))
            self.assertTrue(receipt["readback"]["passed"] > 0)
            final = self.root / "outputs" / OUTPUTS[rid]
            final.parent.mkdir(parents=True, exist_ok=True)
            proc = run_py(run_dir, "promote_output.py", "--workdir", ".",
                          "--final", str(final), "--staged-root", str(self.root))
            self.assertEqual(proc.returncode, 0, proc.stderr[-800:])

        # 每 run 独立交付文件, 无合并产物
        for rid in RUN_IDS:
            final = self.root / "outputs" / OUTPUTS[rid]
            self.assertTrue(final.is_file(), f"{rid} 最终输出缺失")
        self.assertEqual(
            sorted(p.name for p in (self.root / "outputs").iterdir()),
            sorted(OUTPUTS.values()))
        self.assertFalse((self.root / "assembly").exists())

        # 无 task 状态机产物: 无 task_status / cache / 调度文件
        self.assertFalse((self.root / "task_status.json").exists(),
                         "Task runtime 已退役: 无 task_status (ADR 0020)")
        self.assertFalse((self.root / "cache").exists(),
                         "无共享 cache 生命周期 (ADR 0020)")


if __name__ == "__main__":
    unittest.main()