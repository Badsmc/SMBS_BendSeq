"""
Unit tests for tooling_panel module (S.71 C2b).
Tests headless module safety, constants, class creatability, helpers,
clamp config defaults, and real toollibrary catalog loading.
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

    def test_parse_positions_mm_valid(self):
        """Parse comma-separated positions into sorted tuple of floats."""
        from freecad_smbs.ui.tooling_panel import _parse_positions_mm

        self.assertEqual(_parse_positions_mm("10.0, 100.0, 200.0"), (10.0, 100.0, 200.0))

    def test_parse_positions_mm_sorts_dedupes(self):
        """Positions are deduplicated and sorted in ascending order."""
        from freecad_smbs.ui.tooling_panel import _parse_positions_mm

        self.assertEqual(_parse_positions_mm("200.0, 10.0, 10.0"), (10.0, 200.0))

    def test_parse_positions_mm_invalid(self):
        """Non-numeric input raises ValueError."""
        from freecad_smbs.ui.tooling_panel import _parse_positions_mm

        with self.assertRaises(ValueError):
            _parse_positions_mm("abc")

    def test_apply_clamp_config_fields_roundtrip(self):
        """apply_clamp_config_fields replaces fields while preserving all 9 fields."""
        from freecad_smbs.ui.tooling_panel import _apply_clamp_config_fields
        from smbs_core.geometry_backend.io.clamp_validator import ClampConfig

        cfg = ClampConfig.from_defaults()
        updated = _apply_clamp_config_fields(cfg, {"width_mm": 200.0})
        self.assertEqual(updated.width_mm, 200.0)
        self.assertEqual(updated.gap_between_mm, cfg.gap_between_mm)
        self.assertEqual(updated.overhang_ratio, cfg.overhang_ratio)
        self.assertEqual(updated.joint_tolerance_mm, cfg.joint_tolerance_mm)
        self.assertEqual(updated.min_clamps_under_punch, cfg.min_clamps_under_punch)
        self.assertEqual(updated.positions_mm, cfg.positions_mm)
        self.assertEqual(updated.repositionable, cfg.repositionable)
        self.assertEqual(updated.joints_must_be_on_clamps, cfg.joints_must_be_on_clamps)
        self.assertEqual(updated.no_gaps_between_punches, cfg.no_gaps_between_punches)

        with self.assertRaises(ValueError):
            _apply_clamp_config_fields(cfg, {"invalid_field_name": 123})

    def test_machine_frame_badge_when_missing(self):
        """Machine frame missing returns SIMPLIFIED badge (KL-16 pending) and yellow color."""
        finder = None
        try:
            try:
                from smbs_core.viewer.fc_animation import format_machine_frame_badge, COLOR_YELLOW
            except ModuleNotFoundError as e:
                if "OCC" in str(e):
                    import types
                    from unittest.mock import MagicMock

                    class OCCMockFinder:
                        def find_spec(self, fullname, path, target=None):
                            if fullname == "OCC" or fullname.startswith("OCC."):
                                from importlib.machinery import ModuleSpec

                                return ModuleSpec(fullname, self, is_package=True)
                            return None

                        def create_module(self, spec):
                            mod = types.ModuleType(spec.name)
                            mod.__path__ = []
                            return mod

                        def exec_module(self, module):
                            module.__getattr__ = lambda name: MagicMock()

                    finder = OCCMockFinder()
                    sys.meta_path.insert(0, finder)
                    from smbs_core.viewer.fc_animation import format_machine_frame_badge, COLOR_YELLOW
                else:
                    raise

            self.assertEqual(
                format_machine_frame_badge(False),
                ("MACHINE: SIMPLIFIED (KL-16 pending)", COLOR_YELLOW),
            )
        finally:
            if finder and finder in sys.meta_path:
                sys.meta_path.remove(finder)

    def test_clamp_config_defaults_match_raw(self):
        """ClampConfig defaults match raw audit R1.1 values across all 9 fields."""
        from smbs_core.geometry_backend.io.clamp_validator import ClampConfig

        cfg = ClampConfig.from_defaults()
        self.assertEqual(cfg.width_mm, 150.0)
        self.assertEqual(cfg.gap_between_mm, 50.0)
        self.assertEqual(cfg.overhang_ratio, 0.30)
        self.assertEqual(cfg.joint_tolerance_mm, 0.1)
        self.assertEqual(cfg.min_clamps_under_punch, 2)
        self.assertTrue(cfg.repositionable)
        self.assertTrue(cfg.joints_must_be_on_clamps)
        self.assertTrue(cfg.no_gaps_between_punches)


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
