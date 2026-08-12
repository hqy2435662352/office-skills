#!/usr/bin/env python3
"""
scripts/promote_output.py — post-Gate promotion (V2): validated draft → final output.

The ONLY post-Gate write entry. It does not re-execute anything — the
user-approved Validated Draft IS the final file. Promotion:

  1. Reads draft_receipt.json (evidence written by execute_batch.py).
  2. Recomputes fill_spec / execution_plan / validated_draft hashes and
     compares them with the receipt. Any drift → reject (exit 3): the draft
     or plan changed after approval, so re-generate and re-gate.
  3. Atomically copies the draft to the requested final path.
  4. Verifies the final file hash equals the approved draft hash.
  5. Runs the minimal ZIP/structure confirmation (presentation.xml for pptx,
     non-corrupt zip for xlsx).
  6. Writes final_receipt.json.

This is a deterministic Skill-only write entry — not a Runtime Guard. The
Execution Gate confirmation (execution_gate.py --confirm) is expected to have
run before this script: promotion requires the positive `.gate3_confirmed`
record, and its recorded hashes must match the current spec/plan/draft AND
the receipt (fail-closed: an absent pending marker is NOT a confirmation).

Exit codes: 0=promoted, 3=retryable (hash drift / gate pending), 1=fatal.

Usage:
  python scripts/promote_output.py --workdir <dir> --final <final_path>
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _officecli import (  # noqa: E402
    clean_residents, ensure_utf8_stdio, fail, force_writable,
    record_timing as _record_timing, sha256_file, unlink_retry,
)

RECEIPT_NAME = "draft_receipt.json"
FINAL_RECEIPT_NAME = "final_receipt.json"
GATE_MARKER = ".gate3_pending"
CONFIRMED_MARKER = ".gate3_confirmed"




def fail(code: str, message: str, corrective_action: str, exit_code: int) -> None:
    sys.stderr.write(json.dumps({
        "status": "ERROR", "code": code,
        "message": message, "corrective_action": corrective_action,
    }, ensure_ascii=False, indent=2))
    sys.exit(exit_code)


def check_zip(book: Path) -> list[str]:
    errors = []
    try:
        with zipfile.ZipFile(book, "r") as z:
            names = z.namelist()
            if book.suffix == ".pptx" and "ppt/presentation.xml" not in names:
                errors.append("invalid PPTX: missing ppt/presentation.xml")
    except zipfile.BadZipFile:
        errors.append("not a valid ZIP/Office document")
    return errors


def main() -> None:
    ensure_utf8_stdio()
    parser = argparse.ArgumentParser(description="Post-Gate promotion: draft → final")
    parser.add_argument("--workdir", type=Path, required=True)
    parser.add_argument("--final", type=Path, required=True,
                        help="final output path requested by the user")
    args = parser.parse_args()

    workdir = args.workdir
    receipt_path = workdir / RECEIPT_NAME
    if not receipt_path.is_file():
        fail("RECEIPT_NOT_FOUND",
             f"draft_receipt.json missing in {workdir} — no validated draft exists",
             "Run execute_batch.py until the draft validates", 1)

    # Fail-closed: promotion requires a POSITIVE confirmation record. An absent
    # pending marker is NOT a confirmation — it means no gate ever completed.
    confirmed = workdir / CONFIRMED_MARKER
    if not confirmed.is_file():
        fail("GATE_NOT_CONFIRMED",
             "no positive Execution Gate confirmation found — an absent pending "
             "marker is not a confirmation",
             "Present the Execution Gate to the user, get an explicit reply, "
             "then run execution_gate.py --confirm --workdir <dir>", 3)
    try:
        confirmed_record = json.loads(confirmed.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        fail("GATE_CONFIRM_CORRUPT",
             "the confirmation record is unreadable",
             "Re-run the Execution Gate (--set then --confirm)", 3)
    gate_marker = workdir / GATE_MARKER
    if gate_marker.exists():
        fail("GATE_PENDING",
             "Execution Gate is still pending — the draft has not been approved",
             "Present the Execution Gate to the user and run "
             "execution_gate.py --confirm --workdir <dir> after confirmation", 3)

    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except ValueError as e:
        fail("RECEIPT_INVALID", f"corrupt receipt: {e}",
             "Re-run execute_batch.py", 3)

    draft = Path(receipt.get("draft_path", ""))
    plan_path = workdir / "execution_plan.json"
    spec_path = workdir / "fill_spec.yaml"
    if not draft.is_file():
        fail("DRAFT_MISSING", f"draft not found: {draft}",
             "Re-run execute_batch.py", 1)

    # Hash drift check — three-way: the CONFIRMED record (what the user saw),
    # the receipt (what was validated), and the current files. Any change
    # after approval → reject, re-gate.
    current = {
        "fill_spec_sha256": sha256_file(spec_path) if spec_path.is_file() else None,
        "execution_plan_sha256": sha256_file(plan_path) if plan_path.is_file() else None,
        "draft_sha256": sha256_file(draft),
    }
    confirmed_hashes = confirmed_record.get("hashes", {})
    drift = []
    for key, value in current.items():
        if confirmed_hashes.get(key) != value:
            drift.append(f"{key}: confirmed {confirmed_hashes.get(key)} != current {value}")
        if receipt.get(key) != value:
            drift.append(f"{key}: receipt {receipt.get(key)} != current {value}")
    if drift:
        fail("HASH_DRIFT", "approved artifacts changed after the Execution Gate: "
             + "; ".join(drift),
             "Regenerate the draft and re-run the Execution Gate", 3)

    # Promote — atomic replace: stage a same-directory temp copy, VERIFY its
    # hash, then os.replace over the final path. The existing final is never
    # deleted beforehand: if staging or the replace fails, the previous
    # delivered file is left intact.
    clean_residents()
    args.final.parent.mkdir(parents=True, exist_ok=True)
    tmp = args.final.with_suffix(args.final.suffix + ".promoting")
    unlink_retry(tmp)  # stale temp from a crashed previous run is safe to clear
    try:
        shutil.copy2(draft, tmp)
        force_writable(tmp)
    except OSError as e:
        fail("PROMOTE_COPY_FAILED", f"cannot stage promotion copy: {e}",
             "Check the final path permissions", 1)
    if sha256_file(tmp) != receipt.get("draft_sha256"):
        unlink_retry(tmp)
        fail("PROMOTE_STAGED_HASH_MISMATCH",
             "staged copy hash differs from the approved draft hash",
             "Re-run promotion", 3)
    try:
        tmp.replace(args.final)
    except OSError as e:
        unlink_retry(tmp)
        fail("PROMOTE_REPLACE_FAILED",
             f"cannot atomically replace the final file: {e}",
             "Close any program holding the final file (e.g. Excel), then "
             "re-run promotion — the previous final file was preserved", 3)

    if sha256_file(args.final) != receipt.get("draft_sha256"):
        fail("FINAL_HASH_MISMATCH", "final file hash differs from the approved draft hash",
             "Delete the final file and re-run promotion", 3)

    zip_errors = check_zip(args.final)
    if zip_errors:
        fail("ZIP_STRUCTURE_INVALID", "; ".join(zip_errors),
             "The promoted file is corrupt — re-run execute_batch.py", 3)

    final_receipt = {
        "schema_version": 2,
        "draft_sha256": receipt["draft_sha256"],
        "final_path": str(args.final),
        "final_sha256": sha256_file(args.final),
        "zip_check": "pass" if not zip_errors else "fail",
        "promoted_at": None,
    }
    _record_timing(workdir, "promote")
    (workdir / FINAL_RECEIPT_NAME).write_text(
        json.dumps(final_receipt, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps({
        "status": "PASS", "code": "PROMOTED",
        "final": str(args.final),
        "final_sha256": final_receipt["final_sha256"],
        "draft_sha256": receipt["draft_sha256"],
        "receipt": str(workdir / FINAL_RECEIPT_NAME),
    }, ensure_ascii=False, indent=2))
    sys.exit(0)


if __name__ == "__main__":
    main()
