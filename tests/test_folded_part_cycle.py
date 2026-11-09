"""Tests for post-bend ram retract sampled geometry and state restoration."""
import unittest

from tools.run_folded_part_cycle import probe_punch_retract


def rect(x0, y0, x1, y1):
    return ((x0,y0),(x1,y0),(x1,y1),(x0,y1))


class FakeTooling:
    def __init__(self):
        self.ram_z_mm = 5.0
        self.ram_stroke_mm = 0.0
        self.punch_machine_transform = ("PUNCH", 5.0)

    def set_punch_ram_z(self, z, stroke_mm=0.0):
        self.ram_z_mm = z
        self.ram_stroke_mm = stroke_mm
        self.punch_machine_transform = ("PUNCH", z)

    def punch_polygons_machine(self, arc_segments=24):
        z = self.ram_z_mm
        return rect(-1,z,-0.5,z+1), ()

    def die_polygons_machine(self, arc_segments=24):
        return rect(-10,-10,-9,-9), ()


class PostBendCycleTests(unittest.TestCase):
    def test_retract_detects_bottom_interference_and_restores_ram(self):
        tools = FakeTooling()
        panel = (("real_section", rect(-0.75, 0, 0.75, 1)),)
        initial = (tools.ram_z_mm, tools.ram_stroke_mm, tools.punch_machine_transform)
        report = probe_punch_retract(
            panel, tools, bottom_ram_z_mm=0.0, parked_ram_z_mm=5.0,
            sample_count=11,
        )
        self.assertEqual(report["status"], "SAMPLED_2D_INTERFERENCE")
        self.assertEqual(report["samples"][0]["phase"], "BOTTOM")
        self.assertEqual(report["samples"][0]["status"], "INTERFERENCE")
        self.assertEqual(report["samples"][-1]["status"], "CLEAR")
        self.assertTrue(any(
            f["component"] == "real_section" and f["tool"] == "PUNCH"
            for f in report["samples"][0]["findings"]
        ))
        self.assertEqual(
            (tools.ram_z_mm, tools.ram_stroke_mm, tools.punch_machine_transform),
            initial,
        )

    def test_retract_clear_when_punch_outside_sheet(self):
        tools = FakeTooling()
        panel = (("real_section", rect(5,0,6,1)),)
        report = probe_punch_retract(
            panel, tools, bottom_ram_z_mm=0.0,
            parked_ram_z_mm=5.0, sample_count=6,
        )
        self.assertEqual(report["status"], "SAMPLED_2D_CLEAR_NOT_CERTIFIED")

    def test_invalid_retract_range_rejected(self):
        with self.assertRaisesRegex(ValueError, "parked ram"):
            probe_punch_retract(
                (("panel", rect(5,0,6,1)),),
                FakeTooling(), bottom_ram_z_mm=5.0, parked_ram_z_mm=0.0,
            )

    def test_empty_real_section_unverified(self):
        report = probe_punch_retract(
            (), FakeTooling(), bottom_ram_z_mm=0, parked_ram_z_mm=5,
        )
        self.assertEqual(report["status"], "UNVERIFIED_2D")


if __name__ == "__main__":
    unittest.main()
