"""Round-trip tests for MOD parser unification (issue #10).

Verifies that capture → index → nominate works for:
  1. R01-style Rule IDs (not just XXX-NNN)
  2. Escaped pipe characters (\\|) in scope signals
  3. Bare pipe characters are still rejected by capture
  4. mod_nominate consumes _mod_catalog (single parser source)

Uses only Python standard library.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

# ── Test environment setup ────────────────────────────────────────────────

_SKILL_ROOT = os.environ.get(
    "TABLE_FILL_SKILL_ROOT",
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
)
_REFS_DIR = os.path.join(_SKILL_ROOT, "references")
_SCRIPTS_DIR = os.path.join(_SKILL_ROOT, "scripts")
_MOD_CAPTURE_PATH = os.path.join(_SCRIPTS_DIR, "mod_capture.py")
_MOD_NOMINATE_PATH = os.path.join(_SCRIPTS_DIR, "mod_nominate.py")
_MOD_INDEX_PATH = os.path.join(_REFS_DIR, "MOD_INDEX.md")

if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)


# ── Helpers ────────────────────────────────────────────────────────────────


def _read_index_bytes() -> bytes:
    with open(_MOD_INDEX_PATH, "rb") as fh:
        return fh.read()


def _write_index_bytes(data: bytes) -> None:
    with open(_MOD_INDEX_PATH, "wb") as fh:
        fh.write(data)


def _valid_source_rules(*rule_rows: str) -> str:
    """Build a valid six-column rule table with header and at least one rule."""
    lines = [
        "| Rule ID | Group | Gate | Description | Applies to | Notes |",
        "|---------|-------|------|-------------|------------|-------|",
    ]
    if not rule_rows:
        rule_rows = (
            "| R01 | mapping | mod_gate | Test rule description. | * | |",
        )
    lines.extend(rule_rows)
    return "\n".join(lines) + "\n"


def _run_capture(*args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    cmd = [sys.executable, "-X", "utf8", _MOD_CAPTURE_PATH, *args]
    return subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8",
        timeout=30, env=env,
    )


# ── Test: mod_nominate consumes _mod_catalog (single parser source) ──────


class TestNominateConsumesCatalog(unittest.TestCase):
    """mod_nominate.parse_index and parse_rule_table delegate to _mod_catalog."""

    def test_parse_index_returns_dicts_with_expected_keys(self):
        """parse_index returns dicts with name/aliases/scope/exclusion/path/revision/visibility."""
        from mod_nominate import parse_index

        idx = Path(_MOD_INDEX_PATH)
        if not idx.is_file():
            self.skipTest("MOD_INDEX.md not found")
        entries = parse_index(idx)
        if not entries:
            self.skipTest("No entries in MOD_INDEX.md")
        e = entries[0]
        for key in ("name", "aliases", "scope", "exclusion", "path", "revision", "visibility"):
            self.assertIn(key, e, f"Missing key '{key}' in parse_index result")

    def test_parse_rule_table_accepts_r01_format(self):
        """parse_rule_table accepts R01-style Rule IDs (no longer silently skipped)."""
        from mod_nominate import parse_rule_table

        text = (
            "| Rule ID | Group | Gate | Description | Applies to | Notes |\n"
            "|---------|-------|------|-------------|------------|-------|\n"
            "| R01 | mapping | mod_gate | Short ID rule. | * | |\n"
            "| RTE-001 | validation | execution_gate | Long ID rule. | * | |\n"
        )
        rules = parse_rule_table(text)
        self.assertEqual(len(rules), 2, f"Expected 2 rules, got {len(rules)}")
        self.assertEqual(rules[0]["id"], "R01")
        self.assertEqual(rules[1]["id"], "RTE-001")

    def test_parse_rule_table_r01_not_skipped(self):
        """R01 rules are NOT silently skipped (regression: old regex rejected them)."""
        from mod_nominate import parse_rule_table

        text = (
            "| Rule ID | Group | Gate | Description | Applies to | Notes |\n"
            "|---------|-------|------|-------------|------------|-------|\n"
            "| R01 | mapping | mod_gate | Should not be skipped. | * | |\n"
        )
        rules = parse_rule_table(text)
        self.assertEqual(len(rules), 1, "R01 was silently skipped — parser divergence!")
        self.assertEqual(rules[0]["id"], "R01")

    def test_parse_rule_table_all_formats_accepted(self):
        """Various Rule ID formats are all accepted."""
        from mod_nominate import parse_rule_table

        text = (
            "| Rule ID | Group | Gate | Description | Applies to | Notes |\n"
            "|---------|-------|------|-------------|------------|-------|\n"
            "| R01 | mapping | mod_gate | Short. | * | |\n"
            "| RTE-001 | mapping | mod_gate | Long. | * | |\n"
            "| FLD-002 | validation | execution_gate | Field. | * | |\n"
            "| TGT-001 | mapping | mod_gate | Target. | * | |\n"
            "| VAL-001 | validation | execution_gate | Validate. | * | |\n"
        )
        rules = parse_rule_table(text)
        self.assertEqual(len(rules), 5)
        ids = [r["id"] for r in rules]
        self.assertEqual(ids, ["R01", "RTE-001", "FLD-002", "TGT-001", "VAL-001"])


# ── Test: R01 Rule ID round-trip through capture → index → nominate ──────


class TestR01RoundTrip(unittest.TestCase):
    """Capture a MOD with R01 Rule ID → verify index and nominate can read it."""

    def setUp(self):
        self.index_backup = _read_index_bytes()
        self.tmpdir = tempfile.mkdtemp()
        self._created_mod_names: list[str] = []

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        for name in self._created_mod_names:
            for suffix in (f"MOD_{name}.md", f"MOD_{name}.md.bak"):
                p = os.path.join(_REFS_DIR, suffix)
                try:
                    os.remove(p)
                except OSError:
                    pass
        # Remove .bak files from index updates
        bak = _MOD_INDEX_PATH + ".bak"
        if os.path.isfile(bak):
            try:
                os.remove(bak)
            except OSError:
                pass
        try:
            _write_index_bytes(self.index_backup)
        except Exception:
            pass

    def test_capture_with_r01_rule_id_succeeds(self):
        """Capture a MOD source with R01 Rule ID → capture exits 0."""
        source = os.path.join(self.tmpdir, "r01_source.md")
        Path(source).write_text(_valid_source_rules(
            "| R01 | mapping | mod_gate | R01 rule description. | * | |",
        ), encoding="utf-8")

        result = _run_capture(
            "--source", source,
            "--mod-name", "R01_TEST",
            "--action", "create",
            "--scope-signals", "semantic_type::test_r01",
        )
        self.assertEqual(result.returncode, 0,
                         f"Capture failed: stdout={result.stdout} stderr={result.stderr}")
        output = json.loads(result.stdout)
        self.assertEqual(output.get("exit_code"), 0)
        self.assertEqual(output.get("rule_count"), 1)
        self._created_mod_names.append("R01_TEST")

    def test_r01_roundtrip_capture_index_nominate(self):
        """R01 Rule ID round-trip: capture → index → nominate can read rules."""
        from _mod_catalog import parse_mod_index, resolve_by_name_or_alias, parse_mod_rules

        mod_name = "R01_RT"
        source = os.path.join(self.tmpdir, "r01_rt.md")
        Path(source).write_text(_valid_source_rules(
            "| R01 | mapping | mod_gate | First R01 rule. | * | |",
            "| R02 | validation | execution_gate | Second R02 rule. | * | Check. |",
        ), encoding="utf-8")

        # Step 1: Capture
        result = _run_capture(
            "--source", source,
            "--mod-name", mod_name,
            "--action", "create",
            "--scope-signals", "semantic_type::test_r01_rt",
        )
        self.assertEqual(result.returncode, 0, f"Capture failed: {result.stderr}")
        self._created_mod_names.append(mod_name)

        # Step 2: Verify index via _mod_catalog
        index_text = Path(_MOD_INDEX_PATH).read_text(encoding="utf-8")
        entries = parse_mod_index(index_text)
        resolved = resolve_by_name_or_alias(entries, mod_name)
        self.assertIsNotNone(resolved, f"MOD '{mod_name}' not found in index after capture")
        self.assertEqual(resolved.mod_name, mod_name)
        self.assertEqual(resolved.revision, 1)
        self.assertEqual(resolved.visibility, "private")

        # Step 3: Verify rules via _mod_catalog
        mod_path = os.path.join(_REFS_DIR, f"MOD_{mod_name}.md")
        rules = parse_mod_rules(Path(mod_path).read_text(encoding="utf-8"))
        self.assertEqual(len(rules), 2)
        self.assertEqual(rules[0].rule_id, "R01")
        self.assertEqual(rules[1].rule_id, "R02")

        # Step 4: Verify nominate can read rules (via parse_rule_table)
        from mod_nominate import parse_rule_table
        nominate_rules = parse_rule_table(Path(mod_path).read_text(encoding="utf-8"))
        self.assertEqual(len(nominate_rules), 2,
                         "mod_nominate.parse_rule_table should accept R01 rules")
        self.assertEqual(nominate_rules[0]["id"], "R01")
        self.assertEqual(nominate_rules[1]["id"], "R02")

    def test_r01_roundtrip_via_nominate_parse_index(self):
        """R01 MOD appears in nominate's parse_index output (not skipped)."""
        from mod_nominate import parse_index

        mod_name = "R01_IDX"
        source = os.path.join(self.tmpdir, "r01_idx.md")
        Path(source).write_text(_valid_source_rules(
            "| R01 | mapping | mod_gate | Index test. | * | |",
        ), encoding="utf-8")

        result = _run_capture(
            "--source", source,
            "--mod-name", mod_name,
            "--action", "create",
            "--scope-signals", "semantic_type::r01_idx",
        )
        self.assertEqual(result.returncode, 0, f"Capture failed: {result.stderr}")
        self._created_mod_names.append(mod_name)

        # Verify nominate's parse_index finds the entry
        entries = parse_index(Path(_MOD_INDEX_PATH))
        found = [e for e in entries if e["name"] == mod_name]
        self.assertEqual(len(found), 1,
                         f"MOD '{mod_name}' not found via nominate.parse_index")
        self.assertEqual(found[0]["scope"], "semantic_type::r01_idx")


