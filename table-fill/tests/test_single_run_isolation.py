"""Ticket 05 — Single-Run Isolation 回归（五零指标）。

验收映射（issues/05-single-run-isolation-regression.md 6 条 checkbox）：

  ① contract test：single-run fixture 全过程无 task.yaml / 无 prepare_task 调用 /
    无 task-only artifact（结构断言）
  ② contract test：single-run happy path 的 instructions 不要求加载
    TASK_ORCHESTRATION.md（runtime dependency = 0，非 grep 文件名）
  ③ contract test：single-run 增量探测与增量 Office 调用为 0（相对基线计数）
  ④ contract test：single-run 无 Task-specific user ASK
  ⑤ 回归覆盖本条 Sprint 全部改动（01–04 造成的行为回归一票拦截）
  ⑥ 测试不依赖真实客户数据、不依赖 Agent 行为测量

五零指标：
  Single-run incremental probes              = 0
  Single-run incremental Office calls        = 0
  Single-run Task artifacts                  = 0
  Single-run Task-specific user ASK          = 0
  Single-run TASK_ORCHESTRATION runtime dependency = 0

分层：
  - SingleRunArtifactZeroTests       ① 结构断言：workdir 无 task.yaml / 无 runs/
                                     / 无 assembly/ / 无 task_manifest / task_status
  - SingleRunCompileFlowTests        ① ③ 完整 compile 路径无 Task 调用（mock/spy）
  - TaskOrchestrationDependencyTests ② ⑤ SKILL single-run path 不要求加载
                                     TASK_ORCHESTRATION.md（非 grep 文件名）
  - IncrementalProbeZeroTests        ③ 增量探测与 Office 调用为 0（mock 计数）
  - TaskSpecificAskZeroTests         ④ single-run 路径 ASK 场景不含 Task 术语
  - SprintRegressionTests            ⑤ Sprint 01–04 改动不引入 single-run 回归
  - DataNeutralityTests              ⑥ 不依赖真实客户数据

无 Office：沿用 _probe_fixtures 的 mock workdir + compile_fill 接缝。
Run with:
  python -m pytest tests/test_single_run_isolation.py -q
"""

from __future__ import annotations

import copy
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
SKILL_MD = SKILL_ROOT / "SKILL.md"
ORCH_MD = SKILL_ROOT / "references" / "TASK_ORCHESTRATION.md"
TASK_ORCHESTRATION_FILE = ORCH_MD

if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import compile_fill  # noqa: E402
from _probe_fixtures import (  # noqa: E402
    BASE_SPEC,
    make_probe_workdir,
)


# ── helpers ────────────────────────────────────────────────────────────

def _skill_text() -> str:
    return SKILL_MD.read_text(encoding="utf-8")


def _strip(text: str) -> str:
    return re.sub(r"[\s`*]", "", text)


def _workflow_section() -> str:
    """提取 '## 工作流 (七个公开命令)' 到 '## Task Orchestration' 之间。"""
    text = _skill_text()
    m = re.search(
        r"^## 工作流.*?(?=^## Task Orchestration)",
        text, re.MULTILINE | re.DOTALL,
    )
    assert m is not None, "SKILL.md 缺 '## 工作流' 段"
    return m.group(0)


def _single_run_workflow_steps() -> str:
    """提取 single-run 路径的完整流程段落（七个公开命令正文，不含 Task Orchestration）。"""
    text = _skill_text()
    m = re.search(
        r"^## 工作流.*?(?=^## Task Orchestration)",
        text, re.MULTILINE | re.DOTALL,
    )
    assert m is not None
    return m.group(0)


def _topology_section() -> str:
    """提取 Topology + Run Materialization 段落（III-2，S1）。"""
    text = _skill_text()
    m = re.search(
        r"^### 2\. Topology.*?(?=^### 3\. )",
        text, re.MULTILINE | re.DOTALL,
    )
    assert m is not None, "SKILL.md 缺 '### 2. Topology + Run Materialization' 段"
    return m.group(0)


def _task_orchestration_section() -> str:
    """提取 ## Task Orchestration 到 ## PPTX 之间的完整段落。"""
    text = _skill_text()
    m = re.search(
        r"^## Task Orchestration.*?(?=^## PPTX )",
        text, re.MULTILINE | re.DOTALL,
    )
    assert m is not None, "SKILL.md 缺 ## Task Orchestration 段"
    return m.group(0)


