"""Tests for the Python version check in preflight.py — issue 08.

Verifies that preflight.py fails fast on Python < 3.10 and passes on 3.10+,
and that SKILL.md declares the real minimum in its compatibility frontmatter.

Run with:
  python -m unittest tests.test_preflight_version
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SKILL_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS_DIR = SKILL_ROOT / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import preflight  # noqa: E402


class _FakeVersionInfo:
    """Minimal sys.version_info stand-in with major/minor and tuple compare.

    Real sys.version_info is a named tuple; a bare tuple lacks the
    .major/.minor attributes check_python_version's message uses.
    """

    def __init__(self, major: int, minor: int):
        self.major = major
        self.minor = minor

    def __ge__(self, other: tuple) -> bool:
        return (self.major, self.minor) >= (other[0], other[1])


class TestCheckPythonVersion(unittest.TestCase):
    """check_python_version() must return None on 3.10+ and a dict on <3.10."""

    def test_current_python_passes(self):
        """Running Python (3.10+) must pass the version check."""
        result = preflight.check_python_version()
        self.assertIsNone(result, f"Expected None for Python {sys.version_info}, got {result}")

    def test_simulated_old_python_fails(self):
        """Simulating Python 3.9 must return an error dict."""
        with patch.object(preflight.sys, "version_info", _FakeVersionInfo(3, 9)):
            result = preflight.check_python_version()
        self.assertIsNotNone(result)
        self.assertEqual(result["code"], "PYTHON_VERSION_TOO_LOW")
        self.assertIn("3.10", result["message"])
        self.assertIn("Upgrade", result["corrective_action"])

    def test_simulated_very_old_python_fails(self):
        """Simulating Python 3.8 must return an error dict."""
        with patch.object(preflight.sys, "version_info", _FakeVersionInfo(3, 8)):
            result = preflight.check_python_version()
        self.assertIsNotNone(result)
        self.assertEqual(result["code"], "PYTHON_VERSION_TOO_LOW")

    def test_simulated_boundary_310_passes(self):
        """Exactly 3.10 must pass (boundary)."""
        with patch.object(preflight.sys, "version_info", _FakeVersionInfo(3, 10)):
            result = preflight.check_python_version()
        self.assertIsNone(result)


class TestPreflightVersionCheckIntegration(unittest.TestCase):
    """Integration: preflight.main() exits 1 with PYTHON_VERSION_TOO_LOW when
    the Python interpreter is below 3.10, and never fatal on version for
    the current interpreter."""

    def _run_main_faked(self, major: int, minor: int,
                        workdir: str) -> tuple[int, str]:
        """Invoke preflight.main() in-process with a faked version_info.

        Returns (exit_code, stderr_text). Fatal version failure happens at
        Check 0 — before any officecli probe — so the run is fast.
        """
        from io import StringIO

        with patch.object(preflight.sys, "version_info", _FakeVersionInfo(major, minor)), \
             patch.object(preflight.sys, "argv",
                          ["preflight.py", "--workdir", workdir]), \
             patch.object(preflight.sys, "exit", side_effect=SystemExit):
            stderr_buf = StringIO()
            import contextlib
            with contextlib.redirect_stderr(stderr_buf):
                try:
                    preflight.main()
                    code = 0
                except SystemExit as exc:
                    code = exc.code if isinstance(exc.code, int) else 1
        return code, stderr_buf.getvalue()

    def test_preflight_exits_fatal_on_old_python(self):
        """Simulated Python 3.9 -> exit 1 with PYTHON_VERSION_TOO_LOW."""
        ascii_dir = tempfile.mkdtemp(prefix="preflight_ascii_")
        try:
            code, stderr = self._run_main_faked(3, 9, str(ascii_dir))
        finally:
            shutil_rmtree = __import__("shutil").rmtree
            shutil_rmtree(ascii_dir, ignore_errors=True)
        self.assertEqual(code, 1)
        self.assertIn("PYTHON_VERSION_TOO_LOW", stderr)
        self.assertIn("Upgrade Python to 3.10", stderr)

    def test_preflight_passes_on_current_python(self):
        """Current Python (3.10+) -> no PYTHON_VERSION_TOO_LOW."""
        ascii_dir = tempfile.mkdtemp(prefix="preflight_ascii_")
        try:
            result = subprocess.run(
                [sys.executable, str(SKILL_ROOT / "scripts" / "preflight.py"),
                 "--workdir", str(ascii_dir)],
                capture_output=True, text=True, encoding="utf-8",
                timeout=60,
            )
        finally:
            shutil_rmtree = __import__("shutil").rmtree
            shutil_rmtree(ascii_dir, ignore_errors=True)
        # May warn about officecli/encoding, but must NOT fatal on version.
        if result.returncode == 1:
            self.assertNotIn("PYTHON_VERSION_TOO_LOW", result.stderr)


class TestSkillMdVersionDeclaration(unittest.TestCase):
    """SKILL.md must declare Python 3.10+ in its compatibility frontmatter."""

    def setUp(self):
        self.skill_path = SKILL_ROOT / "SKILL.md"
        with open(self.skill_path, "r", encoding="utf-8") as fh:
            self.text = fh.read()

    def test_declares_python_310_plus(self):
        self.assertIn("Python 3.10+", self.text,
                      "SKILL.md must declare Python 3.10+ in compatibility.")

    def test_no_python_38_declaration(self):
        self.assertNotIn("Python 3.8+", self.text,
                         "SKILL.md must not declare Python 3.8+.")

    def test_no_python_39_declaration(self):
        self.assertNotIn("Python 3.9+", self.text,
                         "SKILL.md must not declare Python 3.9+.")


if __name__ == "__main__":
    unittest.main()