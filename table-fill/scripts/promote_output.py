#!/usr/bin/env python3
"""
scripts/promote_output.py — delivery (v3): validated draft → final output.

Deliver is a hash-verified copy — the ONLY post-verify write entry. It does
not re-execute anything and holds no gate, no marker, no confirmation step:
the Validated Draft (already machine-verified by execute_batch.py) IS the
final file, and Deliver follows directly after Verify (ADR 0017 / spec:
"确认映射后就不用再管交付——验证全绿自动把成品放到指定位置").

Deliver:

  1. Reads draft_receipt.json (evidence written by execute_batch.py).
  2. Recomputes fill_spec / execution_plan / validated_draft hashes and
     compares them with the receipt. Any drift → reject (exit 3): the draft,
     plan or spec changed after verification, so re-generate and re-verify.
  3. Re-verifies the staged inputs still match the plan's compile-time
     binding (three-way: plan.input_hashes / receipt execution-time recompute
     / current staged files). Any drift → HASH_DRIFT (exit 3).
  4. Atomically copies the draft to the requested final path.
  5. Verifies the final file hash equals the verified draft hash.
  6. Runs the minimal ZIP/structure confirmation (presentation.xml for pptx,
     non-corrupt zip for xlsx).
  7. Writes final_receipt.json (a fact record: hashes + zip check — no
     PASS/WAITING/APPROVED/REJECTED state word; Verify is a machine evidence
     producer, never a decision maker).

Exit codes: 0=delivered, 3=retryable (hash drift), 1=fatal.

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
    sha256_file, unlink_retry,
)

RECEIPT_NAME = "draft_receipt.json"
FINAL_RECEIPT_NAME = "final_receipt.json"


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
    parser = argparse.ArgumentParser(description="Delivery: validated draft → final")
    parser.add_argument("--workdir", type=Path, required=True)
    parser.add_argument("--final", type=Path, required=True,
                        help="final output path requested by the user")
    parser.add_argument("--staged-root", type=Path, default=None, metavar="DIR",
                        help="optional task root providing shared staged inputs "
                             "(staged/); when set, input hash re-verification reads "
                             "raw inputs from staged-root/staged/<name> (ticket 08: "
                             "shared inputs referenced by name, not byte-copied)")
    args = parser.parse_args()

    workdir = args.workdir
    receipt_path = workdir / RECEIPT_NAME
    if not receipt_path.is_file():
        fail("RECEIPT_NOT_FOUND",
             f"draft_receipt.json missing in {workdir} — no validated draft exists",
             "Run execute_batch.py until the draft validates", 1)

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

    # Hash check (three-way): the receipt (what was validated) vs the current
    # spec/plan/draft files. Any change after verification → reject, re-verify.
    current = {
        "fill_spec_sha256": sha256_file(spec_path) if spec_path.is_file() else None,
        "execution_plan_sha256": sha256_file(plan_path) if plan_path.is_file() else None,
        "draft_sha256": sha256_file(draft),
    }
    drift = []
    for key, value in current.items():
        if receipt.get(key) != value:
            drift.append(f"{key}: receipt {receipt.get(key)} != current {value}")

    # Input hash check — the plan's compile-time binding vs the receipt's
    # execution-time recompute vs the CURRENT staged files. Any drift →
    # HASH_DRIFT. Fail-closed: missing evidence → reject.
    plan_data = {}
    try:
        plan_data = json.loads(plan_path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        plan_data = {}
    plan_inputs = plan_data.get("input_hashes") if isinstance(plan_data, dict) else None
    target = plan_data.get("target") if isinstance(plan_data, dict) else None
    receipt_inputs = receipt.get("source_hashes")
    receipt_tpl = receipt.get("template_sha256")
    if not isinstance(plan_inputs, dict) or not plan_inputs:
        drift.append("input hashes: plan carries no input_hashes binding "
                     "(compile with compile_fill.py)")
    elif not isinstance(receipt_inputs, dict) or not receipt_tpl:
        drift.append("input hashes: receipt carries no execution-time input "
                     "evidence (re-run execute_batch.py)")
    else:
        for name, bound in plan_inputs.items():
            staged = workdir / name
            if not staged.is_file() and args.staged_root is not None:
                # legacy: staged_root/staged/<name>; workspace 平铺 (ADR 0018):
                # staged_root/<name>
                if (args.staged_root / "staged" / name).is_file():
                    staged = args.staged_root / "staged" / name
                elif (args.staged_root / name).is_file():
                    staged = args.staged_root / name
            current_h = sha256_file(staged) if staged.is_file() else None
            rec = receipt_tpl if name == target else receipt_inputs.get(name)
            if bound != rec or rec != current_h:
                drift.append(f"input {name}: plan {bound} / receipt {rec} "
                             f"/ current {current_h}")
    if drift:
        fail("HASH_DRIFT", "verified artifacts changed after execution: "
             + "; ".join(drift),
             "Regenerate the draft and re-run verification", 3)

    # Deliver — atomic replace: stage a same-directory temp copy, VERIFY its
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
        fail("DELIVER_COPY_FAILED", f"cannot stage delivery copy: {e}",
             "Check the final path permissions", 1)
    if sha256_file(tmp) != receipt.get("draft_sha256"):
        unlink_retry(tmp)
        fail("DELIVER_STAGED_HASH_MISMATCH",
             "staged copy hash differs from the verified draft hash",
             "Re-run delivery", 3)
    try:
        tmp.replace(args.final)
    except OSError as e:
        unlink_retry(tmp)
        fail("DELIVER_REPLACE_FAILED",
             f"cannot atomically replace the final file: {e}",
             "Close any program holding the final file (e.g. Excel), then "
             "re-run delivery — the previous final file was preserved", 3)

    if sha256_file(args.final) != receipt.get("draft_sha256"):
        fail("FINAL_HASH_MISMATCH", "final file hash differs from the verified draft hash",
             "Delete the final file and re-run delivery", 3)

    zip_errors = check_zip(args.final)
    if zip_errors:
        fail("ZIP_STRUCTURE_INVALID", "; ".join(zip_errors),
             "The delivered file is corrupt — re-run execute_batch.py", 3)

    final_receipt = {
        "schema_version": 3,
        "draft_sha256": receipt["draft_sha256"],
        "final_path": str(args.final),
        "final_sha256": sha256_file(args.final),
        "zip_check": "pass" if not zip_errors else "fail",
        "delivered_at": None,
    }
    (workdir / FINAL_RECEIPT_NAME).write_text(
        json.dumps(final_receipt, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps({
        "status": "PASS", "code": "DELIVERED",
        "final": str(args.final),
        "final_sha256": final_receipt["final_sha256"],
        "draft_sha256": receipt["draft_sha256"],
        "receipt": str(workdir / FINAL_RECEIPT_NAME),
    }, ensure_ascii=False, indent=2))
    sys.exit(0)


if __name__ == "__main__":
    main()
