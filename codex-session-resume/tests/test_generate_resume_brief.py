"""Tests for scripts/generate_resume_brief.py (V1.3 P0: handoff start page)."""

import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
SKILL_ROOT = HERE.parent
SCRIPTS = SKILL_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import generate_resume_brief as grb  # noqa: E402
from extract_resume_state import main as extract_state  # noqa: E402
from preprocess_session import Preprocessor  # noqa: E402

FIXTURES = HERE / "_fixtures"
GOLDEN_DIR = SKILL_ROOT.parent / "jsonl handoff测试"
GOLDEN1 = GOLDEN_DIR / "rollout-2026-08-30T16-27-06-01a051c7-89d8-7ce0-a063-b89f4c8fd40b.jsonl"


def build_package(fixture_or_path: Path, tmp_path: Path, agent_state=None):
    out = tmp_path / "handoff"
    Preprocessor().run(fixture_or_path, out)
    extract_state(["--clean", str(out / "clean.jsonl"), "--meta", str(out / "meta.json"),
                   "--out", str(out / "resume_state.json")])
    if agent_state is not None:
        src = tmp_path / "agent_src.json"
        src.write_text(json.dumps(agent_state, ensure_ascii=False), encoding="utf-8")
        extract_state(["--clean", str(out / "clean.jsonl"), "--meta", str(out / "meta.json"),
                       "--out", str(out / "resume_state.json"), "--merge", str(src)])
    from extract_artifacts import main as extract_artifacts  # noqa: PLC0415
    extract_artifacts(["--clean", str(out / "clean.jsonl"),
                       "--out", str(out / "artifact_manifest.json")])
    return out


def run_brief(pkg: Path, tmp_path: Path, name="brief.md"):
    grb.main(["--resume-state", str(pkg / "resume_state.json"),
              "--agent-state", str(pkg / "agent_state.json"),
              "--manifest", str(pkg / "artifact_manifest.json"),
              "--meta", str(pkg / "meta.json"),
              "--out", str(tmp_path / name)])
    return (tmp_path / name).read_text(encoding="utf-8")


def test_brief_golden1_start_page(tmp_path):
    if not GOLDEN1.is_file():
        pytest.skip("real rollout not present")
    pkg = build_package(GOLDEN1, tmp_path)
    brief = run_brief(pkg, tmp_path)
    assert brief.startswith("# Resume Brief")
    assert "北非客户参数表整理" in brief          # task identity
    assert "摩洛哥" in brief                       # user goal
    assert "approval_gate" in brief and "waiting_confirmation" in brief
    assert "不要自动执行发布/外发/promote" in brief   # do-not for gate
    assert "required 缺失" in brief                # asset gap surfaced
    assert "证据入口" in brief and "clean.jsonl" in brief
    assert len(brief) < 4000  # ~2000 Chinese tokens ceiling


def test_brief_with_agent_layer(tmp_path):
    if not GOLDEN1.is_file():
        pytest.skip("real rollout not present")
    agent = {
        "completed": [{"item": "摩洛哥+欧洲8份客户参数表草稿", "status": "verified",
                       "evidence_refs": [343, 344]}],
        "pending": [{"action": "等待用户确认发布Excel和PDF", "type": "user_confirmation"}],
        "notes": "--",
    }
    pkg = build_package(GOLDEN1, tmp_path, agent_state=agent)
    brief = run_brief(pkg, tmp_path)
    assert "摩洛哥+欧洲8份客户参数表草稿" in brief
    assert "等待用户确认发布Excel和PDF" in brief
    assert "[evidence: [343, 344]]" in brief
    assert "等待用户确认（审批门禁" in brief


def test_brief_interrupted_tail(tmp_path):
    pkg = build_package(FIXTURES / "aborted_tail.jsonl", tmp_path)
    brief = run_brief(pkg, tmp_path)
    assert "interrupted" in brief
    assert "不要直接继续" in brief
    assert "先定位断点" in brief
    # no required-missing scare when there are no required assets
    assert "required 缺失 0" in brief


def test_brief_determinism(tmp_path):
    pkg = build_package(FIXTURES / "basic_session.jsonl", tmp_path)
    run_brief(pkg, tmp_path, "a.md")
    run_brief(pkg, tmp_path, "b.md")
    a = (tmp_path / "a.md").read_bytes()
    b = (tmp_path / "b.md").read_bytes()
    assert a == b