# ── Test: Escaped pipe round-trip ────────────────────────────────────────


class TestEscapedPipeRoundTrip(unittest.TestCase):
    """Capture with escaped pipe \\| in scope signals → nominate can read it."""

    def setUp(self):
        self.index_backup = _read_index_bytes()
        self.tmpdir = tempfile.mkdtemp()
        self._created_mod_names: list[str] = []

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        for name in self._created_mod_names:
            for suffix in (f"MOD_{name}.md", f"MOD_{name}.md.bak"):
                p = os.path.join(_REFS_DIR, suffix)
                try:
                    os.remove(p)
                except OSError:
                    pass
        bak = _MOD_INDEX_PATH + ".bak"
        if os.path.isfile(bak):
            try:
                os.remove(bak)
            except OSError:
                pass
        try:
            _write_index_bytes(self.index_backup)
        except Exception:
            pass

    def test_capture_with_escaped_pipe_in_scope_succeeds(self):
        """Capture a MOD with sheet_marker::三三三\\|333 → capture exits 0."""
        source = os.path.join(self.tmpdir, "pipe_source.md")
        Path(source).write_text(_valid_source_rules(
            "| R01 | mapping | mod_gate | Pipe rule. | * | |",
        ), encoding="utf-8")

        result = _run_capture(
            "--source", source,
            "--mod-name", "PIPE_TEST",
            "--action", "create",
            "--scope-signals", r"sheet_marker::三三三\|333",
        )
        self.assertEqual(result.returncode, 0,
                         f"Capture with escaped pipe failed: "
                         f"stdout={result.stdout} stderr={result.stderr}")
        output = json.loads(result.stdout)
        self.assertEqual(output.get("exit_code"), 0)
        self.assertIn(r"\|", output.get("scope_signals", ""),
                      "Escaped pipe should be preserved in output")
        self._created_mod_names.append("PIPE_TEST")

    def test_escaped_pipe_roundtrip_capture_index_nominate(self):
        """Escaped pipe round-trip: capture → index → nominate can read scope signals."""
        from _mod_catalog import parse_mod_index, resolve_by_name_or_alias

        mod_name = "PIPE_RT"
        source = os.path.join(self.tmpdir, "pipe_rt.md")
        Path(source).write_text(_valid_source_rules(
            "| R01 | mapping | mod_gate | Pipe roundtrip. | * | |",
        ), encoding="utf-8")

        # Step 1: Capture with escaped pipe
        result = _run_capture(
            "--source", source,
            "--mod-name", mod_name,
            "--action", "create",
            "--scope-signals", r"sheet_marker::三三三\|333",
        )
        self.assertEqual(result.returncode, 0, f"Capture failed: {result.stderr}")
        self._created_mod_names.append(mod_name)

        # Step 2: Verify index via _mod_catalog — escaped pipe is unescaped to |
        index_text = Path(_MOD_INDEX_PATH).read_text(encoding="utf-8")
        entries = parse_mod_index(index_text)
        resolved = resolve_by_name_or_alias(entries, mod_name)
        self.assertIsNotNone(resolved, f"MOD '{mod_name}' not found in index")
        # _mod_catalog unescapes \| → | in the parsed scope_signals
        self.assertIn("三三三|333", resolved.scope_signals,
                      "Escaped pipe should be unescaped to literal | in parsed signals")

        # Step 3: Verify nominate's parse_index also finds it
        from mod_nominate import parse_index
        nominate_entries = parse_index(Path(_MOD_INDEX_PATH))
        found = [e for e in nominate_entries if e["name"] == mod_name]
        self.assertEqual(len(found), 1)
        # nominate also unescapes \| → |
        self.assertIn("三三三|333", found[0]["scope"],
                      "nominate should also unescape \\| to |")

    def test_bare_pipe_in_scope_signals_rejected(self):
        """Bare pipe (not escaped) in scope signals → capture rejects."""
        source = os.path.join(self.tmpdir, "bare_pipe.md")
        Path(source).write_text(_valid_source_rules(
            "| R01 | mapping | mod_gate | Bare pipe rule. | * | |",
        ), encoding="utf-8")

        result = _run_capture(
            "--source", source,
            "--mod-name", "BARE_PIPE",
            "--action", "create",
            "--scope-signals", "sheet_marker::三三三|333",
        )
        self.assertNotEqual(result.returncode, 0,
                            "Bare pipe should be rejected by capture")
        output = json.loads(result.stdout)
        self.assertNotEqual(output.get("exit_code"), 0)
        self.assertIn("pipe", output.get("error", "").lower())

    def test_bare_pipe_in_aliases_rejected(self):
        """Bare pipe in aliases → capture rejects."""
        source = os.path.join(self.tmpdir, "bare_pipe_alias.md")
        Path(source).write_text(_valid_source_rules(
            "| R01 | mapping | mod_gate | Alias pipe. | * | |",
        ), encoding="utf-8")

        result = _run_capture(
            "--source", source,
            "--mod-name", "BARE_ALIAS",
            "--action", "create",
            "--scope-signals", "sig::test",
            "--aliases", "a|b",
        )
        self.assertNotEqual(result.returncode, 0)

    def test_bare_pipe_in_exclusion_signals_rejected(self):
        """Bare pipe in exclusion signals → capture rejects."""
        source = os.path.join(self.tmpdir, "bare_pipe_excl.md")
        Path(source).write_text(_valid_source_rules(
            "| R01 | mapping | mod_gate | Excl pipe. | * | |",
        ), encoding="utf-8")

        result = _run_capture(
            "--source", source,
            "--mod-name", "BARE_EXCL",
            "--action", "create",
            "--scope-signals", "sig::test",
            "--exclusion-signals", "ex|cl",
        )
        self.assertNotEqual(result.returncode, 0)


