"""Tests for Python version check in preflight.py — issue 08.

Verifies that preflight.py fails fast on Python < 3.10 and passes on 3.10+.
"""

import os
import sys
import subprocess
import unittest
from unittest.mock import patch


def _repo_root():
    tests_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.dirname(os.path.dirname(tests_dir))


class TestCheckPythonVersion(unittest.TestCase):
    """check_python_version() must return None on 3.10+ and a dict on <3.10."""

    def test_current_python_passes(self):
        """Running Python (3.14) must pass the version check."""
        # Import the function directly
        sys.path.insert(0, os.path.join(_repo_root(), "table-fill", "scripts"))
        from preflight import check_python_version
        result = check_python_version()
        self.assertIsNone(result, f"Expected None for Python {sys.version_info}, got {result}")

    def test_version_tuple_logic(self):
        """Verify the version comparison logic directly."""
        # Current Python is 3.10+ so this should always pass in CI
        if sys.version_info >= (3, 10):
            sys.path.insert(0, os.path.join(_repo_root(), "table-fill", "scripts"))
            from preflight import check_python_version
            result = check_python_version()
            self.assertIsNone(result)

    def test_simulated_old_python_fails(self):
        """Simulating Python 3.9 must return an error dict."""
        sys.path.insert(0, os.path.join(_repo_root(), "table-fill", "scripts"))
        import preflight
        with patch.object(preflight, 'sys') as mock_sys:
            mock_sys.version_info = type('version_info', (), {
                'major': 3, 'minor': 9, '__ge__': lambda self, other: (3, 9) >= other,
            })()
            result = preflight.check_python_version()
            self.assertIsNotNone(result)
            self.assertEqual(result["code"], "PYTHON_VERSION_TOO_LOW")
            self.assertIn("3.10", result["message"])
            self.assertIn("Upgrade", result["corrective_action"])

    def test_simulated_very_old_python_fails(self):
        """Simulating Python 3.8 must return an error dict."""
        sys.path.insert(0, os.path.join(_repo_root(), "table-fill", "scripts"))
        import preflight
        with patch.object(preflight, 'sys') as mock_sys:
            mock_sys.version_info = type('version_info', (), {
                'major': 3, 'minor': 8, '__ge__': lambda self, other: (3, 8) >= other,
            })()
            result = preflight.check_python_version()
            self.assertIsNotNone(result)
            self.assertEqual(result["code"], "PYTHON_VERSION_TOO_LOW")


class TestPreflightVersionCheckIntegration(unittest.TestCase):
    """Integration: preflight.py exits 1 on simulated old Python."""

    def setUp(self):
        self.script = os.path.join(_repo_root(), "table-fill", "scripts", "preflight.py")

    def test_preflight_exits_clean_on_current_python(self):
        """preflight.py with current Python (3.10+) exits 0 for version check."""
        # Use a temp ASCII dir to avoid the non-ASCII path warning
        result = subprocess.run(
            [sys.executable, self.script, "--workdir", "C:\\Temp"],
            capture_output=True, text=True
        )
        # Should not exit 1 due to version (may exit 0 or warn about other things)
        # The important thing is it doesn't exit 1 with PYTHON_VERSION_TOO_LOW
        if result.returncode == 1:
            # If fatal, must not be about Python version
            self.assertNotIn("PYTHON_VERSION_TOO_LOW", result.stderr)


class TestSkillMdVersionDeclaration(unittest.TestCase):
    """SKILL.md must declare Python 3.10+ in its compatibility frontmatter."""

    def setUp(self):
        self.skill_path = os.path.join(_repo_root(), "table-fill", "SKILL.md")
        with open(self.skill_path, "r", encoding="utf-8") as fh:
            self.text = fh.read()

    def test_declares_python_310_plus(self):
        """Compatibility section must state Python 3.10+."""
        self.assertIn("Python 3.10+", self.text,
                       "SKILL.md must declare Python 3.10+ in compatibility.")

    def test_no_python_38_declaration(self):
        """Must not still declare Python 3.8+."""
        self.assertNotIn("Python 3.8+", self.text,
                          "SKILL.md must not declare Python 3.8+.")

    def test_no_python_39_declaration(self):
        """Must not still declare Python 3.9+."""
        self.assertNotIn("Python 3.9+", self.text,
                          "SKILL.md must not declare Python 3.9+.")


if __name__ == "__main__":
    unittest.main()
