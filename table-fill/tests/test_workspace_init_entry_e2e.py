"""T12 验收: 埃及类 fixture 以 workspace_init --init 为唯一准备入口, 全链绿。

compile 消费 init 写出的 compile-facing 派生视图 prepare_manifest.json
(T12 Phase 1 选项 A —— 编译器零改动); canonical 仍是 workspace_manifest.json
(spec「唯一入口」「此后所有环节只认清单」的落地形态)。

链路: workspace_init --init → MOD 裁决落盘 → FillSpec (build_spec, 三种
locator 形态) → Spec Review (生成 + --confirm) → compile_fill → execute_batch
(含 BATCH_CHUNK_FAILED 文档化重试一次) → promote_output (哈希核对交付)。

夹具与 spec 构造复用 test_matrix_hierarchical_regression (data-neutral
checked-in fixture), 断言取主路径 + 交付证据; officecli 缺失时跳过。

Run:
  python -m pytest table-fill/tests/test_workspace_init_entry_e2e.py -q
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

from test_matrix_hierarchical_regression import (
    EXPECTED_WRITES,
    N_WRITES,
    SRC_ENTRY,
    SRC_FILE,
    SRC_SHEET,
    TGT_ENTRY,
    TGT_FILE,
    TGT_SHEET,
    build_spec,
    load_flat_rows,
    mod_record,
    run_py as mx_run_py,
    zip_text,
)

SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
WS_INIT = SCRIPTS / "workspace_init.py"

INTERNAL_TOKENS = ("Internal", "Cost Code", "USD", "内部成本")


def run_ws_init(workdir: Path, *args) -> subprocess.CompletedProcess:
    """workspace_init CLI subprocess seam (cwd=workdir)。"""
    return subprocess.run(
        [sys.executable, "-X", "utf8", str(WS_INIT), "--workdir", ".", *args],
        cwd=str(workdir), capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=900)


@unittest.skipIf(shutil.which("officecli") is None, "officecli not on PATH")
class WorkspaceInitEntryE2E(unittest.TestCase):
    """单 run 全链以 workspace_init 为唯一准备入口 (T12 验收 1)。"""

    def setUp(self):
        sys.path.insert(0, str(SCRIPTS))
        from _officecli import clean_residents  # noqa: PLC0415
        clean_residents()
        self._tmp = tempfile.TemporaryDirectory(prefix="ws_entry_e2e_")
        self.workdir = Path(self._tmp.name)
        shutil.copy2(Path(__file__).resolve().parent / "_fixtures"
                     / "hierarchical_matrix" / "source_parameter_book.xlsx",
                     self.workdir / SRC_FILE)
        shutil.copy2(Path(__file__).resolve().parent / "_fixtures"
                     / "hierarchical_matrix" / "target_template.xlsx",
                     self.workdir / TGT_FILE)

    def tearDown(self):
        from _officecli import clean_residents, unlink_retry  # noqa: PLC0415
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
        self._tmp.cleanup()

    def test_full_chain_via_workspace_init_entry(self):
        """init → materialize → MOD → FillSpec → compile (COMPILE CLEAN) →
        Spec Review → execute → deliver。

        compile 只认 prepare_manifest.json —— 本测试证明 materialize 投影
        (run-local compiler view) 直接驱动既有编译器 (零编译器改动), 且
        canonical 角色中立 manifest 同场落盘 (ADR 0018/0019)。"""
        wd = self.workdir

        # ── 1. 唯一准备入口: 一次 role-neutral --init (无 --target) ──
        proc = run_ws_init(
            wd, "--init", "--files", f"{SRC_FILE}|{SRC_FILE},{TGT_FILE}|{TGT_FILE}",
            "--sheets", f"{SRC_FILE}:{SRC_SHEET};{TGT_FILE}:{TGT_SHEET}",
            "--task", "T12 入口收链验收")
        self.assertEqual(proc.returncode, 0, proc.stderr[-800:])
        out = json.loads(proc.stdout)
        self.assertEqual(out["code"], "WORKSPACE_INIT_DONE")
        ws = json.loads((wd / "workspace_manifest.json").read_text(
            encoding="utf-8"))
        self.assertEqual(ws["kind"], "workspace_init", "canonical 事实空间在场")
        self.assertNotIn("target", ws, "role-neutral: workspace 无 target 角色")
        self.assertNotIn("fingerprints", ws,
                         "role-neutral: workspace 无二元指纹 (ADR 0018)")

        # ── 1b. Run Materialization: 投影 run-local compiler view ──
        sys.path.insert(0, str(SCRIPTS))
        from _fixtures.run_driver import materialize, target_entry_name
        materialize(wd,
                    sources=target_entry_name(SRC_FILE, SRC_SHEET),
                    target=target_entry_name(TGT_FILE, TGT_SHEET))
        manifest = json.loads((wd / "prepare_manifest.json").read_text(
            encoding="utf-8"))
        self.assertEqual(manifest["schema_version"], 2)
        self.assertEqual(manifest["target"]["name"],
                         f"{TGT_FILE.replace('.xlsx', '')}_{TGT_SHEET}")

        # guarded locators (fixture 契约: Cooling/Capacity/W 源 4 行 / 目标 5 行)
        src_guard = next(orig for vals, orig in load_flat_rows(wd, SRC_ENTRY)  # noqa: F841
                         if vals[1] == "Capacity" and vals[2] == "W")
        tgt_guard = next(orig for vals, orig in load_flat_rows(wd, TGT_ENTRY)
                         if vals[1] == "Capacity" and vals[2] == "W")

        # ── 2. MOD 裁决落盘 (compile C1–C4 一致性) ──
        (wd / "mod_resolution.json").write_text(
            json.dumps(mod_record(wd / "MODS_param"), ensure_ascii=False),
            encoding="utf-8")

        # ── 3. FillSpec 初稿 (同一 data-neutral canonical spec 构造器) ──
        spec = build_spec(manifest, (src_guard, tgt_guard))
        (wd / "fill_spec.yaml").write_text(
            yaml.safe_dump(spec, allow_unicode=True, sort_keys=False),
            encoding="utf-8")

        # ── 4. Compile (公共 CLI; 消费 materialize 投影视图) → COMPILE CLEAN ──
        proc = mx_run_py(wd, "compile_fill.py", "--spec", "fill_spec.yaml",
                         "--workdir", ".")
        self.assertEqual(proc.returncode, 0,
                         proc.stdout[-800:] + proc.stderr[-1500:])
        plan = json.loads((wd / "execution_plan.json").read_text(
            encoding="utf-8"))
        self.assertIsNotNone(plan["matrix"])
        self.assertEqual(len(plan["writes"]), N_WRITES)
        got = {(w["row"], w["col"]): w["value"] for w in plan["writes"]}
        self.assertEqual(got, EXPECTED_WRITES)

        # ── 5. Spec Review (唯一人工点; COMPILE CLEAN 之后, Execute 之前) ──
        proc = mx_run_py(wd, "spec_review.py", "--workdir", ".")
        self.assertEqual(proc.returncode, 0, proc.stderr[-800:])
        proc = mx_run_py(wd, "spec_review.py", "--workdir", ".", "--confirm")
        self.assertEqual(proc.returncode, 0, proc.stderr[-800:])
        self.assertTrue((wd / "review_confirm.json").is_file())

        # ── 6. Execute (BATCH_CHUNK_FAILED 文档化重试一次) ──
        proc = mx_run_py(wd, "execute_batch.py", "--plan", "execution_plan.json",
                         "--template", TGT_FILE, "--workdir", ".",
                         "--round", "1", "--render", "html")
        if proc.returncode != 0:
            failure = json.loads(
                (wd / "_draft_failure.json").read_text(encoding="utf-8"))
            self.assertEqual(failure["code"], "BATCH_CHUNK_FAILED",
                             "非瞬时执行失败不得静默重试: "
                             f"{proc.stdout[-600:]} {proc.stderr[-600:]}")
            proc = mx_run_py(wd, "execute_batch.py", "--plan",
                             "execution_plan.json", "--template", TGT_FILE,
                             "--workdir", ".", "--round", "2", "--render", "html")
        self.assertEqual(proc.returncode, 0,
                         proc.stdout[-800:] + proc.stderr[-1500:])
        receipt = json.loads((wd / "draft_receipt.json").read_text(
            encoding="utf-8"))
        self.assertTrue(receipt["structural"]["pass"])
        self.assertEqual(receipt["readback"]["total"], N_WRITES)
        self.assertEqual(receipt["issue_delta"]["new_issues"], 0)

        # ── 7. Deliver (promote_output 哈希核对复制) ──
        final_path = wd / "final.xlsx"
        proc = mx_run_py(wd, "promote_output.py", "--workdir", ".",
                         "--final", str(final_path))
        self.assertEqual(proc.returncode, 0, proc.stderr[-800:])
        self.assertTrue(final_path.is_file())
        final_receipt = json.loads((wd / "final_receipt.json").read_text(
            encoding="utf-8"))
        self.assertEqual(final_receipt["final_sha256"],
                         final_receipt["draft_sha256"],
                         "交付哈希一致 (验证后绝不再次填充)")
        # internal-only 值不得泄入最终工作簿 (zip/XML 扫描)
        final_text = zip_text(final_path)
        for token in INTERNAL_TOKENS:
            self.assertNotIn(token, final_text,
                             f"internal-only token {token!r} 泄入最终工作簿")


if __name__ == "__main__":
    unittest.main()
