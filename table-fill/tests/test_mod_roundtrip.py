"""Round-trip tests for MOD parser unification (issue #10).

Verifies that capture -> index -> nominate works for:
  1. R01-style Rule IDs (not just XXX-NNN)
  2. Escaped pipe characters (\\|) in scope signals
  3. Bare pipe characters are still rejected by capture
  4. mod_nominate consumes _mod_catalog (single parser source)

HYGIENE: every capture-enforcement test runs against a TEMPORARY capture
root (mock-patched mod_capture path helpers) — these tests NEVER mutate the
live ``references/`` directory (a past run polluted the live MOD_INDEX with
a leftover test row; see test_mod_decontamination for the same discipline).

Run with:
  python -m unittest tests.test_mod_roundtrip
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
)
import mod_nominate  # noqa: E402

from _mod_catalog import (  # noqa: E402
    ModRuleParseError,
    parse_mod_index,
    parse_mod_rules,
    resolve_by_name_or_alias,
)

_REFS_DIR = SKILL_ROOT / "references"
_MOD_INDEX_PATH = _REFS_DIR / "MOD_INDEX.md"
_MOD_NOMINATE_PATH = SKILL_ROOT / "scripts" / "mod_nominate.py"


# ── Helpers ────────────────────────────────────────────────────────────────


def _valid_source_rules(*rule_rows: str) -> str:
    """Build a valid six-column rule table with header and at least one rule."""
    lines = [
        "| Rule ID | Group | Gate | Description | Applies to | Notes |",
        "|---------|-------|------|-------------|------------|-------|",
    ]
    if not rule_rows:
        rule_rows = ("| R01 | mapping | mod_gate | Test rule description. | * | |",)
    lines.extend(rule_rows)
    return "\n".join(lines) + "\n"


def _temp_capture_root() -> tuple[Path, Path, Path]:
    """Create a temp capture root (refs dir + valid MOD_INDEX) for _do_create.

    The index must carry the `## Registered MODs` heading — the unified
    parser contract requires it (same convention as test_mod_decontamination).
    """
    root = Path(tempfile.mkdtemp(prefix="mod_roundtrip_test_"))
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


class _CaptureRootCase(unittest.TestCase):
    """Shared fixture: temp capture root + mocked mod_capture paths."""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp(prefix="mod_rt_case_"))
        self._capture_roots: list[Path] = []
        self._patchers = []

    def make_capture_root(self) -> tuple[Path, Path, Path]:
        """Create a temp capture root, registered for cleanup in tearDown."""
        root, refs, index = _temp_capture_root()
        self._capture_roots.append(root)
        return root, refs, index

    def patch_capture_paths(self, refs: Path, index: Path):
        """Point mod_capture's path helpers at a temp root (no live writes)."""
        patchers = [
            mock.patch("mod_capture._mod_index_path", return_value=str(index)),
            mock.patch("mod_capture._refs_dir", return_value=str(refs)),
        ]
        for p in patchers:
            p.start()
            self._patchers.append(p)

    def tearDown(self):
        for p in reversed(self._patchers):
            p.stop()
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        for root in self._capture_roots:
            shutil.rmtree(root, ignore_errors=True)

    def do_create(self, refs: Path, index: Path, mod_name: str,
                  source_text: str, scope_signals: str, aliases: str = "",
                  exclusion_signals: str = ""):
        self.patch_capture_paths(refs, index)
        source = self.tmpdir / f"{mod_name}_src.md"
        source.write_text(source_text, encoding="utf-8")
        req = CaptureRequest(
            mod_name=mod_name, action="create", source=source,
            scope_signals=scope_signals, aliases=aliases,
            exclusion_signals=exclusion_signals,
        )
        return _do_create(req)


# ── Test: mod_nominate consumes _mod_catalog (single parser source) ──────


