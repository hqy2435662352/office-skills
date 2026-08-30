"""Typed, immutable MOD catalog models and parsers.

Seven-column MOD_INDEX.md parser and six-column MOD rule table parser.
Single source consumed by mod_nominate.py (nomination resolution) and
mod_capture.py (private MOD create/update).

All parsing is read-only: functions accept strings and return typed,
immutable objects. No filesystem access, no mutation, no state.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Sequence

# ── Immutable models ──────────────────────────────────────────────────────


class ModRuleParseError(ValueError):
    """Six-column rule table could not be parsed — no valid rows or malformed."""


@dataclass(frozen=True, slots=True)
class ModIndexEntry:
    """One row from the seven-column Registered MODs table in MOD_INDEX.md.

    Columns: MOD Name, Aliases, Scope Signals, Exclusion Signals,
    Path, Revision, Visibility.
    """

    mod_name: str
    aliases: str  # comma-separated alias names, may be empty
    scope_signals: str
    exclusion_signals: str
    path: str  # relative from table-fill/references/, e.g. "MOD_sales.md"
    revision: int
    visibility: str  # "public" or "private"

    def alias_list(self) -> list[str]:
        """Return non-empty, stripped alias names."""
        return [a.strip() for a in self.aliases.split(",") if a.strip()]


@dataclass(frozen=True, slots=True)
class ModRule:
    """One row from the six-column rule table in a MOD Markdown file.

    Columns: Rule ID, Group, Gate, Description, Applies to, Notes.
    """

    rule_id: str
    group: str
    gate: str  # "mod_gate" or "execution_gate"
    description: str
    applies_to: str
    notes: str


# ── MOD_INDEX.md parsing (seven-column catalog) ───────────────────────────

_INDEX_COLUMNS = 7
# Column names for the seven-column schema (ordered):
# MOD Name | Aliases | Scope Signals (+) | Exclusion Signals (-) | Path | Revision | Visibility

_SECTION_HEADING_RE = re.compile(r"^##\s+Registered MODs")
_SEPARATOR_RE = re.compile(r"^\|[\s\-:|]+\|$")
_PLACEHOLDER_RE = re.compile(r"no\s+MOD.*registered", re.IGNORECASE)


def parse_mod_index(text: str) -> list[ModIndexEntry]:
    """Parse the seven-column Registered MODs table from MOD_INDEX.md.

    Returns a list of ModIndexEntry objects. Rows with fewer than 7 columns,
    placeholder rows (e.g. "*(no MODs registered)*"), and separator rows are
    silently skipped. An empty index returns an empty list.
    """
    in_section = False
    in_table = False
    entries: list[ModIndexEntry] = []

    for line in text.splitlines():
        stripped = line.strip()

        if not in_section and _SECTION_HEADING_RE.match(stripped):
            in_section = True
            continue

        if not in_section:
            continue

        # Stop at the next heading (end of section)
        if stripped.startswith("## ") and "Registered MODs" not in stripped:
            break

        # Detect table header row
        if not in_table and stripped.startswith("|") and "MOD Name" in stripped:
            in_table = True
            continue

        if not in_table:
            continue

        # End of table (blank line or non-table line)
        if not stripped.startswith("|"):
            break

        # Skip separator rows
        if _SEPARATOR_RE.match(stripped):
            continue

        # Skip placeholder rows
        if _PLACEHOLDER_RE.search(stripped):
            continue

        # Data row: split on unescaped pipes — `\|` is an escaped literal pipe
        # inside a signal cell (e.g. sheet_marker::三三三\|333). Interior empty
        # cells (| |) must be preserved as empty strings.
        cells = [c.strip().replace(r"\|", "|")
                 for c in re.split(r"(?<!\\)\|", stripped.strip("|"))]
        if len(cells) < _INDEX_COLUMNS:
            continue

        # Parse revision as int with fallback
        rev_str = cells[5] if cells[5] else "0"
        try:
            revision = int(rev_str)
        except ValueError:
            revision = 0

        entry = ModIndexEntry(
            mod_name=cells[0],
            aliases=cells[1] if len(cells) > 1 and cells[1] else "",
            scope_signals=cells[2] if len(cells) > 2 and cells[2] else "",
            exclusion_signals=cells[3] if len(cells) > 3 and cells[3] else "",
            path=cells[4] if len(cells) > 4 and cells[4] else "",
            revision=revision,
            visibility=cells[6] if len(cells) > 6 and cells[6] else "private",
        )
        entries.append(entry)

    return entries


def render_index_row(entry: ModIndexEntry) -> str:
    """Render a ModIndexEntry to a seven-column markdown table row string."""
    return (
        f"| {entry.mod_name} "
        f"| {entry.aliases} "
        f"| {entry.scope_signals} "
        f"| {entry.exclusion_signals} "
        f"| {entry.path} "
        f"| {entry.revision} "
        f"| {entry.visibility} |"
    )


def render_placeholder_row() -> str:
    """Render the placeholder row used when no MODs are registered."""
    return (
        "| *(no MODs registered)* "
        "|  "
        "|  "
        "|  "
        "|  "
        "|  "
        "|  |"
    )


def resolve_by_name_or_alias(
    entries: Sequence[ModIndexEntry], name: str
) -> ModIndexEntry | None:
    """Case-insensitive resolution of a MOD by its canonical name or any alias.

    Returns the first matching ModIndexEntry, or None if no match.
    """
    name_lower = name.strip().lower()
    if not name_lower:
        return None
    for entry in entries:
        if entry.mod_name.lower() == name_lower:
            return entry
        for alias in entry.alias_list():
            if alias.lower() == name_lower:
                return entry
    return None


# ── Index text mutation helpers (used by mod_capture.py) ───────────────────


def rebuild_index_text(full: str, new: ModIndexEntry) -> str:
    """Append a new row to the Registered MODs table, preserving existing rows."""
    lines = full.splitlines(keepends=True)
    result: list[str] = []
    in_sec = in_tbl = emitted = False
    for line in lines:
        s = line.strip()
        if not in_sec:
            if s.startswith("## ") and "Registered MODs" in s:
                in_sec = True
            result.append(line)
            continue
        if not in_tbl:
            result.append(line)
            if s.startswith("|") and "MOD Name" in s:
                in_tbl = True
            continue
        if not s.startswith("|"):
            if not emitted:
                result.append(render_index_row(new) + "\n")
                result.append("\n")
                emitted = True
            result.append(line)
            continue
        # Table row: skip separator and placeholder, keep data rows
        if "---" in s and s.count("|") >= 3:
            continue
        if "no MOD" in s.lower() and "registered" in s.lower():
            continue
        result.append(line)
    if in_tbl and not emitted:
        result.append(render_index_row(new) + "\n")
        result.append("\n")
    return "".join(result)


def replace_index_row(
    full: str, mod_name: str, new_entry: ModIndexEntry,
) -> str:
    """Replace the row matching mod_name in the index text using a regex sub."""
    pattern = re.compile(
        r"^\|\s*" + re.escape(mod_name) + r"\s*\|.*$",
        re.MULTILINE | re.IGNORECASE)
    result, n = pattern.subn(render_index_row(new_entry), full)
    if n == 0:
        raise ValueError(f"Row for '{mod_name}' not found in index")
    return result


# ── MOD rule table parsing (six columns) ──────────────────────────────────

_RULE_COLUMNS = frozenset(
    {"Rule ID", "Group", "Gate", "Description", "Applies to", "Notes"}
)
# Valid Rule ID: starts with uppercase letter, then uppercase letters / digits / hyphens.
# Accepts both short (R01) and long (RTE-001) formats per MOD_TEMPLATE.md.
# Rejects garbage rows where the first column is not a plausible Rule ID.
_RULE_ID_RE = re.compile(r"^[A-Z][A-Z0-9-]*$")


def parse_mod_rules(text: str) -> list[ModRule]:
    """Parse the six-column rule table from a MOD Markdown file.

    Returns a list of ModRule objects in file order.
    Raises ValueError if no valid rule rows are found.
    """
    lines = text.splitlines()
    in_table = False
    rules: list[ModRule] = []

    for line in lines:
        stripped = line.strip()

        if not in_table:
            if stripped.startswith("|") and "Rule ID" in stripped:
                cells = [c.strip() for c in stripped.split("|")]
                if cells and cells[0] == "":
                    cells.pop(0)
                if cells and cells[-1] == "":
                    cells.pop()
                if _RULE_COLUMNS.issubset(set(cells)):
                    in_table = True
                    continue
        else:
            if not stripped.startswith("|"):
                break

            if _SEPARATOR_RE.match(stripped):
                continue

            cells = [c.strip() for c in stripped.split("|")]
            if cells and cells[0] == "":
                cells.pop(0)
            if cells and cells[-1] == "":
                cells.pop()
            if len(cells) < 5:
                continue

            # Skip rows whose first column is not a valid Rule ID format
            if not _RULE_ID_RE.match(cells[0]):
                continue

            rule = ModRule(
                rule_id=cells[0],
                group=cells[1] if len(cells) > 1 else "",
                gate=cells[2] if len(cells) > 2 else "",
                description=cells[3] if len(cells) > 3 else "",
                applies_to=cells[4] if len(cells) > 4 else "",
                notes=cells[5] if len(cells) > 5 else "",
            )
            rules.append(rule)

    if not rules:
        raise ModRuleParseError(
            "No rule rows found in MOD markdown — expected a six-column rule table"
        )

    return rules


# ── Attention Map parsing (spec §5.2 — presentation/authoring metadata) ────

# Section regex mirrors mod_nominate.parse_mod_file's section extraction
# (`## Name[^\n]*\n(.*?)(?=\n## |\Z)` with re.S): capture everything after the
# header line up to the next `## ` heading or end of text.
_ATTENTION_MAP_SECTION_RE = re.compile(
    r"## Attention Map[^\n]*\n(.*?)(?=\n## |\Z)", re.S)
# Group lines follow the Applicability `- key: value` convention:
# `- <group>: <ID>, <ID>, ...`. Group is a single [\w_]+ token — the parser
# does NOT validate group names (the closed set is the capture validator's job).
_MAP_LINE_RE = re.compile(r"^\s*-\s*([\w_]+):\s*(.*?)\s*$")


class AttentionMapParseError(ValueError):
    """A content line inside the `## Attention Map` section is malformed.

    Carries the 1-based file line number and the offending line text so the
    capture validator can convert the failure into a CaptureError with
    line-scoped corrective hints. The parser NEVER silently ignores a
    non-blank line: if it cannot be understood, it is refused here.
    """

    def __init__(self, message: str, line_number: int, text: str) -> None:
        super().__init__(message)
        self.line_number = line_number
        self.text = text


def parse_attention_map_lines(text: str) -> list[tuple[str, list[str]]] | None:
    """Line-level Attention Map parse — the ordered view capture needs.

    Returns a list of ``(group, ids)`` tuples in file order, or ``None`` when
    the MOD has no ``## Attention Map`` section (legacy MOD — capture-time
    validation must stay disabled). Raises :class:`AttentionMapParseError` on
    the first malformed content line.

    Strictness (documented contract): blank and whitespace-only lines inside
    the section are skipped; every other line must match
    ``- <group>: <ID>, <ID>, ...``. A line with nothing after the colon, a
    line that is not a ``- group:`` list item, or an ID list containing empty
    elements (e.g. a double comma) is malformed and raises — nothing is
    silently dropped, because a validator cannot reject lines it never sees.
    """
    m = _ATTENTION_MAP_SECTION_RE.search(text)
    if m is None:
        return None  # legacy MOD — no Attention Map section
    # 1-based line number of the `## Attention Map` header in the full text.
    header_line = text.count("\n", 0, m.start()) + 1
    result: list[tuple[str, list[str]]] = []
    for offset, raw in enumerate(m.group(1).splitlines()):
        line_no = header_line + 1 + offset
        stripped = raw.strip()
        if not stripped:
            continue  # blank lines between group lines are fine
        mm = _MAP_LINE_RE.match(raw)
        if mm is None:
            raise AttentionMapParseError(
                f"line {line_no}: {stripped!r} must match "
                "`- <group>: <ID>, <ID>, ...`",
                line_no, stripped)
        group, ids_raw = mm.group(1), mm.group(2).strip()
        if not ids_raw:
            raise AttentionMapParseError(
                f"line {line_no}: {stripped!r} has no Rule IDs after the "
                "colon — must match `- <group>: <ID>, <ID>, ...`",
                line_no, stripped)
        ids = [i.strip() for i in ids_raw.split(",")]
        if any(not i for i in ids):
            raise AttentionMapParseError(
                f"line {line_no}: {stripped!r} contains an empty Rule ID "
                "(e.g. a double comma) — must match "
                "`- <group>: <ID>, <ID>, ...`",
                line_no, stripped)
        result.append((group, ids))
    return result


def parse_attention_map(text: str) -> dict[str, list[str]] | None:
    """Parse a MOD's ``## Attention Map`` section into ``{group: [IDs]}``.

    Returns ``None`` when the section is absent — a legacy MOD, for which
    capture-time validation stays disabled. An EMPTY section returns ``{}``:
    the section is present but assigns no rules to any group, which ENABLES
    validation and then fails the coverage check at capture time.

    The dict preserves the file order of group lines (plain dict keeps
    insertion order). Duplicate group lines collapse in this dict view —
    the group-unique and in-group-duplicate checks need the per-line view,
    so capture validation uses :func:`parse_attention_map_lines` instead.

    Deliberately dumb: groups are not validated against the closed set
    (resolve/map/transform/validate — the capture validator's job) and no
    priority/dependency/enforcement/condition fields are parsed.
    """
    lines = parse_attention_map_lines(text)
    if lines is None:
        return None
    out: dict[str, list[str]] = {}
    for group, ids in lines:
        out.setdefault(group, []).extend(ids)
    return out


# ── Dict conversion helpers (for mod_nominate.py compatibility) ────────────


def index_entry_to_dict(entry: ModIndexEntry) -> dict:
    """Convert a ModIndexEntry to the dict shape expected by mod_nominate.py.

    Key mapping: mod_name→name, scope_signals→scope, exclusion_signals→exclusion.
    """
    return {
        "name": entry.mod_name,
        "aliases": entry.aliases,
        "scope": entry.scope_signals,
        "exclusion": entry.exclusion_signals,
        "path": entry.path,
        "revision": entry.revision,
        "visibility": entry.visibility,
    }


def rule_to_dict(rule: ModRule) -> dict:
    """Convert a ModRule to the dict shape expected by mod_nominate.py.

    Key mapping: rule_id→id.
    """
    return {
        "id": rule.rule_id,
        "group": rule.group,
        "gate": rule.gate,
        "description": rule.description,
        "applies_to": rule.applies_to,
        "notes": rule.notes,
    }
