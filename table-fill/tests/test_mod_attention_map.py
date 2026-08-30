"""Attention Map parser + capture-time hard validation (issue #02).

Covers:
  1. ``parse_attention_map`` / ``parse_attention_map_lines`` unit behaviour:
     section extraction, dict order, None vs {} semantics, and the
     no-silent-ignore strictness (malformed lines raise with line info).
  2. Capture-time validation driven through ``_do_create`` / ``_do_update``:
     every rejection case (malformed line / dangling / coverage miss /
     unknown group / duplicate group line / reordered groups / in-group
     duplicate / empty Runtime Core) fails with CaptureError exit 3.
  3. Positive cases: cross-group duplication, group subsets in order,
     Runtime Core with content.
  4. HARD compatibility regression: create AND update a MOD with NO
     Attention Map and NO Runtime Core succeeds exactly as before.
  5. Ticket-01 enablement: update of a MOD WITH a valid map + Runtime Core
     succeeds (rule count preserved, revision increments).

HYGIENE: every capture-enforcement test runs against a TEMPORARY capture
root (mock-patched mod_capture path helpers) — these tests NEVER mutate the
live ``references/`` directory (same discipline as test_mod_roundtrip.py /
test_mod_decontamination.py).

Run with:
  python -m pytest table-fill/tests/test_mod_attention_map.py -q
  python -m unittest tests.test_mod_attention_map
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SKILL_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS_DIR = SKILL_ROOT / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import mod_capture  # noqa: E402
from mod_capture import (  # noqa: E402
    CaptureError,
    CaptureRequest,
    _do_create,
    _do_update,
)
from _mod_catalog import (  # noqa: E402
    AttentionMapParseError,
    parse_attention_map,
    parse_attention_map_lines,
    parse_mod_rules,
)

_REFS_DIR = SKILL_ROOT / "references"


# ── Helpers ────────────────────────────────────────────────────────────────


def _rules_table(*rule_rows: str) -> str:
    """Build a valid six-column rule table with header and at least one rule."""
    lines = [
        "| Rule ID | Group | Gate | Description | Applies to | Notes |",
        "|---------|-------|------|-------------|------------|-------|",
    ]
    if not rule_rows:
        rule_rows = ("| R01 | mapping | mod_gate | Test rule description. | * | |",)
    lines.extend(rule_rows)
    return "\n".join(lines) + "\n"


def _mod_source(rules_text: str, attention_lines=None, runtime_core=None) -> str:
    """Build a MOD source body.

    attention_lines=None → no ``## Attention Map`` section (legacy MOD);
    an empty list → section present but empty ({} semantics). The rules
    table lives under a following ``## 业务场景上下文`` heading so the
    Attention Map section regex stops at the section boundary (a bare table
    would leak into the map body and read as map content lines).
    """
    parts = ["# MOD test\n\n## Metadata\n\n- Scope Signals: s::1\n\n"]
    if runtime_core is not None:
        parts.append(f"## Runtime Core\n\n{runtime_core}\n")
    if attention_lines is not None:
        parts.append("## Attention Map\n\n" + "\n".join(attention_lines) + "\n")
    parts.append("## 业务场景上下文\n\n")
    parts.append(rules_text)
    return "".join(parts)


# 11 rules matching the spec example map; every referenced ID exists and
# every rule is covered by exactly one group (observed order collapses later).
_RULES = _rules_table(
    "| SRC-001 | mapping | mod_gate | Source authority separation. | * | |",
    "| SRC-002 | mapping | mod_gate | Scope restriction. | * | |",
    "| ID-001 | mapping | mod_gate | Z-code identity. | * | |",
    "| ID-002 | mapping | mod_gate | Capacity cross-check. | * | |",
    "| FLD-001 | mapping | mod_gate | Field scope. | * | |",
    "| FLD-002 | mapping | mod_gate | Field mapping. | * | |",
    "| SEC-001 | validation | mod_gate | Internal field range. | * | |",
    "| TRN-001 | mapping | mod_gate | Controlled translation. | * | |",
    "| FMT-001 | mapping | mod_gate | Product column layout. | * | |",
    "| VAL-001 | validation | execution_gate | Scope completeness. | * | |",
    "| VAL-002 | validation | execution_gate | Lineage evidence. | * | |",
)

_GOOD_MAP = [
    "- resolve: SRC-001, SRC-002, ID-001, ID-002",
    "- map: FLD-001, FLD-002, SEC-001",
    "- transform: TRN-001, FMT-001",
    "- validate: VAL-001, VAL-002",
]

_RUNTIME_CORE_CONTENT = (
    "Agent must establish three authorities first: the spectrum defines product "
    "identity, the parameter sheet defines technical facts, and the customer "
    "template defines the export field range. Z-code is the identity key; "
    "capacity only orders columns; unknowns must never be inferred."
)


def _temp_capture_root() -> tuple[Path, Path, Path]:
    """Create a temp capture root (refs dir + valid MOD_INDEX)."""
    root = Path(tempfile.mkdtemp(prefix="mod_att_test_"))
    refs = root / "references"
    refs.mkdir(parents=True)
    index = refs / "MOD_INDEX.md"
    index.write_text(
        "## Registered MODs\n\n"
        "| MOD Name | Aliases | Scope Signals (+) | Exclusion Signals (-) "
        "| Path | Revision | Visibility |\n"
        "|---|---|---|---|---|---|---|\n",
        encoding="utf-8",
    )
    return root, refs, index


class _AttentionCaptureCase(unittest.TestCase):
    """Shared fixture: temp capture root + mocked mod_capture paths."""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp(prefix="mod_att_case_"))
        self._capture_roots: list[Path] = []
        self._patchers: list[mock._patch] = []

    def tearDown(self):
        for p in reversed(self._patchers):
            p.stop()
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        for root in self._capture_roots:
            shutil.rmtree(root, ignore_errors=True)

    def make_capture_root(self) -> tuple[Path, Path, Path]:
        root, refs, index = _temp_capture_root()
        self._capture_roots.append(root)
        return root, refs, index

    def patch_capture_paths(self, refs: Path, index: Path) -> None:
        """Point mod_capture's path helpers at a temp root (no live writes)."""
        new_patchers = [
            mock.patch("mod_capture._mod_index_path", return_value=str(index)),
            mock.patch("mod_capture._refs_dir", return_value=str(refs)),
        ]
        for p in new_patchers:
            p.start()
        self._patchers.extend(new_patchers)

    def write_source(self, name: str, text: str) -> Path:
        source = self.tmpdir / f"{name}_src.md"
        source.write_text(text, encoding="utf-8")
        return source

    def do_create(self, refs, index, mod_name, source_text,
                  scope_signals="s::1", aliases="", exclusion_signals="") -> dict:
        self.patch_capture_paths(refs, index)
        req = CaptureRequest(
            mod_name=mod_name, action="create",
            source=self.write_source(mod_name, source_text),
            scope_signals=scope_signals, aliases=aliases,
            exclusion_signals=exclusion_signals,
        )
        return _do_create(req)

    def do_update(self, refs, index, mod_name, source_text,
                  scope_signals="s::1", aliases="", exclusion_signals="") -> dict:
        self.patch_capture_paths(refs, index)
        req = CaptureRequest(
            mod_name=mod_name, action="update",
            source=self.write_source(mod_name, source_text),
            scope_signals=scope_signals, aliases=aliases,
            exclusion_signals=exclusion_signals,
        )
        return _do_update(req)

    def assert_exit_3(self, call, fragment: str) -> CaptureError:
        """Assert a capture call raises CaptureError with exit 3 + message."""
        with self.assertRaises(CaptureError) as ctx:
            call()
        self.assertEqual(ctx.exception.exit_code, 3,
                         f"expected exit 3, got {ctx.exception.exit_code}: "
                         f"{ctx.exception}")
        self.assertIn(fragment, str(ctx.exception),
                      f"message should contain {fragment!r}: {ctx.exception}")
        return ctx.exception


