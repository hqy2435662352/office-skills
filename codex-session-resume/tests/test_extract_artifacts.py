"""Tests for scripts/extract_artifacts.py (V1.2 asset layer)."""

import hashlib
import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
SKILL_ROOT = HERE.parent
SCRIPTS = SKILL_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import extract_artifacts as ea  # noqa: E402
from preprocess_session import Preprocessor  # noqa: E402

FIXTURES = HERE / "_fixtures"
GOLDEN_DIR = SKILL_ROOT.parent / "jsonl handoff测试"
GOLDEN1 = GOLDEN_DIR / "rollout-2026-08-30T16-27-06-01a051c7-89d8-7ce0-a063-b89f4c8fd40b.jsonl"
GOLDEN2 = GOLDEN_DIR / "rollout-2026-08-30T17-10-56-01a051ef-a84c-7943-b78c-5e2f7093afdc.jsonl"


def run_clean(fixture_or_path: Path, tmp_path: Path) -> Path:
    out = tmp_path / "clean-out"
    Preprocessor().run(fixture_or_path, out)
    return out


def run_manifest(fixture_or_path: Path, tmp_path: Path, max_hash_bytes=1_000_000):
    out = run_clean(fixture_or_path, tmp_path)
    rc = ea.main(["--clean", str(out / "clean.jsonl"),
                  "--out", str(tmp_path / "artifact_manifest.json"),
                  "--max-hash-bytes", str(max_hash_bytes)])
    assert rc == 0
    return json.loads((tmp_path / "artifact_manifest.json").read_text(encoding="utf-8"))