# ── Test: mod_nominate.parse_index delegates to _mod_catalog ─────────────


class TestParseIndexDelegation(unittest.TestCase):
    """mod_nominate.parse_index uses _mod_catalog.parse_mod_index internally."""

    def test_parse_index_source_code_references_mod_catalog(self):
        """mod_nominate.py source code should import from _mod_catalog."""
        src = Path(_MOD_NOMINATE_PATH)
        if not src.exists():
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
        src = Path(_MOD_NOMINATE_PATH)
        if not src.exists():
            self.skipTest("mod_nominate.py not found")
        text = src.read_text(encoding="utf-8")
        # The old duplicate parser had "re.split(r\"(?<!\\\\)\\|\"" in parse_index
        # After unification, parse_index should be a thin wrapper
        # Check that parse_index is a short function (delegate, not parser)
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
        # A thin wrapper should be ~3 lines; the old parser was ~15 lines
        self.assertLessEqual(len(func_lines), 5,
                             f"parse_index should be a thin wrapper, "
                             f"but has {len(func_lines)} lines — still has duplicate parser?")

    def test_parse_rule_table_no_regex_filter(self):
        """mod_nominate.py should NOT filter Rule IDs by regex anymore."""
        src = Path(_MOD_NOMINATE_PATH)
        if not src.exists():
            self.skipTest("mod_nominate.py not found")
        text = src.read_text(encoding="utf-8")
        # The old parser had: re.fullmatch(r"[A-Z]+(?:-[A-Z]+)?-\d{3}", cells[0])
        self.assertNotIn(r"[A-Z]+(?:-[A-Z]+)?-\\d{3}", text,
                         "mod_nominate.py still has the restrictive Rule ID regex")
        # Also check for the non-escaped version in the source
        self.assertNotIn("fullmatch", text.split("def parse_rule_table")[1].split("\ndef ")[0]
                         if "def parse_rule_table" in text else "",
                         "parse_rule_table still uses fullmatch regex filter")


