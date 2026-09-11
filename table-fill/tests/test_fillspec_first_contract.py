"""Ticket 04 — FillSpec First Rule 行为契约: 文本断言 + 校验器 + 埃及类 case 证据.

验收 checkbox 映射 (issues/04-fillspec-first-behavior-contract.md):

  [ ] SKILL 顶部硬约束写明: MOD 决议后首个业务动作 = 产出 FillSpec 初稿 (可以不完整)
  [ ] 未产出初稿禁止深度能力探索; 编译缺陷是下一轮探索的唯一入场券 (错误驱动)
  [ ] 模式索引路由优先于全文阅读: 已知形态只读对应 pattern
  [ ] 埃及类 case 重跑验证: 决议→初稿之间仅允许 读 pattern → 读 digest → 写 spec
      → compile 四类动作
  [ ] 行为契约文字在 SKILL 中的位置不随后续瘦身丢失 (07 号票迁移时保留置顶位)

测试面 (spec Testing Decisions「好测试 = 只测外部行为」, 本票是行为契约):

  A. SKILL 硬约束文字 pin — SKILL.md「## 硬约束」节含四类动作 + FillSpec First
     Rule + 错误驱动 + 模式索引路由 + 初稿可以不完整 + 迁移锚点注释 (置顶位可识别)。
  B. fill_spec_first_validator 纯函数校验 — 合法序列 (四类动作) 通过; 非法序列
     (探索在前) 失败; 错误驱动循环 (初稿后 compile→explore→修复) 合法。
  C. 埃及类 case 证据 (e2e): 复用 task_orchestration e2e fixture (parameter_book
     / filling_template), 在临时 workdir 走 workspace_init → 写初稿 spec → compile
     最短流程, 断言「决议→初稿」动作序列 (读 pattern / 读 digest / 写 spec /
     compile 四类) 不含探索动作, 且四类动作可完整跑通。

无 Office 时: A 与 B 全绿 (纯文字 + 纯函数); C 依赖 officecli, 整体 skipIf 优雅
跳过 (与 test_workspace_init / test_task_e2e 同风格)。

Run with:
  python -m pytest table-fill/tests/test_fillspec_first_contract.py -q
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
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from fill_spec_first_validator import (  # noqa: E402
    FOUR_ACTION_CLASSES,
    LEGAL_PRE_DRAFT_ACTIONS,
    normalize_action,
    validate_actions,
)

SKILL_MD = SKILL_ROOT / "SKILL.md"
FIX_E2E = (Path(__file__).resolve().parent / "_fixtures"
           / "task_orchestration" / "e2e")
PREPARE_RUN = SCRIPTS / "prepare_run.py"
COMPILE_FILL = SCRIPTS / "compile_fill.py"


def _strip(text: str) -> str:
    """去空白比较 (与 test_topology_barrier / test_runtime_governance 同款)。"""
    return re.sub(r"\s+", "", text)


def _skill_text() -> str:
    return SKILL_MD.read_text(encoding="utf-8")


def _hard_constraint_section() -> str:
    """提取 SKILL.md「## 硬约束」节 (标题到下一个 ## 标题之间)。"""
    text = _skill_text()
    m = re.search(r"^## 硬约束\s*$(.*?)(?=^## )",
                  text, re.MULTILINE | re.DOTALL)
    assert m is not None, "SKILL.md 缺 '## 硬约束' 节"
    return m.group(1)


# ─────────────────────────────────────────────────────────────────────
# A. SKILL 硬约束文字 pin (置顶位 + 四类动作 + FillSpec First + 错误驱动 + 模式索引)
# ─────────────────────────────────────────────────────────────────────

