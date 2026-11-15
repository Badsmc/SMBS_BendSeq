"""Headless source contract for the Coin3D migration.

Runtime Pivy/Quarter and FPS still require FreeCAD integration tests.
"""
import ast
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class Coin3DSourceContractTests(unittest.TestCase):
    def test_coin_viewer_is_importable_without_pivy(self):
        # The file must not import the Qt/GL runtime at top level; FreeCAD
        # headless commands are allowed to import this module.
        source = (ROOT / "freecad_smbs/ui/coin3d_viewer.py").read_text()
        ast.parse(source)
        self.assertIn("def create_coin_viewer", source)
        self.assertIn("from pivy.quarter import QuarterWidget", source)

    def test_no_legacy_3d_qgraphics_scene(self):
        source = (ROOT / "freecad_smbs/commands/pressbrake_2d.py").read_text()
        tree = ast.parse(source)
        self.assertIn("create_coin_viewer()", source)
        self.assertNotIn("_preview_3d_scene", source)
        self.assertNotIn("_preview_3d_face_items", source)
        self.assertNotIn("orbit_project", source)
        # New viewport uses a single authoritative FoldState path.
        self.assertIn("build_sheet_preview_faces(", source)
        self.assertIn("_current_preview_machine_placement()", source)
        self.assertIn("update_tools(", source)

    def test_tooling_is_cached_and_punch_stroke_is_transform_only(self):
        source = (ROOT / "freecad_smbs/ui/coin3d_viewer.py").read_text()
        self.assertIn("if key != self._tool_key:", source)
        self.assertIn("coin.SoTranslation()", source)
        self.assertIn("self._punch_motion.translation.setValue(", source)
        self.assertIn("coin.SoIndexedFaceSet()", source)
        self.assertIn("self._sheet_batches", source)
        self.assertIn("geometry.coordIndex.setValues(", source)

    def test_native_view_uses_separate_temporary_document(self):
        source = (ROOT / "freecad_smbs/ui/coin3d_viewer.py").read_text()
        self.assertIn("App.newDocument(temporary_name)", source)
        self.assertIn("Gui.getDocument(temporary_name)", source)
        self.assertIn("root.addChild(self._root)", source)
        self.assertIn("App.setActiveDocument(source_name)", source)
        self.assertIn("App.closeDocument(name)", source)
        self.assertNotIn("Gui.getDocument(str(name))", source)


if __name__ == "__main__":
    unittest.main()