# ── Test: _mod_catalog accepts both R01 and RTE-001 ───────────────────────


class TestCatalogAcceptsBothIdFormats(unittest.TestCase):
    """_mod_catalog.parse_mod_rules accepts both R01 and RTE-001 Rule IDs."""

    def test_parse_mod_rules_accepts_r01(self):
        from _mod_catalog import parse_mod_rules

        text = (
            "| Rule ID | Group | Gate | Description | Applies to | Notes |\n"
            "|---------|-------|------|-------------|------------|-------|\n"
            "| R01 | mapping | mod_gate | Short ID. | * | |\n"
        )
        rules = parse_mod_rules(text)
        self.assertEqual(len(rules), 1)
        self.assertEqual(rules[0].rule_id, "R01")

    def test_parse_mod_rules_accepts_rte_001(self):
        from _mod_catalog import parse_mod_rules

        text = (
            "| Rule ID | Group | Gate | Description | Applies to | Notes |\n"
            "|---------|-------|------|-------------|------------|-------|\n"
            "| RTE-001 | mapping | mod_gate | Long ID. | * | |\n"
        )
        rules = parse_mod_rules(text)
        self.assertEqual(len(rules), 1)
        self.assertEqual(rules[0].rule_id, "RTE-001")

    def test_parse_mod_rules_accepts_mixed_formats(self):
        from _mod_catalog import parse_mod_rules

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
        """The fixture's escaped-pipe content is parsed correctly when embedded
        in a proper ## Registered MODs section."""
        from _mod_catalog import parse_mod_index

        # Use the same data as MOD_INDEX_marker.md but wrapped in a proper section
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
        # Escaped \| in source should be unescaped to | in parsed value
        self.assertIn("三三三|333", entries[0].scope_signals)

    def test_parse_index_escaped_pipe_in_synthetic_text(self):
        """Synthetic index text with \\| is parsed and unescaped."""
        from _mod_catalog import parse_mod_index

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
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    # ── _validate_request: pipe handling ─────────────────────────────────

    def test_validate_request_bare_pipe_in_scope_rejected(self):
        """Bare pipe in scope_signals → raises CaptureError."""
        from mod_capture import CaptureRequest, _validate_request, CaptureError

        req = CaptureRequest(
            mod_name="T", action="create", source=Path("."),
            scope_signals="sig::a|b", aliases="", exclusion_signals="",
        )
        with self.assertRaises(CaptureError):
            _validate_request(req)

    def test_validate_request_escaped_pipe_in_scope_accepted(self):
        """Escaped pipe (\\|) in scope_signals → no error."""
        from mod_capture import CaptureRequest, _validate_request

        req = CaptureRequest(
            mod_name="T", action="create", source=Path("."),
            scope_signals=r"sig::a\|b", aliases="", exclusion_signals="",
        )
        _validate_request(req)  # Should not raise

    def test_validate_request_bare_pipe_in_aliases_rejected(self):
        """Bare pipe in aliases → raises CaptureError."""
        from mod_capture import CaptureRequest, _validate_request, CaptureError

        req = CaptureRequest(
            mod_name="T", action="create", source=Path("."),
            scope_signals="s", aliases="a|b", exclusion_signals="",
        )
        with self.assertRaises(CaptureError):
            _validate_request(req)

    def test_validate_request_escaped_pipe_in_aliases_accepted(self):
        """Escaped pipe (\\|) in aliases → no error."""
        from mod_capture import CaptureRequest, _validate_request

        req = CaptureRequest(
            mod_name="T", action="create", source=Path("."),
            scope_signals="s", aliases=r"a\|b", exclusion_signals="",
        )
        _validate_request(req)

    def test_validate_request_bare_pipe_in_exclusion_rejected(self):
        """Bare pipe in exclusion_signals → raises CaptureError."""
        from mod_capture import CaptureRequest, _validate_request, CaptureError

        req = CaptureRequest(
            mod_name="T", action="create", source=Path("."),
            scope_signals="s", aliases="", exclusion_signals="ex|cl",
        )
        with self.assertRaises(CaptureError):
            _validate_request(req)

    def test_validate_request_escaped_pipe_in_exclusion_accepted(self):
        """Escaped pipe (\\|) in exclusion_signals → no error."""
        from mod_capture import CaptureRequest, _validate_request

        req = CaptureRequest(
            mod_name="T", action="create", source=Path("."),
            scope_signals="s", aliases="", exclusion_signals=r"ex\|cl",
        )
        _validate_request(req)

    def test_validate_request_double_backslash_pipe_rejected(self):
        """Double backslash + bare pipe (\\\\|) → still rejected."""
        from mod_capture import CaptureRequest, _validate_request, CaptureError

        req = CaptureRequest(
            mod_name="T", action="create", source=Path("."),
            scope_signals="sig::a\\\\|b", aliases="", exclusion_signals="",
        )
        with self.assertRaises(CaptureError):
            _validate_request(req)

    def test_validate_request_multiple_escaped_pipes_accepted(self):
        """Multiple escaped pipes in scope → no error."""
        from mod_capture import CaptureRequest, _validate_request

        req = CaptureRequest(
            mod_name="T", action="create", source=Path("."),
            scope_signals=r"sig::a\|b\|c", aliases="", exclusion_signals="",
        )
        _validate_request(req)

    def test_validate_request_invalid_action_rejected(self):
        """Invalid action → raises CaptureError."""
        from mod_capture import CaptureRequest, _validate_request, CaptureError

        req = CaptureRequest(
            mod_name="T", action="delete", source=Path("."),
            scope_signals="s", aliases="", exclusion_signals="",
        )
        with self.assertRaises(CaptureError):
            _validate_request(req)

    def test_validate_request_empty_scope_rejected(self):
        """Empty scope_signals → raises CaptureError."""
        from mod_capture import CaptureRequest, _validate_request, CaptureError

        req = CaptureRequest(
            mod_name="T", action="create", source=Path("."),
            scope_signals="", aliases="", exclusion_signals="",
        )
        with self.assertRaises(CaptureError):
            _validate_request(req)

    # ── _validate_source ────────────────────────────────────────────────

    def test_validate_source_with_valid_rules_returns_rules(self):
        """Valid source with R01 rules → returns list of rules."""
        from mod_capture import _validate_source

        src = Path(self.tmpdir) / "valid.md"
        src.write_text(
            "| Rule ID | Group | Gate | Description | Applies to | Notes |\n"
            "|---------|-------|------|-------------|------------|-------|\n"
            "| R01 | mapping | mod_gate | Test. | * | |\n",
            encoding="utf-8",
        )
        rules = _validate_source(src)
        self.assertEqual(len(rules), 1)
        self.assertEqual(rules[0].rule_id, "R01")

    def test_validate_source_with_no_rules_raises(self):
        """Source with header but no rules → raises CaptureError."""
        from mod_capture import _validate_source, CaptureError

        src = Path(self.tmpdir) / "empty.md"
        src.write_text(
            "| Rule ID | Group | Gate | Description | Applies to | Notes |\n"
            "|---------|-------|------|-------------|------------|-------|\n",
            encoding="utf-8",
        )
        with self.assertRaises(CaptureError):
            _validate_source(src)

    def test_validate_source_with_no_table_raises(self):
        """Source with no table at all → raises CaptureError."""
        from mod_capture import _validate_source, CaptureError

        src = Path(self.tmpdir) / "notable.md"
        src.write_text("# Just text\nNo table here.\n", encoding="utf-8")
        with self.assertRaises(CaptureError):
            _validate_source(src)

    def test_validate_source_missing_file_raises(self):
        """Nonexistent source → raises CaptureError."""
        from mod_capture import _validate_source, CaptureError

        with self.assertRaises(CaptureError):
            _validate_source(Path("/nonexistent/file.md"))

    def test_validate_source_r01_and_rte001_both_accepted(self):
        """Both R01 and RTE-001 Rule IDs accepted by source validation."""
        from mod_capture import _validate_source

        src = Path(self.tmpdir) / "mixed.md"
        src.write_text(
            "| Rule ID | Group | Gate | Description | Applies to | Notes |\n"
            "|---------|-------|------|-------------|------------|-------|\n"
            "| R01 | mapping | mod_gate | Short. | * | |\n"
            "| RTE-001 | validation | execution_gate | Long. | * | |\n",
            encoding="utf-8",
        )
        rules = _validate_source(src)
        self.assertEqual(len(rules), 2)
        self.assertEqual(rules[0].rule_id, "R01")
        self.assertEqual(rules[1].rule_id, "RTE-001")

    # ── _build_index_entry ──────────────────────────────────────────────

    def test_build_index_entry_has_correct_fields(self):
        """Index entry from request has correct mod_name, revision=1, visibility=private."""
        from mod_capture import CaptureRequest, _build_index_entry

        req = CaptureRequest(
            mod_name="MYMOD", action="create", source=Path("."),
            scope_signals="sig::test", aliases="a1, a2",
            exclusion_signals="excl::x",
        )
        entry = _build_index_entry(req)
        self.assertEqual(entry.mod_name, "MYMOD")
        self.assertEqual(entry.aliases, "a1, a2")
        self.assertEqual(entry.scope_signals, "sig::test")
        self.assertEqual(entry.exclusion_signals, "excl::x")
        self.assertEqual(entry.path, "MOD_MYMOD.md")
        self.assertEqual(entry.revision, 1)
        self.assertEqual(entry.visibility, "private")

    # ── _build_mod_content ──────────────────────────────────────────────

    def test_build_mod_content_contains_rule_count(self):
        """MOD content includes the rule count."""
        from mod_capture import CaptureRequest, _build_mod_content

        req = CaptureRequest(
            mod_name="TEST", action="create", source=Path("."),
            scope_signals="sig::t", aliases="", exclusion_signals="",
        )
        content = _build_mod_content("TEST", "body", req, 3)
        self.assertIn("Rule count: 3", content)
        self.assertIn("MOD_TEST", content)

    def test_build_mod_content_with_revision(self):
        """MOD content includes the revision number."""
        from mod_capture import CaptureRequest, _build_mod_content

        req = CaptureRequest(
            mod_name="REV", action="create", source=Path("."),
            scope_signals="sig::t", aliases="", exclusion_signals="",
        )
        content = _build_mod_content("REV", "body", req, 1, revision=5)
        self.assertIn("Revision: 5", content)