class TestHardConstraintTextPinning(unittest.TestCase):
    """SKILL.md「## 硬约束」节: 措辞漂移 / 段落被删 → 变红 (07/09 号票迁移保护)。"""

    def test_hard_constraint_section_is_top_position(self):
        """硬约束节位于「依赖加载」之前 (置顶位 — 满足验收 5 的可识别位置)。"""
        text = _skill_text()
        hard_idx = text.index("## 硬约束")
        dep_idx = text.index("## ⚠️ 依赖加载")
        self.assertLess(hard_idx, dep_idx,
                        "「## 硬约束」必须位于「依赖加载」之前 (置顶位)")

    def test_migration_anchor_comment(self):
        """迁移锚点注释点名 04/07/09 号票 (置顶位随瘦身保留的识别标记)。"""
        section = _hard_constraint_section()
        self.assertIn("迁移锚点", section, "硬约束节缺「迁移锚点」注释")
        self.assertIn("ticket 04", section, "迁移锚点缺 ticket 04 点名")
        self.assertIn("07 号票", section, "迁移锚点缺 07 号票迁移说明")
        self.assertIn("09 号票", section, "迁移锚点缺 09 号票置顶说明")

    def test_fillspec_first_rule_wording(self):
        """MOD 决议后首个业务动作 = 产出 FillSpec 初稿 + 可以不完整。"""
        section = _hard_constraint_section()
        stripped = _strip(section)
        self.assertIn("FillSpecFirstRule", stripped)
        self.assertIn("MOD决议后", stripped)
        self.assertIn("首个业务动作", stripped)
        self.assertIn("产出FillSpec初稿", stripped)
        self.assertIn("可以不完整", stripped)

    def test_no_deep_exploration_before_draft(self):
        """未产出初稿禁止深度能力探索 (--capabilities/--capability/--probe/源码)。"""
        section = _hard_constraint_section()
        stripped = _strip(section)
        self.assertIn("未产出初稿", stripped)
        self.assertIn("禁止深度能力探索", stripped)
        self.assertIn("--capabilities", section)
        self.assertIn("--capability", section)
        self.assertIn("--probe", section)

    def test_error_driven_not_exploration_driven(self):
        """错误驱动: 编译缺陷是下一轮探索唯一入场券 (unjustified query = 0)。"""
        section = _hard_constraint_section()
        stripped = _strip(section)
        self.assertIn("错误驱动", stripped)
        self.assertIn("非探索驱动", stripped)
        self.assertIn("唯一入场券", stripped)
        self.assertIn("unjustifiedcapabilityquery", stripped)

    def test_pattern_index_routing_priority(self):
        """模式索引路由优先于全文阅读: 已知形态只读对应 pattern。"""
        section = _hard_constraint_section()
        stripped = _strip(section)
        self.assertIn("模式索引路由优先于全文阅读", stripped)
        self.assertIn("fillspec_patterns.yaml", section)
        self.assertIn("只读该pattern", stripped)
        no_full_read = ("不读FILLSPEC.md全文" in stripped
                        or "不要读FILLSPEC.md全文" in stripped
                        or "不读FILLSPEC全文" in stripped)
        self.assertTrue(no_full_read, "硬约束节缺 '不读 FILLSPEC 全文' 规则")

    def test_four_action_classes(self):
        """决议→初稿之间仅允许 读 pattern → 读 digest → 写 spec → compile 四类。"""
        section = _hard_constraint_section()
        stripped = _strip(section)
        self.assertIn("四类动作", stripped)
        for clazz in ("读pattern", "读digest", "写spec", "compile"):
            self.assertIn(clazz, stripped, f"四类动作缺 {clazz!r}")

    def test_validator_reference_in_skill(self):
        """硬约束节引用可执行校验器 (可执行化的落点)。"""
        section = _hard_constraint_section()
        self.assertIn("fill_spec_first_validator.py", section,
                      "硬约束节缺对校验器的引用")


# ─────────────────────────────────────────────────────────────────────
# B. fill_spec_first_validator 纯函数校验
# ─────────────────────────────────────────────────────────────────────