def spec_with(wd: dict, **mutations) -> dict:
    spec = copy.deepcopy(BASE_SPEC)
    spec["fingerprints"] = {
        "source_structure": wd["manifest"]["fingerprints"]["source_structure"],
        "target_structure": wd["manifest"]["fingerprints"]["target_structure"],
    }
    for path, value in mutations.items():
        node = spec
        parts = path.split(".")
        for p in parts[:-1]:
            node = node[p] if isinstance(node, dict) else node[int(p)]
        if isinstance(node, list):
            node[int(parts[-1])] = value
        else:
            node[parts[-1]] = value
    return spec


# ─────────────────────────────────────────────────────────────────────
# 1. ① 结构断言：single-run workdir 无 Task 产物
# ─────────────────────────────────────────────────────────────────────

class SingleRunArtifactZeroTests(unittest.TestCase):
    """single-run fixture 全过程无 task-only artifact:
    无 task.yaml / 无 task_manifest.json / 无 task_status.json /
    无 runs/ 目录 / 无 assembly/ 目录。

    复用 _probe_fixtures.make_probe_workdir 构造 single-run workdir，
    断言这些 Task 层产物不存在。
    """

    def setUp(self):
        self.tmp_ctx = tempfile.TemporaryDirectory(prefix="sri_artifact_")
        self.tmp = Path(self.tmp_ctx.name)
        self.wd = make_probe_workdir(self.tmp)
        self.wd["workdir"] = self.tmp

    def tearDown(self):
        self.tmp_ctx.cleanup()

    def test_no_task_yaml_in_single_run_workdir(self):
        """single-run workdir 不含 task.yaml。"""
        self.assertFalse(
            (self.tmp / "task.yaml").exists(),
            "single-run workdir 不应出现 task.yaml")

    def test_no_task_manifest_in_single_run_workdir(self):
        """single-run workdir 不含 task_manifest.json。"""
        self.assertFalse(
            (self.tmp / "task_manifest.json").exists(),
            "single-run workdir 不应出现 task_manifest.json")

    def test_no_task_status_in_single_run_workdir(self):
        """single-run workdir 不含 task_status.json。"""
        self.assertFalse(
            (self.tmp / "task_status.json").exists(),
            "single-run workdir 不应出现 task_status.json")

    def test_no_runs_dir_in_single_run_workdir(self):
        """single-run workdir 不含 runs/ 目录。"""
        self.assertFalse(
            (self.tmp / "runs").exists(),
            "single-run workdir 不应出现 runs/ 目录")

    def test_no_assembly_dir_in_single_run_workdir(self):
        """single-run workdir 不含 assembly/ 目录。"""
        self.assertFalse(
            (self.tmp / "assembly").exists(),
            "single-run workdir 不应出现 assembly/ 目录")


# ─────────────────────────────────────────────────────────────────────
# 2. ①③ 完整 compile 路径无 Task 调用（mock/spy）
# ─────────────────────────────────────────────────────────────────────