# ── Test: _mod_catalog Rule ID validation ────────────────────────────────


class TestRuleIdValidation(unittest.TestCase):
    """_mod_catalog.parse_mod_rules validates Rule ID format."""

    def test_valid_short_id_accepted(self):
        """R01-style short ID is accepted."""
        from _mod_catalog import parse_mod_rules

        text = (
            "| Rule ID | Group | Gate | Description | Applies to | Notes |\n"
            "|---------|-------|------|-------------|------------|-------|\n"
            "| R01 | mapping | mod_gate | Test. | * | |\n"
        )
        rules = parse_mod_rules(text)
        self.assertEqual(len(rules), 1)

    def test_valid_long_id_accepted(self):
        """RTE-001-style long ID is accepted."""
        from _mod_catalog import parse_mod_rules

        text = (
            "| Rule ID | Group | Gate | Description | Applies to | Notes |\n"
            "|---------|-------|------|-------------|------------|-------|\n"
            "| RTE-001 | mapping | mod_gate | Test. | * | |\n"
        )
        rules = parse_mod_rules(text)
        self.assertEqual(len(rules), 1)

    def test_lowercase_id_rejected(self):
        """Lowercase rule ID (r01) is rejected."""
        from _mod_catalog import parse_mod_rules, ModRuleParseError

        text = (
            "| Rule ID | Group | Gate | Description | Applies to | Notes |\n"
            "|---------|-------|------|-------------|------------|-------|\n"
            "| r01 | mapping | mod_gate | Test. | * | |\n"
        )
        with self.assertRaises(ModRuleParseError):
            parse_mod_rules(text)

    def test_numeric_only_id_rejected(self):
        """Pure numeric ID (001) is rejected."""
        from _mod_catalog import parse_mod_rules, ModRuleParseError

        text = (
            "| Rule ID | Group | Gate | Description | Applies to | Notes |\n"
            "|---------|-------|------|-------------|------------|-------|\n"
            "| 001 | mapping | mod_gate | Test. | * | |\n"
        )
        with self.assertRaises(ModRuleParseError):
            parse_mod_rules(text)

    def test_garbage_id_rejected(self):
        """Non-rule garbage row (e.g. Chinese text) is rejected."""
        from _mod_catalog import parse_mod_rules, ModRuleParseError

        text = (
            "| Rule ID | Group | Gate | Description | Applies to | Notes |\n"
            "|---------|-------|------|-------------|------------|-------|\n"
            "| 这不是规则 | mapping | mod_gate | Test. | * | |\n"
        )
        with self.assertRaises(ModRuleParseError):
            parse_mod_rules(text)

    def test_mixed_valid_and_invalid_rows(self):
        """Valid rows are kept; invalid rows are silently skipped."""
        from _mod_catalog import parse_mod_rules

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


if __name__ == "__main__":
    unittest.main()
