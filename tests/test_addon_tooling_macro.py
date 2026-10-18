"""
test_addon_tooling_macro.py - Structural tests for SMBS_ToolingLibrary.FCMacro.
"""

import ast
import unittest
from pathlib import Path


class TestAddonToolingMacro(unittest.TestCase):
    def setUp(self):
        self.macro_path = Path("smbs_core/addon/SMBS_ToolingLibrary.FCMacro")

    def test_macro_file_exists(self):
        """1. FCMacro file exists and is non-empty."""
        self.assertTrue(self.macro_path.exists(), "SMBS_ToolingLibrary.FCMacro must exist")
        self.assertGreater(self.macro_path.stat().st_size, 200, "Macro file should not be empty")

    def test_macro_syntax_and_config(self):
        """2. Macro parses valid Python AST and declares required config variables."""
        content = self.macro_path.read_text(encoding="utf-8")
        parsed = ast.parse(content)
        self.assertIsInstance(parsed, ast.Module)

        self.assertIn("SMBS_REPO_PATH", content)
        self.assertIn("SMBS_LIBRARY_PATH", content)
        self.assertIn("ToolingLibraryDialog", content)
        self.assertIn("def run_macro", content)

    def test_macro_entrypoint(self):
        """3. Entry point '__main__' invokes run_macro."""
        content = self.macro_path.read_text(encoding="utf-8")
        self.assertIn('if __name__ == "__main__":', content)
        self.assertIn('run_macro()', content)


if __name__ == "__main__":
    unittest.main()
