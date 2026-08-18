"""Guard tests for MOD decontamination rules (issue #11).

Verifies that:
1. Public MODs with decontamination violations are rejected by
   ``mod_capture`` (fail-closed); clean public MODs pass.
2. Private MODs are exempt from decontamination enforcement and remain the
   default visibility.
3. The two existing TCL MODs are private, not registered in MOD_INDEX (not
   shipped), and would be flagged if promoted to public — proving the
   private exemption is necessary.
4. MOD_TEMPLATE.md clarifies the public/private decontamination scope.

All capture-enforcement tests run against a temporary capture root via
``mock.patch`` on ``mod_capture``'s path helpers — they NEVER mutate the
live ``references/`` directory (a past run polluted the live MOD_INDEX with
a leftover test row; this suite is written so that cannot recur).

Run with:
  python -m unittest tests.test_mod_decontamination
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from contextlib import ExitStack
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
    _build_index_entry,
    _check_decontamination,
    _DECONTAMINATION_PATTERNS,
    _do_create,
)

REFS_DIR = SKILL_ROOT / "references"
MOD_INDEX_PATH = REFS_DIR / "MOD_INDEX.md"
MOD_TEMPLATE_PATH = REFS_DIR / "MOD_TEMPLATE.md"


# ── Helpers ────────────────────────────────────────────────────────────────


def _valid_source_rules(*rule_rows: str) -> str:
    """Build a valid six-column rule table with header and at least one rule."""
    lines = [
        "| Rule ID | Group | Gate | Description | Applies to | Notes |",
        "|---------|-------|------|-------------|------------|-------|",
    ]
    if not rule_rows:
        rule_rows = ("| R01 | mapping | mod_gate | Test rule. | * | |",)
    lines.extend(rule_rows)
    return "\n".join(lines) + "\n"


def _mod_body(visibility: str, body: str) -> str:
    """Minimal MOD markdown body with a visibility declaration."""
    return (
        f"# MOD test\n\n## Purpose\n\n"
        f"Revision: 1\nVisibility: {visibility}\nRule count: 1\n\n"
        f"## Metadata\n\n- Scope Signals: semantic_type::test\n\n{body}"
    )


def _build_clean_public_source() -> str:
    return _valid_source_rules(
        "| R01 | mapping | mod_gate | Map product by semantic role. | * | |",
        "| R02 | validation | execution_gate | Verify row coverage. | * | |",
    )


def _temp_capture_root() -> tuple[Path, Path, Path]:
    """Create a temp capture root (refs dir + valid MOD_INDEX) for _do_create.

    The index must carry the `## Registered MODs` heading — the unified
    parser contract requires it (see test_optimization ModNominate fixtures).
    """
    root = Path(tempfile.mkdtemp(prefix="mod_capture_test_"))
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


# ── Guard tests: decontamination check function ────────────────────────────


class TestPublicModDecontamination(unittest.TestCase):
    """Public MOD content must not contain single-run facts.

    The check must catch customer names, sheet markers, dates, percentages
    and specific measurements in MOD content.
    """

    def test_clean_public_mod_passes_decontamination(self):
        content = _mod_body("public", _build_clean_public_source())
        has_violations, violations = _check_decontamination(content)
        self.assertFalse(
            has_violations,
            f"Clean public MOD should pass but got violations: {violations}",
        )

    def test_public_mod_with_customer_name_tcl_fails(self):
        content = _mod_body("public", _valid_source_rules(
            "| R01 | mapping | mod_gate | Route to TCL product group. | * | |"))
        has_violations, violations = _check_decontamination(content)
        self.assertTrue(has_violations, "Public MOD with 'TCL' should be flagged")
        self.assertIn("customer_name", [v["pattern_type"] for v in violations])

    def test_public_mod_with_customer_name_fresh_fails(self):
        content = _mod_body("public", _valid_source_rules(
            "| R01 | mapping | mod_gate | Route to FRESH product group. | * | |"))
        has_violations, violations = _check_decontamination(content)
        self.assertTrue(has_violations, "Public MOD with 'FRESH' should be flagged")
        self.assertIn("customer_name", [v["pattern_type"] for v in violations])

    def test_public_mod_with_sheet_marker_fails(self):
        content = _mod_body("public", _valid_source_rules(
            "| R01 | mapping | mod_gate | Match 三三三 sheet. | * | |"))
        has_violations, violations = _check_decontamination(content)
        self.assertTrue(has_violations, "Public MOD with '三三三' should be flagged")
        self.assertIn("sheet_marker", [v["pattern_type"] for v in violations])

    def test_public_mod_with_date_fails(self):
        content = _mod_body("public", _valid_source_rules(
            "| R01 | mapping | mod_gate | Rule from 2026-08-09. | * | |"))
        has_violations, violations = _check_decontamination(content)
        self.assertTrue(has_violations, "Public MOD with a date should be flagged")
        self.assertIn("date", [v["pattern_type"] for v in violations])

    def test_public_mod_with_percentage_fails(self):
        content = _mod_body("public", _valid_source_rules(
            "| R01 | mapping | mod_gate | Threshold is -6%. | * | |"))
        has_violations, violations = _check_decontamination(content)
        self.assertTrue(has_violations, "Public MOD with a percentage should be flagged")
        self.assertIn("percentage", [v["pattern_type"] for v in violations])

    def test_public_mod_with_pipe_measurement_fails(self):
        content = _mod_body("public", _valid_source_rules(
            "| R01 | mapping | mod_gate | Cabinet unit uses 5 米 pipe. | * | |"))
        has_violations, violations = _check_decontamination(content)
        self.assertTrue(has_violations, "Public MOD with a measurement should be flagged")
        self.assertIn("fixed_number", [v["pattern_type"] for v in violations])


# ── Guard tests: private MOD exemption ─────────────────────────────────────


class TestPrivateModExemption(unittest.TestCase):
    """Private MODs are exempt from decontamination enforcement.

    Customer-owned private MODs may carry customer domain facts and business
    context (this is their value). The exemption path must exist and be
    explicit: capture defaults to ``private`` and only an explicit
    ``--visibility public`` enables enforcement.
    """

    def test_capture_request_defaults_to_private(self):
        req = CaptureRequest(
            mod_name="PRIVATE_TEST", action="create",
            source=Path("/nonexistent/source.md"),
            scope_signals="semantic_type::test", aliases="", exclusion_signals="",
        )
        self.assertEqual(req.visibility, "private")

    def test_build_index_entry_defaults_to_private(self):
        req = CaptureRequest(
            mod_name="PRIVATE_TEST", action="create",
            source=Path("/nonexistent/source.md"),
            scope_signals="semantic_type::test", aliases="", exclusion_signals="",
        )
        entry = _build_index_entry(req)
        self.assertEqual(
            entry.visibility, "private",
            "mod_capture must default to visibility=private",
        )

    def test_private_to_public_transition_blocked_by_decontamination(self):
        """Flipping a MOD with forbidden content to public must be blocked:
        the check detects the patterns (proving the guard exists), so the
        caller (public enforcement) rejects the transition."""
        content = _mod_body("private", _valid_source_rules(
            "| R01 | mapping | mod_gate | TCL order 2026-08-09 -6%. | * | |"))
        has_violations, violations = _check_decontamination(content)
        self.assertTrue(has_violations, "Private MOD with forbidden content must be flagged")
        types = {v["pattern_type"] for v in violations}
        self.assertIn("customer_name", types)
        self.assertIn("date", types)
        self.assertIn("percentage", types)


# ── Guard tests: existing TCL MODs stay private ────────────────────────────


class TestTclModsArePrivate(unittest.TestCase):
    """The two TCL MODs must remain private and not registered in MOD_INDEX.

    Per issue #11: private MODs are exempt from decontamination but must not
    be shipped with releases (no MOD_INDEX registration).
    """

    TCL_MODS = [
        "MOD_tcl_quotation_summary_migration.md",
        "MOD_tcl_cost_reply_to_quotation_summary_block.md",
    ]

    def test_tcl_mods_exist_and_declare_private(self):
        for name in self.TCL_MODS:
            mod_path = REFS_DIR / name
            self.assertTrue(mod_path.is_file(), f"TCL MOD not found: {mod_path}")
            content = mod_path.read_text(encoding="utf-8")
            self.assertIn(
                "Visibility: private", content,
                f"{name} must declare 'Visibility: private'",
            )

    def test_tcl_mods_not_registered_in_index(self):
        from _mod_catalog import parse_mod_index  # noqa: E402

        entries = parse_mod_index(MOD_INDEX_PATH.read_text(encoding="utf-8"))
        tcl_entries = [e for e in entries if "tcl" in e.mod_name.lower()]
        self.assertEqual(
            len(tcl_entries), 0,
            f"TCL MODs must not be registered in MOD_INDEX but found: "
            f"{[e.mod_name for e in tcl_entries]}",
        )

    def test_tcl_mods_have_forbidden_content(self):
        """TCL MODs contain forbidden patterns — confirming that they WOULD be
        flagged if promoted to public, i.e. the private exemption is required."""
        for name in self.TCL_MODS:
            content = (REFS_DIR / name).read_text(encoding="utf-8")
            has_violations, violations = _check_decontamination(content)
            self.assertTrue(
                has_violations,
                f"{name} should contain forbidden patterns (proving private "
                f"exemption is needed), but got none",
            )


# ── Guard tests: template scope clarification ──────────────────────────────


class TestTemplateDecontaminationScope(unittest.TestCase):
    """MOD_TEMPLATE.md must state the public/private decontamination scope."""

    def test_template_has_decontamination_section(self):
        content = MOD_TEMPLATE_PATH.read_text(encoding="utf-8")
        self.assertIn("捕获去污染原则", content)

    def test_template_scopes_rule_to_public_mods(self):
        content = MOD_TEMPLATE_PATH.read_text(encoding="utf-8")
        self.assertIn(
            "public MOD 文件任何位置", content,
            "Decontamination rule must specify 'public MOD' scope",
        )

    def test_template_states_private_exemption_with_marker(self):
        content = MOD_TEMPLATE_PATH.read_text(encoding="utf-8")
        self.assertIn("Public/Private 作用域区分", content)
        self.assertIn("visibility: private", content)


# ── Guard tests: capture enforcement (temp root, no live-dir mutation) ──────


class TestPublicCaptureEnforcement(unittest.TestCase):
    """mod_capture --visibility public must enforce decontamination.

    Runs against a temporary capture root (patched path helpers) so the live
    references/ directory is never touched.
    """

    def setUp(self):
        self.root, self.refs, self.index = _temp_capture_root()
        self.source = Path(tempfile.mkdtemp(prefix="src_")) / "src.md"
        self.source.write_text(
            _valid_source_rules(
                "| R01 | mapping | mod_gate | TCL order 2026-08-09. | * | |",
            ),
            encoding="utf-8",
        )

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)
        shutil.rmtree(self.source.parent, ignore_errors=True)

    def _patch_paths(self):
        """Patch mod_capture's path helpers onto the temp capture root."""
        return [
            mock.patch.object(mod_capture, "_mod_index_path",
                              lambda: str(self.index)),
            mock.patch.object(mod_capture, "_mod_file_path",
                              lambda name: str(self.refs / f"MOD_{name}.md")),
            mock.patch.object(mod_capture, "_refs_dir",
                              lambda: str(self.refs)),
        ]

    def _enter_patches(self, stack: ExitStack):
        for patcher in self._patch_paths():
            stack.enter_context(patcher)

    def test_public_create_with_forbidden_content_fails(self):
        req = CaptureRequest(
            mod_name="PUBLIC_FAIL", action="create",
            source=self.source,
            scope_signals="semantic_type::test", aliases="", exclusion_signals="",
            visibility="public",
        )
        with ExitStack() as stack:
            self._enter_patches(stack)
            with self.assertRaises(CaptureError) as ctx:
                _do_create(req)
            self.assertIn("Decontamination", str(ctx.exception))
        # Nothing may have been written to the live references dir.
        self.assertFalse((REFS_DIR / "MOD_PUBLIC_FAIL.md").exists())

    def test_private_create_with_forbidden_content_succeeds(self):
        """visibility=private (default): decontamination is not enforced and
        the MOD is captured into the temp root."""
        req = CaptureRequest(
            mod_name="PRIV_OK", action="create",
            source=self.source,
            scope_signals="semantic_type::test", aliases="", exclusion_signals="",
        )
        with ExitStack() as stack:
            self._enter_patches(stack)
            result = _do_create(req)
        self.assertEqual(result["exit_code"], 0)
        self.assertEqual(result["visibility"], "private")
        self.assertTrue((self.refs / "MOD_PRIV_OK.md").is_file())
        self.assertIn("PRIV_OK", self.index.read_text(encoding="utf-8"))

    def test_public_create_with_clean_content_succeeds(self):
        clean = self.source.parent / "clean.md"
        clean.write_text(_build_clean_public_source(), encoding="utf-8")
        req = CaptureRequest(
            mod_name="CLEAN_PUBLIC", action="create",
            source=clean,
            scope_signals="semantic_type::test", aliases="", exclusion_signals="",
            visibility="public",
        )
        with ExitStack() as stack:
            self._enter_patches(stack)
            result = _do_create(req)
        self.assertEqual(result["exit_code"], 0)
        self.assertEqual(result["visibility"], "public")


