from dataclasses import replace
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from smbs_core.kinematics2d import native_tool_loader
from smbs_core.kinematics2d.tooling_25d import (
    Contour2D, LineSegment2D, ProfileGeometry2D, ToolKind, ToolProfile,
)
from smbs_core.kinematics2d.tooling_assembly import has_native_sections, native_section_options


class PunchOrientationTests(TestCase):
    def test_library_flag_round_trips_and_matches_machine_side(self):
        ring = ((0., 0.), (4., 8.), (-1., 8.))
        contour = Contour2D(tuple(LineSegment2D(ring[i-1], p) for i, p in enumerate(ring)))
        profile = ToolProfile(
            "test", "test", ToolKind.PUNCH, "FCSTD", "test.FCStd", "mm",
            ProfileGeometry2D(contour),
        )
        data = profile.to_dict()
        del data["punch_reversed"]
        self.assertFalse(ToolProfile.from_dict(data).punch_reversed)
        with patch.object(native_tool_loader, "HAS_FREECAD", True), patch.object(
            native_tool_loader, "App", SimpleNamespace(Matrix=lambda *values: values),
        ):
            for reversed_punch in (False, True):
                oriented = replace(profile, punch_reversed=reversed_punch)
                restored = ToolProfile.from_dict(oriented.to_dict())
                self.assertEqual(restored.punch_reversed, reversed_punch)
                options = native_section_options(SimpleNamespace(punch=restored), "punch")
                self.assertEqual(options["reverse_punch"], reversed_punch)
                self.assertFalse(has_native_sections(SimpleNamespace(punch=restored)))
                matrix = native_tool_loader._matrix_for_punch(
                    (0., 0.), 100., reverse_punch=reversed_punch,
                )
                expected = {(-matrix[4] * x, z) for x, z in ring}
                self.assertEqual(set(restored.local_polygons()[0]), expected)
                self.assertEqual(matrix[2] * matrix[4] * matrix[9], 1.)
                self.assertEqual({matrix[3], matrix[2] * 100. + matrix[3]}, {-50., 50.})