class TestNominateConsumesCatalog(unittest.TestCase):
    """mod_nominate.parse_index and parse_rule_table delegate to _mod_catalog."""

    def test_parse_index_returns_dicts_with_expected_keys(self):
        """parse_index returns dicts with name/aliases/scope/exclusion/path/revision/visibility."""
        idx = _MOD_INDEX_PATH
        if not idx.is_file():
            self.skipTest("MOD_INDEX.md not found")
        entries = mod_nominate.parse_index(idx)
        e = entries[0] if entries else None
        if e is None:
            self.skipTest("No entries in MOD_INDEX.md to assert keys on")
        for key in ("name", "aliases", "scope", "exclusion", "path", "revision", "visibility"):
            self.assertIn(key, e, f"Missing key '{key}' in parse_index result")

    def test_parse_rule_table_accepts_r01_format(self):
        """parse_rule_table accepts R01-style Rule IDs (no longer silently skipped)."""
        text = (
            "| Rule ID | Group | Gate | Description | Applies to | Notes |\n"
            "|---------|-------|------|-------------|------------|-------|\n"
            "| R01 | mapping | mod_gate | Short ID rule. | * | |\n"
            "| RTE-001 | validation | execution_gate | Long ID rule. | * | |\n"
        )
        rules = mod_nominate.parse_rule_table(text)
        self.assertEqual(len(rules), 2, f"Expected 2 rules, got {len(rules)}")
        self.assertEqual(rules[0]["id"], "R01")
        self.assertEqual(rules[1]["id"], "RTE-001")

    def test_parse_rule_table_r01_not_skipped(self):
        """R01 rules are NOT silently skipped (regression: old regex rejected them)."""
        text = (
            "| Rule ID | Group | Gate | Description | Applies to | Notes |\n"
            "|---------|-------|------|-------------|------------|-------|\n"
            "| R01 | mapping | mod_gate | Should not be skipped. | * | |\n"
        )
        rules = mod_nominate.parse_rule_table(text)
        self.assertEqual(len(rules), 1, "R01 was silently skipped — parser divergence!")
        self.assertEqual(rules[0]["id"], "R01")


# ── Test: R01 Rule ID round-trip through capture -> index -> nominate ──────


class TestR01RoundTrip(_CaptureRootCase):
    """Capture a MOD with R01 Rule ID -> verify index and nominate can read it."""

    def test_capture_with_r01_rule_id_succeeds(self):
        root, refs, index = self.make_capture_root()
        out = self.do_create(
            refs, index, "R01_TEST",
            _valid_source_rules(
                "| R01 | mapping | mod_gate | R01 rule description. | * | |",
            ),
            scope_signals="semantic_type::test_r01",
        )
        self.assertEqual(out["action"], "create")
        self.assertEqual(out["rule_count"], 1)
        self.assertEqual(out["exit_code"], 0)
        self.assertTrue((refs / "MOD_R01_TEST.md").is_file())

    def test_r01_roundtrip_capture_index_nominate(self):
        """R01 Rule ID round-trip: capture -> index -> nominate can read rules."""
        root, refs, index = self.make_capture_root()
        self.do_create(
            refs, index, "R01_RT",
            _valid_source_rules(
                "| R01 | mapping | mod_gate | First R01 rule. | * | |",
                "| R02 | validation | execution_gate | Second R02 rule. | * | Check. |",
            ),
            scope_signals="semantic_type::test_r01_rt",
        )

        # Index via _mod_catalog — the canonical parser
        index_text = index.read_text(encoding="utf-8")
        entries = parse_mod_index(index_text)
        resolved = resolve_by_name_or_alias(entries, "R01_RT")
        self.assertIsNotNone(resolved, "MOD 'R01_RT' not found in index after capture")
        self.assertEqual(resolved.mod_name, "R01_RT")
        self.assertEqual(resolved.revision, 1)
        self.assertEqual(resolved.visibility, "private")

        # Rules via _mod_catalog
        mod_path = refs / "MOD_R01_RT.md"
        rules = parse_mod_rules(mod_path.read_text(encoding="utf-8"))
        self.assertEqual(len(rules), 2)
        self.assertEqual(rules[0].rule_id, "R01")
        self.assertEqual(rules[1].rule_id, "R02")

        # Rules via nominate's parse_rule_table (thin wrapper over _mod_catalog)
        nominate_rules = mod_nominate.parse_rule_table(mod_path.read_text(encoding="utf-8"))
        self.assertEqual(len(nominate_rules), 2,
                         "mod_nominate.parse_rule_table should accept R01 rules")
        self.assertEqual(nominate_rules[0]["id"], "R01")
        self.assertEqual(nominate_rules[1]["id"], "R02")

    def test_r01_roundtrip_via_nominate_parse_index(self):
        """R01 MOD appears in nominate's parse_index output (not skipped)."""
        root, refs, index = self.make_capture_root()
        self.do_create(
            refs, index, "R01_IDX",
            _valid_source_rules(
                "| R01 | mapping | mod_gate | Index test. | * | |",
            ),
            scope_signals="semantic_type::r01_idx",
        )
        entries = mod_nominate.parse_index(index)
        found = [e for e in entries if e["name"] == "R01_IDX"]
        self.assertEqual(len(found), 1,
                         "MOD 'R01_IDX' not found via nominate.parse_index")
        self.assertEqual(found[0]["scope"], "semantic_type::r01_idx")


