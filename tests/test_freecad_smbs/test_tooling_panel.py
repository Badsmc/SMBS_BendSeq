"""
Unit tests for tooling_panel module (S.71 C2a).
Tests headless module safety, constants, class creatability, and real toollibrary catalog loading.
"""
from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path


class TestToolingPanelHeadless(unittest.TestCase):
    def test_panel_module_headless(self):
        """Importing tooling_panel top-level must not import FreeCAD, Qt, or OCC."""
        cmd = [
            sys.executable,
            "-c",
            "import sys, freecad_smbs.ui.tooling_panel; "
            "forbidden = ['FreeCAD','FreeCADGui','Part','PySide6','PySide2','PyQt5','PyQt6','OCC','OCP']; "
            "loaded = [m for m in forbidden if m in sys.modules]; "
            "assert not loaded, f'Loaded forbidden modules: {loaded}'; print('OK')",
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_module_constants(self):
        """Verify module-level constants match spec (Q11=C and 5-tab titles)."""
        from freecad_smbs.ui.tooling_panel import LIBRARY_TAB_TITLES, SECTION_CATALOG_MM

        self.assertEqual(
            LIBRARY_TAB_TITLES,
            ("Пуансоны", "Матрицы", "Станина", "Задний упор", "Лапки"),
        )
        self.assertEqual(SECTION_CATALOG_MM, (10.0, 15.0, 20.0, 50.0, 100.0))

    def test_panel_class_creatable(self):
        """ToolingPanel wrapper is callable and _PanelClass is None initially."""
        from freecad_smbs.ui import tooling_panel

        self.assertIsNone(tooling_panel._PanelClass)
        self.assertTrue(callable(tooling_panel.ToolingPanel))


class TestToolingCatalogHeadless(unittest.TestCase):
    def test_catalog_loads_real_tools(self):
        """Real canonical toollibrary/ contains puancon (punch) and matrica (die)."""
        from freecad_smbs.integration.smbs_bridge import get_tooling_library

        lib = get_tooling_library()
        catalog = lib.list_tools()
        punch_ids = {p.tool_id for p in catalog.punches}
        die_ids = {d.tool_id for d in catalog.dies}

        self.assertIn("puancon", punch_ids)
        self.assertIn("matrica", die_ids)

    def test_punch_metadata_keys(self):
        """puancon sidecar has expected metadata keys and numeric types."""
        from freecad_smbs.integration.smbs_bridge import get_tooling_library

        lib = get_tooling_library()
        catalog = lib.list_tools()
        punch = next(p for p in catalog.punches if p.tool_id == "puancon")

        self.assertIn("radius_mm", punch.metadata)
        self.assertIn("angle_deg", punch.metadata)
        self.assertIn("height_mm", punch.metadata)
        self.assertIsInstance(punch.metadata["radius_mm"], (int, float))
        self.assertIsInstance(punch.metadata["angle_deg"], (int, float))
        self.assertIsInstance(punch.metadata["height_mm"], (int, float))

    def test_die_metadata_keys(self):
        """matrica sidecar has expected metadata keys and numeric types."""
        from freecad_smbs.integration.smbs_bridge import get_tooling_library

        lib = get_tooling_library()
        catalog = lib.list_tools()
        die = next(d for d in catalog.dies if d.tool_id == "matrica")

        self.assertIn("v_width_mm", die.metadata)
        self.assertIn("angle_deg", die.metadata)
        self.assertIn("height_mm", die.metadata)
        self.assertIsInstance(die.metadata["v_width_mm"], (int, float))
        self.assertIsInstance(die.metadata["angle_deg"], (int, float))
        self.assertIsInstance(die.metadata["height_mm"], (int, float))


if __name__ == "__main__":
    unittest.main()