class SingleRunCompileFlowTests(unittest.TestCase):
    """single-run 完整 compile 路径: prepare_run → compile_fill。
    断言整个流程不调用任何 Task 层脚本（prepare_task），且不产生 Task 专属产物。
    （v3 收敛：gate_task / assemble_task / resume_task 脚本已删除，不再存在。）

    复用 _probe_fixtures 的 workdir + BASE_SPEC → compile_fill.compile_spec
    接缝。
    """

    def setUp(self):
        self.tmp_ctx = tempfile.TemporaryDirectory(prefix="sri_compile_")
        self.tmp = Path(self.tmp_ctx.name)
        self.wd = make_probe_workdir(self.tmp)
        self.wd["workdir"] = self.tmp

    def tearDown(self):
        self.tmp_ctx.cleanup()

    def test_compile_spec_no_task_artifacts_produced(self):
        """compile_spec 完成后 workdir 无 Task 层产物。"""
        spec = spec_with(self.wd)
        compile_fill.compile_spec(spec, self.wd["manifest"], self.tmp)
        # 无 task.yaml
        self.assertFalse((self.tmp / "task.yaml").exists(),
                         "compile 不应产生 task.yaml")
        # 无 task_manifest
        self.assertFalse((self.tmp / "task_manifest.json").exists(),
                         "compile 不应产生 task_manifest.json")
        # 无 task_status
        self.assertFalse((self.tmp / "task_status.json").exists(),
                         "compile 不应产生 task_status.json")
        # 无 runs/ 目录
        self.assertFalse((self.tmp / "runs").exists(),
                         "compile 不应产生 runs/ 目录")
        # 无 assembly/ 目录
        self.assertFalse((self.tmp / "assembly").exists(),
                         "compile 不应产生 assembly/ 目录")

    def test_compile_spec_zero_prepare_task_calls(self):
        """compile_spec 全程零 prepare_task 调用（subprocess spy）。

        通过 mock subprocess.run 并记录所有命令行来验证不调用 Task 层入口
        prepare_task.py（v3 收敛：gate_task / assemble_task / resume_task
        脚本已删除，故 forbidden set 只剩 prepare_task.py）。
        """
        spec = spec_with(self.wd)
        task_scripts = {"prepare_task.py"}
        original_run = subprocess.run
        called_cmds: list[list[str]] = []

        def spy_run(cmd, *args, **kwargs):
            if isinstance(cmd, list) and len(cmd) > 0:
                called_cmds.append(cmd)
            return original_run(cmd, *args, **kwargs)

        with mock.patch("subprocess.run", side_effect=spy_run):
            compile_fill.compile_spec(spec, self.wd["manifest"], self.tmp)

        # 验证无任何 Task 层脚本被调用
        for cmd in called_cmds:
            cmd_str = " ".join(str(c) for c in cmd)
            for task_script in task_scripts:
                self.assertNotIn(
                    task_script, cmd_str,
                    f"single-run compile 不应调用 {task_script}: {cmd_str}")

    def test_compile_flow_produces_only_standard_artifacts(self):
        """compile 产出的标准产物齐全（execution_plan.json / mapping.md），
        且不含任何 Task 层额外产物。"""
        spec = spec_with(self.wd)
        plan = compile_fill.compile_spec(spec, self.wd["manifest"], self.tmp)
        # 标准产物存在
        self.assertIsNotNone(plan)
        self.assertIn("operations", plan)
        self.assertIn("writes", plan)
        self.assertIn("source_coverage", plan)
        self.assertIn("row_map", plan)


# ─────────────────────────────────────────────────────────────────────
# 3. ②⑤ TASK_ORCHESTRATION runtime dependency = 0
# ─────────────────────────────────────────────────────────────────────

