#!/usr/bin/env python3
"""
scripts/verify_output.py - chart-gen EXIT GATE

Verifies that charts were successfully created in the output xlsx and that
their data bindings (series valuesRef) are intact. Must pass before reporting
completion to the user.

Usage:
  python scripts/verify_output.py --output <file.xlsx> --workdir <展平元数据输出/>

Exit codes:
  0 = pass (chart exists, data binding verified)
  1 = fatal (file missing, invalid structure, officecli error)
  3 = retryable (chart not found, empty data binding — fix and re-run)
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path


def _run_officecli(args: list[str], timeout: int = 30) -> tuple[bool, dict | None, str]:
    """Run officecli and return (success, parsed_json, error_message)."""
    try:
        result = subprocess.run(
            ["officecli"] + args,
            capture_output=True,
            timeout=timeout,
        )
    except FileNotFoundError:
        return False, None, "officecli executable not found on PATH"
    except subprocess.TimeoutExpired:
        return False, None, f"officecli {' '.join(args)} timed out after {timeout}s"

    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace").strip()
        return False, None, f"officecli exited {result.returncode}: {stderr or 'no stderr'}"

    try:
        stdout = result.stdout.decode("utf-8")
        data = json.loads(stdout)
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        return False, None, f"Failed to parse officecli JSON output: {e}"

    return True, data, ""


def _extract_chart_paths(data: dict) -> list[str]:
    """Extract chart paths from officecli query results.

    Handles multiple possible JSON shapes from officecli:
    - {"data": {"results": [{"path": "/Sheet1/chart[1]", ...}, ...]}}
    - {"results": [{"path": "/Sheet1/chart[1]", ...}, ...]}
    - A flat list of chart objects
    """
    paths = []

    # Try common structures
    results = data.get("data", data).get("results", None)
    if results is None:
        results = data.get("results", None)
    if results is None and isinstance(data, list):
        results = data

    if not isinstance(results, list):
        return paths

    for item in results:
        if isinstance(item, dict):
            path = item.get("path", "")
            if path:
                paths.append(path)
        elif isinstance(item, str):
            paths.append(item)

    return paths


def _find_chart_in_query(output_path: Path, chart_index: int) -> tuple[str | None, str]:
    """Query all charts and find the one at chart_index. Returns (path, error)."""
    ok, data, err = _run_officecli(["query", str(output_path), "chart", "--json"])
    if not ok:
        return None, f"officecli query chart failed: {err}"

    paths = _extract_chart_paths(data)
    if not paths:
        return None, "No charts found in output file"

    # paths look like "/Sheet1/chart[1]", "/Sheet1/chart[2]", ...
    # Filter to those with chart[N] matching chart_index
    target_suffix = f"chart[{chart_index}]"
    matches = [p for p in paths if target_suffix in p]

    if not matches:
        available = ", ".join(paths) if paths else "none"
        return None, (
            f"Chart index {chart_index} not found in query results. "
            f"Available chart paths: {available}"
        )

    return matches[0], ""


def _unwrap_result(data: dict) -> dict:
    """Normalize officecli response to a flat-ish dict.

    officecli returns several shapes depending on depth and path type:
      - get /path: {"data": {"path": "...", "format": {"valuesRef": "..."}}}
      - get /path (alt): {"data": {"results": [{"format": {"valuesRef": "..."}}]}}
      - query:       {"data": {"results": [{"path": "...", "format": {...}}]}}

    We collapse 'format' keys up one level so callers can look up fields
    like 'valuesRef' or 'chartType' directly on the returned dict.
    """
    result = data.get("data", data)

    # If results array exists, take the first result item
    if isinstance(result, dict) and "results" in result:
        results = result["results"]
        if isinstance(results, list) and len(results) > 0:
            result = results[0]

    if not isinstance(result, dict):
        return {}

    # Merge format.* up to the top level, but don't overwrite existing keys
    fmt = result.get("format", {})
    if isinstance(fmt, dict):
        merged = dict(fmt)  # format keys first (lower priority)
        merged.update(result)  # direct keys override format keys
        return merged

    return result


def check_chart_exists(output_path: Path, chart_index: int) -> tuple[bool, str, str]:
    """Verify at least one chart exists. Returns (ok, chart_path, error)."""
    if not output_path.exists():
        return False, "", f"Output file not found: {output_path}"

    chart_path, err = _find_chart_in_query(output_path, chart_index)
    if chart_path is None:
        return False, "", err

    print(f"[VERIFY] Chart discovered: {chart_path}")
    return True, chart_path, ""


def check_chart_object(output_path: Path, chart_path: str) -> tuple[bool, str]:
    """Verify the chart object is readable via officecli get."""
    ok, data, err = _run_officecli(["get", str(output_path), chart_path, "--json", "--depth", "0"])
    if not ok:
        return False, f"Failed to read chart object at {chart_path}: {err}"

    # Normalize officecli response shape (handles format.* nesting and results[] wrapping)
    result = _unwrap_result(data)
    if not isinstance(result, dict):
        return False, f"Unexpected response type for chart at {chart_path}"

    # chartType can appear directly or nested under format.*
    chart_type = result.get("chartType", "") or result.get("type", "")
    print(f"[VERIFY] Chart object confirmed: type={chart_type or 'unknown'}, path={chart_path}")
    return True, ""


def check_series_binding(output_path: Path, chart_path: str) -> tuple[bool, str]:
    """Verify series[1] exists and valuesRef is non-empty."""
    full_path = f"{chart_path}/series[1]"
    ok, data, err = _run_officecli(["get", str(output_path), full_path, "--json", "--depth", "0"])
    if not ok:
        return False, f"Failed to read series[1] at {full_path}: {err}"

    # Normalize officecli response shape (handles format.* nesting and results[] wrapping)
    result = _unwrap_result(data)
    if not isinstance(result, dict):
        return False, f"Unexpected response type for series at {full_path}"

    # valuesRef can appear directly or nested under format.*
    values_ref = result.get("valuesRef", "")
    if not values_ref or values_ref.strip() == "":
        return False, (
            f"Series[1] at {full_path} has empty valuesRef. "
            "The chart may have been created without data binding. "
            "Delete the chart and re-create with a valid dataRange."
        )

    print(f"[VERIFY] Series[1] binding confirmed: valuesRef={values_ref}")
    return True, ""


def check_workdir_completeness(workdir: Path) -> list[str]:
    """Verify that intermediate outputs exist in workdir."""
    issues = []
    proposals = list(workdir.glob("*_chart_proposal.yaml"))
    if not proposals:
        issues.append(
            f"No *_chart_proposal.yaml found in {workdir}. "
            "The proposal file should exist from Step 1."
        )
    return issues


def main() -> int:
    parser = argparse.ArgumentParser(
        description="chart-gen EXIT GATE — verifies chart creation and data binding"
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Output xlsx file path (the file charts were added to)",
    )
    parser.add_argument(
        "--workdir",
        type=Path,
        required=True,
        help="展平元数据输出 directory (intermediate outputs)",
    )
    parser.add_argument(
        "--chart-index",
        type=int,
        default=1,
        help="Chart index to verify (default: 1)",
    )
    args = parser.parse_args()

    errors: list[str] = []
    chart_path: str = ""

    # 1. Chart existence check
    ok, cp, err = check_chart_exists(args.output, args.chart_index)
    if not ok:
        errors.append(f"[FATAL] {err}")
    else:
        chart_path = cp

    # 2. Chart object readability
    if chart_path:
        ok, err = check_chart_object(args.output, chart_path)
        if not ok:
            errors.append(f"[FATAL] {err}")

    # 3. Series data binding
    if chart_path:
        ok, err = check_series_binding(args.output, chart_path)
        if not ok:
            errors.append(f"[DATA_ERROR] {err}")

    # 4. Workdir completeness (non-fatal info)
    if args.workdir.exists():
        workdir_issues = check_workdir_completeness(args.workdir)
        if workdir_issues:
            for issue in workdir_issues:
                print(f"[VERIFY_INFO] {issue}")

    # Report results
    if errors:
        has_fatal = any("[FATAL]" in e for e in errors)
        corrective = (
            "Re-run Step 3 chart generation. Ensure officecli add chart succeeded."
            if has_fatal
            else (
                "Chart exists but data binding is incomplete. "
                "Delete the chart and re-create with a valid dataRange/categories/values."
            )
        )
        error_report = {
            "code": "FATAL_ERROR" if has_fatal else "DATA_ERROR",
            "message": f"EXIT GATE failed: {len(errors)} issue(s) found.",
            "issues": errors,
            "corrective_action": corrective,
        }
        print(json.dumps(error_report, ensure_ascii=False), file=sys.stderr)
        return 1 if has_fatal else 3

    print(f"[EXIT_GATE_PASSED] All checks passed.")
    print(f"[EXIT_GATE_PASSED] Chart: {chart_path} in {args.output.name}")
    print(f"[EXIT_GATE_PASSED] Data binding verified.")
    print(f"[EXIT_GATE_PASSED] You may now report task completion to the user.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