# ── Test: Escaped pipe round-trip ────────────────────────────────────────


class TestEscapedPipeRoundTrip(_CaptureRootCase):
    """Capture with escaped pipe \\| in scope signals -> nominate can read it."""

    def test_capture_with_escaped_pipe_in_scope_succeeds(self):
        root, refs, index = self.make_capture_root()
        out = self.do_create(
            refs, index, "PIPE_TEST",
            _valid_source_rules(
                "| R01 | mapping | mod_gate | Pipe rule. | * | |",
            ),
            scope_signals=r"sheet_marker::三三三\|333",
        )
        self.assertEqual(out["exit_code"], 0)
        self.assertIn(r"\|", out["scope_signals"],
                      "Escaped pipe should be preserved in output")

    def test_escaped_pipe_roundtrip_capture_index_nominate(self):
        """Escaped pipe round-trip: capture -> index -> nominate can read it."""
        root, refs, index = self.make_capture_root()
        self.do_create(
            refs, index, "PIPE_RT",
            _valid_source_rules(
                "| R01 | mapping | mod_gate | Pipe roundtrip. | * | |",
            ),
            scope_signals=r"sheet_marker::三三三\|333",
        )
        # Index via _mod_catalog — escaped pipe is unescaped to |
        index_text = index.read_text(encoding="utf-8")
        entries = parse_mod_index(index_text)
        resolved = resolve_by_name_or_alias(entries, "PIPE_RT")
        self.assertIsNotNone(resolved, "MOD 'PIPE_RT' not found in index")
        self.assertIn("三三三|333", resolved.scope_signals,
                      "Escaped pipe should be unescaped to literal | in parsed signals")

        # Nominate's parse_index also finds it and unescapes
        nominate_entries = mod_nominate.parse_index(index)
        found = [e for e in nominate_entries if e["name"] == "PIPE_RT"]
        self.assertEqual(len(found), 1)
        self.assertIn("三三三|333", found[0]["scope"],
                      "nominate should also unescape \\| to |")

    def test_bare_pipe_in_scope_signals_rejected(self):
        root, refs, index = self.make_capture_root()
        with self.assertRaises(CaptureError):
            self.do_create(
                refs, index, "BARE_PIPE",
                _valid_source_rules(
                    "| R01 | mapping | mod_gate | Bare pipe rule. | * | |",
                ),
                scope_signals="sheet_marker::三三三|333",
            )

    def test_bare_pipe_in_aliases_rejected(self):
        root, refs, index = self.make_capture_root()
        with self.assertRaises(CaptureError):
            self.do_create(
                refs, index, "BARE_ALIAS",
                _valid_source_rules(
                    "| R01 | mapping | mod_gate | Alias pipe. | * | |",
                ),
                scope_signals="sig::test",
                aliases="a|b",
            )

    def test_bare_pipe_in_exclusion_signals_rejected(self):
        root, refs, index = self.make_capture_root()
        with self.assertRaises(CaptureError):
            self.do_create(
                refs, index, "BARE_EXCL",
                _valid_source_rules(
                    "| R01 | mapping | mod_gate | Excl pipe. | * | |",
                ),
                scope_signals="sig::test",
                exclusion_signals="ex|cl",
            )