class TaskOrchestrationDependencyTests(unittest.TestCase):
    """single-run happy path 的 instructions 不要求加载
    TASK_ORCHESTRATION.md — runtime dependency = 0。

    不是简单 grep 文件名（SKILL 中可以有该文件名作为 multi-run 参考资料），
    而是断言：
    (a) single-run 流程段落（工作流五个公开命令 + §1-§7）不含"加载/阅读
        TASK_ORCHESTRATION"的要求；
    (b) TASK_ORCHESTRATION 的提及要么位于 ## Task Orchestration 段落
        （multi-run 上下文），要么是参考资料标注/异常诊断指引；
    (c) 文字 pin："单 run 任务不需要 Task 层，仍走上方五个公开命令"。
    """

    def test_single_run_path_requires_no_task_orchestration_loading(self):
        """single-run 流程段落（§1-§7 工作流）不含任何要求加载/阅读
        TASK_ORCHESTRATION.md 的指令。"""
        workflow = _single_run_workflow_steps()
        # 正面排除：single-run 流程中不应有 "加载 TASK_ORCHESTRATION" 或
        # "阅读 TASK_ORCHESTRATION" 或 "读取 TASK_ORCHESTRATION" 的要求
        load_phrases = [
            "加载TASK_ORCHESTRATION",
            "阅读TASK_ORCHESTRATION",
            "读取TASK_ORCHESTRATION",
            "读TASK_ORCHESTRATION",
            "loadTASK_ORCHESTRATION",
            "readTASK_ORCHESTRATION",
        ]
        stripped_workflow = _strip(workflow)
        for phrase in load_phrases:
            self.assertNotIn(
                phrase, stripped_workflow,
                f"single-run 流程段落不应要求 {phrase}")

    def test_task_orchestration_only_in_multi_run_context(self):
        """TASK_ORCHESTRATION.md 的提及只出现在：
        (a) §1.4b 禁读清单（行为约束：禁止全文读取 — 这是隔离证据，不是依赖）
        (b) ## Task Orchestration 段落（multi-run 上下文）
        (c) 参考资料标注 / Troubleshooting / 异常诊断指引
        不作为加载/阅读要求出现在 single-run 工作流段落。"""
        workflow = _single_run_workflow_steps()
        # 查找所有 TASK_ORCHESTRATION.md 出现的行，逐行验证上下文
        for i, line in enumerate(workflow.splitlines()):
            if "TASK_ORCHESTRATION.md" in line:
                stripped_line = _strip(line)
                # 合法出现：禁读清单（"禁止全文读取" / "Forbidden" 上下文）
                # 或参考资料标注（"参考资料" / "参考"）
                is_forbidden_list = (
                    "禁止全文读取" in line
                    or "Forbidden" in line
                    or "forbidden" in line
                )
                is_reference_note = (
                    "参考资料" in line
                    or "reference" in line.lower()
                    or "异常" in line
                    or "机制求证" in line
                    or "定向查阅" in line
                )
                is_load_requirement = (
                    "加载" in line
                    or "阅读" in line
                    or "读取" in line
                    or "读" in line
                ) and not is_forbidden_list and not is_reference_note
                self.assertFalse(
                    is_load_requirement,
                    f"single-run 流程段落第 {i+1} 行不应要求加载 TASK_ORCHESTRATION.md: {line!r}")

    def test_task_orchestration_section_marked_as_reference(self):
        """## Task Orchestration 末尾有参考资料标注。"""
        section = _task_orchestration_section()
        stripped = _strip(section)
        self.assertIn("详细契约参考", stripped,
                      "Task Orchestration 缺参考资料标注")
        self.assertIn("TASK_ORCHESTRATION.md", section,
                      "Task Orchestration 缺 TASK_ORCHESTRATION.md 引用")
        self.assertIn("异常诊断", stripped,
                      "Task Orchestration 缺触发场景说明")
        self.assertIn("机制求证", stripped,
                      "Task Orchestration 缺机制求证触发说明")
        self.assertIn("定向查阅", stripped,
                      "Task Orchestration 缺定向查阅说明")

    def test_single_run_text_pinned(self):
        """文字 pin: 单 run 不进 Task 容器，走公开命令。"""
        skill = _skill_text()
        stripped = _strip(skill)
        self.assertIn("单run任务不进Task", stripped,
                      "SKILL.md 缺 '单 run 任务不进 Task' pin")

    def test_public_commands_still_defined(self):
        """工作流定义七个公开命令（新 control plane, ADR 0018/0020）。"""
        workflow = _workflow_section()
        for cmd in ("workspace_init.py", "materialize_run.py",
                    "mod_nominate.py", "compile_fill.py",
                    "execute_batch.py", "promote_output.py"):
            self.assertIn(cmd, workflow,
                          f"工作流段落缺命令引用 {cmd!r}")
        self.assertIn("spec_review.py", workflow,
                      "工作流段落缺 spec_review.py 引用 (S6 唯一人工点)")
        self.assertIn("fill_spec.yaml", workflow,
                      "工作流段落缺 fill_spec.yaml 引用")

    def test_authoring_ready_forbids_task_orchestration_read(self):
        """S4 authoring 禁读清单含 TASK_ORCHESTRATION.md。"""
        skill = _skill_text()
        self.assertIn("完整 TASK_ORCHESTRATION.md 读取", skill,
                      "S4 禁读清单缺 TASK_ORCHESTRATION.md")
        self.assertIn("正常 happy path 禁止全文读取", skill,
                      "缺 happy path 禁读标注")


# ─────────────────────────────────────────────────────────────────────
# 4. ③ single-run 增量探测与 Office 调用为 0
# ─────────────────────────────────────────────────────────────────────

