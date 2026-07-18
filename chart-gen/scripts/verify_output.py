#!/usr/bin/env python3
"""
scripts/verify_output.py - chart-gen EXIT GATE

Verifies that charts were successfully created in the output xlsx and that
their data bindings (series valuesRef) are intact. Must pass before reporting
completion to the user.

Supports two binding modes:
  - explicit: categories + seriesN.values (primary)
  - dataRange: auto-inference from contiguous range (fallback)

Usage:
  python scripts/verify_output.py --output <file.xlsx> --workdir <flat_output/>

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

_GHOST_SERIES_NAMES = {"汇总", "总计", "Total", "Sum", "Grand Total"}


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


def _load_proposal(workdir: Path) -> dict | None:
    """Load the chart proposal YAML. Returns None if not found or unparseable."""
    try:
        import yaml
    except ImportError:
        print("[VERIFY_INFO] PyYAML not installed; explicit binding checks skipped.")
        return None

    proposals = list(workdir.glob("*_chart_proposal.yaml"))
    if not proposals:
        print("[VERIFY_INFO] No chart proposal found; explicit binding checks skipped.")
        return None

    proposal_path = proposals[0]
    try:
        with open(proposal_path, "r", encoding="utf-8") as f:
            proposal = yaml.safe_load(f)
        if not isinstance(proposal, dict):
            return None
        return proposal
    except Exception as e:
        print(f"[VERIFY_INFO] Failed to load proposal: {e}")
        return None


def _check_ghost_series(output_path: Path, chart_path: str) -> list[str]:
    """Check for ghost aggregate series (汇总, Total, etc.). Returns list of ghost series names found."""
    ghosts = []
    # Try up to 20 series — find where the series list ends
    for i in range(1, 21):
        full_path = f"{chart_path}/series[{i}]"
        ok, data, err = _run_officecli(["get", str(output_path), full_path, "--json", "--depth", "0"])
        if not ok:
            break  # series list exhausted

        result = _unwrap_result(data)
        name = result.get("name", "") or result.get("seriesName", "")
        if not name.strip():
            break  # empty name = end of series

        if name.strip() in _GHOST_SERIES_NAMES:
            ghosts.append(name.strip())
    return ghosts


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
            "Delete the chart and re-create with a valid dataRange/categories/values."
        )

    print(f"[VERIFY] Series[1] binding confirmed: valuesRef={values_ref}")
    return True, ""


def check_explicit_binding(
    output_path: Path, chart_path: str, proposal: dict
) -> list[str]:
    """Verify explicit binding: series count, categoriesRef, each series name and valuesRef.

    Returns a list of error strings (empty = all good).
    """
    errors = []
    chart_options = proposal.get("chart_options", [])
    selected_idx = proposal.get("selected_index", 0)

    if selected_idx >= len(chart_options):
        errors.append(f"selected_index {selected_idx} out of range (max {len(chart_options)-1})")
        return errors

    option = chart_options[selected_idx]
    eb = option.get("explicit_binding", {})
    if not eb:
        errors.append("binding_mode is explicit but explicit_binding section is missing")
        return errors

    expected_series = eb.get("series", [])
    expected_count = len(expected_series)
    expected_categories = eb.get("categories_range", "")

    # 1. Verify seriesCount
    ok, data, err = _run_officecli(["get", str(output_path), chart_path, "--json", "--depth", "0"])
    if not ok:
        errors.append(f"Failed to read chart object: {err}")
        return errors

    result = _unwrap_result(data)
    actual_count = result.get("seriesCount", 0)
    if isinstance(actual_count, str):
        try:
            actual_count = int(actual_count)
        except ValueError:
            actual_count = 0

    if actual_count != expected_count:
        errors.append(
            f"seriesCount mismatch: expected {expected_count}, got {actual_count}"
        )
    else:
        print(f"[VERIFY] Explicit binding: seriesCount={actual_count} matches proposal")

    # 2. Verify categoriesRef
    actual_categories = result.get("categoriesRef", "")
    if expected_categories and actual_categories:
        if actual_categories != expected_categories:
            errors.append(
                f"categoriesRef mismatch: expected {expected_categories}, got {actual_categories}"
            )
        else:
            print(f"[VERIFY] Explicit binding: categoriesRef={actual_categories} matches proposal")

    # 3. Verify each series name and valuesRef
    for i, expected in enumerate(expected_series, 1):
        full_path = f"{chart_path}/series[{i}]"
        ok_s, data_s, err_s = _run_officecli(
            ["get", str(output_path), full_path, "--json", "--depth", "0"]
        )
        if not ok_s:
            errors.append(f"Series[{i}]: {err_s}")
            continue

        series_result = _unwrap_result(data_s)
        actual_name = series_result.get("name", "")
        actual_values = series_result.get("valuesRef", "")
        expected_name = expected.get("name", "")
        expected_values = expected.get("values_range", "")

        if actual_name != expected_name:
            errors.append(
                f"Series[{i}] name mismatch: expected '{expected_name}', got '{actual_name}'"
            )
        else:
            print(f"[VERIFY] Series[{i}] name: '{actual_name}' matches proposal")

        if expected_values and actual_values != expected_values:
            errors.append(
                f"Series[{i}] valuesRef mismatch: expected {expected_values}, got {actual_values}"
            )
        elif actual_values:
            print(f"[VERIFY] Series[{i}] valuesRef: {actual_values} matches proposal")
        else:
            errors.append(f"Series[{i}] has empty valuesRef")

    return errors


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
        help="Flat output directory for intermediate files (e.g., chart proposals)",
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

    # 3. Load proposal for binding mode detection
    proposal = _load_proposal(args.workdir)

    # 4. Series data binding — mode-aware
    if chart_path and proposal:
        chart_options = proposal.get("chart_options", [])
        selected_idx = proposal.get("selected_index", 0)
        if selected_idx < len(chart_options):
            option = chart_options[selected_idx]
            binding_mode = option.get("binding_mode", "dataRange")

            if binding_mode == "explicit":
                # Explicit binding verification
                print("[VERIFY] Binding mode: explicit — verifying series count, categoriesRef, each series")
                explicit_errors = check_explicit_binding(args.output, chart_path, proposal)
                errors.extend(explicit_errors)
            else:
                # dataRange fallback: verify series[1] binding
                print("[VERIFY] Binding mode: dataRange (fallback) — verifying series[1]")
                ok, err = check_series_binding(args.output, chart_path)
                if not ok:
                    errors.append(f"[DATA_ERROR] {err}")
        else:
            ok, err = check_series_binding(args.output, chart_path)
            if not ok:
                errors.append(f"[DATA_ERROR] {err}")
    elif chart_path:
        # No proposal — fallback to basic series check
        ok, err = check_series_binding(args.output, chart_path)
        if not ok:
            errors.append(f"[DATA_ERROR] {err}")

    # 5. Ghost series detection (always run regardless of binding mode)
    if chart_path:
        ghosts = _check_ghost_series(args.output, chart_path)
        if ghosts:
            for g in ghosts:
                errors.append(
                    f"[DATA_ERROR] Ghost series detected: '{g}'. "
                    "This suggests dataRange auto-inference picked up an unwanted aggregate column. "
                    "Use explicit binding (seriesN.values) to prevent this."
                )
            print(f"[VERIFY] Ghost series check: detected {ghosts}")

    # 6. Workdir completeness (non-fatal info)
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