# ── Test: mod_nominate.parse_index delegates to _mod_catalog ─────────────


class TestParseIndexDelegation(unittest.TestCase):
    """mod_nominate.parse_index uses _mod_catalog.parse_mod_index internally."""

    def test_parse_index_source_code_references_mod_catalog(self):
        src = _MOD_NOMINATE_PATH
        if not src.exists() or not src.is_file():
            self.skipTest("mod_nominate.py not found")
        text = src.read_text(encoding="utf-8")
        self.assertIn("from _mod_catalog import", text,
                      "mod_nominate.py must import from _mod_catalog")
        self.assertIn("parse_mod_index", text,
                      "mod_nominate.py must reference parse_mod_index")
        self.assertIn("parse_mod_rules", text,
                      "mod_nominate.py must reference parse_mod_rules")

    def test_parse_index_no_duplicate_inline_parser(self):
        """mod_nominate.py should NOT have its own inline index parser."""
        src = _MOD_NOMINATE_PATH
        if not src.exists() or not src.is_file():
            self.skipTest("mod_nominate.py not found")
        text = src.read_text(encoding="utf-8")
        lines = text.splitlines()
        in_func = False
        func_lines = []
        for line in lines:
            if line.startswith("def parse_index("):
                in_func = True
                func_lines = []
                continue
            if in_func:
                if line and not line.startswith(" ") and not line.startswith("\t"):
                    break
                func_lines.append(line)
        self.assertLessEqual(len(func_lines), 5,
                             f"parse_index should be a thin wrapper, "
                             f"but has {len(func_lines)} lines — still has duplicate parser?")

    def test_parse_rule_table_no_regex_filter(self):
        """mod_nominate.py should NOT filter Rule IDs by regex anymore."""
        src = _MOD_NOMINATE_PATH
        if not src.exists() or not src.is_file():
            self.skipTest("mod_nominate.py not found")
        text = src.read_text(encoding="utf-8")
        self.assertNotIn(r"[A-Z]+(?:-[A-Z]+)?-\\d{3}", text,
                         "mod_nominate.py still has the restrictive Rule ID regex")
        self.assertNotIn("fullmatch",
                         text.split("def parse_rule_table")[1].split("\ndef ")[0]
                         if "def parse_rule_table" in text else "",
                         "parse_rule_table still uses fullmatch regex filter")


# ── Test: _mod_catalog accepts both R01 and RTE-001 ───────────────────────


class TestCatalogAcceptsBothIdFormats(unittest.TestCase):
    """_mod_catalog.parse_mod_rules accepts both R01 and RTE-001 Rule IDs."""

    def test_parse_mod_rules_accepts_r01(self):
        text = (
            "| Rule ID | Group | Gate | Description | Applies to | Notes |\n"
            "|---------|-------|------|-------------|------------|-------|\n"
            "| R01 | mapping | mod_gate | Short ID. | * | |\n"
        )
        rules = parse_mod_rules(text)
        self.assertEqual(len(rules), 1)
        self.assertEqual(rules[0].rule_id, "R01")

    def test_parse_mod_rules_accepts_rte_001(self):
        text = (
            "| Rule ID | Group | Gate | Description | Applies to | Notes |\n"
            "|---------|-------|------|-------------|------------|-------|\n"
            "| RTE-001 | mapping | mod_gate | Long ID. | * | |\n"
        )
        rules = parse_mod_rules(text)
        self.assertEqual(len(rules), 1)
        self.assertEqual(rules[0].rule_id, "RTE-001")

    def test_parse_mod_rules_accepts_mixed_formats(self):
        text = (
            "| Rule ID | Group | Gate | Description | Applies to | Notes |\n"
            "|---------|-------|------|-------------|------------|-------|\n"
            "| R01 | mapping | mod_gate | Short. | * | |\n"
            "| RTE-001 | validation | execution_gate | Long. | * | |\n"
            "| FLD-002 | mapping | mod_gate | Field. | * | |\n"
        )
        rules = parse_mod_rules(text)
        self.assertEqual(len(rules), 3)
        self.assertEqual([r.rule_id for r in rules], ["R01", "RTE-001", "FLD-002"])