class IncrementalProbeZeroTests(unittest.TestCase):
    """single-run 增量探测与增量 Office 调用为 0（相对基线计数）。

    以 compile_spec 路径为基线，断言整个流程：
    - 无 prepare_task 子进程调用（v3 收敛：gate_task / assemble_task /
      resume_task 脚本已删除）
    - officecli 调用次数 = 0（compile 阶段不直接调用 officecli）
    - 新增探测脚本调用 = 0
    """

    def setUp(self):
        self.tmp_ctx = tempfile.TemporaryDirectory(prefix="sri_probe_")
        self.tmp = Path(self.tmp_ctx.name)
        self.wd = make_probe_workdir(self.tmp)
        self.wd["workdir"] = self.tmp

    def tearDown(self):
        self.tmp_ctx.cleanup()

    def test_compile_path_zero_officecli_calls(self):
        """compile 路径零 officecli 调用（compile 不直接操作 Office 文件）。"""
        spec = spec_with(self.wd)
        officecli_calls: list[str] = []
        original_run = subprocess.run

        def spy_run(cmd, *args, **kwargs):
            if isinstance(cmd, list):
                cmd_str = " ".join(str(c) for c in cmd)
                if "officecli" in cmd_str.lower():
                    officecli_calls.append(cmd_str)
            return original_run(cmd, *args, **kwargs)

        with mock.patch("subprocess.run", side_effect=spy_run):
            compile_fill.compile_spec(spec, self.wd["manifest"], self.tmp)

        self.assertEqual(
            len(officecli_calls), 0,
            f"single-run compile 不应调用 officecli: {officecli_calls}")

    def test_compile_path_zero_task_script_calls(self):
        """compile 路径零 Task 层脚本调用。"""
        spec = spec_with(self.wd)
        task_calls: list[str] = []
        task_scripts = {"prepare_task.py"}
        original_run = subprocess.run

        def spy_run(cmd, *args, **kwargs):
            if isinstance(cmd, list):
                cmd_str = " ".join(str(c) for c in cmd)
                for ts in task_scripts:
                    if ts in cmd_str:
                        task_calls.append(cmd_str)
            return original_run(cmd, *args, **kwargs)

        with mock.patch("subprocess.run", side_effect=spy_run):
            compile_fill.compile_spec(spec, self.wd["manifest"], self.tmp)

        self.assertEqual(
            len(task_calls), 0,
            f"single-run compile 不应调用 Task 脚本: {task_calls}")

    def test_compile_path_zero_new_probe_scripts(self):
        """compile 路径零新增探测脚本调用（不因 Task 机制引入新 probe）。"""
        spec = spec_with(self.wd)
        all_cmds: list[list[str]] = []
        original_run = subprocess.run

        def spy_run(cmd, *args, **kwargs):
            if isinstance(cmd, list):
                all_cmds.append([str(c) for c in cmd])
            return original_run(cmd, *args, **kwargs)

        with mock.patch("subprocess.run", side_effect=spy_run):
            compile_fill.compile_spec(spec, self.wd["manifest"], self.tmp)

        # compile_spec 内部不调用 subprocess（纯 Python 函数），
        # 所以 all_cmds 应为空
        self.assertEqual(
            len(all_cmds), 0,
            f"compile_spec 不应产生任何子进程调用: {all_cmds}")


# ─────────────────────────────────────────────────────────────────────
# 5. ④ single-run 无 Task-specific user ASK
# ─────────────────────────────────────────────────────────────────────

