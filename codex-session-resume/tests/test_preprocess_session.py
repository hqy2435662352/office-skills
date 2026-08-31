"""Tests for scripts/preprocess_session.py (codex-session-resume skill).

Covers: parser robustness (valid/blank/malformed/non-object/unicode/Windows
paths), filtering policy (reasoning, encrypted content, token_count, duplicate
item_completed, developer messages, client-injected blocks), normalization
(user/assistant/tool_call/tool_output/file_change/lifecycle/turn_context/
compaction/world_state_marker/rollback/unknown), source traceability, rollback
semantics, determinism, truncation markers, and pinned golden stats on the two
real Codex Desktop rollouts (skipped when the files are absent).
"""

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
SKILL_ROOT = HERE.parent
SCRIPTS = SKILL_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from preprocess_session import (  # noqa: E402
    Preprocessor,
    extract_exec_command,
    strip_client_blocks,
    truncate_head_tail,
)

FIXTURES = HERE / "_fixtures"
GOLDEN_DIR = SKILL_ROOT.parent / "jsonl handoff测试"
GOLDEN1 = GOLDEN_DIR / "rollout-2026-08-30T16-27-06-01a051c7-89d8-7ce0-a063-b89f4c8fd40b.jsonl"
GOLDEN2 = GOLDEN_DIR / "rollout-2026-08-30T17-10-56-01a051ef-a84c-7943-b78c-5e2f7093afdc.jsonl"


def run_fixture(name: str, tmp_path: Path, caps: dict | None = None):
    return run_input(FIXTURES / name, tmp_path, caps)