# ── parse_attention_map unit tests ────────────────────────────────────────


class TestParseAttentionMap(unittest.TestCase):

    def test_normal_map_group_order_and_ids(self):
        result = parse_attention_map(_mod_source(_RULES, attention_lines=_GOOD_MAP))
        self.assertEqual(
            list(result.keys()),
            ["resolve", "map", "transform", "validate"],
            "dict must preserve file order of group lines",
        )
        self.assertEqual(result["resolve"], ["SRC-001", "SRC-002", "ID-001", "ID-002"])
        self.assertEqual(result["map"], ["FLD-001", "FLD-002", "SEC-001"])
        self.assertEqual(result["transform"], ["TRN-001", "FMT-001"])
        self.assertEqual(result["validate"], ["VAL-001", "VAL-002"])

    def test_cross_group_duplicate_preserved(self):
        text = _mod_source(
            _rules_table(
                "| FLD-001 | mapping | mod_gate | A. | * | |",
                "| VAL-001 | validation | execution_gate | B. | * | |",
                "| SEC-001 | validation | mod_gate | C. | * | |",
            ),
            attention_lines=[
                "- map: FLD-001, SEC-001",
                "- validate: VAL-001, SEC-001",
            ],
        )
        result = parse_attention_map(text)
        self.assertIn("SEC-001", result["map"])
        self.assertIn("SEC-001", result["validate"],
                      "cross-group duplication must be preserved, not dropped")

    def test_section_absent_returns_none(self):
        text = _mod_source(_RULES)  # no Attention Map section
        self.assertIsNone(parse_attention_map(text))
        self.assertIsNone(parse_attention_map_lines(text))

    def test_empty_section_returns_empty_dict(self):
        # {} semantics: section present but assigns nothing — ENABLES
        # validation, which then fails the coverage check at capture time.
        text = _mod_source(_RULES, attention_lines=[])
        self.assertEqual(parse_attention_map(text), {})

    def test_malformed_prose_line_raises_with_line_number(self):
        text = (
            "# MOD test\n\n"
            "## Metadata\n\n"
            "- Scope Signals: s::1\n\n"
            "## Attention Map\n\n"
            "- resolve: SRC-001\n"
            "This is a stray prose sentence, not a map line.\n"
            "- validate: VAL-001\n\n"
            "## 业务场景上下文\n\n"
        ) + _rules_table(
            "| SRC-001 | mapping | mod_gate | A. | * | |",
            "| VAL-001 | validation | execution_gate | B. | * | |",
        )
        with self.assertRaises(AttentionMapParseError) as ctx:
            parse_attention_map(text)
        self.assertEqual(ctx.exception.line_number, 10)
        self.assertIn("This is a stray prose sentence", ctx.exception.text)
        self.assertIn("must match", str(ctx.exception))

    def test_nothing_after_colon_raises(self):
        text = _mod_source(_RULES, attention_lines=["- resolve: "])
        with self.assertRaises(AttentionMapParseError) as ctx:
            parse_attention_map(text)
        self.assertIn("no Rule IDs after the colon", str(ctx.exception))

    def test_empty_id_element_raises(self):
        # Strictness decision: an empty element in the ID list (double comma)
        # is MALFORMED, never silently dropped.
        text = _mod_source(_RULES, attention_lines=["- resolve: SRC-001,, ID-001"])
        with self.assertRaises(AttentionMapParseError) as ctx:
            parse_attention_map(text)
        self.assertIn("empty Rule ID", str(ctx.exception))

    def test_blank_and_whitespace_only_lines_skipped(self):
        text = _mod_source(
            _rules_table(
                "| SRC-001 | mapping | mod_gate | A. | * | |",
                "| VAL-001 | validation | execution_gate | B. | * | |",
            ),
            attention_lines=[
                "- resolve: SRC-001",
                "   ",
                "",
                "- validate: VAL-001",
            ],
        )
        result = parse_attention_map(text)
        self.assertEqual(result, {"resolve": ["SRC-001"], "validate": ["VAL-001"]})

    def test_section_at_end_of_text_parses(self):
        text = "# MOD test\n\n## Attention Map\n\n- resolve: SRC-001\n"
        self.assertEqual(parse_attention_map(text), {"resolve": ["SRC-001"]})

    def test_group_name_not_closed_set_validated_at_parse(self):
        # Dumb parser: group names are NOT validated here (that is the
        # capture validator's closed-set job). Any [\w_]+ token parses.
        text = _mod_source(_RULES, attention_lines=["- resolve_map: SRC-001"])
        self.assertEqual(parse_attention_map(text), {"resolve_map": ["SRC-001"]})

    def test_stops_at_next_section_boundary(self):
        text = _mod_source(
            _rules_table(
                "| SRC-001 | mapping | mod_gate | A. | * | |",
                "| VAL-001 | validation | execution_gate | B. | * | |",
            ),
            attention_lines=[
                "- resolve: SRC-001",
                "- validate: VAL-001",
            ],
        )
        result = parse_attention_map(text)
        self.assertEqual(result, {"resolve": ["SRC-001"], "validate": ["VAL-001"]},
                         "content of later sections must not leak into the map")