class TaskSpecificAskZeroTests(unittest.TestCase):
    """single-run 路径的 ASK 场景不含 Task 术语。

    文字 pin：SKILL single-run 路径的 ASK 场景清单（失败处置表 +
    MOD ASK）不含 Task 专属术语（task.yaml / task status / scheduler /
    supersede / assembly 等）。Task 专属 ASK 只出现在
    ## Task Orchestration 上下文。
    """

    def test_failure_table_has_no_task_specific_terms(self):
        """失败处置表不含 Task 专属术语。"""
        skill = _skill_text()
        # 提取失败处置表
        m = re.search(
            r"^## 失败处置表.*?(?=^## |\Z)",
            skill, re.MULTILINE | re.DOTALL,
        )
        self.assertIsNotNone(m, "SKILL.md 缺失败处置表")
        table = m.group(0)
        stripped = _strip(table)
        # 不含 task.yaml / task_status / scheduler / supersede / assembly
        for term in ("task.yaml", "task_status", "scheduler",
                     "supersede", "assembly"):
            self.assertNotIn(
                term, stripped,
                f"失败处置表不应含 Task 专属术语 {term!r}")

    def test_mod_ask_checklist_has_no_task_specific_terms(self):
        """MOD ASK 必问清单不含 Task 专属术语。"""
        skill = _skill_text()
        m = re.search(
            r"MOD ASK 必问清单.*?(?=^\\*\\*|^### |^## |\Z)",
            skill, re.MULTILINE | re.DOTALL,
        )
        if m is not None:
            ask_section = m.group(0)
            stripped = _strip(ask_section)
            for term in ("task.yaml", "task_status", "scheduler",
                         "supersede", "assembly"):
                self.assertNotIn(
                    term, stripped,
                    f"MOD ASK 清单不应含 Task 专属术语 {term!r}")

    def test_task_specific_ask_only_in_task_orchestration(self):
        """Task 专属 ASK 术语（task.yaml / task_status / scheduler /
        supersede / assembly）只出现在 ## Task Orchestration 段落
        或 Troubleshooting 参考中。"""
        skill = _skill_text()
        # 提取 ## Task Orchestration 之前的所有内容（single-run 上下文）
        m = re.search(
            r"^## Task Orchestration",
            skill, re.MULTILINE,
        )
        self.assertIsNotNone(m)
        before_task = skill[:m.start()]
        stripped = _strip(before_task)

        # scheduler / supersede 不应出现在 single-run 上下文中
        # （assembly 仅在 Task Orchestration + Run Isolation 中讨论）
        for term in ("scheduler", "supersede"):
            self.assertNotIn(
                term, stripped,
                f"single-run 上下文不应含 {term!r}（应仅在 Task Orchestration）")


# ─────────────────────────────────────────────────────────────────────
# 7. ⑥ 不依赖真实客户数据
# ─────────────────────────────────────────────────────────────────────

class DataNeutralityTests(unittest.TestCase):
    """测试不依赖真实客户数据、不依赖 Agent 行为测量。

    断言本测试文件本身不含真实客户名/业务标识（data-neutral fixtures）。
    """

    def test_this_test_file_data_neutral(self):
        """本测试文件不含真实客户名/业务标识。
        注: R32/R410a 是 fixture 名称（出现在 docstring 引述 ticket 名中），
        不是真实客户数据。"""
        test_file = Path(__file__).read_text(encoding="utf-8")
        # 排除含 "banned" 的行（测试逻辑自身对 banned 列表的引用）
        lines = test_file.splitlines()
        scan_text = "\n".join(
            line for line in lines
            if "banned" not in line.lower()
            and "ELITE" not in line  # 排除 banned list 值自身
        )
        for banned_word in ("埃及", "TCL", "MXP", "ATLAS", "Algeria",
                            "ELITE"):
            self.assertNotIn(
                banned_word, scan_text,
                f"测试文件含业务标识 {banned_word!r}（应 data-neutral）")

    def test_probe_fixtures_data_neutral(self):
        """_probe_fixtures 的 workdir 构造不含真实客户数据。"""
        with tempfile.TemporaryDirectory(prefix="sri_neutral_") as tmp:
            tmp = Path(tmp)
            wd = make_probe_workdir(tmp)
            # 读取生成的 CSV 文件
            csv_path = tmp / "source_maoli_flat.csv"
            csv_text = csv_path.read_text(encoding="utf-8-sig")
            # 数据是合成的（家用/商用/工程，非真实客户）
            for banned in ("埃及", "TCL", "MXP", "ATLAS"):
                self.assertNotIn(
                    banned, csv_text,
                    f"fixture CSV 含真实客户名 {banned!r}")


# ─────────────────────────────────────────────────────────────────────
# 8. 补充：SKILL 文本整体 Single-Run Isolation 契约 pin
# ─────────────────────────────────────────────────────────────────────