def write_synthetic_clean(path: Path, events: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as f:
        for i, ev in enumerate(events, 1):
            ev = {"seq": i, **ev}
            f.write(json.dumps(ev, ensure_ascii=False) + "\n")


def test_filechange_only_artifacts(tmp_path):
    m = run_manifest(FIXTURES / "filechange_item_completed.jsonl", tmp_path)
    assert m["stats"]["file_change_artifacts"] == 2
    assert m["stats"]["total"] == 2
    by_path = {a["path"]: a for a in m["artifacts"]}
    assert r"c:\temp\proj\a.py" in by_path
    assert by_path[r"c:\temp\proj\a.py"]["role"] == "generator"
    assert by_path[r"c:\temp\proj\a.py"]["verification"]["exists"] is False
    assert by_path[r"c:\temp\proj\a.py"]["source"] == "file_change"


def test_verification_real_files_hash_and_large_skip(tmp_path):
    small = tmp_path / "out" / "a.py"
    small.parent.mkdir()
    small.write_bytes(b"print('hello')\n")
    big = tmp_path / "out" / "big.bin"
    big.write_bytes(b"\x00" * 2_000_000)
    clean = tmp_path / "clean.jsonl"
    write_synthetic_clean(clean, [
        {"kind": "file_change", "source_line": 1, "source_type": "event_msg",
         "changes": {str(small): {"type": "add", "content": "x"},
                     str(big): {"type": "add", "content": "y"}}},
    ])
    rc = ea.main(["--clean", str(clean), "--out", str(tmp_path / "m.json"),
                  "--max-hash-bytes", "1000000"])
    assert rc == 0
    m = json.loads((tmp_path / "m.json").read_text(encoding="utf-8"))
    by_path = {a["path"].lower(): a for a in m["artifacts"]}
    a = by_path[str(small).lower()]
    assert a["verification"]["exists"] is True
    assert a["verification"]["size"] == len(b"print('hello')\n")
    assert a["verification"]["sha256"] == \
        hashlib.sha256(b"print('hello')\n").hexdigest()
    b = by_path[str(big).lower()]
    assert b["verification"]["exists"] is True
    assert b["verification"]["sha256"] is None
    assert b["verification"]["hash_skipped"] == "large_file"
    assert m["stats"]["hashed"] == 1 and m["stats"]["exists_on_disk"] == 2


def test_command_verbs_produced_and_input(tmp_path):
    clean = tmp_path / "clean.jsonl"
    write_synthetic_clean(clean, [
        {"kind": "tool_call", "source_line": 1, "source_type": "response_item",
         "tool": "exec", "call_id": "c1", "parse_status": "parsed",
         "command": r"New-Item -ItemType File -Path C:\tmp\gen\build.py"},
        {"kind": "tool_call", "source_line": 2, "source_type": "response_item",
         "tool": "exec", "call_id": "c2", "parse_status": "parsed",
         "command": r"Get-Content -LiteralPath D:\数据\source.xlsx"},
        {"kind": "tool_call", "source_line": 3, "source_type": "response_item",
         "tool": "exec", "call_id": "c3", "parse_status": "parsed",
         "command": r"Get-Content -LiteralPath C:\Users\x\.codex\skills\demo\SKILL.md"},
        {"kind": "tool_call", "source_line": 4, "source_type": "response_item",
         "tool": "exec", "call_id": "c4", "parse_status": "parsed",
         "command": r"officecli query --sheet '/R32 摩洛哥能效 ELITE' --range A1:G5"},
    ])
    out = tmp_path / "m.json"
    rc = ea.main(["--clean", str(clean), "--out", str(out)]); assert rc == 0
    m = json.loads(out.read_text(encoding="utf-8"))
    by_path = {a["path"]: a for a in m["artifacts"]}
    assert r"c:\tmp\gen\build.py" in by_path
    assert by_path[r"c:\tmp\gen\build.py"]["category"] == "produced"
    assert by_path[r"c:\tmp\gen\build.py"]["source"] == "tool_command"
    assert r"d:\数据\source.xlsx" in by_path
    assert by_path[r"d:\数据\source.xlsx"]["category"] == "input"
    # env reads filtered and counted, sheet-query paths not treated as files
    assert m["stats"]["env_reads_filtered"] == 1
    assert not any("R32 摩洛哥能效" in p for p in by_path)
    assert not any("A1:G5" in p for p in by_path)


def test_dedup_keep_latest_seq(tmp_path):
    clean = tmp_path / "clean.jsonl"
    write_synthetic_clean(clean, [
        {"kind": "file_change", "source_line": 1, "source_type": "event_msg",
         "changes": {r"C:\tmp\a.py": {"type": "add", "content": "v1"}}},
        {"kind": "file_change", "source_line": 2, "source_type": "event_msg",
         "changes": {r"C:\tmp\a.py": {"type": "update", "content": "v2"}}},
    ])
    out = tmp_path / "m.json"
    rc = ea.main(["--clean", str(clean), "--out", str(out)])
    assert rc == 0
    m = json.loads(out.read_text(encoding="utf-8"))
    assert m["stats"]["total"] == 1
    assert m["artifacts"][0]["source_ref"] == 2  # latest event wins


def test_golden1_manifest_asset_gap_detection(tmp_path):
    if not GOLDEN1.is_file():
        pytest.skip("real rollout not present")
    m = run_manifest(GOLDEN1, tmp_path)
    assert m["stats"]["total"] >= 10
    assert m["stats"]["file_change_artifacts"] >= 8
    assert m["stats"]["missing_on_disk"] == m["stats"]["total"]  # old-machine assets
    assert any("mapping_review.json" in a["path"] for a in m["artifacts"])
    imp = m["stats"]["importance"]
    assert sum(imp.values()) == m["stats"]["total"]
    # structurally nothing is required: G1's deliverables were written by
    # officecli subprocesses (absent from FileChange) and its source reads are
    # staging-area references -> optional/historical is the honest answer
    assert imp["required"] == 0
    assert imp["historical"] > imp["optional"]


def test_golden2_manifest(tmp_path):
    if not GOLDEN2.is_file():
        pytest.skip("real rollout not present")
    m = run_manifest(GOLDEN2, tmp_path)
    assert m["stats"]["total"] >= 20
    assert m["stats"]["produced"] > 0


def test_importance_classification(tmp_path):
    clean = tmp_path / "clean.jsonl"
    write_synthetic_clean(clean, [
        {"kind": "file_change", "source_line": 1, "source_type": "event_msg",
         "changes": {
             r"C:\work\project\out\final_receipt.json": {"type": "add", "content": "{}"},
             r"C:\work\project\scripts\gen.py": {"type": "add", "content": "x"},
             r"C:\work\project\build": {"type": "add", "content": "y"},
         }},
        {"kind": "tool_call", "source_line": 2, "source_type": "response_item",
         "tool": "exec", "call_id": "c1", "parse_status": "parsed",
         "command": r"Get-Content -LiteralPath D:\数据\source.xlsx"},
        {"kind": "tool_call", "source_line": 3, "source_type": "response_item",
         "tool": "exec", "call_id": "c2", "parse_status": "parsed",
         "command": r"Get-Content -LiteralPath C:\Temp\tablefill\morocco\validated_draft.xlsx"},
    ])
    out = tmp_path / "m.json"
    rc = ea.main(["--clean", str(clean), "--out", str(out)])
    assert rc == 0
    m = json.loads(out.read_text(encoding="utf-8"))
    by_path = {a["path"].lower(): a for a in m["artifacts"]}
    # evidence role -> required
    assert by_path[r"c:\work\project\out\final_receipt.json"]["importance"] == "required"
    # file_change produced generator -> optional (rebuildable)
    assert by_path[r"c:\work\project\scripts\gen.py"]["importance"] == "optional"
    # no-extension path -> ephemeral
    assert by_path[r"c:\work\project\build"]["importance"] == "ephemeral"
    # source input outside staging -> required; staging read -> historical
    assert by_path[r"d:\数据\source.xlsx"]["importance"] == "required"
    assert by_path[r"c:\temp\tablefill\morocco\validated_draft.xlsx"]["importance"] == "historical"
    # aggregation sanity
    imp = m["stats"]["importance"]
    assert sum(imp.values()) == m["stats"]["total"]
    assert m["stats"]["required_exists"] == 0              # nothing on disk
    assert m["stats"]["required_missing"] == imp["required"]  # both required are missing
    assert imp["required"] == 2 and imp["optional"] == 1
    assert imp["historical"] == 1 and imp["ephemeral"] == 1


def test_determinism(tmp_path):
    out = run_clean(FIXTURES / "basic_session.jsonl", tmp_path)
    a = tmp_path / "a.json"
    b = tmp_path / "b.json"
    ea.main(["--clean", str(out / "clean.jsonl"), "--out", str(a)])
    ea.main(["--clean", str(out / "clean.jsonl"), "--out", str(b)])
    assert a.read_bytes() == b.read_bytes()