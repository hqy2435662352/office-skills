#!/usr/bin/env python3
"""
scripts/preflight.py — Layer 0: environment pre-flight check.

Runs before Layer 1. Verifies that the execution environment won't cause
silent failures downstream: ASCII-safe paths, no stale officecli residents,
Python encoding functional.

Fingerprint cache (2026-08-09):
  Static environment checks (officecli presence/version, Python encoding,
  ASCII path) are cached in <workdir>/.preflight_cache.json keyed by an
  environment fingerprint (workdir + officecli binary path/mtime/size +
  Python version). On a fingerprint match, the expensive `officecli --version`
  probe (~2.8s on Windows cold start) is skipped. Stateful checks are NEVER
  cached: resident-process cleanup still runs every time. Use --no-cache to
  force a full re-check.

Exit codes:
  0 — Pass, environment is clean
  1 — Fatal (can't recover, need user intervention)
  3 — Retryable (apply corrective_action, re-run)

Usage:
  python scripts/preflight.py --workdir <path> [--no-cache]
"""

import os, sys, json, argparse, subprocess, stat, shutil, platform
from datetime import datetime
from pathlib import Path

from _officecli import clean_residents, officecli  # noqa: E402

CACHE_FILENAME = ".preflight_cache.json"


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
        r = officecli("--version", timeout=10)
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
            clean_residents()  # shared adapter: taskkill 强杀 + 句柄释放纪律
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


# ── Fingerprint cache (static checks only) ─────────────────────────────

def compute_fingerprint(workdir):
    """Environment fingerprint for static checks.

    Includes:
      - workdir (input to ASCII-path check)
      - officecli binary (resolved via PATH) path + mtime + size — a binary
        reinstall/upgrade changes mtime/size → fingerprint mismatch → re-check
      - Python version
    Returns a JSON-serializable dict. Any component that cannot be resolved
    (officecli not on PATH) is recorded as None so the fingerprint still
    changes when the tool appears later.
    """
    exe_path = shutil.which("officecli")
    officecli_fp = None
    if exe_path:
        try:
            st = os.stat(exe_path)
            officecli_fp = {
                "path": os.path.realpath(exe_path),
                "mtime": st.st_mtime,
                "size": st.st_size,
            }
        except OSError:
            officecli_fp = None
    return {
        "workdir": str(workdir),
        "officecli": officecli_fp,
        "python": platform.python_version(),
    }


def load_cache(workdir):
    """Load cached fingerprint+checks from <workdir>/.preflight_cache.json.

    Returns None on missing/corrupt cache (treated as no cache).
    """
    cache_path = Path(workdir) / CACHE_FILENAME
    try:
        if not cache_path.exists():
            return None
        data = json.loads(cache_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or "fingerprint" not in data:
            return None
        return data
    except (OSError, ValueError):
        return None


def save_cache(workdir, fingerprint, checks):
    """Persist fingerprint + per-check results. Best-effort: cache write
    failure (read-only dir, permission) must not block the pipeline."""
    cache_path = Path(workdir) / CACHE_FILENAME
    try:
        cache_path.write_text(
            json.dumps({
                "fingerprint": fingerprint,
                "checks": checks,
                "checked_at": datetime.now().isoformat(timespec="seconds"),
            }, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError:
        pass  # non-blocking


def main():
    parser = argparse.ArgumentParser(description="Layer 0: environment pre-flight check")
    parser.add_argument("--workdir", type=Path, required=True, help="Working directory")
    parser.add_argument("--no-cache", action="store_true",
                        help="Force full re-check, ignoring the fingerprint cache")
    args = parser.parse_args()

    issues = []
    fingerprint = compute_fingerprint(str(args.workdir))
    cache = None if args.no_cache else load_cache(args.workdir)
    cache_hit = bool(cache) and cache.get("fingerprint") == fingerprint
    try:
        str(args.workdir).encode("ascii")
        workdir_is_ascii = True
    except UnicodeEncodeError:
        workdir_is_ascii = False

    # Check 1: ASCII path (0ms, but must run every time — a non-ASCII workdir
    # warning must never be suppressed by a stale cache)
    result = check_ascii_path(str(args.workdir))
    if result:
        issues.append(result)

    # Check 2: officecli available — the expensive one (~2.8s cold start).
    # Skipped only when the cached fingerprint matches (same workdir, same
    # officecli binary, same Python).
    if cache_hit:
        cached_officecli = (cache.get("checks") or {}).get("officecli", "pass")
        print(f"[PREFLIGHT] CACHE HIT — fingerprint unchanged, officecli check "
              f"reused (cached status: {cached_officecli})", file=sys.stderr)
        if cached_officecli != "pass":
            # Cached result was a warning/failure; surface it so the caller
            # sees the same signal it would have gotten from a full run.
            issues.append({
                "code": cached_officecli,
                "message": f"officecli check cached from previous run (checked_at={cache.get('checked_at', '?')}); "
                           f"status was {cached_officecli}",
                "corrective_action": "Run with --no-cache to re-verify officecli."
            })
    else:
        result = check_officecli()
        if result:
            issues.append(result)

    # Check 3: resident cleanup — stateful, NEVER cached.
    result = check_resident_cleanup()
    if result:
        issues.append(result)

    # Check 4: Python encoding (0ms, static; run every time for the same
    # reason as ASCII path — cheap and cannot be stale-suppressed).
    result = check_python_encoding()
    if result:
        issues.append(result)

    # Refresh the cache after a full run (only when officecli itself is fine —
    # a broken install must stay visible on every run until fixed).
    # Cache is written ONLY for ASCII workdirs: a non-ASCII workdir is the
    # "copy to ASCII temp dir" scenario, and we must not drop a cache file
    # into the user's original (possibly shared) folder.
    if not cache_hit and workdir_is_ascii:
        officecli_ok = not any(
            i["code"] in {"OFFICECLI_NOT_FOUND", "OFFICECLI_NOT_FUNCTIONAL"}
            for i in issues
        )
        if officecli_ok:
            save_cache(args.workdir, fingerprint, {
                "ascii": "pass" if not any(i["code"] == "NON_ASCII_PATH" for i in issues) else "warn",
                "officecli": "pass",
                "encoding": "pass" if not any(i["code"] == "ENCODING_WARNING" for i in issues) else "warn",
            })

    if not issues:
        print("[PREFLIGHT] PASS — environment clean")
        sys.exit(0)

    # Non-fatal issues — report and let caller decide
    fatal_codes = {"OFFICECLI_NOT_FOUND", "OFFICECLI_NOT_FUNCTIONAL"}
    has_fatal = any(i["code"] in fatal_codes for i in issues)

    report = {
        "status": "FATAL" if has_fatal else "WARN",
        "code": "PREFLIGHT_FATAL" if has_fatal else "PREFLIGHT_WARN",
        "issue_count": len(issues),
        "issues": issues,
        "corrective_action": "Fix the reported issues, or review them before proceeding",
    }
    print(json.dumps(report, ensure_ascii=False, indent=2), file=sys.stderr)

    if has_fatal:
        sys.exit(1)
    else:
        print("[PREFLIGHT] WARN — non-fatal issues detected, review stderr before proceeding")
        sys.exit(0)


if __name__ == "__main__":
    main()