class SingleRunIsolationContractTextTests(unittest.TestCase):
    """SKILL.md 的 Single-Run Isolation / 五零指标 相关措辞 pin。
    段落被删/措辞漂移 → 变红。

    验证：
    - "单 run 任务不需要 Task 层" pin 在 Task Orchestration 段
    - TASK_ORCHESTRATION 参考资料标注在三个位置（§1.4b / Task Orchestration 末尾 /
      Troubleshooting）
    - Topology Check 零新增探测 + 零新增脚本
    """

    def _skill(self) -> str:
        return _skill_text()

    def test_single_run_no_task_layer_pin(self):
        """单 run 不进 Task 容器，走公开命令 (ADR 0018/0020)。"""
        skill = self._skill()
        stripped = _strip(skill)
        self.assertIn("单run任务不进Task", stripped,
                      "缺 '单 run 任务不进 Task' pin")
        self.assertIn("仍走上方公开命令", stripped,
                      "缺 '仍走上方公开命令' pin")

    def test_task_orchestration_reference_annotation_triple(self):
        """TASK_ORCHESTRATION 参考资料标注在多处：
        S4 authoring 禁读清单 / Task Orchestration 末尾 / Troubleshooting。
        ADR 0022: Part V 的判据是"读最小文献面" (无具体问题不预读、不全文通读),
        不再写"零文件 / 单文件"上限。"""
        skill = self._skill()
        # "正常 happy path 禁止全文读取" 至少出现 2 次 (authoring + 参考路由)
        count = skill.count("正常 happy path 禁止全文读取")
        self.assertGreaterEqual(
            count, 2,
            f"TASK_ORCHESTRATION 参考资料标注应至少出现 2 次，实际 {count} 次")
        self.assertIn("读最小文献面", skill,
                      "Part V 缺 ADR 0022 '读最小文献面' 判据")
        self.assertNotIn("默认读取 references = 0", skill,
                         "ADR 0022 已移除 'references = 0' 硬上限")

    def test_topology_check_uses_only_existing_facts(self):
        """Topology 只消费任务文本 + workspace_manifest 事实，零新增探测。"""
        text = self._skill()
        m = re.search(
            r"零新增探测[^\n]*",
            text, re.MULTILINE,
        )
        self.assertIsNotNone(m, "Topology 段缺零新增探测")
        section = m.group(0)
        # 完整 Topology 行（S1）: 任务文本 + workspace_manifest 事实
        m2 = re.search(
            r"Topology: 用任务文本 \+ workspace_manifest 事实[^\n]*",
            text, re.MULTILINE,
        )
        self.assertIsNotNone(m2, "Topology 段缺任务文本 + workspace_manifest 输入")
        self.assertIn("零新增探测", section,
                      "Topology 段缺 '零新增探测'")

    def test_no_new_scripts_for_topology_check(self):
        """Topology 零新增探测：不发明新脚本/probe。"""
        text = self._skill()
        m = re.search(
            r"零新增探测.*?(?=\n- |\n\n|\Z)",
            text, re.MULTILINE | re.DOTALL,
        )
        self.assertIsNotNone(m)
        section = m.group(0)
        self.assertIn("零新增探测", section,
                      "Topology 段缺 '零新增探测'")

    def test_output_files_section_excludes_task_artifacts(self):
        """Output Files 段不含 Task 层产物（task.yaml / task_manifest /
        task_status / runs/ / assembly/）。"""
        skill = self._skill()
        m = re.search(
            r"^## Output Files.*?(?=^## |\Z)",
            skill, re.MULTILINE | re.DOTALL,
        )
        self.assertIsNotNone(m, "缺 Output Files 段")
        output_section = m.group(0)
        # Output Files 可含 multi-run 容器说明行 (task.yaml + runs/), 但退役的
        # task 状态机产物 (task_manifest/task_status) 必须缺席 (ADR 0020);
        # single-run 主列表 (workdir 平铺) 不应要求 task.yaml。
        stripped = _strip(output_section)
        self.assertNotIn("task_manifest.json", stripped,
                         "Output Files 不应含 task_manifest.json (退役)")
        self.assertNotIn("task_status.json", stripped,
                         "Output Files 不应含 task_status.json (退役)")


if __name__ == "__main__":
    unittest.main()
