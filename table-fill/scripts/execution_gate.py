#!/usr/bin/env python3
"""
scripts/execution_gate.py — Execution Gate marker (V2, positive confirmation).

The V2 workflow has exactly ONE Human Gate on the normal path: the Execution
Gate before promotion. The gate is FAIL-CLOSED: promotion requires a positive
`.gate3_confirmed` record, and that record is bound to the spec/plan/draft
hashes that were presented — a draft rebuilt after confirmation can no longer
be promoted without a fresh gate.

  python scripts/execution_gate.py --set --workdir <dir>
      → writes .gate3_pending recording the PRESENTED hashes
        (fill_spec.yaml / execution_plan.json / validated_draft).
        The agent stops responding until the user confirms.

  python scripts/execution_gate.py --confirm --workdir <dir>
      → REQUIRES a pending marker; recomputes the hashes; if they drifted
        since presentation the confirmation is REFUSED (re-run the gate).
        On match: removes the pending marker and writes .gate3_confirmed
        with the hash trio + timestamp. Run only after a real user
        confirmation.

Exit codes: 0=ok, 1=usage/path error, 3=retryable (hash drift).
"""

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from _officecli import (  # noqa: E402
    ensure_utf8_stdio as _utf8_stdio, fail, record_timing as _record_timing,
    sha256_file,
)

GATE_MARKER = ".gate3_pending"
CONFIRMED_MARKER = ".gate3_confirmed"


def gate_hashes(workdir: Path) -> dict:
    """The presented-state hashes: spec + plan + validated draft.

    The draft path comes from draft_receipt.json when present, else the
    validated_draft.* glob in the workdir. Missing inputs are recorded as
    None so a later change is still detectable."""
    spec = workdir / "fill_spec.yaml"
    plan = workdir / "execution_plan.json"
    draft = None
    receipt = workdir / "draft_receipt.json"
    if receipt.is_file():
        try:
            draft = Path(json.loads(receipt.read_text(encoding="utf-8"))
                         .get("draft_path", ""))
        except (ValueError, OSError):
            draft = None
    if draft is None or not draft.is_file():
        drafts = sorted(workdir.glob("validated_draft.*"))
        draft = drafts[0] if drafts else None
    return {
        "fill_spec_sha256": sha256_file(spec) if spec.is_file() else None,
        "execution_plan_sha256": sha256_file(plan) if plan.is_file() else None,
        "draft_sha256": sha256_file(draft) if draft else None,
    }


def main() -> None:
    _utf8_stdio()
    parser = argparse.ArgumentParser(description="Execution Gate marker (V2)")
    parser.add_argument("--workdir", type=Path, required=True)
    parser.add_argument("--set", action="store_true", help="mark the gate pending")
    parser.add_argument("--confirm", action="store_true",
                        help="positively confirm the gate (binds presented hashes)")
    args = parser.parse_args()

    if args.set == args.confirm:
        print("[GATE_ERROR] choose exactly one of --set or --confirm", file=sys.stderr)
        sys.exit(1)

    workdir = args.workdir
    pending = workdir / GATE_MARKER
    confirmed = workdir / CONFIRMED_MARKER

    if args.set:
        workdir.mkdir(parents=True, exist_ok=True)
        record = {
            "presented_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "hashes": gate_hashes(workdir),
        }
        pending.write_text(json.dumps(record, ensure_ascii=False, indent=2),
                           encoding="utf-8")
        print("[GATE_SET] Execution Gate pending. Present the gate to the user "
              "and STOP — do not promote until confirmed.")
        print(f"[GATE_SET] presented hashes: {json.dumps(record['hashes'])}")
        sys.exit(0)

    # --confirm
    if not pending.is_file():
        print(json.dumps({
            "status": "ERROR", "code": "GATE_NOT_PENDING",
            "message": "no pending gate marker — there is nothing to confirm. "
                       "A gate must be SET (presented to the user) before it "
                       "can be confirmed.",
            "corrective_action": "Run execution_gate.py --set after presenting "
                                 "the Execution Gate content",
        }, ensure_ascii=False), file=sys.stderr)
        sys.exit(3)

    try:
        record = json.loads(pending.read_text(encoding="utf-8"))
        presented = record.get("hashes", {})
    except (ValueError, OSError):
        print(json.dumps({
            "status": "ERROR", "code": "GATE_MARKER_CORRUPT",
            "message": "the pending gate marker is unreadable",
            "corrective_action": "Re-run --set to re-present the gate",
        }, ensure_ascii=False), file=sys.stderr)
        sys.exit(3)

    current = gate_hashes(workdir)
    drift = [k for k in presented if presented.get(k) != current.get(k)]
    if drift:
        print(json.dumps({
            "status": "ERROR", "code": "GATE_HASH_DRIFT",
            "message": "artifacts changed since the gate was presented: "
                       + ", ".join(drift),
            "corrective_action": "Re-generate the draft, re-present the "
                                 "Execution Gate (--set), then confirm again",
        }, ensure_ascii=False), file=sys.stderr)
        sys.exit(3)

    confirmed_record = {
        "confirmed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "presented_at": record.get("presented_at"),
        "hashes": current,
    }
    confirmed.write_text(json.dumps(confirmed_record, ensure_ascii=False, indent=2),
                         encoding="utf-8")
    pending.unlink(missing_ok=True)
    print("[GATE_CONFIRMED] Execution Gate positively confirmed. Promotion is "
          "now allowed for exactly these hashes:")
    print(f"[GATE_CONFIRMED] {json.dumps(current)}")
    sys.exit(0)


if __name__ == "__main__":
    main()
