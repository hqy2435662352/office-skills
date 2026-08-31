"""Tests for scripts/index_sessions.py (codex-session-resume V1.1 discovery)."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
SKILL_ROOT = HERE.parent
SCRIPTS = SKILL_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from index_sessions import index_file, main  # noqa: E402

FIXTURES = HERE / "_fixtures"


def write_session(root: Path, name: str, started: str, users: list[str],
                  provider: str = "deepseek", legacy: bool = False) -> Path:
    """Write a tiny realistic rollout file into root; returns its path.

    Filename format mirrors real Codex: rollout-<ts>-<session_id>.jsonl
    (the session id is embedded in the file name).
    """
    p = root / name
    sid = name.split("-")[6].split(".")[0]  # rollout-YYYY-MM-DDTHH-MM-SS-<id>.jsonl
    meta = {
        "type": "session_meta",
        "timestamp": started,
        "payload": {
            "session_id": sid,
            "timestamp": started,
            "cwd": "C:\\Users\\测试\\项目",
            "model_provider": provider,
            "originator": "codex-tui",
            "history_mode": "legacy",
        },
    }
    lines = [json.dumps(meta, ensure_ascii=False)]
    for i, text in enumerate(users):
        if legacy:
            lines.append(json.dumps({
                "type": "event_msg",
                "timestamp": started,
                "payload": {"type": "user_message", "message": text}},
                ensure_ascii=False))
        else:
            lines.append(json.dumps({
                "type": "response_item",
                "timestamp": started,
                "payload": {
                    "type": "message",
                    "id": f"msg-{i}",
                    "role": "user",
                    "content": [{"type": "input_text", "text": text}]}},
                ensure_ascii=False))
    lines.append(json.dumps({
        "type": "event_msg",
        "timestamp": started,
        "payload": {"type": "task_complete", "turn_id": "t1"}},
        ensure_ascii=False))
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


def test_index_file_extracts_fingerprint(tmp_path):
    f = write_session(tmp_path, "rollout-2026-08-30T10-00-00-aa01.jsonl",
                      "2026-08-30T02:00:00.000Z",
                      ["继续摩洛哥参数表任务", "先看摩洛哥能效部分", "是摩洛哥和欧洲都要更新"])
    entry = index_file(f, {"unknown": 8000})
    assert entry["session_id"] == "aa01"
    assert entry["cwd"].endswith("项目")
    assert entry["model_provider"] == "deepseek"
    assert entry["started_at"] == "2026-08-30T02:00:00.000Z"
    assert entry["last_timestamp"] == "2026-08-30T02:00:00.000Z"
    # ALL user messages kept, in order, none truncated
    assert entry["user_messages"] == ["继续摩洛哥参数表任务", "先看摩洛哥能效部分", "是摩洛哥和欧洲都要更新"]


def test_index_file_skips_client_injected_only(tmp_path):
    f = write_session(tmp_path, "rollout-2026-08-30T10-00-00-bb01.jsonl",
                      "2026-08-30T02:00:00.000Z", [])
    lines = f.read_text(encoding="utf-8").splitlines()[:-1]  # drop the task_complete
    injected = json.dumps({
        "type": "response_item",
        "timestamp": "2026-08-30T02:00:00.000Z",
        "payload": {
            "type": "message",
            "id": "msg-inj",
            "role": "user",
            "content": [{
                "type": "input_text",
                "text": "<recommended_plugins>\nA\n</recommended_plugins>\n\n"
                        "<environment_context>\n<cwd>C:\\x</cwd>\n</environment_context>"}]}},
        ensure_ascii=False)
    f.write_text("\n".join(lines + [injected]) + "\n", encoding="utf-8")
    entry = index_file(f, {"unknown": 8000})
    assert entry["user_messages"] == []


def test_index_file_legacy_user_message(tmp_path):
    f = write_session(tmp_path, "rollout-2026-08-30T10-00-00-cc01.jsonl",
                      "2026-08-30T02:00:00.000Z", ["旧格式用户消息"], legacy=True)
    entry = index_file(f, {"unknown": 8000})
    assert entry["user_messages"] == ["旧格式用户消息"]


def test_index_file_dedupes_legacy_duplicate_marker(tmp_path):
    """A response_item user message plus its event_msg/user_message twin (real
    legacy codex-tui layout) must index as ONE user message."""
    p = tmp_path / "rollout-2026-08-30T10-00-00-cc02.jsonl"
    lines = [
        json.dumps({"type": "turn_context", "timestamp": "2026-08-30T02:00:00.000Z",
                    "payload": {"turn_id": "t1", "model": "m"}}, ensure_ascii=False),
        json.dumps({"type": "response_item", "timestamp": "2026-08-30T02:00:00.000Z",
                    "payload": {"type": "message", "id": "m1", "role": "user",
                                "content": [{"type": "input_text", "text": "hi"}]}},
                   ensure_ascii=False),
        json.dumps({"type": "event_msg", "timestamp": "2026-08-30T02:00:00.000Z",
                    "payload": {"type": "user_message", "message": "hi"}},
                   ensure_ascii=False),
        json.dumps({"type": "event_msg", "timestamp": "2026-08-30T02:00:00.000Z",
                    "payload": {"type": "user_message", "message": "what's up"}},
                   ensure_ascii=False),
    ]
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    entry = index_file(p, {"unknown": 8000})
    assert entry["user_messages"] == ["hi", "what's up"]


def test_index_main_cli_and_determinism(tmp_path):
    store = tmp_path / "codex-store" / "sessions" / "2026" / "08" / "07"
    store.mkdir(parents=True)
    write_session(store, "rollout-2026-08-07T08-00-00-s0001.jsonl",
                  "2026-08-07T00:00:00.000Z", ["昨天早上的探测任务"])
    write_session(store, "rollout-2026-08-07T10-00-00-s0002.jsonl",
                  "2026-08-07T02:00:00.000Z", ["继续表填充工作"])
    write_session(store, "rollout-2026-08-07T12-00-00-s0003.jsonl",
                  "2026-08-07T04:00:00.000Z", ["摩洛哥能效参数表处理"])
    (store / "rollout-2026-08-07T12-00-00-s0003.jsonl").open("a", encoding="utf-8").write(
        "not-valid-json{{{\n")
    env = dict(__import__("os").environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    out1 = tmp_path / "index-a.json"
    r = subprocess.run(
        [sys.executable, str(SCRIPTS / "index_sessions.py"),
         "--root", str(tmp_path / "codex-store" / "sessions"),
         "--out", str(out1)],
        capture_output=True, text=True, encoding="utf-8", env=env)
    assert r.returncode == 0, r.stderr
    data1 = json.loads(out1.read_text(encoding="utf-8"))
    assert data1["schema_version"] == 1
    ids = [s["session_id"] for s in data1["sessions"]]
    assert ids == ["s0003", "s0002", "s0001"]  # newest first
    s3 = data1["sessions"][0]
    assert s3["user_messages"] == ["摩洛哥能效参数表处理"]
    assert s3["malformed_lines"] == 1
    s1 = data1["sessions"][2]
    assert s1["user_messages"] == ["昨天早上的探测任务"]
    # deterministic: second run produces byte-identical output
    out2 = tmp_path / "index-b.json"
    subprocess.run(
        [sys.executable, str(SCRIPTS / "index_sessions.py"),
         "--root", str(tmp_path / "codex-store" / "sessions"),
         "--out", str(out2)],
        capture_output=True, text=True, encoding="utf-8", env=env)
    assert out1.read_bytes() == out2.read_bytes()


def test_index_main_multiple_roots(tmp_path):
    r1 = tmp_path / "sessions"
    r2 = tmp_path / "archived_sessions"
    r1.mkdir()
    r2.mkdir()
    write_session(r1, "rollout-2026-08-30T10-00-00-d001.jsonl",
                  "2026-08-30T02:00:00.000Z", ["活跃 session"])
    write_session(r2, "rollout-2026-08-29T10-00-00-d002.jsonl",
                  "2026-08-29T02:00:00.000Z", ["已归档 session"])
    env = dict(__import__("os").environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    out = tmp_path / "merged.json"
    r = subprocess.run(
        [sys.executable, str(SCRIPTS / "index_sessions.py"),
         "--root", str(r1), "--root", str(r2), "--out", str(out)],
        capture_output=True, text=True, encoding="utf-8", env=env)
    assert r.returncode == 0, r.stderr
    data = json.loads(out.read_text(encoding="utf-8"))
    assert [s["session_id"] for s in data["sessions"]] == ["d001", "d002"]
    assert data["search_roots"] == [str(r1), str(r2)]


def test_index_main_no_rollouts_is_error(tmp_path):
    env = dict(__import__("os").environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    empty = tmp_path / "empty"
    empty.mkdir()
    r = subprocess.run(
        [sys.executable, str(SCRIPTS / "index_sessions.py"),
         "--root", str(empty), "--out", str(tmp_path / "x.json")],
        capture_output=True, text=True, encoding="utf-8", env=env)
    assert r.returncode == 1
    assert "no rollout" in r.stderr


def test_index_file_unreadable_returns_none(tmp_path):
    p = tmp_path / "rollout-bad-encoding.jsonl"
    p.write_bytes(b"\xff\xfe invalid utf8 \x00")
    assert index_file(p, {"unknown": 8000}) is None


def test_index_on_real_golden_fixture(tmp_path):
    """The real golden rollout indexes with full user-message coverage."""
    if not (FIXTURES / "basic_session.jsonl").is_file():
        pytest.skip("fixture missing")
    from preprocess_session import Preprocessor  # noqa: F401  (module import sanity)
    entry = index_file(FIXTURES / "basic_session.jsonl", {"unknown": 8000})
    assert entry["session_id"] == "sess-basic-001"
    assert any("摩洛哥能效参数表" in u for u in entry["user_messages"])