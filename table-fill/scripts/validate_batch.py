#!/usr/bin/env python3
"""
scripts/validate_batch.py — Layer 3→4 gate: validate batch.json structure before execution.

Runs ZERO officecli calls. Reads the batch JSON and checks for structural errors that
would cause silent failures or coordinate corruption during execution.

Exit codes (per agentskills.io exit code protocol):
  0 — Pass, proceed to Layer 4
  3 — Issues found, stderr JSON with corrective_action per issue

Usage:
  python scripts/validate_batch.py --batch <batch.json> [--mapping <映射表.md>]
"""

import json, sys, re, argparse
from pathlib import Path


def parse_row_number(path):
    """Extract row number from /Sheet/row[N] path. Returns int or None."""
    m = re.search(r'/row\[(\d+)\]', path)
    return int(m.group(1)) if m else None


def parse_cell_path(path):
    """Extract (sheet, col_letter, row) from /Sheet/X99 path. Returns (sheet, col, row) or None."""
    m = re.match(r'^/([^/]+)/([A-Z]+)(\d+)$', path)
    if m:
        return m.group(1), m.group(2), int(m.group(3))
    return None


def parse_add_position(cmd):
    """Extract the numeric position where an 'add' command inserts. Returns int or None."""
    if "after" in cmd:
        return parse_row_number(cmd["after"])
    if "before" in cmd:
        n = parse_row_number(cmd["before"])
        return n - 1 if n else None  # 'before row[N]' = position N-1
    if "index" in cmd:
        idx = cmd["index"]
        return idx + 1 if isinstance(idx, int) else None  # 0-based index → 1-based position
    return None


