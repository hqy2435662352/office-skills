"""Private MOD capture — create or update with backups. Emits structured UTF-8 JSON.

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
    ModIndexEntry,
    ModRuleParseError,
    parse_mod_index,
    parse_mod_rules,
    render_index_row,
    rebuild_index_text,
    replace_index_row,
)

_NAME_RE = re.compile(r"^[A-Za-z0-9_]+$")
_EXIT_OK, _EXIT_ENV, _EXIT_BUSINESS = 0, 1, 3


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
    _BARE_PIPE_RE = re.compile(r"(?<!\\)\|")
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
        path=f"MOD_{req.mod_name}.md", revision=1, visibility="private")


def _build_mod_content(
    name: str, body: str, req: CaptureRequest, n: int, revision: int = 1,
) -> str:
    lines = [
        f"# MOD_{name}\n\n## Purpose\n\n",
        "Private MOD created by table-fill capture.\n",
        f"Revision: {revision}\nVisibility: private\nRule count: {n}\n\n",
        "## Metadata\n\n",
        f"- Scope Signals: {req.scope_signals}\n",
    ]
    if req.aliases:
        lines.append(f"- Aliases: {req.aliases}\n")
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


def _do_create(req: CaptureRequest) -> dict:  # noqa: DICT_OK — JSON emission contract
    _validate_request(req)
    rules = _validate_source(req.source)
    body = req.source.read_text(encoding="utf-8")
    index_path = _mod_index_path()
    index_text = Path(index_path).read_text(encoding="utf-8")
    existing = parse_mod_index(index_text)
    _check_preconditions(req.mod_name, existing, _refs_dir())
    _atomic_write(_mod_file_path(req.mod_name),
                  _build_mod_content(req.mod_name, body, req, len(rules)))
    _atomic_write(index_path, rebuild_index_text(index_text, _build_index_entry(req)))
    return {"action": "create", "mod_name": req.mod_name, "revision": 1,
            "visibility": "private", "rule_count": len(rules),
            "index_updated": True, "scope_signals": req.scope_signals,
            "exit_code": _EXIT_OK}


def _do_update(req: CaptureRequest) -> dict:  # noqa: DICT_OK — JSON emission contract
    """Update an existing MOD — single-user best effort, not atomic concurrency."""
    _validate_request(req)
    rules = _validate_source(req.source)
    body = req.source.read_text(encoding="utf-8")
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
        path=f"MOD_{req.mod_name}.md", revision=new_revision, visibility="private")
    for p in (mod_path, index_path):
        shutil.copy2(p, p + ".bak")
    try:
        new_index = replace_index_row(index_text, req.mod_name, new_entry)
    except ValueError as exc:
        raise CaptureError(str(exc), _EXIT_ENV) from exc
    new_mod = _build_mod_content(req.mod_name, body, req, len(rules), new_revision)
    _atomic_write(mod_path, new_mod)
    try:
        _atomic_write(index_path, new_index)
    except OSError:
        bak = mod_path + ".bak"
        if os.path.isfile(bak):
            shutil.copy2(bak, mod_path)
        raise CaptureError("Index write failed; MOD restored from backup", _EXIT_ENV)
    return {"action": "update", "mod_name": req.mod_name,
            "revision": new_revision, "visibility": "private",
            "rule_count": len(rules), "index_updated": True,
            "scope_signals": req.scope_signals, "exit_code": _EXIT_OK}


# ── CLI ────────────────────────────────────────────────────────────────────


def _emit_json(data: dict, exit_code: int) -> None:
    json.dump(data, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")
    sys.exit(exit_code)


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(
        description="Create a private MOD from a prepared rule-source file.")
    p.add_argument("--source", required=True)
    p.add_argument("--mod-name", required=True)
    p.add_argument("--action", required=True, choices=["create", "update"])
    p.add_argument("--scope-signals", required=True)
    p.add_argument("--aliases", default="")
    p.add_argument("--exclusion-signals", default="")
    args = p.parse_args(argv)

    try:
        req = CaptureRequest(
            mod_name=args.mod_name, action=args.action,
            source=Path(args.source).resolve(),
            scope_signals=args.scope_signals,
            aliases=args.aliases, exclusion_signals=args.exclusion_signals,
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