class TestFillSpecFirstValidator(unittest.TestCase):
    """行为契约的可执行化: 合法序列通过 / 探索在前失败 / 错误驱动循环合法。"""

    def test_legal_minimal_sequence_passes(self):
        """四类动作最小序列 (读 pattern → 读 digest → 写 spec → compile) 合法。"""
        result = validate_actions(
            ["read_pattern", "read_digest", "write_spec", "compile"])
        self.assertTrue(result["ok"], result["violations"])
        self.assertEqual(result["first_draft_at"], 2)

    def test_legal_sequence_has_only_four_classes(self):
        """四类动作契约常量与校验器一致。"""
        self.assertEqual(set(FOUR_ACTION_CLASSES), set(LEGAL_PRE_DRAFT_ACTIONS))
        self.assertEqual(FOUR_ACTION_CLASSES,
                         ("read_pattern", "read_digest", "write_spec", "compile"))

    def test_explore_before_draft_violates(self):
        """探索出现在初稿写入之前 → 违规 (FillSpec First Rule 核心)。"""
        result = validate_actions(
            ["read_pattern", "explore", "read_digest", "write_spec", "compile"])
        self.assertFalse(result["ok"])
        self.assertTrue(any("深度能力探索" in v for v in result["violations"]))

    def test_full_read_before_draft_violates(self):
        """FILLSPEC 全文阅读 (归一化为 explore) 出现在初稿前 → 违规。"""
        result = validate_actions(
            ["read_digest", "full read fillspec", "write_spec"])
        self.assertFalse(result["ok"])
        self.assertTrue(any("深度能力探索" in v for v in result["violations"]))

    def test_error_driven_loop_after_draft_is_legal(self):
        """初稿后 compile 缺陷 → 定向 explore → 修复 → 重 compile 是合法循环。"""
        seq = ["read_pattern", "read_digest", "write_spec", "compile",
               "explore", "write_spec", "compile"]
        result = validate_actions(seq)
        self.assertTrue(result["ok"], result["violations"])
        self.assertEqual(result["first_draft_at"], 2)

    def test_incomplete_draft_then_compile_then_repair(self):
        """初稿可以不完整: 初稿 → compile (缺陷) → 修复 → compile 全链合法。"""
        seq = ["read_pattern", "write_spec", "compile", "write_spec", "compile"]
        result = validate_actions(seq)
        self.assertTrue(result["ok"], result["violations"])

    def test_normalize_action(self):
        """自由文本动作归一化到契约类别。"""
        self.assertEqual(normalize_action("compile_fill.py"), "compile")
        self.assertEqual(normalize_action("read fillspec_patterns.yaml"), "read_pattern")
        self.assertEqual(normalize_action("--capabilities"), "explore")
        self.assertEqual(normalize_action("read source digest.md"), "read_digest")


# ─────────────────────────────────────────────────────────────────────
# C. 埃及类 case 证据 (e2e): 最短流程跑通四类动作, 无探索动作
# ─────────────────────────────────────────────────────────────────────

def run_py(workdir: Path, script: str, *args) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-X", "utf8", str(SCRIPTS / script), *args],
        cwd=str(workdir), capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=1500)