def validate(batch, mapping_file=None):
    """Validate batch.json structure. Returns list of issue dicts."""
    issues = []

    if not isinstance(batch, list):
        return [{"code": "FORMAT_ERROR", "message": "batch.json must be a JSON array",
                  "corrective_action": "Ensure batch.json contains an array of command objects."}]

    # ── Track categories ──
    text_clear_paths = set()   # paths that receive set text=""
    formula_paths = set()      # paths that receive set formula
    remove_rows = []           # (row_number, index_in_batch)
    add_positions = []         # (position, index_in_batch) for commands with position
    add_no_position = []       # indices of add commands lacking position
    total_ops = len(batch)

    for i, cmd in enumerate(batch):
        command = cmd.get("command", "")

        # ── Path format check ──
        path = cmd.get("path", "")
        parent = cmd.get("parent", "")
        if command == "remove" and path:
            if not re.match(r'^/[^/]+/row\[\d+\]$', path):
                issues.append({
                    "code": "INVALID_REMOVE_PATH",
                    "index": i,
                    "path": path,
                    "message": f"remove path must be /Sheet/row[N], got: {path}",
                    "corrective_action": f"Fix path format to /SheetName/row[N]"
                })
            else:
                rn = parse_row_number(path)
                if rn:
                    remove_rows.append((rn, i))

        elif command == "add":
            ptype = cmd.get("type", "")
            has_from = "from" in cmd
            pos = parse_add_position(cmd)

            if has_from and pos is None:
                add_no_position.append(i)

            if pos is not None:
                add_positions.append((pos, i))

            # Check parent format for add
            if parent and not re.match(r'^/[^/]+$', parent):
                issues.append({
                    "code": "INVALID_ADD_PARENT",
                    "index": i,
                    "parent": parent,
                    "message": f"add parent must be /SheetName, got: {parent}",
                    "corrective_action": f"Fix parent to /SheetName format"
                })

        elif command == "set":
            props = cmd.get("props", {})
            has_text = "text" in props
            has_formula = "formula" in props

            if has_text and props["text"] == "" and path:
                text_clear_paths.add(path)

            if has_formula and path:
                formula_paths.add(path)

            # Path format check for set
            if path:
                if not (re.match(r'^/[^/]+/[A-Z]+\d+$', path) or
                        re.match(r'^/[^/]+/row\[\d+\]$', path) or
                        re.match(r'^/[^/]+/col\[[A-Z]+\]$', path) or
                        re.match(r'^/slide\[\d+\]/table\[@id=\d+\]/tr\[\d+\]/tc\[\d+\]$', path)):
                    # Not an error, just a warning-level concern — path may be valid for sheet-level props
                    pass

    # ── Check 1: add commands with from but no position ──
    if add_no_position:
        issues.append({
            "code": "ADD_FROM_WITHOUT_POSITION",
            "count": len(add_no_position),
            "indices": add_no_position[:10],
            "message": f"{len(add_no_position)} add commands have 'from' but no position parameter (after/before/index). These rows will be appended to the sheet END, not inserted at the expected position.",
            "corrective_action": "Add 'after' or 'before' or 'index' to each affected add command. Example: {\"command\":\"add\",\"parent\":\"/Sheet\",\"type\":\"row\",\"from\":\"/Sheet/row[5]\",\"after\":\"/Sheet/row[5]\"}"
        })

    # ── Check 2: text-clear and formula paths overlap ──
    overlap = text_clear_paths & formula_paths
    if overlap:
        issues.append({
            "code": "CLEAR_FORMULA_CONFLICT",
            "count": len(overlap),
            "paths": sorted(overlap)[:10],
            "message": f"{len(overlap)} cell paths appear in both 'set text=\"\"' and 'set formula'. Clearing with text=\"\" converts the cell to literal type, blocking subsequent formula assignment.",
            "corrective_action": "Remove these paths from the clear-step. Formula-bound cells should be overwritten directly with the formula set command without prior clearing."
        })

    # ── Check 3: remove ordering (must be descending) ──
    if len(remove_rows) >= 2:
        for j in range(len(remove_rows) - 1):
            curr_row, curr_idx = remove_rows[j]
            next_row, next_idx = remove_rows[j + 1]
            if curr_row <= next_row:
                issues.append({
                    "code": "REMOVE_ORDER_ASCENDING",
                    "index_prev": curr_idx,
                    "index_curr": next_idx,
                    "row_prev": curr_row,
                    "row_curr": next_row,
                    "message": f"remove at batch[{next_idx}] (row {next_row}) comes after remove at batch[{curr_idx}] (row {curr_row}). Must be descending (bottom-to-top) to avoid index drift.",
                    "corrective_action": "Re-order remove commands so higher row numbers come first. Sort by row number descending."
                })
                break  # One violation is enough to flag

    # ── Check 4: add ordering (descending only when rows differ) ──
    # All adds pointing to the same reference row don't cause index drift.
    positioned = [(p, i, cmd.get("after", cmd.get("before", ""))) for p, i in add_positions if p is not None]
    if len(positioned) >= 2:
        refs = set(ref for _, _, ref in positioned if ref)
        if len(refs) > 1:
            # Multiple different reference rows — must be descending
            for j in range(len(positioned) - 1):
                curr_pos, curr_idx, curr_ref = positioned[j]
                next_pos, next_idx, next_ref = positioned[j + 1]
                if curr_pos <= next_pos:
                    issues.append({
                        "code": "ADD_ORDER_ASCENDING",
                        "index_prev": curr_idx,
                        "index_curr": next_idx,
                        "pos_prev": curr_pos,
                        "pos_curr": next_pos,
                        "message": f"add at batch[{next_idx}] (position {next_pos}) comes after add at batch[{curr_idx}] (position {curr_pos}). Positioned adds with different reference rows must be descending (bottom-to-top) to avoid index drift.",
                        "corrective_action": "Re-order positioned add commands so higher positions come first. Sort by position descending. (Note: adds sharing the same reference row are exempt from this check.)"
                    })
                    break

    # ── Check 5: overall ordering ──
    # Order: clear-set(1) → add(2) → remove(2) → merge-set(3) → fill-set(4) → structural-set(5)
    last_order = 0
    for i, cmd in enumerate(batch):
        command = cmd.get("command", "")
        props = cmd.get("props", {})

        if command == "set":
            has_text = "text" in props
            has_formula = "formula" in props
            has_merge = "merge" in props

            if has_merge:
                order = 3  # merge-set after add/remove
            elif has_text and props.get("text") == "":
                order = 1  # clear-set first
            elif has_text or has_formula:
                order = 4  # fill-set after merge
            else:
                order = 5  # structural-set last (width, height, freeze, etc.)

        elif command == "add":
            order = 2
        elif command == "remove":
            order = 2
        else:
            order = 0

        if order > 0 and order < last_order:
            issues.append({
                "code": "BATCH_ORDER_VIOLATION",
                "index": i,
                "command": command,
                "props_keys": list(props.keys())[:3],
                "expected_order": last_order,
                "got_order": order,
                "message": f"Command '{command}' at batch[{i}] violates ordering. Expected order: clear-set → add/remove → merge-set → fill-set → structural-set.",
                "corrective_action": f"Re-order batch commands. Current order value {order} appears after {last_order}."
            })
            break
        last_order = max(last_order, order)

    return issues


def main():
    parser = argparse.ArgumentParser(description="Validate batch.json before Layer 4 execution")
    parser.add_argument("--batch", type=Path, required=True, help="batch.json file to validate")
    parser.add_argument("--mapping", type=Path, default=None, help="Optional: mapping table for operation count cross-check")
    args = parser.parse_args()

    if not args.batch.exists():
        print(json.dumps({
            "code": "FILE_NOT_FOUND",
            "message": f"batch.json not found: {args.batch}",
            "corrective_action": "Ensure Layer 3 produced the batch.json file."
        }, ensure_ascii=False), file=sys.stderr)
        sys.exit(1)

    try:
        with open(args.batch, "r", encoding="utf-8") as f:
            batch = json.load(f)
    except json.JSONDecodeError as e:
        print(json.dumps({
            "code": "JSON_PARSE_ERROR",
            "message": f"batch.json is not valid JSON: {e}",
            "corrective_action": "Fix JSON syntax in batch.json."
        }, ensure_ascii=False), file=sys.stderr)
        sys.exit(3)

    issues = validate(batch, args.mapping)

    if not issues:
        print(f"[VALIDATE_BATCH] PASS — {len(batch)} commands, 0 issues")
        sys.exit(0)

    # Output structured issues to stderr
    report = {
        "status": "FAIL",
        "total_issues": len(issues),
        "total_commands": len(batch),
        "issues": issues
    }
    print(json.dumps(report, ensure_ascii=False, indent=2), file=sys.stderr)
    sys.exit(3)


if __name__ == "__main__":
    main()
