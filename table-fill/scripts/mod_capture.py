"""MOD capture — create or update with backups. Emits structured UTF-8 JSON.

Exit codes: 0=success, 1=environment, 3=business validation.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

_script_dir = os.path.dirname(os.path.abspath(__file__))
if _script_dir not in sys.path:
    sys.path.insert(0, _script_dir)

from _mod_catalog import (  # noqa: E402
    AttentionMapParseError,
    ModIndexEntry,
    ModRuleParseError,
    parse_attention_map_lines,
    parse_mod_index,
    parse_mod_rules,
    render_index_row,
    rebuild_index_text,
    replace_index_row,
)

_NAME_RE = re.compile(r"^[A-Za-z0-9_]+$")
# Matches a bare pipe '|' that is NOT preceded by a backslash.
# Handles double-backslash correctly: '\\|' (literal backslash + bare pipe)
# still matches because the regex skips past '\\\\' pairs before checking.
_BARE_PIPE_RE = re.compile(r"(?<!\\)(?:\\\\)*\|")
_EXIT_OK, _EXIT_ENV, _EXIT_BUSINESS = 0, 1, 3

# Attention Map (spec §5.2): closed set of attention groups, in the relative
# reading order MOD files must follow (subsets allowed, reordering refused).
_ATTENTION_GROUPS = ("resolve", "map", "transform", "validate")
_ATTENTION_GROUP_ORDER = {g: i for i, g in enumerate(_ATTENTION_GROUPS)}
# Section extraction mirroring _mod_catalog's Attention Map regex — used only
# for the minimal "declared Runtime Core must be non-empty" check.
_RUNTIME_CORE_SECTION_RE = re.compile(
    r"## Runtime Core[^\n]*\n(.*?)(?=\n## |\Z)", re.S)


class CaptureError(Exception):
    """Signals a capture validation/operation failure with an exit code."""

    def __init__(self, message: str, exit_code: int = _EXIT_ENV) -> None:
        super().__init__(message)
        self.exit_code = exit_code


@dataclass(frozen=True, slots=True)
class CaptureRequest:
    """Parsed CLI input for one capture operation. Immutable."""
    mod_name: str
    action: str
    source: Path
    scope_signals: str
    aliases: str
    exclusion_signals: str
    visibility: str = "private"


def _skill_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _refs_dir() -> str:
    return os.path.join(_skill_root(), "references")


def _mod_index_path() -> str:
    return os.path.join(_refs_dir(), "MOD_INDEX.md")


def _mod_file_path(name: str) -> str:
    return os.path.join(_refs_dir(), f"MOD_{name}.md")


# ── Validation ────────────────────────────────────────────────────────────


def _validate_name(name: str) -> None:
    if not name or not _NAME_RE.match(name):
        raise CaptureError(
            f"Invalid MOD name '{name}': must match [A-Za-z0-9_]+", _EXIT_ENV)


def _validate_source(source: Path) -> list:
    if not source.is_file():
        raise CaptureError(f"Source file not found: {source}", _EXIT_ENV)
    try:
        text = source.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError) as exc:
        raise CaptureError(f"Cannot read source: {source} ({exc})", _EXIT_ENV) from exc
    try:
        rules = parse_mod_rules(text)
    except ModRuleParseError as exc:
        raise CaptureError(str(exc), _EXIT_BUSINESS) from exc
    if not rules:
        raise CaptureError("No valid rule rows in source", _EXIT_BUSINESS)
    return rules


def _validate_request(req: CaptureRequest) -> None:
    _validate_name(req.mod_name)
    if req.action not in ("create", "update"):
        raise CaptureError(f"Unsupported action '{req.action}'", _EXIT_ENV)
    if not req.scope_signals or not req.scope_signals.strip():
        raise CaptureError("Scope signals must be non-empty", _EXIT_ENV)
    # Reject pipe injection: '|' in table columns breaks markdown parsing.
    # Allow escaped pipes ('\|') which are a valid markdown table convention
    # for literal pipe characters inside cell values (e.g. sheet_marker::X\|Y).
    for field_name, value in [
        ("aliases", req.aliases),
        ("scope_signals", req.scope_signals),
        ("exclusion_signals", req.exclusion_signals),
    ]:
        if _BARE_PIPE_RE.search(value):
            raise CaptureError(
                f"Invalid character '|' in {field_name}: "
                "bare pipe characters break markdown table parsing; "
                "use '\\|' for a literal pipe",
                _EXIT_ENV,
            )


def _validate_attention_metadata(text: str, rules: list) -> None:
    """Capture-time hard validation of the Attention Map + Runtime Core.

    MUST run after ``_validate_source`` and before any file write, on the
    FINAL candidate body (the full source text as it will be written), in
    both create and update paths. It receives the full source text and the
    rule list produced by ``_validate_source`` (which is
    ``parse_mod_rules(text)`` on the same text).

    Enabled ONLY when the MOD declares an ``## Attention Map`` section
    (``parse_attention_map_lines`` returns non-None — None ⇔
    ``parse_attention_map`` returns None). Legacy MODs without the section
    return immediately: behaviour is byte-for-byte unchanged.

    Checks (spec §5.2): 1 malformed line, 2 dangling ID, 3 coverage miss,
    4 closed group set, 5 group unique, 6 relative resolve→map→transform→
    validate order, 7 in-group duplicate; 8 cross-group duplicate is legal.
    Plus the minimal Runtime Core check: a declared section must be non-empty.
    All failures raise CaptureError with exit code 3 and corrective hints.
    Violations are aggregated into ONE message (single exit 3) — malformed
    lines are the exception: the dumb parser refuses them first, and the
    parse error is converted with line info.
    """
    try:
        lines = parse_attention_map_lines(text)
    except AttentionMapParseError as exc:
        raise CaptureError(
            f"Attention Map validation failed:\n"
            f"  - line {exc.line_number}: '{exc.text}' must match "
            "`- <group>: <ID>, <ID>, ...`",
            _EXIT_BUSINESS,
        ) from exc
    if lines is None:
        return  # legacy MOD — no Attention Map, no new validation

    problems: list[str] = []
    rule_id_set = {r.rule_id for r in rules}

    # 2. Dangling: every referenced Rule ID must exist in the rule table.
    referenced = [i for _, ids in lines for i in ids]
    dangling = sorted({i for i in referenced if i not in rule_id_set})
    if dangling:
        problems.append(
            "ID not found in rule table: " + ", ".join(dangling)
            + " (check for typos / rule removed)")

    # 3. Coverage: every rule-table Rule ID appears in ≥1 group.
    referenced_set = set(referenced)
    missing = [r.rule_id for r in rules if r.rule_id not in referenced_set]
    if missing:
        problems.append("rules missing from any group: " + ", ".join(missing))

    # 4. Closed set: group ∈ {resolve, map, transform, validate} (deduped).
    unknown: list[str] = []
    for group, _ in lines:
        if group not in _ATTENTION_GROUP_ORDER and group not in unknown:
            unknown.append(group)
    if unknown:
        problems.append(
            "unknown group '" + "', '".join(unknown) + "'; "
            "allowed: resolve/map/transform/validate")

    # 5. Group unique: each group at most one line (no append/override).
    seen_groups: list[str] = []
    dup_groups: list[str] = []
    for group, _ in lines:
        if group in seen_groups and group not in dup_groups:
            dup_groups.append(group)
        if group not in seen_groups:
            seen_groups.append(group)
    for group in dup_groups:
        problems.append(f"group '{group}' appears twice, merge into one line")

    # 6. Relative order: resolve → map → transform → validate (subsets OK).
    #    Only known groups participate — unknown ones are already reported above.
    known = [(group, i) for group, i in lines if group in _ATTENTION_GROUP_ORDER]
    positions = [_ATTENTION_GROUP_ORDER[group] for group, _ in known]
    for i in range(1, len(positions)):
        if positions[i] < positions[i - 1]:
            problems.append(
                "groups must follow resolve → map → transform → validate; "
                f"found '{known[i][0]}' after '{known[i - 1][0]}'")
            break  # one ordering failure is enough to report

    # 7. In-group duplicate: same Rule ID twice within one group → reject.
    for group, ids in lines:
        dup_ids = sorted({i for i in ids if ids.count(i) > 1})
        if dup_ids:
            problems.append(
                f"ID repeated within group '{group}': " + ", ".join(dup_ids))

    # Minimal Runtime Core check: a present section must be non-empty.
    # No heavy checks (no counts / length / keywords) per spec §5.1.
    rc = _RUNTIME_CORE_SECTION_RE.search(text)
    if rc is not None and not rc.group(1).strip():
        problems.append(
            "`## Runtime Core` section is empty — a declared Runtime Core "
            "must contain the business world-model guidance")

    if problems:
        raise CaptureError(
            "Attention Map validation failed:\n  - " + "\n  - ".join(problems),
            _EXIT_BUSINESS,
        )

# ── Decontamination patterns ──────────────────────────────────────────────

# Forbidden patterns for public MODs: single-run facts must not appear.
_DECONTAMINATION_PATTERNS = [
    (re.compile(r"\b(?:TCL|FRESH)\b"), "customer_name",
     "Specific customer name (TCL/FRESH)"),
    (re.compile(r"三三三|333"), "sheet_marker",
     "Specific sheet marker (三三三/333)"),
    (re.compile(r"\d{4}-\d{2}-\d{2}"), "date",
     "Specific date (YYYY-MM-DD)"),
    (re.compile(r"\d{4}\.\d{1,2}\.\d{1,2}"), "date",
     "Specific date (YYYY.M.D)"),
    (re.compile(r"-?\d+(?:\.\d+)?%"), "percentage",
     "Specific percentage"),
    (re.compile(r"(?:USD|RMB|CNY)\s*\d"), "currency",
     "Specific currency amount"),
    (re.compile(r"\d+(?:\.\d+)?\s*(?:米|meter)"), "fixed_number",
     "Specific measurement (e.g. 5 米)"),
    (re.compile(r"\d+\s*[~\-至]\s*\d+\s*(?:米|meter)"), "fixed_number",
     "Specific measurement range"),
]


def _check_decontamination(content: str) -> tuple[bool, list[dict]]:
    """Check MOD content for decontamination violations (public MODs only).

    Returns (has_violations, violations) where violations is a list of dicts
    with keys: pattern_type, description, match.
    """
    violations = []
    for pattern, pattern_type, description in _DECONTAMINATION_PATTERNS:
        match = pattern.search(content)
        if match:
            violations.append({
                "pattern_type": pattern_type,
                "description": description,
                "match": match.group(),
            })
    return bool(violations), violations


def _check_preconditions(
    mod_name: str, existing: list[ModIndexEntry], refs_dir: str,
) -> None:
    for entry in existing:
        if entry.mod_name.lower() == mod_name.lower():
            raise CaptureError(
                f"MOD '{mod_name}' already registered in MOD_INDEX.md", _EXIT_ENV)
    orphan = _mod_file_path(mod_name)
    if os.path.isfile(orphan):
        raise CaptureError(f"Orphan MOD file exists: {orphan}", _EXIT_BUSINESS)


# ── Build helpers ─────────────────────────────────────────────────────────


def _build_index_entry(req: CaptureRequest) -> ModIndexEntry:
    return ModIndexEntry(
        mod_name=req.mod_name, aliases=req.aliases,
        scope_signals=req.scope_signals,
        exclusion_signals=req.exclusion_signals,
        path=f"MOD_{req.mod_name}.md", revision=1, visibility=req.visibility)


_METADATA_SECTION_RE = re.compile(r"## Metadata[^\n]*\n(.*?)(?=\n## |\Z)", re.S)
_DISPLAY_NAME_RE = re.compile(r"^\s*-\s*Display Name:\s*(.+?)\s*$", re.MULTILINE)


def _extract_display_name(text: str) -> str:
    """Return the existing MOD's `Display Name` metadata entry ('' if absent).

    Keeps the nomination-card Chinese label across an update: the rebuilt
    header must not silently drop it, and the fix belongs in the capture
    path (the stored file must equal the reviewed candidate), not in a
    post-capture edit.
    """
    m = _METADATA_SECTION_RE.search(text)
    if m is None:
        return ""
    dm = _DISPLAY_NAME_RE.search(m.group(1))
    return dm.group(1).strip() if dm else ""


def _build_mod_content(
    name: str, body: str, req: CaptureRequest, n: int, revision: int = 1,
    display_name: str = "",
) -> str:
    """Assemble the final stored MOD text = generated header + candidate body.

    ``display_name`` is carried over from the existing registered MOD on
    update (see ``_extract_display_name``) so the rebuilt header keeps the
    nomination-card Chinese label — the final stored file must equal the
    reviewed candidate, never a post-capture patch.
    """
    lines = [
        f"# MOD_{name}\n\n## Purpose\n\n",
        "MOD created by table-fill capture.\n",
        f"Revision: {revision}\nVisibility: {req.visibility}\nRule count: {n}\n\n",
        "## Metadata\n\n",
        f"- Scope Signals: {req.scope_signals}\n",
    ]
    if req.aliases:
        lines.append(f"- Aliases: {req.aliases}\n")
    if display_name:
        lines.append(f"- Display Name: {display_name}\n")
    if req.exclusion_signals:
        lines.append(f"- Exclusion Signals: {req.exclusion_signals}\n")
    lines.append("\n")
    lines.append(body)
    return "".join(lines)


def _atomic_write(path: str, content: str) -> None:
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".tmp_mod_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
        os.replace(tmp, path)
    except Exception:  # noqa: BROAD_EXCEPT_OK — temp file cleanup regardless of error type
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ── Action ────────────────────────────────────────────────────────────────


def _enforce_decontamination(req: CaptureRequest) -> None:
    """Reject public MOD sources carrying single-run facts (fail-closed).

    Runs before any file mutation. Private sources are exempt (private MODs
    may carry customer domain facts); only an explicit ``--visibility public``
    triggers the check.
    """
    if req.visibility != "public":
        return
    source_text = req.source.read_text(encoding="utf-8")
    has_violations, violations = _check_decontamination(source_text)
    if has_violations:
        violation_msgs = [f"  - [{v['pattern_type']}] {v['description']}: '{v['match']}'"
                          for v in violations]
        raise CaptureError(
            f"Decontamination violations in public MOD source:\n" +
            "\n".join(violation_msgs),
            _EXIT_BUSINESS,
        )


def _do_create(req: CaptureRequest) -> dict:  # noqa: DICT_OK — JSON emission contract
    _validate_request(req)
    rules = _validate_source(req.source)
    text = req.source.read_text(encoding="utf-8")
    _validate_attention_metadata(text, rules)
    _enforce_decontamination(req)
    body = text
    index_path = _mod_index_path()
    index_text = Path(index_path).read_text(encoding="utf-8")
    existing = parse_mod_index(index_text)
    _check_preconditions(req.mod_name, existing, _refs_dir())
    _atomic_write(_mod_file_path(req.mod_name),
                  _build_mod_content(req.mod_name, body, req, len(rules)))
    _atomic_write(index_path, rebuild_index_text(index_text, _build_index_entry(req)))
    return {"action": "create", "mod_name": req.mod_name, "revision": 1,
            "visibility": req.visibility, "rule_count": len(rules),
            "index_updated": True, "scope_signals": req.scope_signals,
            "exit_code": _EXIT_OK}


def _do_update(req: CaptureRequest) -> dict:  # noqa: DICT_OK — JSON emission contract
    """Update an existing MOD — single-user best effort, not atomic concurrency."""
    _validate_request(req)
    rules = _validate_source(req.source)
    text = req.source.read_text(encoding="utf-8")
    _validate_attention_metadata(text, rules)
    _enforce_decontamination(req)
    body = text
    index_path = _mod_index_path()
    index_text = Path(index_path).read_text(encoding="utf-8")
    existing = parse_mod_index(index_text)
    try:
        match = next(e for e in existing
                     if e.mod_name.lower() == req.mod_name.lower())
    except StopIteration:
        raise CaptureError(
            f"MOD '{req.mod_name}' not found in MOD_INDEX.md", _EXIT_BUSINESS)
    mod_path = _mod_file_path(req.mod_name)
    if not os.path.isfile(mod_path):
        raise CaptureError(f"MOD file missing: {mod_path}", _EXIT_BUSINESS)
    new_revision = match.revision + 1
    new_entry = ModIndexEntry(
        mod_name=req.mod_name, aliases=req.aliases,
        scope_signals=req.scope_signals, exclusion_signals=req.exclusion_signals,
        path=f"MOD_{req.mod_name}.md", revision=new_revision, visibility=req.visibility)
    for p in (mod_path, index_path):
        shutil.copy2(p, p + ".bak")
    try:
        new_index = replace_index_row(index_text, req.mod_name, new_entry)
    except ValueError as exc:
        raise CaptureError(str(exc), _EXIT_ENV) from exc
    existing_text = Path(mod_path).read_text(encoding="utf-8")
    display_name = _extract_display_name(existing_text)
    new_mod = _build_mod_content(req.mod_name, body, req, len(rules),
                                 new_revision, display_name=display_name)
    _atomic_write(mod_path, new_mod)
    try:
        _atomic_write(index_path, new_index)
    except OSError:
        bak = mod_path + ".bak"
        if os.path.isfile(bak):
            shutil.copy2(bak, mod_path)
        raise CaptureError("Index write failed; MOD restored from backup", _EXIT_ENV)
    return {"action": "update", "mod_name": req.mod_name,
            "revision": new_revision, "visibility": req.visibility,
            "rule_count": len(rules), "index_updated": True,
            "scope_signals": req.scope_signals, "exit_code": _EXIT_OK}


# ── CLI ────────────────────────────────────────────────────────────────────


def _emit_json(data: dict, exit_code: int) -> None:
    json.dump(data, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")
    sys.exit(exit_code)


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(
        description="Create or update a MOD from a prepared rule-source file.")
    p.add_argument("--source", required=True)
    p.add_argument("--mod-name", required=True)
    p.add_argument("--action", required=True, choices=["create", "update"])
    p.add_argument("--scope-signals", required=True)
    p.add_argument("--aliases", default="")
    p.add_argument("--exclusion-signals", default="")
    p.add_argument("--visibility", default="private", choices=["private", "public"],
                   help="MOD visibility (default: private). "
                        "Public MODs are checked for decontamination violations.")
    args = p.parse_args(argv)

    try:
        req = CaptureRequest(
            mod_name=args.mod_name, action=args.action,
            source=Path(args.source).resolve(),
            scope_signals=args.scope_signals,
            aliases=args.aliases, exclusion_signals=args.exclusion_signals,
            visibility=args.visibility,
        )
    except Exception as exc:
        _emit_json({"error": f"Invalid arguments: {exc}", "exit_code": _EXIT_ENV},
                   _EXIT_ENV)

    try:
        if req.action == "create":
            result = _do_create(req)
        else:
            result = _do_update(req)
        _emit_json(result, _EXIT_OK)
    except CaptureError as exc:
        _emit_json({"error": str(exc), "exit_code": exc.exit_code}, exc.exit_code)
    except Exception as exc:  # noqa: BROAD_EXCEPT_OK — top-level boundary, emits structured JSON
        _emit_json({"error": f"Unexpected: {exc}", "exit_code": _EXIT_ENV},
                   _EXIT_ENV)


if __name__ == "__main__":
    main()
