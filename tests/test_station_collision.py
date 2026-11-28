from types import SimpleNamespace
import unittest

from freecad.bendseq.core.kinematics2d.press_cycle import CyclePhase
from freecad.bendseq.core.kinematics2d.station_collision import check_station_tool_overlap

try:
    import FreeCAD as App
    import Part
except ImportError:
    App = Part = None


@unittest.skipIf(Part is None, "Requires FreeCAD Part for real common-volume tests")
class NativeStationCollisionTests(unittest.TestCase):
    @staticmethod
    def box(x, y, z, size=2):
        return Part.makeBox(size, size, size, App.Vector(x, y, z))

    def test_overhanging_panel_hits_neighbor_when_active_station_is_clear(self):
        sheet = (("P2", self.box(95, 10, 1)),)
        active = SimpleNamespace(machine_shape=self.box(-100, 10, 1), tool_id="D1")
        neighbor = SimpleNamespace(machine_shape=self.box(94, 10, 1, 4), tool_id="D2")
        meshes = (("S1", "die", active, ()), ("S2", "die", neighbor, ()))
        hit = check_station_tool_overlap(
            sheet, meshes, 8, active_station="S1", active_bend="B1", phase=CyclePhase.TRANSFER,
        )
        self.assertEqual((hit.station_id, hit.tool_id, hit.sheet_component), ("S2", "D2", "P2"))
        self.assertAlmostEqual(hit.volume_mm3, 8.0)

    def test_all_punches_follow_shared_ram_and_contact_is_not_penetration(self):
        sheet = (("P2", self.box(95, 10, 5)),)
        die = SimpleNamespace(machine_shape=self.box(95, 10, 3), tool_id="D2")
        punch = SimpleNamespace(machine_shape=self.box(95, 10, 0), ram_z_mm=0,
                                validity_proof=None, kind="PUNCH", tool_id="P2")
        from freecad.bendseq.core.kinematics2d.native_tool_loader import LoadedTool
        punch = LoadedTool(punch.tool_id, punch.kind, "", punch.machine_shape,
                           punch.machine_shape, 1, 85)
        meshes = (("S2", "die", die, ()), ("S2", "punch", punch, ()))
        args = dict(active_station="S1", active_bend="B1", phase=CyclePhase.BENDING)
        self.assertIsNone(check_station_tool_overlap(sheet, meshes, 10, **args))
        self.assertEqual(check_station_tool_overlap(sheet, meshes, 6, **args).tool_kind, "punch")