# ── Capture validation: rejection cases (exit 3) ──────────────────────────


class TestAttentionCaptureRejections(_AttentionCaptureCase):
    """Each rejection case must fail with CaptureError exit 3, before any
    write, and carry a corrective hint."""

    def test_malformed_line_rejected(self):
        root, refs, index = self.make_capture_root()
        text = (
            "# MOD test\n\n"
            "## Metadata\n\n"
            "- Scope Signals: s::1\n\n"
            "## Attention Map\n\n"
            "- resolve: SRC-001\n"
            "This is a stray prose sentence, not a map line.\n"
            "- validate: VAL-001\n\n"
            "## 业务场景上下文\n\n"
        ) + _rules_table(
            "| SRC-001 | mapping | mod_gate | A. | * | |",
            "| VAL-001 | validation | execution_gate | B. | * | |",
        )
        self.assert_exit_3(
            lambda: self.do_create(refs, index, "MALFORMED", text),
            "line 10",
        )
        self.assertFalse((refs / "MOD_MALFORMED.md").exists(),
                         "no MOD file may be written on validation failure")

    def test_dangling_id_rejected(self):
        root, refs, index = self.make_capture_root()
        text = _mod_source(
            _rules_table(
                "| SRC-001 | mapping | mod_gate | A. | * | |",
                "| VAL-001 | validation | execution_gate | B. | * | |",
            ),
            attention_lines=[
                "- resolve: SRC-001",
                "- validate: VAL-001, FLD-999",
            ],
        )
        self.assert_exit_3(
            lambda: self.do_create(refs, index, "DANGLING", text),
            "ID not found in rule table: FLD-999",
        )

    def test_coverage_miss_rejected(self):
        root, refs, index = self.make_capture_root()
        text = _mod_source(
            _rules_table(
                "| SRC-001 | mapping | mod_gate | A. | * | |",
                "| VAL-001 | validation | execution_gate | B. | * | |",
                "| SEC-003 | validation | mod_gate | Ungrouped. | * | |",
            ),
            attention_lines=[
                "- resolve: SRC-001",
                "- validate: VAL-001",
            ],
        )
        self.assert_exit_3(
            lambda: self.do_create(refs, index, "COVERAGE", text),
            "rules missing from any group: SEC-003",
        )

    def test_unknown_group_rejected(self):
        root, refs, index = self.make_capture_root()
        text = _mod_source(
            _rules_table("| SRC-001 | mapping | mod_gate | A. | * | |"),
            attention_lines=["- foo: SRC-001"],
        )
        self.assert_exit_3(
            lambda: self.do_create(refs, index, "UNKNOWN_GROUP", text),
            "unknown group 'foo'; allowed: resolve/map/transform/validate",
        )

    def test_same_group_two_lines_rejected(self):
        root, refs, index = self.make_capture_root()
        text = _mod_source(
            _rules_table(
                "| SRC-001 | mapping | mod_gate | A. | * | |",
                "| VAL-001 | validation | execution_gate | B. | * | |",
            ),
            attention_lines=[
                "- resolve: SRC-001",
                "- resolve: VAL-001",
            ],
        )
        self.assert_exit_3(
            lambda: self.do_create(refs, index, "DUP_GROUP_LINE", text),
            "group 'resolve' appears twice, merge into one line",
        )

    def test_group_order_violation_rejected(self):
        root, refs, index = self.make_capture_root()
        text = _mod_source(
            _rules_table(
                "| SRC-001 | mapping | mod_gate | A. | * | |",
                "| VAL-001 | validation | execution_gate | B. | * | |",
            ),
            attention_lines=[
                "- validate: VAL-001",
                "- resolve: SRC-001",
            ],
        )
        self.assert_exit_3(
            lambda: self.do_create(refs, index, "BAD_ORDER", text),
            "groups must follow resolve → map → transform → validate; "
            "found 'resolve' after 'validate'",
        )

    def test_in_group_duplicate_id_rejected(self):
        root, refs, index = self.make_capture_root()
        text = _mod_source(
            _rules_table("| SRC-001 | mapping | mod_gate | A. | * | |"),
            attention_lines=["- resolve: SRC-001, SRC-001"],
        )
        self.assert_exit_3(
            lambda: self.do_create(refs, index, "IN_GROUP_DUP", text),
            "ID repeated within group 'resolve': SRC-001",
        )

    def test_empty_runtime_core_rejected(self):
        root, refs, index = self.make_capture_root()
        text = _mod_source(
            _rules_table("| SRC-001 | mapping | mod_gate | A. | * | |"),
            attention_lines=["- resolve: SRC-001"],
            runtime_core="   ",
        )
        self.assert_exit_3(
            lambda: self.do_create(refs, index, "EMPTY_CORE", text),
            "`## Runtime Core` section is empty",
        )

    def test_empty_map_section_rejected_via_coverage(self):
        """{} semantics locked: an empty map section enables validation and
        fails the coverage check (every rule is missing from any group)."""
        root, refs, index = self.make_capture_root()
        text = _mod_source(
            _rules_table("| SRC-001 | mapping | mod_gate | A. | * | |"),
            attention_lines=[],
        )
        self.assert_exit_3(
            lambda: self.do_create(refs, index, "EMPTY_MAP", text),
            "rules missing from any group: SRC-001",
        )

    def test_multiple_violations_aggregated_in_one_message(self):
        """Design decision locked: ALL violations are collected into one
        CaptureError (single exit 3), not first-error-wins."""
        root, refs, index = self.make_capture_root()
        text = _mod_source(
            _rules_table(
                "| SRC-001 | mapping | mod_gate | A. | * | |",
                "| VAL-001 | validation | execution_gate | B. | * | |",
                "| SEC-003 | validation | mod_gate | C. | * | |",
            ),
            attention_lines=[
                "- validate: SRC-001",
                "- foo: VAL-001, FLD-999",
                "- resolve: SEC-003",
            ],
        )
        err = self.assert_exit_3(
            lambda: self.do_create(refs, index, "MULTI", text),
            "Attention Map validation failed",
        )
        for fragment in (
            "unknown group 'foo'",
            "ID not found in rule table: FLD-999",
            "found 'resolve' after 'validate'",
        ):
            self.assertIn(fragment, str(err),
                          f"aggregated message should contain {fragment!r}")

    def test_update_rejects_when_attention_fails(self):
        """Update path runs the same validation; a regression in the map
        blocks the update before any .bak / write happens."""
        root, refs, index = self.make_capture_root()
        good = _mod_source(
            _rules_table("| SRC-001 | mapping | mod_gate | A. | * | |"),
            attention_lines=["- resolve: SRC-001"],
        )
        self.do_create(refs, index, "UPD_BAD", good)
        bad = _mod_source(
            _rules_table("| SRC-001 | mapping | mod_gate | A. | * | |"),
            attention_lines=["- resolve: SRC-001, FLD-999"],
        )
        self.assert_exit_3(
            lambda: self.do_update(refs, index, "UPD_BAD", bad),
            "ID not found in rule table: FLD-999",
        )
        self.assertFalse((refs / "MOD_UPD_BAD.md.bak").exists(),
                         "no backup may be written on validation failure")


