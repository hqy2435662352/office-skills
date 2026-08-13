#!/usr/bin/env python3
"""
scripts/layer_gate.py - chart-gen step gate

Checks prerequisites for each step and manages human gate state files.
Chart-gen has 3 steps (vs table-fill's 4 layers), with one human gate
between Step 1 (analysis) and Step 3 (generation).

Usage:
  python scripts/layer_gate.py --target 1 --input <file.xlsx>
  python scripts/layer_gate.py --target 2 --workdir <flat_output/>
  python scripts/layer_gate.py --target 3 --workdir <flat_output/>
  python scripts/layer_gate.py --set-gate 1 --workdir <flat_output/>
  python scripts/layer_gate.py --confirm-gate 1 --workdir <flat_output/>

Exit codes:
  0 = pass (prerequisites satisfied)
  1 = fatal (file missing, locked, invalid YAML, illegal step jump)
  3 = retryable (proposal not yet confirmed; run --confirm-gate first)
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path

STEP_NAMES = {
    1: "Step 1 - Analysis (Pre-Flight)",
    2: "Step 2 - Human Confirmation",
    3: "Step 3 - Chart Generation",
}


def _find_proposal(workdir: Path) -> Path | None:
    """Find the first *_chart_proposal.yaml in workdir."""
    candidates = list(workdir.glob("*_chart_proposal.yaml"))
    return candidates[0] if candidates else None


def _check_yaml_valid(filepath: Path) -> tuple[bool, str]:
    """Check file contains parsable YAML. Returns (ok, error_message)."""
    try:
        import yaml

        with open(filepath, "r", encoding="utf-8") as f:
            yaml.safe_load(f)
        return True, ""
    except ImportError:
        # PyYAML not available; fall back to structural check
        try:
            content = filepath.read_text(encoding="utf-8").strip()
            if not content:
                return False, "Proposal file is empty"
            if ":" not in content:
                return False, "Proposal file does not appear to be YAML (no ':' found)"
            return True, ""
        except Exception as e:
            return False, f"Failed to read proposal file: {e}"
    except Exception as e:
        return False, f"YAML parse error: {e}"


def _check_confirmed_flag(filepath: Path) -> tuple[bool, str]:
    """Check if proposal YAML has confirmed: true. Returns (confirmed, error)."""
    try:
        import yaml

        with open(filepath, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if not isinstance(data, dict):
            return False, "Proposal YAML root is not a mapping"
        confirmed = data.get("confirmed", False)
        if confirmed is True:
            return True, ""
        return False, f"Proposal 'confirmed' flag is {repr(confirmed)}, expected true"
    except ImportError:
        # PyYAML unavailable; do a text-based check
        try:
            content = filepath.read_text(encoding="utf-8").lower()
            if "confirmed: true" in content:
                return True, ""
            return False, "Proposal does not contain 'confirmed: true' (text check, PyYAML unavailable)"
        except Exception as e:
            return False, f"Failed to read proposal: {e}"
    except Exception as e:
        return False, f"Failed to parse proposal YAML: {e}"


def _format_error(tag: str, root_cause: str, corrective: str, context: str) -> str:
    """Four-segment defensive error output per design doc §3.5."""
    return (
        f"[{tag}] {root_cause}\n"
        f"[{tag}] CORRECTIVE ACTION: {corrective}\n"
        f"[{tag}] {context}"
    )


def _detect_file_lock(filepath: Path) -> bool:
    """Check if file is likely locked by WPS/Excel.

    Tries to open the file in append mode (non-destructive). If denied,
    the file is almost certainly open in another application.
    """
    try:
        with open(filepath, "a") as f:
            pass
        return False
    except (PermissionError, OSError):
        return True


_WPS_LOCK_MESSAGE = (
    "File appears to be locked by WPS/Excel (open-in-app lock detected). "
    "Please close the file in WPS/Excel before retrying. "
    "Do NOT attempt to kill the host process."
)


def _officecli_close(filepath: Path) -> tuple[bool, str]:
    """Run officecli close on file to release lingering locks."""
    try:
        result = subprocess.run(
            ["officecli", "close", str(filepath)],
            capture_output=True,
            timeout=15,
        )
        # officecli close may exit non-zero if no lock exists; treat as success
        return True, result.stdout.decode("utf-8", errors="replace").strip()
    except FileNotFoundError:
        return False, "officecli executable not found on PATH"
    except subprocess.TimeoutExpired:
        # Timeout may indicate file is held by another process (WPS/Excel)
        if _detect_file_lock(filepath):
            return False, _WPS_LOCK_MESSAGE
        return False, "officecli close timed out (file may be locked by another process)"
    except Exception as e:
        # On Windows, PermissionError often means WPS/Excel has the file open
        if "Permission" in str(e) or "denied" in str(e).lower():
            return False, _WPS_LOCK_MESSAGE
        return False, f"officecli close failed: {e}"


def check_pre_step_1(input_path: Path) -> int:
    """Pre-Step 1: verify input file exists, release locks, check readable."""
    if not input_path.exists():
        msg = _format_error(
            "LAYER_GATE_ERROR",
            f"Input file not found: {input_path}",
            "Verify the file path and ensure the file exists.",
            f"Current state: step=pre-init, check=file_existence",
        )
        print(msg, file=sys.stderr)
        return 1

    # Release lingering officecli locks (design §1.2 - toolbelt interop protocol)
    ok, detail = _officecli_close(input_path)
    if not ok:
        msg = _format_error(
            "LAYER_GATE_ERROR",
            f"officecli close failed for {input_path}: {detail}",
            "Ensure officecli is on PATH and the file is not held by another process.",
            f"Current state: step=pre-init, check=lock_release",
        )
        print(msg, file=sys.stderr)
        print(f"[LAYER_GATE_ERROR] detail: {detail}", file=sys.stderr)
        return 1
    if detail:
        print(f"[LAYER_GATE_INFO] officecli close: {detail}")

    # Verify file is readable
    if not os.access(input_path, os.R_OK):
        msg = _format_error(
            "LAYER_GATE_ERROR",
            f"Input file is not readable: {input_path}",
            "Check file permissions and ensure the file is not exclusively locked.",
            f"Current state: step=pre-init, check=readable",
        )
        print(msg, file=sys.stderr)
        return 1

    print(f"[LAYER_GATE_OK] Pre-Step 1 passed. File ready: {input_path}")
    return 0


def check_step_2(workdir: Path) -> int:
    """Step 2 prerequisite: proposal.yaml must exist and be valid YAML."""
    proposal = _find_proposal(workdir)
    if proposal is None:
        msg = _format_error(
            "LAYER_GATE_ERROR",
            f"Missing prerequisite: no *_chart_proposal.yaml found in {workdir}",
            "Complete Step 1 analysis to generate the proposal file.",
            f"Current state: step=pre-step-2, check=proposal_existence",
        )
        print(msg, file=sys.stderr)
        existing = list(workdir.glob("*")) if workdir.exists() else []
        print(f"[LAYER_GATE_ERROR] Files in workdir: {[p.name for p in existing]}", file=sys.stderr)
        return 1

    ok, err = _check_yaml_valid(proposal)
    if not ok:
        msg = _format_error(
            "LAYER_GATE_ERROR",
            f"Invalid proposal YAML: {proposal.name} — {err}",
            "Regenerate the proposal file. The file may be corrupted or empty.",
            f"Current state: step=pre-step-2, check=yaml_validity",
        )
        print(msg, file=sys.stderr)
        return 1

    print(f"[LAYER_GATE_OK] Step 2 prerequisites satisfied.")
    print(f"[LAYER_GATE_OK] Proposal found: {proposal.name}")
    return 0


def check_step_3(workdir: Path) -> int:
    """Step 3 prerequisite: proposal.yaml must exist AND have confirmed: true."""
    proposal = _find_proposal(workdir)
    if proposal is None:
        msg = _format_error(
            "LAYER_GATE_ERROR",
            f"Missing prerequisite: no *_chart_proposal.yaml found in {workdir}",
            "Complete Step 1 analysis to generate the proposal file.",
            f"Current state: step=pre-step-3, check=proposal_existence",
        )
        print(msg, file=sys.stderr)
        return 1

    # Check human gate 1 is not still pending (illegal jump)
    gate_file = workdir / ".gate1_pending"
    if gate_file.exists():
        msg = _format_error(
            "LAYER_GATE_ERROR",
            "Step 3 BLOCKED: Human Gate 1 is still PENDING confirmation.",
            "Present the proposal to the user for review. After confirmation, run: "
            f"python scripts/layer_gate.py --confirm-gate 1 --workdir {workdir}",
            f"Current state: step=pre-step-3, check=gate1_pending, "
            f"proposal={proposal.name}",
        )
        print(msg, file=sys.stderr)
        return 1

    # Check confirmed: true in the YAML
    confirmed, err = _check_confirmed_flag(proposal)
    if not confirmed:
        msg = _format_error(
            "LAYER_GATE_ERROR",
            f"Proposal NOT confirmed: {proposal.name} — {err}",
            "Set 'confirmed: true' in the proposal YAML after the user approves the chart recommendation. "
            "If the user rejected, return to Step 1 to re-analyse.",
            f"Current state: step=pre-step-3, check=confirmed_flag, "
            f"gate1=confirmed",
        )
        print(msg, file=sys.stderr)
        return 3

    ok, err = _check_yaml_valid(proposal)
    if not ok:
        msg = _format_error(
            "LAYER_GATE_ERROR",
            f"Invalid proposal YAML: {proposal.name} — {err}",
            "Regenerate the proposal file.",
            f"Current state: step=pre-step-3, check=yaml_validity",
        )
        print(msg, file=sys.stderr)
        return 1

    print(f"[LAYER_GATE_OK] Step 3 prerequisites satisfied.")
    print(f"[LAYER_GATE_OK] Proposal confirmed: {proposal.name}")
    print(f"[LAYER_GATE_OK] Human Gate 1: CONFIRMED")
    return 0


def set_gate(gate_num: int, workdir: Path) -> int:
    """Write a .gate{N}_pending file to mark human gate as pending."""
    gate_file = workdir / f".gate{gate_num}_pending"
    gate_file.write_text(
        f"Human Gate {gate_num} pending confirmation\n"
        f"gate={gate_num}\n"
        f"status=pending\n",
        encoding="utf-8",
    )
    print(f"[GATE_SET] Human Gate {gate_num} marked as PENDING.")
    print(f"[GATE_SET] File: {gate_file}")
    print(f"[GATE_SET] Present output to user. DO NOT proceed until confirmed.")
    return 0


def confirm_gate(gate_num: int, workdir: Path) -> int:
    """Remove a .gate{N}_pending file to confirm human gate."""
    gate_file = workdir / f".gate{gate_num}_pending"
    if gate_file.exists():
        gate_file.unlink()
        print(f"[GATE_CONFIRMED] Human Gate {gate_num} confirmed. Gate file removed.")
    else:
        print(f"[GATE_INFO] No pending gate file for Human Gate {gate_num}.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="chart-gen step gate — checks prerequisites per step and manages human gates"
    )
    parser.add_argument(
        "--target",
        type=int,
        required=False,
        choices=[1, 2, 3],
        help="Which step's prerequisites to check",
    )
    parser.add_argument(
        "--workdir",
        type=Path,
        required=True,
        help="Flat output directory for intermediate files (e.g., chart proposals)",
    )
    parser.add_argument(
        "--input",
        type=Path,
        required=False,
        help="Input xlsx file path (required for --target 1)",
    )
    parser.add_argument(
        "--set-gate",
        type=int,
        choices=[1, 2],
        help="Mark a human gate as pending (write .gateN_pending)",
    )
    parser.add_argument(
        "--confirm-gate",
        type=int,
        choices=[1, 2],
        help="Confirm a human gate (remove .gateN_pending)",
    )
    args = parser.parse_args()

    # Workdir must exist for all operations
    if not args.workdir.exists():
        print(
            f"[LAYER_GATE_ERROR] Work directory not found: {args.workdir}",
            file=sys.stderr,
        )
        return 1

    # Human gate management
    if args.set_gate is not None:
        return set_gate(args.set_gate, args.workdir)

    if args.confirm_gate is not None:
        return confirm_gate(args.confirm_gate, args.workdir)

    # Step prerequisite checks
    if args.target is None:
        print(
            "[LAYER_GATE] No action specified. Use --target, --set-gate, or --confirm-gate.",
            file=sys.stderr,
        )
        return 1

    if args.target == 1:
        if args.input is None:
            print(
                "[LAYER_GATE_ERROR] --input is required for --target 1 (pre-step file check)",
                file=sys.stderr,
            )
            return 1
        return check_pre_step_1(args.input)

    if args.target == 2:
        return check_step_2(args.workdir)

    if args.target == 3:
        return check_step_3(args.workdir)

    return 1


if __name__ == "__main__":
    sys.exit(main())