# ── Test: _mod_catalog escapes/unescapes pipes correctly ──────────────────


class TestCatalogPipeHandling(unittest.TestCase):
    """_mod_catalog.parse_mod_index correctly handles escaped pipes."""

    def test_parse_index_with_escaped_pipe_from_fixture_content(self):
        text = (
            "# MOD Index\n\n## Registered MODs\n\n"
            "| MOD Name | Aliases | Scope Signals (+) | Exclusion Signals (-) "
            "| Path | Revision | Visibility |\n"
            "|---|---|---|---|---|---|---|\n"
            "| tmark | t | semantic_type::quotation,sheet_marker::三三三\\|333 "
            "|  | MOD_test.md | 1 | private |\n"
        )
        entries = parse_mod_index(text)
        self.assertEqual(len(entries), 1)
        self.assertIn("三三三|333", entries[0].scope_signals)

    def test_parse_index_escaped_pipe_in_synthetic_text(self):
        text = (
            "# MOD Index\n\n## Registered MODs\n\n"
            "| MOD Name | Aliases | Scope Signals (+) | Exclusion Signals (-) "
            "| Path | Revision | Visibility |\n"
            "|---|---|---|---|---|---|---|\n"
            "| TEST | t | sheet_marker::AAA\\|BBB | | MOD_TEST.md | 1 | private |\n"
        )
        entries = parse_mod_index(text)
        self.assertEqual(len(entries), 1)
        self.assertIn("AAA|BBB", entries[0].scope_signals,
                      "Escaped pipe should be unescaped to literal |")


# ── Test: ModCaptureTests — unit tests for mod_capture internals ─────────


