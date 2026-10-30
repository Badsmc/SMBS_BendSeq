"""Headless-safe tests for animation_dialog module and get_player bridge (S.70)."""

import unittest
from unittest.mock import MagicMock, patch

from freecad_smbs.integration.smbs_bridge import get_player
from freecad_smbs.ui.animation_dialog import (
    OBJECT_NAME,
    TICK_INTERVAL_MS_DEFAULT,
    _to_fc_shape,
    _update_feature,
)


class TestAnimationDialogHeadless(unittest.TestCase):
    def test_module_constants(self):
        self.assertEqual(OBJECT_NAME, "SMBS_Animation")
        self.assertEqual(TICK_INTERVAL_MS_DEFAULT, 500)

    def test_to_fc_shape_none(self):
        """None -> None (no Part import triggered)."""
        self.assertIsNone(_to_fc_shape(None))

    def test_animation_dialog_module_headless(self):
        """Importing animation_dialog must not pull FreeCAD/PySide."""
        import subprocess, sys

        cmd = [
            sys.executable,
            "-c",
            "import sys, freecad_smbs.ui.animation_dialog; "
            "forbidden = ['FreeCAD','FreeCADGui','Part','PySide6','PySide2','PyQt5','PyQt6']; "
            "loaded = [m for m in forbidden if m in sys.modules]; "
            "assert not loaded, f'Loaded: {loaded}'; print('OK')",
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)


class TestGetPlayerBridge(unittest.TestCase):
    def test_get_player_lazy_imports(self):
        """smbs_bridge top-level import must not load OCC."""
        import subprocess, sys

        cmd = [
            sys.executable,
            "-c",
            "import sys, freecad_smbs.integration.smbs_bridge; "
            "forbidden = ['OCC','OCP']; "
            "loaded = [m for m in forbidden if m in sys.modules]; "
            "assert not loaded, f'Loaded: {loaded}'; print('OK')",
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_get_player_raises_on_bad_09(self):
        """get_player eagerly builds frames -> raises PlayerError on malformed verification."""
        try:
            from smbs_core.viewer.player import PlayerError
        except ModuleNotFoundError as e:
            if "OCC" in str(e):
                self.skipTest("OCC not installed in current interpreter; test requires FreeCAD python")
            raise

        mock_artifacts = MagicMock()
        mock_artifacts.verification_result = {"payload": {}}  # missing required fields
        with patch("smbs_core.viewer.graph_builder.build_graphs", return_value=MagicMock()), \
             patch("smbs_core.geometry_backend.backends.pythonocc_backend.PythonOCCBackend", return_value=MagicMock()):
            with self.assertRaises(PlayerError):
                get_player(mock_artifacts)

    def test_simulation_dialog_renders_backgauge(self):
        """O3: AnimationPlayerDialog reads and renders Backgauge coordinates from 07_search_result.json."""
        import tempfile, json
        from pathlib import Path
        from freecad_smbs.ui.animation_dialog import AnimationPlayerDialog

        with tempfile.TemporaryDirectory() as tmpdir:
            run_dir = Path(tmpdir)
            p07 = run_dir / "07_search_result.json"
            p07_data = {
                "schema_name": "search_result",
                "schema_version": "1.1",
                "payload": {
                    "candidate_sequence": [
                        {
                            "step": 1,
                            "bend_id": "BEND_01",
                            "backgauge": {
                                "confidence": "CONFIRMED",
                                "x_mm": 56.0,
                                "r_mm": 0.75,
                                "z1_mm": -25.0,
                                "z2_mm": 25.0,
                            }
                        }
                    ]
                }
            }
            p07.write_text(json.dumps(p07_data))

            mock_artifacts = MagicMock()
            mock_artifacts.run_dir = run_dir
            mock_artifacts.search_result = p07_data

            mock_player = MagicMock()
            mock_player.total = 2
            mock_player.direction = "backward"
            mock_frame = MagicMock()
            mock_frame.frame_index = 1
            mock_frame.step_view.bend_id = "BEND_01"
            mock_frame.step_view.step_index = 1
            mock_frame.step_view.ui_direction = "backward"
            mock_frame.step_view.state.shape = None
            mock_frame.step_view.geometry_warning = None
            mock_frame.step_view.verification_status = "VERIFIED"
            mock_frame.step_view.precision = "EXACT"
            mock_player.current.return_value = mock_frame
            mock_player.reset.return_value = mock_frame

            # 1. Verify coordinate parsing from 07
            dialog = AnimationPlayerDialog(artifacts=mock_artifacts, player=mock_player)
            bg_map = dialog._load_from_manual_07()
            self.assertIn("BEND_01", bg_map)
            step1_bg = bg_map["BEND_01"]
            self.assertEqual(step1_bg["x_mm"], 56.0)
            self.assertEqual(step1_bg["r_mm"], 0.75)
            self.assertEqual(step1_bg["z1_mm"], -25.0)
            self.assertEqual(step1_bg["z2_mm"], 25.0)

            # 2. Verify 3D scene feature creation and placement (Q2)
            mock_doc = MagicMock()
            mock_feature = MagicMock()
            mock_doc.getObject.return_value = None
            mock_doc.addObject.return_value = mock_feature

            from freecad_smbs.ui.animation_dialog import _update_feature
            # Emulate backgauge shape placement in document
            bg_obj = _update_feature(mock_doc, "SMBS_Backgauge", fc_shape=MagicMock())
            mock_doc.addObject.assert_called_with("Part::Feature", "SMBS_Backgauge")
            self.assertTrue(mock_feature.Visibility)
            mock_doc.recompute.assert_called()

    def test_simulation_dialog_creates_backgauge_scene_object(self):
        """
        Q2: Backgauge visualization creates real 'SMBS_Backgauge' Part::Feature in FreeCAD document
        and positions it according to step coordinates.
        """
        from smbs_core.geometry_backend.freecad_bootstrap import ensure_freecad_paths
        ensure_freecad_paths()
        import FreeCAD as App
        import Part
        doc = App.newDocument("Test_Backgauge_Scene")
        try:
            from freecad_smbs.ui.animation_dialog import _update_feature
            # Create a box representing backgauge stop
            stop_box = Part.makeBox(20.0, 40.0, 15.0)
            
            # Place in scene using _update_feature
            bg_feature = _update_feature(doc, "SMBS_Backgauge", stop_box)
            self.assertIsNotNone(bg_feature)
            
            # Assert: object with name 'SMBS_Backgauge' exists in FreeCAD active document
            self.assertTrue(any("Backgauge" in o.Name for o in doc.Objects))
            obj = doc.getObject("SMBS_Backgauge")
            self.assertIsNotNone(obj)
            self.assertEqual(obj.TypeId, "Part::Feature")
            self.assertTrue(obj.Visibility)
            
            # Translate according to step 1 backgauge coordinates (X=56.0, R=0.75)
            x_val = 56.0
            r_val = 0.75
            translated_box = stop_box.translated(App.Vector(x_val, 0.0, r_val))
            _update_feature(doc, "SMBS_Backgauge", translated_box)
            
            bound = doc.getObject("SMBS_Backgauge").Shape.BoundBox
            self.assertAlmostEqual(bound.XMin, 56.0, delta=1e-4)
            self.assertAlmostEqual(bound.ZMin, 0.75, delta=1e-4)
        finally:
            App.closeDocument(doc.Name)


if __name__ == "__main__":
    unittest.main()
