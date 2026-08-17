#!/usr/bin/env python3
"""
scripts/_officecli.py — shared Windows-safe officecli adapter (V2).

The single home for every subprocess/encoding/timeout/file-lock concern that
used to be reimplemented in each script:
  - officecli():  UTF-8 subprocess wrapper (never raw PowerShell — GBK mangles
    Chinese text).
  - clean_residents(): on Windows, officecli leaves resident processes holding
    file locks; kill them before/after batch work.
  - force_writable() / unlink_retry(): copy2 preserves read-only attributes and
    Windows releases handles asynchronously — standard retry helpers.
  - issues_delta(): NEW-issues comparison vs the template baseline. Templates
    carry their own baseline issues; only issues absent from the template
    matter (Egypt FRESH replay: 235 baseline issues, all pre-existing).
  - read_cell() / resolve_check_path(): readback helpers shared by the draft
    executor and verification.

All deterministic, no state. Import from the scripts directory:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from _officecli import officecli, clean_residents, issues_delta, ...
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

DEFAULT_TIMEOUT = 300


def ensure_utf8_stdio() -> None:
    """Force UTF-8 stdout/stderr so Chinese text survives Windows consoles.

    Python on Windows defaults to the ANSI codepage (e.g. GBK) for stdio,
    which mangles UTF-8 JSON. All scripts call this at startup; the files
    they write are UTF-8 regardless."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def officecli(*args: str, timeout: int = DEFAULT_TIMEOUT) -> subprocess.CompletedProcess:
    """Run officecli with UTF-8 decoding. Returns CompletedProcess, never raises."""
    return subprocess.run(
        ["officecli", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )


def clean_residents() -> None:
    """Kill officecli resident processes holding file locks (Windows only)."""
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/F", "/IM", "officecli.exe"],
            capture_output=True, text=True,
        )


def force_writable(path: Path) -> None:
    """copy2 preserves the source's read-only attribute; officecli then cannot
    write the copy. Force write permission on every staged/copied file."""
    try:
        import stat
        current = stat.S_IMODE(os.stat(path).st_mode)
        os.chmod(path, current | stat.S_IWRITE)
    except OSError:
        pass


def unlink_retry(path: Path, attempts: int = 6) -> None:
    """Windows releases file handles asynchronously after taskkill — retry."""
    for _ in range(attempts):
        try:
            path.unlink(missing_ok=True)
            return
        except PermissionError:
            time.sleep(0.5)


def copy_template(src: Path, dst: Path) -> None:
    """Copy template → draft/output, forcing writable (copy2 preserves the
    read-only attribute, which made officecli batch fail with Access denied)."""
    clean_residents()
    unlink_retry(dst)
    try:
        import shutil
        shutil.copy2(src, dst)
        force_writable(dst)
    except OSError as e:
        raise RuntimeError(f"cannot copy {src} -> {dst}: {e}") from e


def issue_key(issue: dict) -> tuple:
    """Stable identity for an issue dict from `view issues --json`."""
    return (
        issue.get("path", ""),
        issue.get("subtype", ""),
        (issue.get("message", "") or "")[:40],
    )


def collect_issues(book: Path) -> set | None:
    """Run `officecli view <book> issues --json`, return set of issue keys.

    Returns None when the mode is unsupported (older officecli) — callers
    treat None as 'cannot verify, fall back to validate-only'."""
    proc = officecli("view", str(book), "issues", "--json")
    if proc.returncode != 0:
        return None
    try:
        data = json.loads(proc.stdout)
        inner = data.get("data", {}) if isinstance(data, dict) else {}
        issues = inner.get("issues", []) if isinstance(inner, dict) else []
        return {issue_key(i) for i in issues}
    except (json.JSONDecodeError, AttributeError):
        return None


def issues_delta(output: Path, template: Path) -> tuple[bool, int, set | None]:
    """Compare output issues against the template baseline.

    Returns (supported, new_count, new_issues_set).
      supported=False → issues mode unavailable (fall back to validate-only).
      new_count = number of issues in output NOT present in the template.
    """
    output_issues = collect_issues(output)
    if output_issues is None:
        return False, 0, None
    baseline = collect_issues(template)
    if baseline is None:
        return True, len(output_issues), output_issues
    new = output_issues - baseline
    return True, len(new), new


def officecli_validate(book: Path) -> bool:
    """Run `officecli validate`; returns True when it exits 0.

    Validate runs BEFORE issue-delta in the draft executor: it flushes pending
    edits and forces formula evaluation — checking issues before validate
    misreports freshly-written formulas as formula_not_evaluated."""
    return officecli("validate", str(book)).returncode == 0


def read_cell(book: Path, path: str) -> str:
    """Read a cell's rendered text via officecli get --json. '' on failure."""
    proc = officecli("get", str(book), path, "--json")
    if proc.returncode != 0:
        return ""
    try:
        data = json.loads(proc.stdout)
        results = (data or {}).get("data", {}).get("results") or []
        text = results[0].get("text") if results else None
        return str(text) if text is not None else ""
    except (ValueError, AttributeError, IndexError):
        return ""


def resolve_check_path(operations: list, raw_path: str) -> str:
    """Turn a bare 'E7' check into '/Sheet/E7' using the first cell path's sheet.

    Windows/Git Bash: 禁止在 --checks 参数里写前导 '/' 的完整路径 —
    MSYS2 会把 / 前导 token 转换成 Windows 路径。跨 sheet 检查一律用
    'Sheet!A1' 形式, 这里统一转换为 '/Sheet/A1'。"""
    raw_path = raw_path.strip()
    if raw_path.startswith("/"):
        return raw_path
    if "!" in raw_path:
        sheet, cell = raw_path.split("!", 1)
        return f"/{sheet}/{cell}"
    for op in operations:
        p = op.get("path", "")
        m = re.match(r'^/([^/]+)/[A-Z]+\d+', p)
        if m:
            return f"/{m.group(1)}/{raw_path}"
    return raw_path


# ── Shared script infrastructure ───────────────────────────────────────

_T0 = time.perf_counter()


def fail(code: str, message: str, corrective_action: str,
         defects: list | None = None, exit_code: int = 3) -> None:
    """Emit a structured defect to stderr and exit (suite-wide error contract:
    every failure carries code + message + corrective_action)."""
    payload = {
        "status": "ERROR", "code": code,
        "message": message, "corrective_action": corrective_action,
    }
    if defects is not None:
        payload["defects"] = defects
    sys.stderr.write(json.dumps(payload, ensure_ascii=False, indent=2))
    sys.exit(exit_code)


def record_timing(workdir, phase: str) -> None:
    """Append one machine-phase record to run_timing.json (observability:
    kind: machine vs note_phase.py's kind: agent entries)."""
    entry = {
        "kind": "machine", "phase": phase,
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "duration_ms": round((time.perf_counter() - _T0) * 1000),
    }
    path = Path(workdir) / "run_timing.json"
    try:
        data = []
        if path.is_file():
            data = json.loads(path.read_text(encoding="utf-8"))
        data.append(entry)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except (OSError, ValueError):
        pass


def sha256_file(path) -> str:
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()