class ModCaptureTests(unittest.TestCase):
    """Unit tests for mod_capture.py internal validation logic.

    Directly tests _validate_request, _validate_source, _build_index_entry,
    and _build_mod_content without subprocess E2E overhead.
    """

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp(prefix="mod_capture_unit_"))

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    # ── _validate_request: pipe handling ─────────────────────────────────

    def test_validate_request_bare_pipe_in_scope_rejected(self):
        req = CaptureRequest(
            mod_name="T", action="create", source=Path("."),
            scope_signals="sig::a|b", aliases="", exclusion_signals="",
        )
        with self.assertRaises(CaptureError):
            mod_capture._validate_request(req)

    def test_validate_request_escaped_pipe_in_scope_accepted(self):
        req = CaptureRequest(
            mod_name="T", action="create", source=Path("."),
            scope_signals=r"sig::a\|b", aliases="", exclusion_signals="",
        )
        mod_capture._validate_request(req)  # Should not raise

    def test_validate_request_bare_pipe_in_aliases_rejected(self):
        req = CaptureRequest(
            mod_name="T", action="create", source=Path("."),
            scope_signals="s", aliases="a|b", exclusion_signals="",
        )
        with self.assertRaises(CaptureError):
            mod_capture._validate_request(req)

    def test_validate_request_escaped_pipe_in_aliases_accepted(self):
        req = CaptureRequest(
            mod_name="T", action="create", source=Path("."),
            scope_signals="s", aliases=r"a\|b", exclusion_signals="",
        )
        mod_capture._validate_request(req)

    def test_validate_request_bare_pipe_in_exclusion_rejected(self):
        req = CaptureRequest(
            mod_name="T", action="create", source=Path("."),
            scope_signals="s", aliases="", exclusion_signals="ex|cl",
        )
        with self.assertRaises(CaptureError):
            mod_capture._validate_request(req)

    def test_validate_request_escaped_pipe_in_exclusion_accepted(self):
        req = CaptureRequest(
            mod_name="T", action="create", source=Path("."),
            scope_signals="s", aliases="", exclusion_signals=r"ex\|cl",
        )
        mod_capture._validate_request(req)

    def test_validate_request_double_backslash_pipe_rejected(self):
        """Double backslash + bare pipe (\\\\|) -> still rejected."""
        req = CaptureRequest(
            mod_name="T", action="create", source=Path("."),
            scope_signals="sig::a\\\\|b", aliases="", exclusion_signals="",
        )
        with self.assertRaises(CaptureError):
            mod_capture._validate_request(req)

    def test_validate_request_multiple_escaped_pipes_accepted(self):
        req = CaptureRequest(
            mod_name="T", action="create", source=Path("."),
            scope_signals=r"sig::a\|b\|c", aliases="", exclusion_signals="",
        )
        mod_capture._validate_request(req)

    def test_validate_request_invalid_action_rejected(self):
        req = CaptureRequest(
            mod_name="T", action="delete", source=Path("."),
            scope_signals="s", aliases="", exclusion_signals="",
        )
        with self.assertRaises(CaptureError):
            mod_capture._validate_request(req)

    def test_validate_request_empty_scope_rejected(self):
        req = CaptureRequest(
            mod_name="T", action="create", source=Path("."),
            scope_signals="", aliases="", exclusion_signals="",
        )
        with self.assertRaises(CaptureError):
            mod_capture._validate_request(req)

    # ── _validate_source ────────────────────────────────────────────────

    def test_validate_source_with_valid_rules_returns_rules(self):
        src = self.tmpdir / "valid.md"
        src.write_text(
            "| Rule ID | Group | Gate | Description | Applies to | Notes |\n"
            "|---------|-------|------|-------------|------------|-------|\n"
            "| R01 | mapping | mod_gate | Test. | * | |\n",
            encoding="utf-8",
        )
        rules = mod_capture._validate_source(src)
        self.assertEqual(len(rules), 1)
        self.assertEqual(rules[0].rule_id, "R01")

    def test_validate_source_with_no_rules_raises(self):
        src = self.tmpdir / "empty.md"
        src.write_text(
            "| Rule ID | Group | Gate | Description | Applies to | Notes |\n"
            "|---------|-------|------|-------------|------------|-------|\n",
            encoding="utf-8",
        )
        with self.assertRaises(CaptureError):
            mod_capture._validate_source(src)

    def test_validate_source_with_no_table_raises(self):
        src = self.tmpdir / "notable.md"
        src.write_text("# Just text\nNo table here.\n", encoding="utf-8")
        with self.assertRaises(CaptureError):
            mod_capture._validate_source(src)

    def test_validate_source_missing_file_raises(self):
        with self.assertRaises(CaptureError):
            mod_capture._validate_source(Path("/nonexistent/file.md"))

    def test_validate_source_r01_and_rte001_both_accepted(self):
        src = self.tmpdir / "mixed.md"
        src.write_text(
            "| Rule ID | Group | Gate | Description | Applies to | Notes |\n"
            "|---------|-------|------|-------------|------------|-------|\n"
            "| R01 | mapping | mod_gate | Short. | * | |\n"
            "| RTE-001 | validation | execution_gate | Long. | * | |\n",
            encoding="utf-8",
        )
        rules = mod_capture._validate_source(src)
        self.assertEqual(len(rules), 2)
        self.assertEqual(rules[0].rule_id, "R01")
        self.assertEqual(rules[1].rule_id, "RTE-001")

    # ── _build_index_entry ──────────────────────────────────────────────

    def test_build_index_entry_has_correct_fields(self):
        req = CaptureRequest(
            mod_name="MYMOD", action="create", source=Path("."),
            scope_signals="sig::test", aliases="a1, a2",
            exclusion_signals="excl::x",
        )
        entry = mod_capture._build_index_entry(req)
        self.assertEqual(entry.mod_name, "MYMOD")
        self.assertEqual(entry.aliases, "a1, a2")
        self.assertEqual(entry.scope_signals, "sig::test")
        self.assertEqual(entry.exclusion_signals, "excl::x")
        self.assertEqual(entry.path, "MOD_MYMOD.md")
        self.assertEqual(entry.revision, 1)
        self.assertEqual(entry.visibility, "private")

    # ── _build_mod_content ──────────────────────────────────────────────

    def test_build_mod_content_contains_rule_count(self):
        req = CaptureRequest(
            mod_name="TEST", action="create", source=Path("."),
            scope_signals="sig::t", aliases="", exclusion_signals="",
        )
        content = mod_capture._build_mod_content("TEST", "body", req, 3)
        self.assertIn("Rule count: 3", content)
        self.assertIn("MOD_TEST", content)

    def test_build_mod_content_with_revision(self):
        req = CaptureRequest(
            mod_name="REV", action="create", source=Path("."),
            scope_signals="sig::t", aliases="", exclusion_signals="",
        )
        content = mod_capture._build_mod_content("REV", "body", req, 1, revision=5)
        self.assertIn("Revision: 5", content)