# ── Capture validation: positive cases ────────────────────────────────────


class TestAttentionCapturePositive(_AttentionCaptureCase):

    def test_cross_group_duplicate_passes(self):
        root, refs, index = self.make_capture_root()
        text = _mod_source(
            _rules_table(
                "| SRC-001 | mapping | mod_gate | A. | * | |",
                "| VAL-001 | validation | execution_gate | B. | * | |",
                "| SEC-001 | validation | mod_gate | C. | * | |",
            ),
            attention_lines=[
                "- resolve: SRC-001",
                "- map: SEC-001",
                "- validate: VAL-001, SEC-001",  # SEC-001 in two groups: legal
            ],
        )
        out = self.do_create(refs, index, "CROSS_DUP", text)
        self.assertEqual(out["exit_code"], 0)
        self.assertTrue((refs / "MOD_CROSS_DUP.md").is_file())

    def test_group_subset_in_order_passes(self):
        # resolve + map + validate, no transform: subsets allowed.
        root, refs, index = self.make_capture_root()
        text = _mod_source(
            _rules_table(
                "| SRC-001 | mapping | mod_gate | A. | * | |",
                "| FLD-001 | mapping | mod_gate | B. | * | |",
                "| VAL-001 | validation | execution_gate | C. | * | |",
            ),
            attention_lines=[
                "- resolve: SRC-001",
                "- map: FLD-001",
                "- validate: VAL-001",
            ],
        )
        out = self.do_create(refs, index, "SUBSET", text)
        self.assertEqual(out["exit_code"], 0)

    def test_runtime_core_with_content_passes(self):
        root, refs, index = self.make_capture_root()
        text = _mod_source(_RULES, attention_lines=_GOOD_MAP,
                           runtime_core=_RUNTIME_CORE_CONTENT)
        out = self.do_create(refs, index, "CORE_OK", text)
        self.assertEqual(out["exit_code"], 0)
        self.assertEqual(out["rule_count"], 11)

    def test_map_without_runtime_core_passes(self):
        # Runtime Core section is optional; only a DECLARED one must be
        # non-empty.
        root, refs, index = self.make_capture_root()
        text = _mod_source(_RULES, attention_lines=_GOOD_MAP)
        out = self.do_create(refs, index, "NO_CORE", text)
        self.assertEqual(out["exit_code"], 0)

    def test_legacy_mod_map_none_capture_alive(self):
        # Spec §7.1 static acceptance (post ticket 01 migration): the live
        # parameter MOD now DECLARES an Attention Map that covers all of its
        # rules with no dangling IDs — keep this as the drift guard.
        mod_path = _REFS_DIR / "MOD_tcl_internal_parameter_to_customer_parameter_sheet.md"
        if not mod_path.is_file():
            self.skipTest("parameter MOD not found")
        text = mod_path.read_text(encoding="utf-8")
        am = parse_attention_map(text)
        self.assertIsNotNone(am, "parameter MOD must declare an Attention Map (revision 2)")
        rule_ids = {r.rule_id for r in parse_mod_rules(text)}
        mapped = {i for ids in am.values() for i in ids}
        self.assertEqual(mapped, rule_ids,
                         "Attention Map must cover every rule table ID, no dangling")
        self.assertEqual(list(am.keys()),
                         ["resolve", "map", "transform", "validate"],
                         "groups must appear in fixed order")


