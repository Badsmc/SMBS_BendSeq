"""The 3D tool preview follows the same fixed machine frame as the 2D setup."""
import unittest
from types import SimpleNamespace

from smbs_core.kinematics2d.preview_mesh_3d import build_tool_preview_faces
from smbs_core.kinematics2d.tooling_25d import (
    ToolingSetup2D, ValidationStatus,
)


def _tool(tool_id):
    return SimpleNamespace(
        tool_id=tool_id, length_mm=100.0,
        validation_status=ValidationStatus.GEOMETRY_VERIFIED,
        punch_datums=object(), die_datums=object(),
        working_datum={"s": 0.0, "z": 0.0},
        local_polygons=lambda arc_segments: (
            ((-5.0, 0.0), (5.0, 0.0), (5.0, 20.0), (-5.0, 20.0)),
            (((-1.0, 5.0), (1.0, 5.0), (0.0, 7.0)),),
        ),
    )


class StationaryToolPreviewTests(unittest.TestCase):
    def test_different_bend_sections_move_sheet_not_tool_station(self):
        setup = ToolingSetup2D(punch=_tool("punch"), die=_tool("die"))
        for bend_id, section_midpoint in (("B01", 0.0), ("B02", 12.0)):
            section = {
                "neutral_curve": ((-1.0, 0.0), (section_midpoint, 0.0),
                                  (1.0, 0.0)),
                "zone_polygon": ((0.0, 0.0), (0.0, 1.5)),
                "thickness_mm": 1.5,
                "angle_deg": 0.0,
            }
            setup.align_to_cross_section(section, bend_id=bend_id)
            self.assertEqual(setup.die_v_center_machine(), (0.0, 0.0))
            self.assertEqual(setup.punch_tip_machine()[0], 0.0)

        initial = build_tool_preview_faces(setup)
        self.assertEqual(
            {depth for face in initial for _, depth, _ in face.vertices},
            {-50.0, 50.0},
        )
        self.assertTrue(any(face.holes for face in initial))
        self.assertTrue(any(face.kind.startswith("punch") for face in initial))
        self.assertTrue(any(face.kind.startswith("die") for face in initial))

        setup.set_punch_ram_z(setup.ram_z_mm - 8.0)
        moved = build_tool_preview_faces(setup)
        for before, after in zip(initial, moved):
            self.assertEqual(before.kind, after.kind)
            self.assertEqual(
                [(s, x) for s, x, _ in before.vertices],
                [(s, x) for s, x, _ in after.vertices],
            )
            delta = -8.0 if before.kind.startswith("punch") else 0.0
            for a, b in zip(before.vertices, after.vertices):
                self.assertAlmostEqual(b[2] - a[2], delta)

        # A saved native collision pose has its own measured ram height.
        # Rendering that pose must not move the live 2D tool setup.
        inspected = build_tool_preview_faces(
            setup, punch_ram_z_mm=setup.ram_z_mm + 3.0,
        )
        for current, logged in zip(moved, inspected):
            offset = 3.0 if current.kind.startswith("punch") else 0.0
            for a, b in zip(current.vertices, logged.vertices):
                self.assertAlmostEqual(b[2] - a[2], offset)

    def test_multiple_selected_sections_keep_their_machine_stations(self):
        setup = ToolingSetup2D(punch=_tool("punch"), die=_tool("die"))
        setup.set_native_sections((-60.0, 60.0), (-60.0, 60.0))
        faces = build_tool_preview_faces(setup)
        self.assertEqual(
            {depth for face in faces for _, depth, _ in face.vertices},
            {-110.0, -10.0, 10.0, 110.0},
        )


if __name__ == "__main__":
    unittest.main()