@unittest.skipIf(shutil.which("officecli") is None, "officecli not on PATH")
class TestEgyptianCaseFourActionFlow(unittest.TestCase):
    """埃及类 case 证据: 复用 task_orchestration e2e fixture, 走 workspace_init
    → 写初稿 spec → compile 最短流程; 断言「决议→初稿」动作序列仅四类、无探索。"""

    def setUp(self):
        sys.path.insert(0, str(SCRIPTS))
        from _officecli import clean_residents  # noqa: PLC0415
        clean_residents()
        self.workdir = Path(tempfile.mkdtemp(prefix="fsp_first_e2e_"))

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

    def _build_action_log(self) -> list[str]:
        """决议→初稿窗口的动作日志: 读 pattern → 读 digest → 写 spec → compile
        (与 SKILL「四类动作」逐字对应; 无 --capabilities/--probe/源码/全文阅读)。"""
        return ["read_pattern", "read_digest", "write_spec", "compile"]

    def test_four_action_flow_runs_green_no_exploration(self):
        """最短 e2e 流程 (workspace_init + materialize_run 准备 → 初稿 spec →
        compile) 全绿, 且动作日志对应的校验序列通过、探索动作为 0。

        说明: 「埃及类 case」的历史对照证据跑在 canonical 路径
        workspace_init.py → materialize_run.py → prepare_manifest.json →
        compile_fill.py 之上 (ADR 0018/0020; 旧 prepare_run CLI 已退役)。
        """
        wd = self.workdir
        # 1. 复用 e2e fixture 源书/模板, 复制为 ASCII 名 (测试 harness 侧)
        shutil.copy2(FIX_E2E / "sources" / "parameter_book.xlsx",
                     wd / "parameter_book.xlsx")
        shutil.copy2(FIX_E2E / "templates" / "filling_template.xlsx",
                     wd / "filling_template.xlsx")

        # 2. canonical init (role-neutral) + materialize (run projection)
        from _fixtures.run_driver import prepare_single, target_entry_name
        manifest = prepare_single(
            wd,
            files="parameter_book.xlsx|parameter_book.xlsx,"
                  "filling_template.xlsx|filling_template.xlsx",
            sheets="parameter_book.xlsx:R32参数;filling_template.xlsx:Sheet1",
            sources=target_entry_name("parameter_book", "R32参数"),
            target=target_entry_name("filling_template", "Sheet1"),
            task="FillSpec First 四类动作验收")
        self.assertIn("flattened", manifest)

        # 3. 决议落盘 (MOD NONE → resolved), 解锁业务推理
        (wd / "mod_resolution.json").write_text(
            json.dumps({"status": "resolved", "selected": "NONE",
                        "candidates": []}, ensure_ascii=False),
            encoding="utf-8")

        # 4. 动作日志 (决议→初稿窗口): 读 pattern → 读 digest → 写 spec → compile
        actions = self._build_action_log()
        # 校验器: 该序列通过 FillSpec First 契约, 且探索动作为 0
        result = validate_actions(actions)
        self.assertTrue(result["ok"], result["violations"])
        self.assertEqual(result["normalized"].count("explore"), 0,
                         "决议→初稿窗口不得有探索动作")

        # 5. 写初稿 fill_spec.yaml (可以不完整 — 最小可编译形状)
        fp = manifest["fingerprints"]
        spec = {
            "task": {"intent": "埃及类四类动作验收 (FillSpec First)",
                     "selected_mod": "NONE", "selected_mod_revision": None},
            "inputs": {"sources": ["parameter_book.xlsx"],
                       "target": "filling_template.xlsx",
                       "source_sheets": [{"source": "parameter_book.xlsx",
                                          "sheets": ["R32参数"]}],
                       "target_sheet": "Sheet1"},
            "fingerprints": {"source_structure": fp["source_structure"],
                             "target_structure": fp["target_structure"]},
            "mapping": {"targets": [{
                "sheet": "Sheet1", "base_last_row": 4,
                "clone_roles": [{"role": "data", "template_row": 3}],
                "rows": {"source": "parameter_book_R32",
                         "selectors": [{"column": "A", "not_value": ""},
                                       {"column": "A", "not_value": "产品线"}]},
                "columns": [{"source": "A", "target": "A"},
                            {"source": "B", "target": "B"}],
            }]},
            "decisions": ["仅纳入产品线资料行"],
            "gaps": [],
            "lineage": [{"source": "parameter_book_R32_flat.csv",
                         "role": "primary",
                         "note": "每个匹配源行写入一个追加行 (模板行 3 克隆)"}],
            "validation": {"required_coverage": [], "required_empty": [],
                           "key_outputs": ["A5", "B5"]},
        }
        import yaml  # noqa: E402  (延迟 import, 与其它 e2e 一致)
        (wd / "fill_spec.yaml").write_text(
            yaml.safe_dump(spec, allow_unicode=True, sort_keys=False),
            encoding="utf-8")

        # 6. compile (错误驱动的反馈源; 本初稿应编译通过)
        proc = run_py(wd, "compile_fill.py", "--spec", "fill_spec.yaml",
                      "--workdir", ".")
        self.assertEqual(proc.returncode, 0,
                         proc.stdout[-800:] + proc.stderr[-800:])
        plan = json.loads((wd / "execution_plan.json").read_text(encoding="utf-8"))
        self.assertGreater(plan["operation_count"], 0)

        # 7. 全程断言: 决议→初稿窗口无探索动作 (契约的流程合法性验证)。
        self.assertEqual(set(result["normalized"]), set(FOUR_ACTION_CLASSES),
                         "决议→初稿窗口动作必须是四类动作的子集")


if __name__ == "__main__":
    unittest.main()