# ── Test: _mod_catalog Rule ID validation ────────────────────────────────


class TestRuleIdValidation(unittest.TestCase):
    """_mod_catalog.parse_mod_rules validates Rule ID format."""

    def test_valid_short_id_accepted(self):
        text = (
            "| Rule ID | Group | Gate | Description | Applies to | Notes |\n"
            "|---------|-------|------|-------------|------------|-------|\n"
            "| R01 | mapping | mod_gate | Test. | * | |\n"
        )
        rules = parse_mod_rules(text)
        self.assertEqual(len(rules), 1)

    def test_valid_long_id_accepted(self):
        text = (
            "| Rule ID | Group | Gate | Description | Applies to | Notes |\n"
            "|---------|-------|------|-------------|------------|-------|\n"
            "| RTE-001 | mapping | mod_gate | Test. | * | |\n"
        )
        rules = parse_mod_rules(text)
        self.assertEqual(len(rules), 1)

    def test_lowercase_id_rejected(self):
        text = (
            "| Rule ID | Group | Gate | Description | Applies to | Notes |\n"
            "|---------|-------|------|-------------|------------|-------|\n"
            "| r01 | mapping | mod_gate | Test. | * | |\n"
        )
        with self.assertRaises(ModRuleParseError):
            parse_mod_rules(text)

    def test_numeric_only_id_rejected(self):
        text = (
            "| Rule ID | Group | Gate | Description | Applies to | Notes |\n"
            "|---------|-------|------|-------------|------------|-------|\n"
            "| 001 | mapping | mod_gate | Test. | * | |\n"
        )
        with self.assertRaises(ModRuleParseError):
            parse_mod_rules(text)

    def test_garbage_id_rejected(self):
        text = (
            "| Rule ID | Group | Gate | Description | Applies to | Notes |\n"
            "|---------|-------|------|-------------|------------|-------|\n"
            "| 这不是规则 | mapping | mod_gate | Test. | * | |\n"
        )
        with self.assertRaises(ModRuleParseError):
            parse_mod_rules(text)

    def test_mixed_valid_and_invalid_rows(self):
        """Valid rows are kept; invalid rows are silently skipped."""
        text = (
            "| Rule ID | Group | Gate | Description | Applies to | Notes |\n"
            "|---------|-------|------|-------------|------------|-------|\n"
            "| R01 | mapping | mod_gate | Valid. | * | |\n"
            "| not-a-rule | mapping | mod_gate | Invalid. | * | |\n"
            "| RTE-002 | validation | execution_gate | Also valid. | * | |\n"
        )
        rules = parse_mod_rules(text)
        self.assertEqual(len(rules), 2)
        self.assertEqual(rules[0].rule_id, "R01")
        self.assertEqual(rules[1].rule_id, "RTE-002")