# ── Compatibility regression: legacy MODs behave exactly as before ────────


class TestLegacyModCompatibilityRegression(_AttentionCaptureCase):
    """Create AND update a MOD with NO Attention Map and NO Runtime Core —
    must succeed exactly as before (no new validation errors)."""

    def test_create_without_map_or_core_succeeds(self):
        root, refs, index = self.make_capture_root()
        out = self.do_create(
            refs, index, "LEGACY_CREATE",
            _rules_table(
                "| R01 | mapping | mod_gate | First legacy rule. | * | |",
                "| R02 | validation | execution_gate | Second legacy rule. | * | |",
            ),
        )
        self.assertEqual(out["exit_code"], 0)
        self.assertEqual(out["rule_count"], 2)
        written = (refs / "MOD_LEGACY_CREATE.md").read_text(encoding="utf-8")
        self.assertNotIn("## Attention Map", written)
        self.assertNotIn("## Runtime Core", written)

    def test_create_then_update_without_map_or_core_succeeds(self):
        root, refs, index = self.make_capture_root()
        source = _rules_table(
            "| R01 | mapping | mod_gate | Legacy rule. | * | |",
        )
        out_create = self.do_create(refs, index, "LEGACY_RT", source)
        self.assertEqual(out_create["exit_code"], 0)
        out_update = self.do_update(refs, index, "LEGACY_RT", source)
        self.assertEqual(out_update["exit_code"], 0)
        self.assertEqual(out_update["revision"], 2,
                         "update with no map must still increment revision")
        self.assertEqual(out_update["rule_count"], 1)


