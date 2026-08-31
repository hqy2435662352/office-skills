#!/usr/bin/env python3
"""
scripts/index_sessions.py — lightweight Codex session index for natural-language discovery.

Bridges the gap between "I do not know the rollout path or session id" and
"pick the session that matches this command". Scans Codex session roots
recursively for rollout-*.jsonl and writes a compact, deterministic fingerprint
per session. The index is for SESSION SELECTION ONLY — no semantic judgment,
no summarization, no LLM.

  python scripts/index_sessions.py --root <sessions dir> --out <session-index.json>
  python scripts/index_sessions.py --root <dir1> --root <dir2> --out <idx.json>

Search roots (repeatable --root) cover the real Codex layouts encountered so far:

  %USERPROFILE%\\.codex\\sessions                (Windows; nested YYYY/MM/DD/)
  ~/.codex/sessions                             (POSIX)
  ~/.codex/archived_sessions                    (archived threads, if present)

Output: one JSON object:

  {
    "schema_version": 1,
    "search_roots": ["..."],
    "sessions": [
      {
        "session_id": "...",
        "rollout_path": "...",
        "started_at": "...",
        "last_timestamp": "...",
        "cwd": "...",
        "model_provider": "...",
        "originator": "...",
        "history_mode": "...",
        "user_messages": ["...", "..."]     // ALL real user messages, in order
      }
    ]
  }

Design rules:
  * user_messages keep EVERY real user message in full (no recent-N window):
    they are the primary semantic basis for choosing the right session.
  * client-injected boilerplate-only messages are excluded (shared stripping
    rules from _rollout_common); real text glued to injected blocks survives.
  * output is deterministic (no clock/random; sessions sorted by started_at
    desc, ties by path). No writes outside --out.
  * files that cannot be read are counted and listed, never fatal.

Exit codes: 0 = ok; 1 = usage / no readable roots.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from _rollout_common import extract_session_meta, extract_user_message_text, parse_timestamp

CONFIG = {
    "schema_version": 1,
    "unknown_cap": 8000,
}


def index_file(path: Path, caps: dict) -> dict[str, Any] | None:
    """Index a single rollout file; returns its entry or None when unusable."""
    entry: dict[str, Any] = {
        "session_id": None,
        "rollout_path": str(path),
        "started_at": None,
        "last_timestamp": None,
        "cwd": None,
        "model_provider": None,
        "originator": None,
        "history_mode": None,
        "user_messages": [],
    }
    malformed = 0
    last_user_text: str | None = None
    try:
        with path.open("r", encoding="utf-8-sig", errors="strict", newline="") as f:
            for line in f:
                line = line.rstrip("\r\n")
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    malformed += 1
                    continue
                if not isinstance(rec, dict):
                    malformed += 1
                    continue
                ts = rec.get("timestamp")
                if isinstance(ts, str):
                    entry["last_timestamp"] = ts
                top = rec.get("type")
                payload = rec.get("payload")
                if top == "session_meta":
                    entry.update(extract_session_meta(payload))
                    if entry["started_at"] is None:
                        entry["started_at"] = ts if isinstance(ts, str) else None
                elif top == "event_msg" and isinstance(payload, dict) \
                        and payload.get("type") == "user_message":
                    text = extract_user_message_text(payload, "event_msg", 0, caps)
                    if text and text != last_user_text:
                        entry["user_messages"].append(text)
                        last_user_text = text
                elif top == "response_item" and isinstance(payload, dict) \
                        and payload.get("type") == "message" and payload.get("role") == "user":
                    text = extract_user_message_text(payload, "response_item", 0, caps)
                    if text and text != last_user_text:
                        entry["user_messages"].append(text)
                        last_user_text = text
    except (OSError, UnicodeDecodeError, ValueError):
        return None
    if malformed:
        entry["malformed_lines"] = malformed
    return entry


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Build a Codex session index for natural-language discovery.")
    ap.add_argument("--root", action="append", required=True,
                    help="session root to scan (repeatable: sessions + archived_sessions)")
    ap.add_argument("--out", required=True, help="output JSON path (only file written)")
    args = ap.parse_args(argv)

    roots = [Path(r) for r in args.root]
    rollout_files: list[Path] = []
    for root in roots:
        if not root.is_dir():
            print(f"warning: root not found, skipped: {root}", file=sys.stderr)
            continue
        rollout_files.extend(sorted(root.rglob("rollout-*.jsonl")))
    if not rollout_files:
        print("error: no rollout-*.jsonl found under the given roots", file=sys.stderr)
        return 1

    sessions: list[dict[str, Any]] = []
    skipped: list[str] = []
    for path in rollout_files:
        entry = index_file(path, {"unknown": CONFIG["unknown_cap"]})
        if entry is None:
            skipped.append(str(path))
            continue
        sessions.append(entry)

    def sort_key(entry: dict[str, Any]) -> tuple:
        dt = parse_timestamp(entry.get("started_at"))
        if dt is None:
            return (1, 0, entry["rollout_path"])  # unparseable timestamps last
        return (0, -dt.timestamp(), entry["rollout_path"])  # newest first

    sessions.sort(key=sort_key)

    index = {
        "schema_version": CONFIG["schema_version"],
        "search_roots": [str(r) for r in roots],
        "sessions": sessions,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(index, ensure_ascii=False, indent=2) + "\n")

    total_malformed = sum(e.get("malformed_lines", 0) for e in sessions)
    total_users = sum(len(e["user_messages"]) for e in sessions)
    print(f"indexed sessions: {len(sessions)}  (user messages: {total_users}, "
          f"malformed lines: {total_malformed})")
    if skipped:
        print(f"skipped unreadable files: {len(skipped)}")
        for s in skipped:
            print(f"  {s}", file=sys.stderr)
    print(f"wrote: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())