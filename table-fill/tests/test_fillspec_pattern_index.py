"""
tests/test_fillspec_pattern_index.py — table-fill runtime control-plane contract
(Control-plane Pin Reversal, ADR 0020/Q8).

One canonical SKILL control-plane test replaces the four duplicate
AUTHORING_READY text-presence pin files (they are deleted). It contains:

- negative tombstones: explicitly retired mechanisms must NOT reappear in
  SKILL.md (TOPOLOGY_BARRIER / AUTHORING_READY / Adopt Discovery /
  Task Bootstrap / Shared Prepare / 五个公开命令 / Review-before-Compile);
- negative artifact tombstones: adjudicated-forbidden artifacts/mechanisms
  must not appear (run_definition / task_status-driven progression /
  workspace-init role tags);
- positive control-plane assertions: stable runtime stage names and critical
  ordering semantics (Workspace before Topology · Compile before Review ·
  Review before Execute), NOT incidental stage numbering (S0/S1 are
  navigation aids, not contract) and NOT prose blocks;
- pattern-index machine assertions (unchanged from the original file):
  grid_record.rows → columns / grid_record.columns → matrix.

Retired mechanism names may still appear in NEGATIVE assertions (the
tombstone list) — the test asserts they are ABSENT from SKILL.md, so the
only occurrences are within this test file itself.
"""

import os
import re

import pytest
import yaml

ASSET_PATH = os.path.join(
    os.path.dirname(__file__), os.pardir, "assets", "fillspec_patterns.yaml"
)

SKILL_PATH = os.path.join(
    os.path.dirname(__file__), os.pardir, "SKILL.md"
)


@pytest.fixture(scope="module")
def skill_text():
    with open(SKILL_PATH, encoding="utf-8") as f:
        return f.read()


@pytest.fixture(scope="module")
def pattern_data():
    with open(ASSET_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


# ── Pattern Index machine checks (retained from original pin file) ────────

class TestPatternIndexMachine:
    def test_grid_record_rows_maps_to_columns(self, pattern_data):
        assert pattern_data["grid_record"]["rows"]["primary"] == "columns"

    def test_grid_record_columns_maps_to_matrix(self, pattern_data):
        cols = pattern_data["grid_record"]["columns"]
        assert cols.get("primary") == "matrix"
        assert "field_map" in cols.get("fields", [])
        assert "record_map" in cols.get("fields", [])

    def test_grouped_grid_maps_to_blocks(self, pattern_data):
        assert pattern_data["grouped_grid"].get("primary") == "blocks"


# ── Negative tombstones: retired mechanisms must not reappear ─────────────

RETIRED_MECHANISMS = (
    "TOPOLOGY_BARRIER",
    "AUTHORING_READY",
    "Adopt Discovery",
    "Task Bootstrap",
    "Shared Prepare",
    "五个公开命令",
)

# Review-before-Compile wording (ADR 0019): must not resurface as an ordering
# statement. The positive order assertions below are the contract; this
# negative guards the specific legacy phrase.
LEGACY_REVIEW_PHRASES = (
    "初稿后、编译前",
)


class TestRetiredMechanismTombstones:
    @pytest.mark.parametrize("term", RETIRED_MECHANISMS)
    def test_retired_mechanism_absent(self, skill_text, term):
        assert term not in skill_text, (
            f"退役机制 {term!r} 不得在 SKILL.md 重现 (ADR 0020)")

    @pytest.mark.parametrize("phrase", LEGACY_REVIEW_PHRASES)
    def test_legacy_review_before_compile_absent(self, skill_text, phrase):
        assert phrase not in skill_text, (
            f"旧 Review-before-Compile 表述 {phrase!r} 不得重现 (ADR 0019)")


# ── Negative artifact tombstones (only adjudicated-forbidden items) ────────

FORBIDDEN_ARTIFACTS = (
    "task_status",
    "run_definition",
)


class TestForbiddenArtifactTombstones:
    @pytest.mark.parametrize("term", FORBIDDEN_ARTIFACTS)
    def test_forbidden_artifact_absent(self, skill_text, term):
        assert term not in skill_text, (
            f"已被裁决禁止的 artifact {term!r} 不得在 SKILL.md 出现 "
            "(ADR 0018/0020)")


# ── Positive control-plane assertions: stage names + ordering, not numbers ─

STAGE_NAMES = (
    "Workspace Init",
    "Topology",
    "Task Shape",
    "MOD Resolution",
    "FillSpec",
    "Compile",
    "Spec Review",
    "Execute",
    "Deliver",
)

# Critical ordering semantics — the actual architectural contract.
ORDER_SEMANTICS = (
    ("Workspace Init", "Topology"),  # Workspace before Topology
    ("Compile", "Spec Review"),      # Compile before Review
    ("Spec Review", "Execute"),      # Review before Execute
)


class TestControlPlanePositive:
    def test_stage_names_present(self, skill_text):
        missing = [s for s in STAGE_NAMES if s not in skill_text]
        assert not missing, f"canonical control plane 缺阶段名: {missing}"

    def test_critical_ordering_semantics(self, skill_text):
        """顺序契约: 状态顺序代码块 (S0 Workspace Init → S1 Topology →
        ... → S8 Deliver) 内, 每个 (before, after) 对中 before 必须出现在
        after 之前。只 pin 顺序关系, 不 pin S 编号。"""
        m = re.search(r"```\nS0 Workspace Init.*?S8 Deliver\n```",
                      skill_text, re.DOTALL)
        assert m is not None, "SKILL.md 缺 S0→S8 状态顺序代码块"
        order_line = m.group(0)
        idx = {s: order_line.find(s) for s in STAGE_NAMES}
        for before, after in ORDER_SEMANTICS:
            assert idx[before] != -1 and idx[after] != -1, \
                f"顺序锚点缺失: {before}/{after}"
            assert idx[before] < idx[after], \
                f"顺序契约被破坏: {before} 必须在 {after} 之前"

    def test_stage_numbering_not_contract(self, skill_text):
        """S0/S1 是导航辅助不是契约: 断言不依赖具体编号出现。此测试的存在
        本身即契约 — 若未来重编号, 只改 SKILL, 本测试不应失败于编号。"""
        # 仅作占位锚点: 控制面段落必须以 "Runtime Control Plane" 或
        # 等价标题存在 (阶段名已由 test_stage_names_present 覆盖)。
        assert any(t in skill_text for t in ("Control Plane", "Runtime")), \
            "SKILL.md 缺少控制面段落锚点"

    def test_materialize_run_declared(self, skill_text):
        """materialize_run 是 Run Materialization 的唯一 lowering 边界
        (ADR 0018) — 必须显式命名。"""
        assert "materialize_run" in skill_text

    def test_review_hash_binding_declared(self, skill_text):
        """Review 绑定 execute 前必须一致的 fill_spec hash (ADR 0019)。"""
        assert "review_confirm" in skill_text
        assert "fill_spec_sha256" in skill_text or "fill_spec 哈希" in skill_text