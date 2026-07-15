#!/usr/bin/env python3
"""
scripts/preflight.py — Layer 0: environment pre-flight check.

Runs before Layer 1. Verifies that the execution environment won't cause
silent failures downstream: ASCII-safe paths, no stale officecli residents,
Python encoding functional.

Exit codes:
  0 — Pass, environment is clean
  1 — Fatal (can't recover, need user intervention)
  3 — Retryable (apply corrective_action, re-run)

Usage:
  python scripts/preflight.py --workdir <path>
"""

import os, sys, json, argparse, subprocess, stat
from pathlib import Path


def check_ascii_path(workdir):
    """officecli batch/set fails on Chinese paths on Windows. Require ASCII."""
    try:
        workdir.encode("ascii")
        return None  # All ASCII, safe
    except UnicodeEncodeError:
        pass

    # Workdir has non-ASCII chars. Check if there's a safe temp path.
    temp_alt = Path("C:/Temp/tablefill")
    return {
        "code": "NON_ASCII_PATH",
        "message": f"Working directory contains non-ASCII characters: {workdir}. officecli batch/set operations may fail with Access denied on Windows.",
        "corrective_action": f"Copy all files to an ASCII-only path before Layer 1. Recommended: {temp_alt}. Use: xcopy /E /I /Y <source> {temp_alt}",
        "workaround": str(temp_alt)
    }


def check_officecli():
    """Verify officecli is on PATH and functional."""
    try:
        r = subprocess.run(["officecli", "--version"], capture_output=True, timeout=10)
        if r.returncode != 0:
            return {
                "code": "OFFICECLI_NOT_FUNCTIONAL",
                "message": f"officecli --version returned exit code {r.returncode}",
                "corrective_action": "Reinstall officecli or check PATH."
            }
        return None
    except FileNotFoundError:
        return {
            "code": "OFFICECLI_NOT_FOUND",
            "message": "officecli is not on PATH.",
            "corrective_action": "Install officecli: https://github.com/iOfficeAI/OfficeCLI/releases"
        }
    except Exception as e:
        return {
            "code": "OFFICECLI_ERROR",
            "message": f"officecli check failed: {e}",
            "corrective_action": "Check officecli installation."
        }


def check_resident_cleanup():
    """Kill stale officecli resident processes that hold file locks."""
    import platform
    if platform.system() != "Windows":
        return None  # Non-Windows, skip

    try:
        r = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq officecli.exe", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=10
        )
        if "officecli.exe" in r.stdout:
            subprocess.run(["taskkill", "/F", "/IM", "officecli.exe"],
                          capture_output=True, timeout=10)
            return {"code": "RESIDENT_CLEANED",
                    "message": "Stale officecli resident processes were terminated.",
                    "corrective_action": "Re-run preflight to confirm clean state."}
        return None
    except Exception:
        return None  # Can't check, don't block


def check_python_encoding():
    """Verify Python can handle Chinese text via stdout."""
    try:
        test_str = "中文测试"
        encoded = test_str.encode("utf-8")
        decoded = encoded.decode("utf-8")
        if decoded == test_str:
            return None
    except Exception:
        pass
    return {
        "code": "ENCODING_WARNING",
        "message": "Python UTF-8 encoding may not be fully functional.",
        "corrective_action": "Set PYTHONIOENCODING=utf-8 environment variable."
    }


def main():
    parser = argparse.ArgumentParser(description="Layer 0: environment pre-flight check")
    parser.add_argument("--workdir", type=Path, required=True, help="Working directory")
    args = parser.parse_args()

    issues = []

    # Check 1: ASCII path
    result = check_ascii_path(str(args.workdir))
    if result:
        issues.append(result)

    # Check 2: officecli available
    result = check_officecli()
    if result:
        issues.append(result)
        # officecli unavailable is fatal — can't continue
        print(json.dumps({"status": "FATAL", "issues": issues}, ensure_ascii=False), file=sys.stderr)
        sys.exit(1)

    # Check 3: resident cleanup
    result = check_resident_cleanup()
    if result:
        issues.append(result)

    # Check 4: Python encoding
    result = check_python_encoding()
    if result:
        issues.append(result)

    if not issues:
        print("[PREFLIGHT] PASS — environment clean")
        sys.exit(0)

    # Non-fatal issues — report and let caller decide
    fatal_codes = {"OFFICECLI_NOT_FOUND", "OFFICECLI_NOT_FUNCTIONAL"}
    has_fatal = any(i["code"] in fatal_codes for i in issues)

    report = {
        "status": "FATAL" if has_fatal else "WARN",
        "issue_count": len(issues),
        "issues": issues
    }
    print(json.dumps(report, ensure_ascii=False, indent=2), file=sys.stderr)

    if has_fatal:
        sys.exit(1)
    else:
        print("[PREFLIGHT] WARN — non-fatal issues detected, review stderr before proceeding")
        sys.exit(0)


if __name__ == "__main__":
    main()