# ── Ticket-01 enablement: update of a MOD WITH map + core ─────────────────


class TestUpdateWithAttentionMap(_AttentionCaptureCase):
    """Update of a MOD that HAS a valid Attention Map + Runtime Core succeeds
    (rule count preserved, revision increments) — proving ticket 01 will be
    able to run its capture update."""

    def test_create_and_update_with_map_and_core(self):
        root, refs, index = self.make_capture_root()
        source = _mod_source(_RULES, attention_lines=_GOOD_MAP,
                             runtime_core=_RUNTIME_CORE_CONTENT)
        out_create = self.do_create(refs, index, "PARAM_BODY", source)
        self.assertEqual(out_create["exit_code"], 0)
        self.assertEqual(out_create["revision"], 1)
        self.assertEqual(out_create["rule_count"], 11)

        out_update = self.do_update(
            refs, index, "PARAM_BODY", source,
            scope_signals="semantic_type::internal_parameter_to_customer_parameter_sheet",
            aliases="tcl-param-sheet",
        )
        self.assertEqual(out_update["exit_code"], 0)
        self.assertEqual(out_update["revision"], 2,
                         "revision must increment on a map-bearing update")
        self.assertEqual(out_update["rule_count"], 11,
                         "rule count must be preserved on update")

        written = (refs / "MOD_PARAM_BODY.md").read_text(encoding="utf-8")
        self.assertIn("## Attention Map", written)
        self.assertIn("## Runtime Core", written)
        self.assertEqual(len(parse_mod_rules(written)), 11)


if __name__ == "__main__":
    unittest.main()