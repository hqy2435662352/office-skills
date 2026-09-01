#!/usr/bin/env python3
"""
scripts/extract_artifacts.py — Artifact Manifest (asset layer, V1.2).

Consumes ONLY clean.jsonl and emits artifact_manifest.json: the files a resumed
Agent needs to continue — produced files, input files the task depended on, and
the evidence files that prove what was completed. The manifest then verifies
each path against the CURRENT filesystem (exists / size / mtime / sha256):
this turns "resume needs assets" into "resume needs these assets and these are
missing", which is exactly what Workspace Rebind needs.

  python scripts/extract_artifacts.py --clean out/clean.jsonl --out out/artifact_manifest.json

Sources (deterministic, in priority order):
  1. file_change events (the primary source — real Codex rollouts record adds/
     updates there and nowhere else in the response stream)
  2. write-command paths parsed from exec tool calls (New-Item/Set-Content/
     Copy-Item/...), only plain literal paths — conservative
  3. read-command paths (Get-Content/cat/...) as INPUT artifacts, only when they
     are not already produced paths

Verification policy (per file, no full-tree scans):
  exists ? size + mtime (UTC) ; sha256 when size <= --max-hash-bytes (default 1MB)
  missing -> exists:false, verification null (this is the actionable signal)

No semantic judgment: roles (generator/deliverable/evidence/input) follow
extension and naming heuristics that are visible in each entry's "role_basis".
Exit codes: 0 = ok; 1 = usage/input error.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path, PurePath
from typing import Any

DELIVERABLE_EXT = {".xlsx", ".xls", ".docx", ".pptx", ".pdf", ".csv", ".png", ".html", ".htm"}
EVIDENCE_MARKERS = ("receipt", "manifest", "validation", "report", "summary", "hash")
# input files a task genuinely depends on (source data / config), when NOT in a
# staging area — staging reads (Temp/tmp) are historical references, not assets
SOURCE_INPUT_EXT = {".xlsx", ".xls", ".xlsm", ".csv", ".docx", ".pptx", ".pdf",
                    ".json", ".yaml", ".yml", ".md", ".txt"}
_STAGING = ("\\temp\\", "\\tmp\\")

WRITE_VERBS = {"New-Item", "Set-Content", "Copy-Item", "Move-Item", "Out-File",
               "mkdir", "cp", "mv", "touch", "tee"}
READ_VERBS = {"Get-Content", "cat", "type"}

MAX_TOOL_ARTIFACTS = 150  # bound on noisy command-derived artifacts (deterministic cap)

# A path token must start with a drive letter or a dot-relative form.
# Slash/backslash-rooted quoted strings (officecli sheet paths like
# '/R32 摩洛哥能效 ELITE/A1:G5', PS magic) are NOT file paths; bare-rooted
# POSIX paths are a documented V1.2 limitation (primary target: Windows).
_PATH_TOKEN = re.compile(r"""
    ["'](?P<q>(?:[A-Za-z]:[\\/]|\.{1,2}[\\/])[^"']+)["']   # quoted path (spaces ok)
  | (?P<b>(?:[A-Za-z]:[\\/]|\.{1,2}[\\/])[^\s;|&"']+)      # bare path token
""", re.X)

# regex fragments that are NOT file paths
_JUNK_CHARS = set("[]()^$+*?{}|")

# well-known environment reads (the machine's codex home: skills, memories,
# instruction files), not task assets; filtered and counted instead of listed
_ENV_MARKS = ("\\.codex\\", "/.codex/")


def _norm(path_text: str) -> str:
    """Deterministic normalization for dedup: lowercase drive, backslashes kept."""
    return os.path.normcase(path_text.strip())


def extract_file_changes(events: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    produced: dict[str, dict[str, Any]] = {}
    for ev in events:
        if ev["kind"] != "file_change":
            continue
        changes = ev.get("changes")
        if not isinstance(changes, dict):
            continue
        for raw_path in changes:
            path = _norm(raw_path)
            if not path:
                continue
            produced[path] = {
                "path": path,
                "category": "produced",
                "role": _role_for(raw_path),
                "role_basis": "file_change",
                "source": "file_change",
                "source_ref": ev["seq"],
            }
    return produced


def _role_for(path_text: str) -> str:
    name = PurePath(path_text).name.lower()
    if any(m in name for m in EVIDENCE_MARKERS):
        return "evidence"
    if PurePath(path_text).suffix.lower() in DELIVERABLE_EXT:
        return "deliverable"
    return "generator"


def _importance(entry: dict[str, Any], path_text: str) -> tuple[str, str]:
    """White-box importance: required / optional / historical / ephemeral.

    required : must exist before continuing (deliverables, proof, source inputs)
    optional : rebuildable with the session's own scripts
    historical : was read/created mid-run only; missing is harmless
    ephemeral : staging directories / no-extension paths
    """
    low = path_text.lower()
    ext = PurePath(path_text).suffix.lower()
    in_staging = any(s in low for s in _STAGING)
    if entry["role"] in ("evidence", "deliverable"):
        return "required", "deliverable/evidence"
    if entry["category"] == "input":
        if not in_staging and ext in SOURCE_INPUT_EXT:
            return "required", "source input outside staging"
        return "historical", "staging or derived input read"
    if not ext:
        return "ephemeral", "no extension (likely directory)"
    if entry["source"] == "file_change":
        return "optional", "file_change-produced (rebuildable)"
    return "optional", "tool-command derived"


def extract_command_paths(events: list[dict[str, Any]],
                          produced: dict[str, dict[str, Any]],
                          stats: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Conservative write/read path extraction from parsed exec commands."""
    out: dict[str, dict[str, Any]] = {}
    tool_derived = 0
    for ev in events:
        if ev["kind"] != "tool_call" or not isinstance(ev.get("command"), str):
            continue
        cmd = ev["command"]
        for verb in WRITE_VERBS:
            m = re.search(rf"\b{re.escape(verb)}\b", cmd)
            if not m:
                continue
            for pm in _PATH_TOKEN.finditer(cmd[m.end():]):
                p = _norm(pm.group("q") or pm.group("b"))
                if not p or any(c in p for c in _JUNK_CHARS):
                    continue
                if any(env in p for env in _ENV_MARKS):
                    stats["env_reads_filtered"] += 1
                    continue
                if p in produced or p in out:
                    continue
                if tool_derived >= MAX_TOOL_ARTIFACTS:
                    return out
                out[p] = {
                    "path": p,
                    "category": "produced",
                    "role": _role_for(p),
                    "role_basis": f"tool_command:{verb}",
                    "source": "tool_command",
                    "source_ref": ev["seq"],
                }
                tool_derived += 1
            break  # one verb per command line is enough for a candidate
    inputs: dict[str, dict[str, Any]] = {}
    input_cap = 0
    for ev in events:
        if ev["kind"] != "tool_call" or not isinstance(ev.get("command"), str):
            continue
        cmd = ev["command"]
        for verb in READ_VERBS:
            m = re.search(rf"\b{re.escape(verb)}\b", cmd)
            if not m:
                continue
            for pm in _PATH_TOKEN.finditer(cmd[m.end():]):
                p = _norm(pm.group("q") or pm.group("b"))
                if not p or any(c in p for c in _JUNK_CHARS):
                    continue
                if any(env in p for env in _ENV_MARKS):
                    stats["env_reads_filtered"] += 1
                    continue
                if p in produced or p in out or p in inputs:
                    continue
                if input_cap >= MAX_TOOL_ARTIFACTS:
                    return out | inputs
                inputs[p] = {
                    "path": p,
                    "category": "input",
                    "role": "input",
                    "role_basis": f"tool_command:{verb}",
                    "source": "tool_command",
                    "source_ref": ev["seq"],
                }
                input_cap += 1
            break
    return out | inputs


def verify(path_text: str, max_hash_bytes: int) -> dict[str, Any]:
    path = Path(path_text)
    try:
        st = path.stat()
    except OSError:
        return {"exists": False, "size": None, "mtime": None, "sha256": None}
    size = st.st_size
    mtime = datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat()
    entry: dict[str, Any] = {"exists": True, "size": size, "mtime": mtime, "sha256": None}
    if size > max_hash_bytes:
        entry["sha256"] = None
        entry["hash_skipped"] = "large_file"
        return entry
    try:
        h = hashlib.sha256()
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(1024 * 256), b""):
                h.update(chunk)
        entry["sha256"] = h.hexdigest()
    except OSError:
        entry["sha256"] = None
    return entry


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Extract artifact_manifest.json from a preprocessed session.")
    ap.add_argument("--clean", required=True, help="clean.jsonl from preprocess_session.py")
    ap.add_argument("--out", required=True, help="output artifact_manifest.json")
    ap.add_argument("--max-hash-bytes", type=int, default=1_000_000,
                    help="files larger than this get size+mtime instead of sha256")
    args = ap.parse_args(argv)

    clean = Path(args.clean)
    try:
        with clean.open("r", encoding="utf-8") as f:
            events = [json.loads(l) for l in f if l.strip()]
    except FileNotFoundError:
        raise SystemExit(f"error: clean file not found: {clean}")
    except json.JSONDecodeError as e:
        raise SystemExit(f"error: {clean} is not a valid normalized stream: {e}")

    produced = extract_file_changes(events)
    stats = {"file_change_artifacts": len(produced),
             "tool_command_artifacts": 0, "env_reads_filtered": 0}
    with_tool = extract_command_paths(events, produced, stats)
    stats["tool_command_artifacts"] = len(with_tool) - len(produced)
    stats.update({"total": 0, "produced": 0, "input": 0, "evidence": 0,
                  "exists_on_disk": 0, "missing_on_disk": 0, "hashed": 0})
    artifacts: list[dict[str, Any]] = []
    merged = {**produced, **with_tool}  # tool-derived entries never override file_change truth
    stats.update({"importance": {"required": 0, "optional": 0, "historical": 0, "ephemeral": 0},
                  "required_exists": 0, "required_missing": 0})
    for path in sorted(merged):
        entry = dict(merged[path])
        importance, basis = _importance(entry, entry["path"])
        entry["importance"] = importance
        entry["importance_basis"] = basis
        entry["verification"] = verify(entry["path"], args.max_hash_bytes)
        artifacts.append(entry)
        stats["total"] += 1
        stats[entry["category"]] = stats.get(entry["category"], 0) + 1
        if entry["role"] == "evidence":
            stats["evidence"] += 1
        stats["importance"][importance] += 1
        if entry["verification"]["exists"]:
            stats["exists_on_disk"] += 1
            if importance == "required":
                stats["required_exists"] += 1
        else:
            stats["missing_on_disk"] += 1
            if importance == "required":
                stats["required_missing"] += 1
        if entry["verification"].get("sha256"):
            stats["hashed"] += 1

    manifest = {
        "schema_version": 1,
        "generated_by": "extract_artifacts.py",
        "max_hash_bytes": args.max_hash_bytes,
        "stats": stats,
        "artifacts": artifacts,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")

    print(f"artifacts={stats['total']} produced={stats['produced']} "
          f"input={stats['input']} evidence={stats['evidence']} "
          f"exists={stats['exists_on_disk']} missing={stats['missing_on_disk']} "
          f"required_missing={stats['required_missing']} hashed={stats['hashed']}")
    print(f"importance={stats['importance']}")
    print(f"wrote: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())