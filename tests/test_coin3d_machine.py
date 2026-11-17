import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from freecad_smbs.ui.coin3d_machine import CoinMachineScene


class TestCoinMachine(unittest.TestCase):
    def setUp(self):
        self.coin = SimpleNamespace(
            SoSeparator=Mock, SoSwitch=Mock, SoTranslation=Mock,
            SO_SWITCH_NONE=-1, SO_SWITCH_ALL=-3,
        )
        self.scene = CoinMachineScene(self.coin)
        self.library = Path("toollibrary")

    def test_bed_is_stationary_and_beam_follows_punch_even_after_tool_change(self):
        tooling = SimpleNamespace(
            punch=None, die=None, aligned=False, ram_z_mm=0,
            punch_polygons_machine=lambda: None,
            die_polygons_machine=lambda: None,
            die_machine_transform=SimpleNamespace(to_matrix_tuple=lambda: (0, 0)),
        )
        with patch("freecad_smbs.ui.coin3d_machine._load_frame",
                   return_value=(Mock(), Mock(), 900)) as load:
            self.assertEqual(self.scene.update_frame(self.library, tooling, 0, True), "")
            self.scene.frame_mount.translation.setValue.assert_called_once_with(0.0, 0.0, 0.0)
            self.scene.ram_motion.translation.setValue.assert_called_with(0.0, 0.0, 900)

            tooling.punch = object()
            tooling.die = object()
            tooling.aligned = True
            tooling.ram_z_mm = 8
            tooling.punch_polygons_machine = lambda: (((0, 8), (2, 108)), ())
            tooling.die_polygons_machine = lambda: (((0, 0), (2, -80)), ())
            self.scene.update_frame(self.library, tooling, 8, True)
            self.scene.update_frame(self.library, tooling, 4, True)
            load.assert_called_once()
        self.assertEqual(self.scene.frame_mount.translation.setValue.call_count, 2)
        self.scene.frame_mount.translation.setValue.assert_called_with(0.0, 0.0, -80)
        self.scene.ram_motion.translation.setValue.assert_called_with(0.0, 0.0, 104)

    def test_two_gauges_reuse_the_cad_mesh_and_keep_their_x_stations(self):
        with patch("freecad_smbs.ui.coin3d_machine._load_gauge", return_value=Mock()) as load:
            self.scene.update_gauges(self.library, (-110, 110), (90, 1))
            first, second = self.scene.fingers
            self.scene.update_gauges(self.library, (-110, 110), (110, 2))
            self.assertEqual(self.scene.fingers, (first, second))
            first.translation.setValue.assert_called_with(110.0, -110.0, 2.0)
            second.translation.setValue.assert_called_with(110.0, 110.0, 2.0)
            load.assert_called_once()
        self.scene.update_gauges(self.library, (), None)
        self.scene.gauges.whichChild.setValue.assert_called_with(self.coin.SO_SWITCH_NONE)

    def test_qgraphics_canvas_is_only_used_for_flat_pattern(self):
        path = Path(__file__).resolve().parents[1] / "freecad_smbs/commands/pressbrake_2d.py"
        source = path.read_text(encoding="utf-8")
        self.assertEqual(source.count("QtWidgets.QGraphicsScene("), 1)
        self.assertIn("self._flat_scene = QtWidgets.QGraphicsScene(self._flat_view)", source)
        self.assertIn("viewer.set_sheet_faces(faces, highlighted=highlighted)", source)