class TestDisplayNameAndNominationEvaluators(unittest.TestCase):
    """2026-08-18 Skill Development (MXP 提名复盘): ① MOD Metadata
    Display Name (中文展示名) 解析与候选输出回退; ② 六角色排除 evaluator
    (此前该排除信号无验证器 → 恒 pending); ③ block_layout digest 验证器
    (此前结构信号只有 dimension_set 可验证)。"""

    def test_parse_mod_file_extracts_display_name(self):
        with tempfile.TemporaryDirectory() as td:
            mods = Path(td)
            (mods / "MOD_x.md").write_text(
                "# MOD_x\n\n## Metadata\n\n- Scope Signals: s::1\n"
                "- Aliases: x\n- Display Name: 测试中文名\n"
                "- Exclusion Signals: e::1\n\n"
                "| Rule ID | Group | Gate | Description | Applies to | Notes |\n"
                "|---------|-------|------|-------------|------------|-------|\n"
                "| R01 | mapping | mod_gate | rule | * | |\n",
                encoding="utf-8")
            parsed = mod_nominate.parse_mod_file(mods, "MOD_x.md")
            self.assertEqual(parsed["display_name"], "测试中文名")

    def test_evaluate_entry_display_name_fallback_to_english_name(self):
        with tempfile.TemporaryDirectory() as td:
            mods = Path(td)
            (mods / "MOD_y.md").write_text(
                "# MOD_y\n\n## Metadata\n\n- Scope Signals: s::1\n"
                "- Exclusion Signals: e::1\n\n"
                "| Rule ID | Group | Gate | Description | Applies to | Notes |\n"
                "|---------|-------|------|-------------|------------|-------|\n"
                "| R01 | mapping | mod_gate | rule | * | |\n",
                encoding="utf-8")
            entry = {"name": "MOD_y", "aliases": "", "scope": "semantic_type::quotation",
                     "exclusion": "目标缺少客户报价六角色表头指纹",
                     "path": "MOD_y.md", "revision": 1, "visibility": "private"}
            cand, _ = mod_nominate._evaluate_entry(
                entry, mods, "报价单 客户报价 quotation", [], [])
            self.assertEqual(cand["display_name"], "MOD_y",
                             "无 Display Name → 回退英文名 (机器身份不变)")

    def test_six_field_exclusion_not_fired_when_roles_present(self):
        digests = ["- 表头: Type | Model | C&H capacity | Connecting pipe | "
                   "Unit Price (USD/SET) | Panel looking\n- 合并区(2): A1:F1, A25:E25"]
        fired, pending = mod_nominate.exclusion_checks(
            "目标缺少客户报价六角色表头指纹", "ev", digests, [])
        self.assertEqual(fired, [], "六角色齐 → 排除不得触发")
        self.assertEqual(pending, [], "有验证器 → 不得进 pending_exclusions")

    def test_six_field_exclusion_fired_when_roles_missing(self):
        digests = ["- 表头: 类别 | 型号 | 数量 | 报价"]
        fired, pending = mod_nominate.exclusion_checks(
            "目标缺少客户报价六角色表头指纹", "ev", digests, [])
        self.assertTrue(fired, "角色缺失 → 排除应触发")
        self.assertIn("缺少客户报价角色", fired[0]["reason"])

    def test_six_field_exclusion_evidence_missing_fired_with_hint(self):
        fired, pending = mod_nominate.exclusion_checks(
            "目标缺少客户报价六角色表头指纹", "ev", [], [])
        self.assertTrue(fired)
        self.assertIn("证据缺失", fired[0]["reason"],
                      "证据缺失 → fired with hint (勿当结构不符)")

    def test_block_layout_customer_quote_verified_from_digest(self):
        digests = ["- 表头: Type | Model | C&H capacity | Connecting pipe | "
                   "Unit Price (USD/SET) | Panel looking\n"
                   "- 合并区(34): A1:F1, A7:A10, A25:E25, A34:F34"]
        r = mod_nominate.signal_matched(
            "block_layout", "customer_quote_header_data_total_terms", "ev", digests, [])
        self.assertIs(r, True, "六角色表头 + 合并区 + 宽合并 → 布局指纹验证为 hit")

    def test_block_layout_pending_without_digest(self):
        r = mod_nominate.signal_matched(
            "block_layout", "customer_quote_header_data_total_terms", "ev", [], [])
        self.assertIsNone(r, "无 digest 证据 → pending (不冒充命中)")

    def test_block_layout_miss_when_header_roles_missing(self):
        digests = ["- 表头: 类别 | 型号\n- 合并区(1): A1:F1"]
        r = mod_nominate.signal_matched(
            "block_layout", "customer_quote_header_data_total_terms", "ev", digests, [])
        self.assertIs(r, False, "表头角色缺失 → miss")


if __name__ == "__main__":
    unittest.main()