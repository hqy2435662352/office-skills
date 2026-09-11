"""Ticket 05 — Spec Review 人工点落地: 三节摘要 + 哈希绑定确认 + 多 run + Gate 并存.

验收 checkbox 映射 (issues/05-spec-review-checkpoint.md):

  [ ] 初稿后生成三节摘要, 无 YAML 原文转储
  [ ] 确认绑定 spec 哈希; spec 改动后旧确认失效 (fail-closed)
  [ ] 多 run 摘要一次呈现覆盖全部 run
  [ ] 与现有 Gate 并存期间两条路径各自可运行、互不破坏
  [ ] contract test: 确认绑定哈希、改动即失效

测试面 (spec Testing Decisions「好测试 = 只测外部行为」):
  - spec_review.py CLI (subprocess) + 产物 (spec_review.json / review_confirm.json /
    摘要 stdout)。只测外部行为与结构化输出, 不测实现细节。
  - build_summary 纯函数: 三节结构 + 业务语言重组 (非 YAML 转储 — 断言摘要文本
    不含填充 spec 的关键 YAML 键片段)。
  - 哈希绑定: --confirm 重算哈希, 改动 → exit 3 SPEC_HASH_DRIFT (旧确认失效)。

无 Office 依赖 (摘要器不依赖 compile 成功 / 不需要 workdir 里的展平 CSV 也能
回退为裸列字母, 故全程不依赖 officecli)。

Run with:
  python -m pytest table-fill/tests/test_spec_review.py -q
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import spec_review  # noqa: E402
from spec_review import build_summary, render_summary  # noqa: E402


def run_cli(workdir: Path, *args) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-X", "utf8", str(SCRIPTS / "spec_review.py"),
         "--workdir", str(workdir), *args],
        capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=300,
    )


def _min_spec(**overrides) -> dict:
    """最小可填 fill_spec (映射/转换/排除三节都有内容)。"""
    spec = {
        "task": {"intent": "迁移毛利数据到报价汇总", "selected_mod": "NONE",
                 "selected_mod_revision": None},
        "inputs": {"sources": ["source_maoli.xlsx"], "target": "target.xlsx",
                   "source_sheets": [{"source": "source_maoli.xlsx",
                                      "sheets": ["毛利"]}],
                   "target_sheet": "报价汇总"},
        "fingerprints": {"source_structure": "a", "target_structure": "b"},
        "mapping": {
            "transforms": [
                {"name": "fin_translate", "function": "controlled_translation",
                 "translations": {"宽片": "wide fin"}},
                {"name": "strip_sku", "function": "strip"},
            ],
            "lookups": [
                {"name": "sku_fields", "from": "inheritance.json",
                 "key_column": "G", "fields": ["factory_model"], "missing": "empty"},
            ],
            "targets": [{
                "sheet": "报价汇总", "base_last_row": 21,
                "clone_roles": [{"role": "data", "template_row": 10}],
                "rows": {"source": "source_maoli_FRESH",
                         "selectors": [
                             {"column": "G", "pattern": "Z*"},
                             {"column": "A", "not_pattern": "拖多*"},
                         ]},
                "columns": [
                    {"source": "A", "target": "A"},
                    {"source": "G", "target": "C",
                     "transform": "strip_sku"},
                    {"target": "D", "lookup": {"name": "sku_fields",
                                               "field": "factory_model",
                                               "missing": "empty"}},
                    {"target": "J", "value": "0"},
                ],
                "formulas": {"per_row": {"O": "IFERROR(ROUND(J{r},2),0)"}},
                "nulls": [{"col": "L", "rows": "all"}],
            }],
        },
        "decisions": ["只迁移分体单冷机型"],
        "gaps": ["源目标价缺失机型留空"],
        "lineage": [],
        "validation": {"required_coverage": [], "required_empty": [],
                       "key_outputs": ["A25"]},
    }
    spec.update(overrides)
    return spec


def _write_spec(workdir: Path, spec: dict, name="fill_spec.yaml") -> Path:
    p = workdir / name
    p.write_text(yaml.safe_dump(spec, allow_unicode=True, sort_keys=False),
                 encoding="utf-8")
    return p


def _write_task_multi(workdir: Path, specs: list[dict]) -> Path:
    """写 task.yaml + runs/<id>/fill_spec.yaml (多 run)。"""
    runs = []
    for i, s in enumerate(specs):
        rid = f"r{i}"
        d = workdir / "runs" / rid
        d.mkdir(parents=True, exist_ok=True)
        (d / "fill_spec.yaml").write_text(
            yaml.safe_dump(s, allow_unicode=True, sort_keys=False),
            encoding="utf-8")
        runs.append({"id": rid})
    task = {"task": {"id": "t"}, "runs": runs}
    p = workdir / "task.yaml"
    p.write_text(yaml.safe_dump(task, allow_unicode=True, sort_keys=False),
                 encoding="utf-8")
    return p


# ─────────────────────────────────────────────────────────────────────
# 验收 1: 三节摘要 + 业务语言重组, 无 YAML 转储
# ─────────────────────────────────────────────────────────────────────

class SummaryStructureTests(unittest.TestCase):
    """build_summary 纯函数: 三节 + 业务语言重组 (非 YAML 转储)。"""

    def test_three_sections_present(self):
        with tempfile.TemporaryDirectory() as td:
            spec = _min_spec()
            s = build_summary(Path(td), spec)
        self.assertEqual(set(s.keys()),
                         {"intent", "mapping", "transforms", "exclusions"})
        self.assertIsInstance(s["mapping"], list)
        self.assertIsInstance(s["transforms"], list)
        self.assertIsInstance(s["exclusions"], list)

    def test_mapping_uses_business_columns(self):
        """映射节描述 源列→目标列 语义 (含查表/常量), 用业务措辞。"""
        with tempfile.TemporaryDirectory() as td:
            s = build_summary(Path(td), _min_spec())
        joined = "\n".join(s["mapping"])
        self.assertIn("目标 sheet「报价汇总」", joined)
        self.assertIn("源数据「source_maoli_FRESH」", joined)
        self.assertIn("目标列 D ← 查表「sku_fields」", joined)
        self.assertIn("目标列 J 写入常量 '0'", joined)

    def test_transforms_section_describes_named_transforms(self):
        with tempfile.TemporaryDirectory() as td:
            s = build_summary(Path(td), _min_spec())
        joined = "\n".join(s["transforms"])
        self.assertIn("受控翻译「fin_translate」", joined)
        self.assertIn("宽片→wide fin", joined)
        self.assertIn("去首尾空白「strip_sku」", joined)
        self.assertIn("逐行公式 列 O", joined)

    def test_exclusions_section_lists_exclusions_and_gaps(self):
        with tempfile.TemporaryDirectory() as td:
            s = build_summary(Path(td), _min_spec())
        joined = "\n".join(s["exclusions"])
        self.assertIn("排除「A」列匹配 '拖多*'", joined)
        self.assertIn("业务决策: 只迁移分体单冷机型", joined)
        self.assertIn("数据缺口: 源目标价缺失机型留空", joined)

    def test_mapping_uses_csv_headers_when_available(self):
        """展平 CSV 存在时, 摘要用真实表头/角色词汇 (源列字母 → 业务表头名)。"""
        with tempfile.TemporaryDirectory() as td:
            wd = Path(td)
            csv = ("产品线,型号,容量,数量,备注\n"
                   "R32,ABC,26,10,x\n")
            (wd / "src_flat.csv").write_text(csv, encoding="utf-8")
            spec = _min_spec()
            spec["mapping"]["targets"][0]["rows"]["source"] = "src"
            spec["mapping"]["targets"][0]["columns"] = [
                {"source": "A", "target": "A"},
                {"source": "B", "target": "B"},
                {"source": "E", "target": "C", "transform": "strip"},
            ]
            s = build_summary(wd, spec)
        joined = "\n".join(s["mapping"])
        self.assertIn("「产品线」(列 A)", joined)
        self.assertIn("「型号」(列 B)", joined)
        self.assertIn("「备注」(列 E)", joined)

    def test_no_raw_yaml_dump(self):
        """总结不含原始 YAML 结构键片段 (columns:/selectors:/clone_roles:
        /base_last_row:/nulls:/mapping:/targets: 等不出现于文本)。"""
        with tempfile.TemporaryDirectory() as td:
            s = build_summary(Path(td), _min_spec())
        text = render_summary(s)
        for fragment in ("columns:", "selectors:", "clone_roles:", "base_last_row:",
                         "nulls:", "mapping:", "targets:", "fingerprints:",
                         "required_coverage:", "  - column:"):
            self.assertNotIn(fragment, text,
                             f"摘要不应包含原始 YAML 键 {fragment!r}")


# ─────────────────────────────────────────────────────────────────────
# 验收 2 + 5 (contract test): 确认绑定哈希, 改动即失效 (fail-closed)
# ─────────────────────────────────────────────────────────────────────

class HashBindingTests(unittest.TestCase):
    """确认绑定 fill_spec sha256; 改动后旧确认失效 (fail-closed)。"""

    def test_confirm_binds_spec_hash(self):
        with tempfile.TemporaryDirectory() as td:
            wd = Path(td)
            _write_spec(wd, _min_spec())
            run_cli(wd)  # 呈现
            proc = run_cli(wd, "--confirm")
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            conf = json.loads(
                (wd / "review_confirm.json").read_text(encoding="utf-8"))
            self.assertEqual(conf["kind"], "review_confirm")
            self.assertEqual(len(conf["runs"]), 1)
            self.assertTrue(conf["runs"][0]["fill_spec_sha256"])
            # 呈现记录里的哈希 == 确认记录里的哈希 (同一 spec 版本)
            presented = json.loads(
                (wd / "spec_review.json").read_text(encoding="utf-8"))
            self.assertEqual(presented["runs"][0]["fill_spec_sha256"],
                             conf["runs"][0]["fill_spec_sha256"])

    def test_change_after_present_invalidates(self):
        """契约核心: 摘要→改 spec→再确认被拒 (SPEC_HASH_DRIFT, exit 3)。"""
        with tempfile.TemporaryDirectory() as td:
            wd = Path(td)
            _write_spec(wd, _min_spec())
            run_cli(wd)  # 呈现 (记录哈希 H1)
            # 改 spec → 哈希变
            changed = _min_spec()
            changed["mapping"]["targets"][0]["columns"].append(
                {"source": "B", "target": "B"})
            _write_spec(wd, changed)
            proc = run_cli(wd, "--confirm")
            self.assertEqual(proc.returncode, 3,
                             f"改动后确认必须失败: {proc.stdout} {proc.stderr}")
            self.assertEqual(json.loads(proc.stderr)["code"], "SPEC_HASH_DRIFT")
            self.assertFalse((wd / "review_confirm.json").exists(),
                             "漂移后不得写出确认记录 (fail-closed)")

    def test_reconfirm_succeeds_after_regenerate(self):
        """改回/重新生成摘要 → 再确认成功 (旧确认失效后可重确认)。"""
        with tempfile.TemporaryDirectory() as td:
            wd = Path(td)
            _write_spec(wd, _min_spec())
            run_cli(wd)
            proc = run_cli(wd, "--confirm")
            self.assertEqual(proc.returncode, 0)
            # 改 spec → 重新生成摘要 (新哈希 H2) → 再确认成功
            changed = _min_spec()
            changed["gaps"].append("新缺口")
            _write_spec(wd, changed)
            run_cli(wd)  # 重新呈现 (覆盖 spec_review.json 里的哈希)
            proc = run_cli(wd, "--confirm")
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertTrue((wd / "review_confirm.json").is_file())

    def test_confirm_without_present_fails(self):
        with tempfile.TemporaryDirectory() as td:
            wd = Path(td)
            _write_spec(wd, _min_spec())
            proc = run_cli(wd, "--confirm")  # 未先呈现
            self.assertEqual(proc.returncode, 3)
            self.assertEqual(json.loads(proc.stderr)["code"], "REVIEW_NOT_PRESENTED")


# ─────────────────────────────────────────────────────────────────────
# 验收 3: 多 run 一次摘要覆盖全部 run
# ─────────────────────────────────────────────────────────────────────

class MultiRunTests(unittest.TestCase):
    """task.yaml → 一次 --task 摘要覆盖全部 run, 一次确认绑定全部 run 哈希。"""

    def test_multi_run_one_summary_covers_all(self):
        with tempfile.TemporaryDirectory() as td:
            wd = Path(td)
            specs = [_min_spec(), _min_spec()]
            specs[1]["task"]["intent"] = "第二个 run 的意图"
            _write_task_multi(wd, specs)
            proc = run_cli(wd, "--task", "task.yaml")
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            rec = json.loads((wd / "spec_review.json").read_text(encoding="utf-8"))
            self.assertEqual(len(rec["runs"]), 2)
            ids = {r["run_id"] for r in rec["runs"]}
            self.assertEqual(ids, {"r0", "r1"})
            # 每个 run 的哈希分别记录
            for r in rec["runs"]:
                self.assertTrue(r["fill_spec_sha256"])
            # 一次呈现覆盖两个 run 的摘要 (stdout 含两节标题)
            self.assertEqual(proc.stdout.count("=== Spec Review — "), 2)

    def test_multi_run_confirm_binds_all_specs(self):
        with tempfile.TemporaryDirectory() as td:
            wd = Path(td)
            _write_task_multi(wd, [_min_spec(), _min_spec()])
            run_cli(wd, "--task", "task.yaml")
            proc = run_cli(wd, "--task", "task.yaml", "--confirm")
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            conf = json.loads(
                (wd / "review_confirm.json").read_text(encoding="utf-8"))
            self.assertEqual(len(conf["runs"]), 2)

    def test_multi_run_one_run_changed_invalidates(self):
        """多 run 中任一 run 的 spec 改动 → 整体 fail-closed (旧确认失效)。"""
        with tempfile.TemporaryDirectory() as td:
            wd = Path(td)
            _write_task_multi(wd, [_min_spec(), _min_spec()])
            run_cli(wd, "--task", "task.yaml")
            # 改 run r1 的 spec
            s1 = _min_spec()
            s1["gaps"].append("r1 改动了")
            (wd / "runs" / "r1" / "fill_spec.yaml").write_text(
                yaml.safe_dump(s1, allow_unicode=True, sort_keys=False),
                encoding="utf-8")
            proc = run_cli(wd, "--task", "task.yaml", "--confirm")
            self.assertEqual(proc.returncode, 3)
            self.assertEqual(json.loads(proc.stderr)["code"], "SPEC_HASH_DRIFT")


# ─────────────────────────────────────────────────────────────────────
# 验收 4（v3 收敛已改写）: Spec Review 独立产物/入口；Gate 已退役
# ─────────────────────────────────────────────────────────────────────

class CoexistenceTests(unittest.TestCase):
    """Spec Review 独立产物/入口（Gate 已退役，不再有 marker）。"""

    def test_review_artifacts_distinct_names(self):
        """Spec Review 产物名与交付产物名不相交。"""
        self.assertEqual(spec_review.REVIEW_JSON_NAME, "spec_review.json")
        self.assertEqual(spec_review.CONFIRM_JSON_NAME, "review_confirm.json")
        self.assertNotIn(spec_review.REVIEW_JSON_NAME,
                         ["final_receipt.json", "draft_receipt.json"])
        self.assertNotIn(spec_review.CONFIRM_JSON_NAME,
                         ["final_receipt.json", "draft_receipt.json"])

    def test_review_writes_no_gate_marker(self):
        """Spec Review 不写任何 gate marker（交付无 marker 门槛）。"""
        with tempfile.TemporaryDirectory() as td:
            wd = Path(td)
            _write_spec(wd, _min_spec())
            run_cli(wd)
            run_cli(wd, "--confirm")
            # 全程不产生任何 .gate3_* marker
            self.assertFalse(list(wd.glob(".gate3_*")))

    def test_skip_review_explicit_path(self):
        """--skip-review 是唯一跳过路径 (用户显式指示)。"""
        with tempfile.TemporaryDirectory() as td:
            wd = Path(td)
            _write_spec(wd, _min_spec())
            proc = run_cli(wd, "--skip-review")
            self.assertEqual(proc.returncode, 0)
            self.assertEqual(json.loads(proc.stdout)["code"], "REVIEW_SKIPPED")
            # 跳过不写 spec_review.json / review_confirm.json
            self.assertFalse((wd / "spec_review.json").exists())
            self.assertFalse((wd / "review_confirm.json").exists())


if __name__ == "__main__":
    unittest.main()