def run_input(path: Path, tmp_path: Path, caps: dict | None = None):
    out = tmp_path / ("out-" + path.stem)
    pre = Preprocessor(caps)
    meta, stats = pre.run(path, out)
    events = [
        json.loads(line)
        for line in (out / "clean.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    return events, meta, stats


def raw_lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines()


# ---------------------------------------------------------------------------
# unit helpers
# ---------------------------------------------------------------------------


def test_truncate_head_tail_behavior():
    assert truncate_head_tail("short", 100, 7) == "short"
    long_text = "x" * 1000
    out = truncate_head_tail(long_text, 200, 42)
    assert len(out) < len(long_text)
    assert out.startswith("x" * 120)
    assert out.endswith("x" * 80)
    assert "TRUNCATED" in out and "1000" in out and "42" in out
    # determinism
    assert truncate_head_tail(long_text, 200, 42) == out


def test_strip_client_blocks_behavior():
    # block + real text in the SAME item: real text survives
    t, n = strip_client_blocks("<recommended_plugins>\nA\nB\n</recommended_plugins>\n\n请继续做摩洛哥部分")
    assert n == 1 and "请继续做摩洛哥部分" in t and "recommended_plugins" not in t
    # pure block -> empty
    t, n = strip_client_blocks("<environment_context>\n<cwd>C:\\x</cwd>\n</environment_context>")
    assert n == 1 and t == ""
    # consecutive blocks separated by blank lines are all stripped
    t, n = strip_client_blocks(
        "<recommended_plugins>\nA\n</recommended_plugins>\n\n"
        "<environment_context>\n<cwd>C:\\x</cwd>\n</environment_context>\n\n正文")
    assert n == 2 and t == "\n\n正文" and "environment_context" not in t
    # INCOMPLETE boundary -> nothing deleted (conservative V1.1 rule)
    t, n = strip_client_blocks("<permissions\nsandbox")
    assert n == 0 and t == "<permissions\nsandbox"
    t, n = strip_client_blocks("<recommended_plugins> 未闭合\n\n真实正文")
    assert n == 0 and t == "<recommended_plugins> 未闭合\n\n真实正文"
    # AGENTS.md complete block stripped, trailing text preserved
    t, n = strip_client_blocks(
        "# AGENTS.md instructions\n\n<INSTRUCTIONS>stuff</INSTRUCTIONS>\n\n我的要求")
    assert n == 1 and t == "\n\n我的要求"
    # AGENTS.md without a complete INSTRUCTIONS boundary -> kept
    t, n = strip_client_blocks("# AGENTS.md instructions\n\n<INSTRUCTIONS>stuff")
    assert n == 0 and t.startswith("# AGENTS.md instructions")
    # unknown content untouched
    t, n = strip_client_blocks("普通用户消息")
    assert n == 0 and t == "普通用户消息"


def test_extract_exec_command_behavior():
    cmd, status = extract_exec_command(
        'const r = await tools.exec_command({"cmd":"Get-ChildItem C:\\\\数据"}); text(r.output);')
    assert status == "parsed" and cmd == r"Get-ChildItem C:\数据"
    cmd, status = extract_exec_command(
        'const r = await tools.exec_command({"cmd":"dir","cwd":"C:\\\\a b"}, {"x":1}); text(r.output);')
    assert status == "parsed" and cmd == "dir"
    cmd, status = extract_exec_command("const r = await tools.exec_command({bad json});")
    assert status == "unparsed" and cmd is None
    cmd, status = extract_exec_command("const r = await tools.write_stdin({\"x\":1});")
    assert status == "unparsed" and cmd is None
    cmd, status = extract_exec_command('const r = await tools.exec_command({"cmd":""});')
    assert status == "unparsed" and cmd is None


# ---------------------------------------------------------------------------
# parser / filtering / normalization
# ---------------------------------------------------------------------------


def test_basic_session_normalization(tmp_path):
    events, meta, stats = run_fixture("basic_session.jsonl", tmp_path)
    kinds = [e["kind"] for e in events]
    assert kinds == [
        "world_state_marker", "turn_context", "user", "assistant",
        "tool_call", "tool_output", "tool_call", "tool_output",
        "assistant", "lifecycle",
    ]
    # user message preserved verbatim (Chinese + Windows path + emoji)
    user = events[2]
    assert "摩洛哥能效参数表 🚀 path=C:\\数据\\摩洛哥.xlsx" in user["text"]
    assert user["active"] is True
    assert user["id"] == "msg-user-1"
    # assistant phases preserved
    assert [e["phase"] for e in events if e["kind"] == "assistant"] == ["commentary", "final_answer"]
    # tool pairing via call_id + cmd extraction
    calls = [e for e in events if e["kind"] == "tool_call"]
    outs = [e for e in events if e["kind"] == "tool_output"]
    assert calls[0]["call_id"] == "call-1" and calls[0]["tool"] == "exec"
    assert calls[0]["command"] == r"Get-ChildItem C:\数据" and calls[0]["parse_status"] == "parsed"
    assert calls[1]["command"] is None and calls[1]["parse_status"] == "unparsed"
    assert {o["tool"] for o in outs} == {"exec"}
    assert outs[0]["call_id"] == "call-1"
    assert "摩洛哥.xlsx" in outs[0]["output"] and "2 files" in outs[0]["output"]
    # turn_context trimmed: useful fields kept, giant boilerplate dropped
    tc = events[1]
    assert tc["turn_id"] == "turn-0001" and tc["model"] == "gpt-5.6-sol"
    assert tc["cwd"] == "C:\\Users\\测试\\项目" and tc["approval_policy"] == "never"
    assert tc["sandbox_policy"] == "danger-full-access"
    assert "collaboration_mode" not in tc and "personality" not in tc
    # lifecycle kept light
    lc = events[-1]
    assert lc["event"] == "task_complete" and lc["turn_id"] == "turn-0001"
    assert lc["duration_ms"] == 13000 and "处理完成。" in lc["message"]
    # world_state marker: payload omitted
    ws = events[0]
    assert ws["payload_omitted"] is True and ws["full"] is True
    assert ws["state_keys"] == ["agents_md"] and len(ws["sha256"]) == 64
    # clean stream carries no encrypted reasoning, no world-state body, no environment boilerplate
    blob = "\n".join(json.dumps(e, ensure_ascii=False) for e in events)
    assert "encrypted_content" not in blob and "gAAAA" not in blob
    assert "BOILERPLATE" not in blob and "GLOBAL" not in blob
    # meta
    assert meta["session_id"] == "sess-basic-001"
    assert meta["cwd"] == "C:\\Users\\测试\\项目"
    assert meta["model"] == "gpt-5.6-sol" and meta["model_provider"] == "openai"
    assert meta["turn_count"] == 1 and meta["tool_names"] == ["exec"]
    assert meta["source_rollout"] == "basic_session.jsonl"


def test_basic_session_stats_pinned(tmp_path):
    _, _, stats = run_fixture("basic_session.jsonl", tmp_path)
    assert stats["raw_records"] == 18
    assert stats["clean_events"] == 10
    assert stats["user_messages"] == 1
    assert stats["assistant_messages"] == 2
    assert stats["tool_calls"] == 2
    assert stats["tool_outputs"] == 2
    assert stats["file_changes"] == 0
    assert stats["lifecycle_events"] == 1
    assert stats["turn_contexts"] == 1
    assert stats["world_state_markers"] == 1
    assert stats["compactions"] == 0 and stats["rollbacks"] == 0
    assert stats["reasoning_removed"] == 1
    assert stats["token_events_removed"] == 1
    assert stats["duplicates_removed"] == 2
    assert stats["bookkeeping_removed"] == 2
    assert stats["developer_messages_removed"] == 1
    assert stats["unknown_records"] == 0 and stats["malformed_lines"] == 0
    assert stats["client_blocks_stripped"] == 0 and stats["client_context_messages"] == 0
    assert stats["tool_inputs_parsed"] == 1 and stats["tool_inputs_unparsed"] == 1
    assert stats["legacy_eventmsg_messages"] == 0
    assert stats["truncated_events"] == 0
    assert stats["unknown_by_source"] == {}
    assert sum(stats["events_by_kind"].values()) == stats["clean_events"]


def test_source_traceability(tmp_path):
    events, _, _ = run_fixture("basic_session.jsonl", tmp_path)
    lines = raw_lines(FIXTURES / "basic_session.jsonl")
    for ev in events:
        raw = json.loads(lines[ev["source_line"] - 1])
        # source_type matches the raw top-level type for every event kind
        assert ev["source_type"] == raw["type"], ev
        assert ev.get("source_ordinal") == raw.get("ordinal"), ev
    # targeted backfill: user event points at the raw user message record
    user = next(e for e in events if e["kind"] == "user")
    raw = json.loads(lines[user["source_line"] - 1])
    assert raw["payload"]["role"] == "user"
    assert raw["payload"]["id"] == user["id"]
    # tool pairing is traceable to raw call_ids
    call = next(e for e in events if e["kind"] == "tool_call" and e["parse_status"] == "unparsed")
    raw_call = json.loads(lines[call["source_line"] - 1])
    assert raw_call["payload"]["call_id"] == call["call_id"]


def test_filechange_kept_only_in_item_completed(tmp_path):
    events, _, stats = run_fixture("filechange_item_completed.jsonl", tmp_path)
    assert stats["file_changes"] == 1
    assert stats["duplicates_removed"] == 2  # CommandExecution + AgentMessage
    fc = next(e for e in events if e["kind"] == "file_change")
    assert fc["item_id"] == "exec-2"
    assert set(fc["changes"].keys()) == {"C:\\Temp\\proj\\a.py", "C:\\Temp\\proj\\b.py"}
    assert "print('hello')" in fc["changes"]["C:\\Temp\\proj\\a.py"]["content"]
    assert "+y" in fc["changes"]["C:\\Temp\\proj\\b.py"]["unified_diff"]
    assert len([e for e in events if e["kind"] == "unknown"]) == 0


def test_rollback_one_turn_invalidation(tmp_path):
    events, _, stats = run_fixture("rollback_one_turn.jsonl", tmp_path)
    assert stats["rollbacks"] == 1
    users = [e for e in events if e["kind"] == "user"]
    # newest user turn (B) invalidated, surviving turn (A) untouched
    assert users[0]["text"].startswith("方案 A") and users[0]["active"] is True
    assert users[1]["text"].startswith("方案 B")
    assert users[1]["active"] is False
    assert users[1]["invalidated_by"] == "thread_rolled_back"
    assts = [e for e in events if e["kind"] == "assistant"]
    assert assts[0]["active"] is True
    assert assts[1]["active"] is False and assts[1]["invalidated_by"] == "thread_rolled_back"
    rb = next(e for e in events if e["kind"] == "rollback")
    assert rb["num_turns"] == 1
    assert rb["invalidated_users"] == 1
    assert rb["invalidated_events"] == 2
    # no turn_context records -> user-boundary fallback is honestly flagged
    assert rb["confidence"] == "approximate"


def test_rollback_two_turns_invalidation(tmp_path):
    events, _, stats = run_fixture("rollback_two_turns.jsonl", tmp_path)
    assert stats["rollbacks"] == 1
    users = [e for e in events if e["kind"] == "user"]
    assert users[0]["active"] is True  # oldest survives
    assert users[1]["active"] is False
    assert users[2]["active"] is False
    rb = next(e for e in events if e["kind"] == "rollback")
    assert rb["num_turns"] == 2
    assert rb["invalidated_users"] == 2
    assert rb["invalidated_events"] == 3  # B user, C user, C assistant
    assert rb["confidence"] == "approximate"


def test_rollback_turn_aware(tmp_path):
    events, _, stats = run_fixture("rollback_turn_aware.jsonl", tmp_path)
    users = [e for e in events if e["kind"] == "user"]
    # turn-A survives (two user messages, one segment); turn-B (one turn) is rolled back
    assert users[0]["active"] is True and users[1]["active"] is True
    assert users[2]["active"] is False
    rb = next(e for e in events if e["kind"] == "rollback")
    assert rb["confidence"] == "exact"
    assert rb["invalidated_users"] == 1
    assert rb["invalidated_events"] == 2  # b1 user + b2 assistant
    assts = [e for e in events if e["kind"] == "assistant"]
    assert assts[0]["active"] is False


def test_rollback_same_turn_single_unit(tmp_path):
    events, _, stats = run_fixture("rollback_same_turn.jsonl", tmp_path)
    users = [e for e in events if e["kind"] == "user"]
    # both user messages live in ONE turn -> one rollback unit, whole turn invalidated
    assert all(u["active"] is False for u in users)
    rb = next(e for e in events if e["kind"] == "rollback")
    assert rb["invalidated_users"] == 1
    assert rb["invalidated_events"] == 4  # x, x2, y, y2
    assert rb["confidence"] == "exact"


def test_item_completed_unknown_fail_visible(tmp_path):
    events, _, stats = run_fixture("item_completed_unknown.jsonl", tmp_path)
    assert stats["unknown_records"] == 2
    assert stats["duplicates_removed"] == 1
    assert stats["unknown_by_source"] == {
        "event_msg/item_completed:FutureNewItem": 1,
        "event_msg/item_completed:<missing-item>": 1,
    }
    u = [e for e in events if e["kind"] == "unknown"]
    assert len(u) == 2
    assert u[0]["raw_type"] == "item_completed:FutureNewItem"
    assert u[0]["source_line"] == 1 and "FutureNewItem" in u[0]["raw_payload"]
    assert u[1]["raw_type"] == "item_completed:<missing-item>"


def test_client_blocks_incomplete_kept_conservative(tmp_path):
    events, _, stats = run_fixture("client_blocks_edge.jsonl", tmp_path)
    assert stats["client_blocks_stripped"] == 3  # only fully-bounded blocks removed
    assert stats["user_messages"] == 1
    assert stats["client_context_messages"] == 0
    user = next(e for e in events if e["kind"] == "user")
    t = user["text"]
    assert "第一段真实正文" in t and "第二段真实正文" in t
    assert "真实内容不应被吞" in t and "第四段真实正文" in t
    assert "Airtable" not in t
    assert "<environment_context>" not in t and "<INSTRUCTIONS>" not in t
    # incomplete / unclosed boundaries are NEVER deleted
    assert "<permissions" in t
    assert "<recommended_plugins> 悬空未闭合" in t


def test_compaction_marker_no_replacement_leak(tmp_path):
    events, _, stats = run_fixture("compacted_with_replacement_history.jsonl", tmp_path)
    assert stats["compactions"] == 2
    markers = [e for e in events if e["kind"] == "compaction"]
    m0, m1 = markers
    assert m0["message"] == "总结：已完成摩洛哥部分，剩余欧洲部分。"
    assert m0["window_number"] == 2 and m0["window_id"] == "win-2"
    assert m0["first_window_id"] == "win-1" and m0["previous_window_id"] == "win-0"
    assert m0["replacement_history_present"] is True
    assert m0["replacement_history_items"] == 2
    assert m0["replacement_history_bytes"] > 100
    assert m1["message"] == "嵌套形式摘要" and m1["replacement_history_present"] is True
    blob = "\n".join(json.dumps(e, ensure_ascii=False) for e in events)
    # the full replacement_history is never re-injected into the clean stream
    assert "SECRET-REPLACEMENT-CONTENT" not in blob
    assert "rh-1" not in blob


def test_world_state_full_and_patch_markers(tmp_path):
    events, _, stats = run_fixture("world_state_full_patch.jsonl", tmp_path)
    assert stats["world_state_markers"] == 2
    m0, m1 = [e for e in events if e["kind"] == "world_state_marker"]
    assert m0["full"] is True and m0["state_keys"] == ["agents_md", "skills"]
    assert m1["full"] is False
    # deterministic sha256 of the canonical payload
    payload = {"full": True, "state": {"agents_md": {"text": "BIG-AGENTS-BODY-SHOULD-NOT-LEAK"},
                                       "skills": {"list": [1, 2, 3]}}}
    expect = hashlib.sha256(json.dumps(payload, ensure_ascii=False).encode("utf-8")).hexdigest()
    assert m0["sha256"] == expect
    blob = "\n".join(json.dumps(e, ensure_ascii=False) for e in events)
    assert "BIG-AGENTS-BODY-SHOULD-NOT-LEAK" not in blob


def test_lifecycle_turn_aborted_and_complete(tmp_path):
    events, _, stats = run_fixture("aborted_turn.jsonl", tmp_path)
    assert stats["lifecycle_events"] == 2
    ab, done = [e for e in events if e["kind"] == "lifecycle"]
    assert ab["event"] == "turn_aborted"
    assert ab["turn_id"] == "turn-77" and ab["reason"] == "interrupted"
    assert ab["duration_ms"] == 5000 and "message" not in ab
    assert done["event"] == "task_complete" and done["turn_id"] == "turn-78"


def test_unknown_records_fail_visible(tmp_path):
    events, _, stats = run_fixture("unknown_future_event.jsonl", tmp_path)
    assert stats["unknown_records"] == 4
    assert stats["malformed_lines"] == 0
    assert stats["unknown_by_source"] == {
        "top-level/future_record": 1,
        "event_msg/quantum_leap": 1,
        "response_item/telepathy": 1,
        "response_item/message:superuser": 1,
    }
    unknowns = [e for e in events if e["kind"] == "unknown"]
    assert len(unknowns) == 4
    for e in unknowns:
        assert e["raw_type"] is not None and "raw_payload" in e
        assert e["source_line"] >= 1 and "source_ordinal" in e


def test_malformed_json_and_non_object_lines(tmp_path):
    events, _, stats = run_fixture("malformed_and_unicode.jsonl", tmp_path)
    assert stats["malformed_lines"] == 2  # malformed JSON line + non-object line
    assert stats["unknown_records"] == 2
    assert stats["unknown_by_source"] == {
        "raw-line/malformed-json": 1,
        "raw-line/non-object": 1,
    }
    user = next(e for e in events if e["kind"] == "user")
    assert "中文内容：摩洛哥能效 🚀 Windows路径 C:\\Users\\测试\\文件夹\\表.xlsx" in user["text"]
    assert len([e for e in events if e["kind"] == "unknown"]) == 2
    assert stats["token_events_removed"] == 1


def test_legacy_eventmsg_user_and_agent_messages(tmp_path):
    events, _, stats = run_fixture("legacy_eventmsg_messages.jsonl", tmp_path)
    assert stats["legacy_eventmsg_messages"] == 2
    assert stats["user_messages"] == 1 and stats["assistant_messages"] == 1
    users = [e for e in events if e["kind"] == "user"]
    assts = [e for e in events if e["kind"] == "assistant"]
    assert users[0]["text"] == "旧格式用户消息" and users[0]["via"] == "event_msg"
    assert assts[0]["text"] == "旧格式助手消息" and assts[0]["via"] == "event_msg"
    assert users[0]["active"] is True


def test_legacy_user_message_duplicate_dedup(tmp_path):
    """legacy codex-tui rollouts write BOTH response_item message and an
    event_msg/user_message marker for the same input; the marker is a duplicate
    within the same turn and must not double-count the user."""
    events, _, stats = run_fixture("legacy_duplicate_user.jsonl", tmp_path)
    assert stats["legacy_eventmsg_messages"] == 2
    assert stats["legacy_duplicates_removed"] == 1
    assert stats["user_messages"] == 2
    users = [e for e in events if e["kind"] == "user"]
    assert [u["text"] for u in users] == ["hi", "what's up"]
    assert "via" not in users[0]  # canonical response_item form
    assert users[1]["via"] == "event_msg"


def test_client_injected_strip_not_position_heuristic(tmp_path):
    events, _, stats = run_fixture("client_injected_user.jsonl", tmp_path)
    assert stats["client_blocks_stripped"] == 3
    assert stats["user_messages"] == 1
    assert stats["client_context_messages"] == 1
    user = next(e for e in events if e["kind"] == "user")
    # real question glued to an injected block survives; block removed
    assert "请继续做摩洛哥部分" in user["text"]
    assert "recommended_plugins" not in user["text"] and "Airtable" not in user["text"]
    cc = next(e for e in events if e["kind"] == "client_context")
    assert cc["id"] == "m-pure"


def test_function_call_mcp_normalization(tmp_path):
    events, meta, stats = run_fixture("function_call_mcp.jsonl", tmp_path)
    assert stats["tool_calls"] == 1 and stats["tool_outputs"] == 1
    # function_call arguments participate in the same parsed/unparsed accounting
    assert stats["tool_inputs_parsed"] == 1 and stats["tool_inputs_unparsed"] == 0
    call = next(e for e in events if e["kind"] == "tool_call")
    assert call["tool"] == "load_workspace_dependencies"
    assert call["namespace"] == "mcp__codex_app"
    assert call["call_id"] == "call-fc-1"
    assert call["parse_status"] == "parsed" and call["command"] is None
    assert '"path": "C:\\\\x"' in call["input"] or "C:\\\\x" in call["input"]
    out = next(e for e in events if e["kind"] == "tool_output")
    assert out["tool"] == "load_workspace_dependencies" and out["call_id"] == "call-fc-1"
    assert "Bundle 26.826.12353 available" in out["output"]
    assert meta["tool_names"] == ["load_workspace_dependencies"]


def test_truncation_marker_and_cap_flag(tmp_path):
    # small cap forces deterministic head+tail truncation with a visible marker
    events, _, stats = run_fixture("basic_session.jsonl", tmp_path,
                                   caps={"tool_output": 30})
    assert stats["truncated_events"] >= 1
    out = next(e for e in events if e["kind"] == "tool_output" and e["call_id"] == "call-1")
    assert "TRUNCATED" in out["output"] and "raw source line" in out["output"]
    assert out["output"].startswith("Script completed")
    assert out["output"].endswith("2 files")


# ---------------------------------------------------------------------------
# determinism
# ---------------------------------------------------------------------------


def test_determinism_fixture_byte_identical(tmp_path):
    d1, d2 = tmp_path / "a", tmp_path / "b"
    Preprocessor().run(FIXTURES / "basic_session.jsonl", d1)
    Preprocessor().run(FIXTURES / "basic_session.jsonl", d2)
    for name in ("clean.jsonl", "meta.json", "stats.json"):
        assert (d1 / name).read_bytes() == (d2 / name).read_bytes()
    # caps variant is also deterministic
    d3, d4 = tmp_path / "c", tmp_path / "d"
    caps = {"tool_output": 77, "unknown": 33}
    Preprocessor(caps).run(FIXTURES / "basic_session.jsonl", d3)
    Preprocessor(caps).run(FIXTURES / "basic_session.jsonl", d4)
    assert (d3 / "clean.jsonl").read_bytes() == (d4 / "clean.jsonl").read_bytes()


@pytest.mark.skipif(not GOLDEN1.is_file(), reason="real rollout not present")
def test_determinism_golden_byte_identical(tmp_path):
    d1, d2 = tmp_path / "a", tmp_path / "b"
    Preprocessor().run(GOLDEN1, d1)
    Preprocessor().run(GOLDEN1, d2)
    for name in ("clean.jsonl", "meta.json", "stats.json"):
        assert (d1 / name).read_bytes() == (d2 / name).read_bytes()


# ---------------------------------------------------------------------------
# golden samples (real Codex Desktop rollouts)
# ---------------------------------------------------------------------------


GOLDEN1_EXPECTED = {
    "source_rollout": "rollout-2026-08-30T16-27-06-01a051c7-89d8-7ce0-a063-b89f4c8fd40b.jsonl",
    "raw_bytes": 3813567, "clean_bytes": 920692, "compression_ratio": 4.14,
    "raw_records": 985, "malformed_lines": 0, "clean_events": 344,
    "user_messages": 9, "assistant_messages": 39, "tool_calls": 129,
    "tool_outputs": 129, "file_changes": 16, "lifecycle_events": 9,
    "turn_contexts": 9, "compactions": 0, "rollbacks": 0,
    "world_state_markers": 3, "unknown_records": 0, "client_context_messages": 1,
    "client_blocks_stripped": 3, "reasoning_removed": 177,
    "token_events_removed": 138, "duplicates_removed": 303,
    "bookkeeping_removed": 18, "developer_messages_removed": 4,
    "tool_inputs_parsed": 78, "tool_inputs_unparsed": 51,
    "legacy_eventmsg_messages": 0, "truncated_events": 3,
    "events_by_kind": {
        "client_context": 1, "world_state_marker": 3, "turn_context": 9,
        "user": 9, "assistant": 39, "tool_call": 129, "tool_output": 129,
        "file_change": 16, "lifecycle": 9,
    },
    "unknown_by_source": {},
}


@pytest.mark.skipif(not GOLDEN1.is_file(), reason="real rollout not present")
def test_golden1_pinned_stats_and_metadata(tmp_path):
    events, meta, stats = run_input(GOLDEN1, tmp_path)
    got = dict(stats)
    assert got["raw_bytes"] == GOLDEN1_EXPECTED["raw_bytes"]  # sanity gate first
    for k, v in GOLDEN1_EXPECTED.items():
        assert got[k] == v, f"stats[{k}] = {got[k]!r}, expected {v!r}"
    assert meta["session_id"] == "01a051c7-89d8-7ce0-a063-b89f4c8fd40b"
    assert meta["cwd"].endswith("北非客户参数表整理")
    assert meta["model"] == "gpt-5.6-sol" and meta["model_provider"] == "openai"
    assert meta["originator"] == "Codex Desktop"
    assert meta["started_at"] == "2026-08-30T08:27:06.767Z"
    assert meta["last_timestamp"] == "2026-08-30T14:33:12.713Z"
    assert meta["turn_count"] == 9
    assert meta["tool_names"] == ["exec"]
    assert meta["source_rollout"] == GOLDEN1.name


@pytest.mark.skipif(not GOLDEN1.is_file(), reason="real rollout not present")
def test_golden1_resume_relevant_content_recovered(tmp_path):
    events, _, _ = run_input(GOLDEN1, tmp_path)
    users = [e for e in events if e["kind"] == "user"]
    # all real user messages survive, in order, incl. corrections and approvals
    assert len(users) == 9
    assert all(u["active"] is True for u in users)
    assert "tcl-customer-parameter-sheet" in users[0]["text"]
    assert "确认 MOD NONE" in users[1]["text"]
    assert "先看摩洛哥能效的部分" in users[3]["text"]
    scope_fix = next(u for u in users if "摩洛哥能效和欧洲能效都需要更新外发参数表" in u["text"])
    assert scope_fix is not None
    approval = users[-1]
    assert "可以的" in approval["text"]
    # chronology preserved
    seqs = [e["seq"] for e in events]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)
    # resume point: final visible message is the gated final answer, then lifecycle
    last_assistant = [e for e in events if e["kind"] == "assistant"][-1]
    assert last_assistant["phase"] == "final_answer"
    assert "尚未写入正式目录、尚未导出PDF" in last_assistant["text"]
    assert "8份客户版Excel草稿" in last_assistant["text"]
    assert events[-1]["kind"] == "lifecycle" and events[-1]["event"] == "task_complete"
    # no hidden reasoning leaks into the clean stream
    blob = "\n".join(json.dumps(e, ensure_ascii=False) for e in events)
    assert "encrypted_content" not in blob


GOLDEN2_EXPECTED = {
    "source_rollout": "rollout-2026-08-30T17-10-56-01a051ef-a84c-7943-b78c-5e2f7093afdc.jsonl",
    "raw_bytes": 5814287, "clean_bytes": 1263181, "compression_ratio": 4.6,
    "raw_records": 1653, "malformed_lines": 0, "clean_events": 579,
    "user_messages": 20, "assistant_messages": 56, "tool_calls": 212,
    "tool_outputs": 212, "file_changes": 31, "lifecycle_events": 20,
    "turn_contexts": 20, "compactions": 0, "rollbacks": 0,
    "world_state_markers": 3, "unknown_records": 3, "client_context_messages": 2,
    "client_blocks_stripped": 4, "reasoning_removed": 275,
    "token_events_removed": 230, "duplicates_removed": 520,
    "bookkeeping_removed": 40, "developer_messages_removed": 8,
    "tool_inputs_parsed": 169, "tool_inputs_unparsed": 43,
    "legacy_eventmsg_messages": 0, "truncated_events": 5,
    "events_by_kind": {
        "client_context": 2, "world_state_marker": 3, "turn_context": 20,
        "user": 20, "assistant": 56, "tool_call": 212, "tool_output": 212,
        "lifecycle": 20, "file_change": 31, "unknown": 3,
    },
    "unknown_by_source": {"event_msg/item_completed:ImageView": 3},
}


@pytest.mark.skipif(not GOLDEN2.is_file(), reason="real rollout not present")
def test_golden2_pinned_stats(tmp_path):
    events, meta, stats = run_input(GOLDEN2, tmp_path)
    got = dict(stats)
    assert got["raw_bytes"] == GOLDEN2_EXPECTED["raw_bytes"]
    for k, v in GOLDEN2_EXPECTED.items():
        assert got[k] == v, f"stats[{k}] = {got[k]!r}, expected {v!r}"
    assert meta["session_id"] == "01a051ef-a84c-7943-b78c-5e2f7093afdc"
    aborted = [e for e in events if e["kind"] == "lifecycle" and e["event"] == "turn_aborted"]
    assert len(aborted) == 2 and all(a["reason"] == "interrupted" for a in aborted)
    # tool pairing: every output resolves to its call's tool name (exec or MCP)
    outs = [e for e in events if e["kind"] == "tool_output"]
    assert all(o.get("tool") in ("exec", "open_in_codex", "load_workspace_dependencies") for o in outs)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_cli_roundtrip_and_exit_codes(tmp_path):
    script = SCRIPTS / "preprocess_session.py"
    out = tmp_path / "cli-out"
    env = dict(__import__("os").environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    r = subprocess.run(
        [sys.executable, str(script), "--input", str(FIXTURES / "basic_session.jsonl"),
         "--output", str(out), "--cap-tool-output", "30"],
        capture_output=True, text=True, encoding="utf-8", env=env)
    assert r.returncode == 0, r.stderr
    assert (out / "clean.jsonl").is_file()
    assert (out / "meta.json").is_file() and (out / "stats.json").is_file()
    clean = (out / "clean.jsonl").read_text(encoding="utf-8")
    assert "...<TRUNCATED:" in clean
    stats = json.loads((out / "stats.json").read_text(encoding="utf-8"))
    assert stats["truncated_events"] >= 1
    # missing input must fail loudly
    r2 = subprocess.run(
        [sys.executable, str(script), "--input", str(tmp_path / "nope.jsonl"),
         "--output", str(tmp_path / "x")],
        capture_output=True, text=True, encoding="utf-8", env=env)
    assert r2.returncode == 1
    assert "not found" in r2.stderr


def test_web_search_call_end_pairing(tmp_path):
    """legacy web search: response_item/web_search_call (id-form) + its
    event_msg/web_search_end marker must normalize and pair via call_id."""
    events, _, stats = run_fixture("web_search_pair.jsonl", tmp_path)
    assert stats["tool_calls"] == 1
    assert stats["unknown_records"] == 0
    call = next(e for e in events if e["kind"] == "tool_call")
    assert call["tool"] == "web_search_call"
    assert call["call_id"] == "call_00_mIVld5U0Ffe4WIPJGrbs8114"
    assert "trusted_hash" in call["input"]
    done = next(e for e in events if e["kind"] == "search_end")
    assert done["call_id"] == call["call_id"]
    assert done["tool"] == "web_search_call"
    assert "trusted_hash" in done["query"]


def test_input_never_modified(tmp_path):
    fixture = FIXTURES / "basic_session.jsonl"
    before = fixture.read_bytes()
    run_fixture("basic_session.jsonl", tmp_path)
    assert fixture.read_bytes() == before