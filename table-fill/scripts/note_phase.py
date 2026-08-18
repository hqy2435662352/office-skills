#!/usr/bin/env python3
"""
scripts/note_phase.py — record agent-side (LLM reasoning/wait) time.

Machine phases are auto-recorded by the pipeline scripts (prepare/compile/
draft_execute/promote). The dominant cost of a run is usually the LLM
reasoning between those calls — which only the agent itself can timestamp.

Usage: call this immediately BEFORE the next script invocation, naming the
thinking block that just ended:

    python scripts/note_phase.py --workdir <dir> --phase spec_authoring
    python scripts/note_phase.py --workdir <dir> --phase mod_resolution
    python scripts/note_phase.py --workdir <dir> --phase gate_wait
    python scripts/note_phase.py --workdir <dir> --phase execute_review

It appends {"kind": "agent", "phase", "started_at", "finished_at",
"duration_ms"} to run_timing.json, where started_at is the finish time of
the previous entry (machine or agent) — so the duration is the wall time
spent thinking/waiting since the last checkpoint. Self-reported and
approximate, but it turns 'wall clock' into a measurable decomposition:
machine Xs + agent Ys.

Exit codes: 0=ok, 1=no prior entry (recorded with duration 0).
"""

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

from _officecli import (  # noqa: E402
    ensure_utf8_stdio as _utf8_stdio, fail, record_timing as _record_timing,
    sha256_file,
)

TIMING_NAME = "run_timing.json"


def parse_ts(text: str) -> datetime:
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%S")


def last_finish(entries: list[dict]) -> datetime:
    """Finish time of the last entry (started_at + duration_ms)."""
    last = entries[-1]
    started = parse_ts(last["started_at"])
    duration_ms = last.get("duration_ms") or 0
    return datetime.fromtimestamp(started.timestamp() + duration_ms / 1000.0)


def main() -> None:
    _utf8_stdio()
    parser = argparse.ArgumentParser(description="Record agent-side reasoning time")
    parser.add_argument("--workdir", type=Path, required=True)
    parser.add_argument("--phase", type=str, required=True,
                        help="thinking block name (e.g. spec_authoring, gate_wait)")
    args = parser.parse_args()

    path = args.workdir / TIMING_NAME
    entries = []
    if path.is_file():
        try:
            entries = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            entries = []
    if not isinstance(entries, list):
        entries = []

    now = datetime.now()
    if entries:
        try:
            start = last_finish(entries)
        except (ValueError, KeyError):
            start = now
    else:
        start = now

    duration_ms = max(0, int((now - start).total_seconds() * 1000))
    entries.append({
        "kind": "agent",
        "phase": args.phase,
        "started_at": start.strftime("%Y-%m-%dT%H:%M:%S"),
        "finished_at": now.strftime("%Y-%m-%dT%H:%M:%S"),
        "duration_ms": duration_ms,
    })
    args.workdir.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "status": "PASS", "kind": "agent", "phase": args.phase,
        "duration_ms": duration_ms,
        "started_at": entries[-1]["started_at"],
        "finished_at": entries[-1]["finished_at"],
    }, ensure_ascii=False))
    sys.exit(0)


if __name__ == "__main__":
    main()