# ── Decontamination function contract tests ────────────────────────────────


class TestDecontaminationFunction(unittest.TestCase):
    """_check_decontamination contract: return shape, pattern coverage."""

    def test_returns_tuple_of_bool_and_list(self):
        result = _check_decontamination("some content")
        self.assertIsInstance(result, tuple)
        self.assertEqual(len(result), 2)
        self.assertIsInstance(result[0], bool)
        self.assertIsInstance(result[1], list)

    def test_violation_dict_has_required_keys(self):
        _, violations = _check_decontamination("TCL product 2026-08-09")
        self.assertGreater(len(violations), 0)
        for v in violations:
            self.assertIn("pattern_type", v)
            self.assertIn("description", v)
            self.assertIn("match", v)

    def test_clean_content_returns_no_violations(self):
        has_violations, violations = _check_decontamination(
            "Route to the sheet containing this product family."
        )
        self.assertFalse(has_violations)
        self.assertEqual(len(violations), 0)

    def test_multiple_violations_detected(self):
        has_violations, violations = _check_decontamination(
            "TCL order 2026-08-09 with -6% margin on 三三三 sheet"
        )
        self.assertTrue(has_violations)
        self.assertGreaterEqual(len(violations), 3)

    def test_decontamination_patterns_exported(self):
        self.assertIsInstance(_DECONTAMINATION_PATTERNS, list)
        self.assertGreater(len(_DECONTAMINATION_PATTERNS), 0)
        for item in _DECONTAMINATION_PATTERNS:
            self.assertEqual(len(item), 3)  # (pattern, type, description)


if __name__ == "__main__":
    unittest.main()