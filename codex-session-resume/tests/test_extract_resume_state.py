"""Tests for scripts/extract_resume_state.py (V1.2 state layer)."""

import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
SKILL_ROOT = HERE.parent
SCRIPTS = SKILL_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import extract_resume_state as rs  # noqa: E402
from preprocess_session import Preprocessor  # noqa: E402

FIXTURES = HERE / "_fixtures"
GOLDEN_DIR = SKILL_ROOT.parent / "jsonl handoff测试"
GOLDEN1 = GOLDEN_DIR / "rollout-2026-08-30T16-27-06-01a051c7-89d8-7ce0-a063-b89f4c8fd40b.jsonl"
GOLDEN2 = GOLDEN_DIR / "rollout-2026-08-30T17-10-56-01a051ef-a84c-7943-b78c-5e2f7093afdc.jsonl"


def run_clean(fixture_or_path: Path, tmp_path: Path) -> Path:
    out = tmp_path / "clean-out"
    Preprocessor().run(fixture_or_path, out)
    return out


def extract(fixture_or_path: Path, tmp_path: Path):
    out = run_clean(fixture_or_path, tmp_path)
    state = rs.main(["--clean", str(out / "clean.jsonl"),
                     "--meta", str(out / "meta.json"),
                     "--out", str(tmp_path / "resume_state.json")])
    assert state == 0
    return json.loads((tmp_path / "resume_state.json").read_text(encoding="utf-8"))


def test_golden1_approval_gate(tmp_path):
    if not GOLDEN1.is_file():
        pytest.skip("real rollout not present")
    s = extract(GOLDEN1, tmp_path)
    assert s["version"] == "1.0"
    ex = s["execution"]
    assert ex["phase"] == "approval_gate"
    assert ex["status"] == "waiting_confirmation"
    assert ex["confidence"] >= 0.85
    assert ex["abort_count"] == 0 and ex["has_rollback"] is False
    assert s["next_action"] == {"action": "ask_user", "target": "confirmation"}
    assert s["task"]["identity"] == "北非客户参数表整理"
    assert "摩洛哥" in s["task"]["user_goal"]
    assert s["session"]["session_id"] == "01a051c7-89d8-7ce0-a063-b89f4c8fd40b"
    # completed etc. are left to the Agent (no semantic judgment by script)
    assert s["completed"] == [] and s["awaiting_agent"] is True
    kinds = [r["kind"] for r in s["evidence_refs"]]
    assert "final_answer" in kinds and "last_lifecycle" in kinds
    # every ref points at a real clean event
    with (tmp_path / "clean-out" / "clean.jsonl").open(encoding="utf-8") as f:
        clean = [json.loads(l) for l in f if l.strip()]
    seqs = {e["seq"] for e in clean}
    for r in s["evidence_refs"]:
        assert r["seq"] in seqs


def test_golden2_approval_gate_with_abort_history(tmp_path):
    if not GOLDEN2.is_file():
        pytest.skip("real rollout not present")
    s = extract(GOLDEN2, tmp_path)
    ex = s["execution"]
    # tail completed with a confirmation request ("请回复"确认输出"")
    assert ex["phase"] == "approval_gate"
    assert ex["status"] == "waiting_confirmation"
    assert s["next_action"]["action"] == "ask_user"
    # mid-session interruptions are deterministic facts for the reader
    assert ex["abort_count"] == 2
    assert ex["tail_validation_marker"] is True  # readback/validated evidence in tail
    assert any(r["kind"] == "final_answer" for r in s["evidence_refs"])
    assert isinstance(s["task"]["user_goal"], str)


def test_fixture_aborted_turn_interrupted(tmp_path):
    s = extract(FIXTURES / "aborted_tail.jsonl", tmp_path)
    assert s["execution"]["phase"] == "execution"
    assert s["execution"]["status"] == "interrupted"
    assert s["execution"]["abort_count"] == 1
    assert s["next_action"]["action"] == "verify"


def test_fixture_rollback_tail(tmp_path):
    s = extract(FIXTURES / "rollback_same_turn.jsonl", tmp_path)
    assert s["execution"]["phase"] == "execution"
    assert s["execution"]["status"] == "rolled_back"
    assert s["next_action"]["action"] == "recover"


def test_fixture_basic_completed_turn(tmp_path):
    s = extract(FIXTURES / "basic_session.jsonl", tmp_path)
    ex = s["execution"]
    assert ex["phase"] == "execution" and ex["status"] == "completed_turn"
    assert s["next_action"]["action"] == "continue"


def test_merge_writes_agent_layer_keeps_machine_pure(tmp_path):
    out = run_clean(FIXTURES / "basic_session.jsonl", tmp_path)
    with (tmp_path / "clean-out" / "clean.jsonl").open(encoding="utf-8") as f:
        clean = [json.loads(l) for l in f if l.strip()]
    valid_seq = clean[2]["seq"]  # the user event
    agent_state = {
        "completed": [{"item": "检查源表结构", "status": "verified",
                       "evidence_refs": [valid_seq]}],
        "pending": [{"action": "等待用户确认", "type": "user_confirmation"}],
        "notes": "来源表已核对，草稿阶段结束",
    }
    agent_file = tmp_path / "agent_src.json"
    agent_file.write_text(json.dumps(agent_state, ensure_ascii=False), encoding="utf-8")
    rs.main(["--clean", str(out / "clean.jsonl"), "--meta", str(out / "meta.json"),
             "--out", str(tmp_path / "resume_state.json"), "--merge", str(agent_file)])
    # MACHINE layer stays pure and reproducible
    machine = json.loads((tmp_path / "resume_state.json").read_text(encoding="utf-8"))
    assert machine["layer"] == "machine"
    assert machine["completed"] == [] and machine["awaiting_agent"] is True
    assert machine["evidence_refs"]  # untouched by the merge
    # AGENT layer carries the interpretation, evidence-validated
    agent_out = json.loads((tmp_path / "agent_state.json").read_text(encoding="utf-8"))
    assert agent_out["layer"] == "agent"
    assert agent_out["completed"][0]["item"] == "检查源表结构"
    assert agent_out["pending"][0]["type"] == "user_confirmation"
    assert agent_out["notes"] == "来源表已核对，草稿阶段结束"
    assert agent_out["evidence_validated"] is True
    assert agent_out["next_action"]["action"] == "continue"  # machine fallback
    # invalid ref must fail loudly (exit code 2) and write NOTHING extra
    bad = tmp_path / "bad_state.json"
    bad.write_text(json.dumps({"completed": [{"item": "x", "evidence_refs": [999999]}]}),
                   encoding="utf-8")
    with pytest.raises(SystemExit) as ei:
        rs.main(["--clean", str(out / "clean.jsonl"), "--meta", str(out / "meta.json"),
                 "--out", str(tmp_path / "r2.json"), "--merge", str(bad)])
    assert ei.value.code == 2


def test_determinism(tmp_path):
    a = extract(FIXTURES / "basic_session.jsonl", tmp_path)
    b = extract(FIXTURES / "basic_session.jsonl", tmp_path)
    assert json.dumps(a, ensure_ascii=False, sort_keys=True) == \
        json.dumps(b, ensure_ascii=False, sort_keys=True)


def test_rejects_non_normalized_clean(tmp_path):
    junk = tmp_path / "junk.jsonl"
    junk.write_text('{"not_normalized": true}\n', encoding="utf-8")
    with pytest.raises(SystemExit) as ei:
        rs.main(["--clean", str(junk), "--out", str(tmp_path / "x.json")])
    assert ei.value.code == 1