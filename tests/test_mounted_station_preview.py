from dataclasses import dataclass, replace
from types import SimpleNamespace
import unittest

from freecad.bendseq.core.kinematics2d.bending_stations import BendingStation
from freecad.bendseq.core.kinematics2d.mounted_station_preview import mounted_station_meshes


@dataclass(frozen=True)
class Profile:
    tool_id: str
    height: int
    punch_reversed: bool = False


class MountedStationTests(unittest.TestCase):
    def setUp(self):
        self.stations = (
            BendingStation("A", "A", "P1", "D1", -60, ((-25, 25),), ((-30, 30),)),
            BendingStation("B", "B", "P2", "D2", 60, ((-25, 25),), ((-30, 30),),
                           enabled=False, punch_reversed=True),
        )
        self.profiles = {
            (kind, name): Profile(name, height)
            for kind, name, height in (("punch", "P1", 75), ("punch", "P2", 75),
                                       ("die", "D1", 45), ("die", "D2", 45))
        }
        self.calls = []

    def mesh_loader(self, tooling, virtual_length):
        self.calls.append((tooling, virtual_length))
        for kind in ("punch", "die"):
            profile = getattr(tooling, kind)
            bounds = SimpleNamespace(ZMax=profile.height, ZMin=-profile.height)
            yield kind, SimpleNamespace(machine_shape=SimpleNamespace(BoundBox=bounds)), ()

    def test_mounts_all_tools_in_machine_coordinates_and_uses_per_station_reverse(self):
        meshes = mounted_station_meshes(self.stations, self.profiles, mesh_loader=self.mesh_loader)
        self.assertEqual([(sid, kind) for sid, kind, *_ in meshes],
                         [("A", "punch"), ("A", "die"), ("B", "punch"), ("B", "die")])
        first, second = (call[0] for call in self.calls)
        self.assertEqual(first.punch_section_spans_xm, ((-85, -35),))
        self.assertEqual(second.die_section_spans_xm, ((30, 90),))
        self.assertTrue(second.punch.punch_reversed)
        self.assertFalse(self.profiles["punch", "P2"].punch_reversed)
        self.assertTrue(all(virtual is None for _, virtual in self.calls))

    def test_incompatible_mounting_planes_abort_preview(self):
        self.profiles["punch", "P2"] = replace(self.profiles["punch", "P2"], height=85)
        with self.assertRaisesRegex(ValueError, "B: incompatible punch mounting height"):
            mounted_station_meshes(self.stations, self.profiles, mesh_loader=self.mesh_loader)
