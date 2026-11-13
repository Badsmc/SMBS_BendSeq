"""Conservative X1 component OCC screening contracts; no fake CLEAR allowed.

Headless tests exercise status/fallback behavior. Actual geometric equivalence
and timing require a local FreeCAD run against the original FCStd tooling.
"""
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from smbs_core.kinematics2d.native_bend_solid_3d import (
    NativeBendSolidError, NativeSheetComponents,
    join_multi_bend_sheet_components,
)
from smbs_core.kinematics2d.native_collision_3d import (
    CollisionClass, PunchCollisionZoneClassification,
)
from smbs_core.kinematics2d.native_component_collision_3d import (
    proven_component_report, screen_native_sheet_components,
)
from smbs_core.kinematics2d.motion_trajectory_3d import AdaptiveSamplingConfig


def bounds(left, right):
    return SimpleNamespace(
        XMin=left, XMax=right, YMin=0.0, YMax=5.0,
        ZMin=0.0, ZMax=5.0,
    )


class FakeShape:
    def __init__(self, name, lo, hi, *, valid=True):
        self.name = name
        self.BoundBox = bounds(lo, hi)
        self.Volume = max(hi - lo, 1e-2)
        self.valid = valid
        self.transformed = False

    def copy(self):
        return FakeShape(
            self.name, self.BoundBox.XMin, self.BoundBox.XMax,
            valid=self.valid,
        )

    def transformShape(self, matrix):
        self.transformed = True

    def isNull(self):
        return False

    def isValid(self):
        return self.valid

    def isClosed(self):
        return True


def components(*shapes):
    return NativeSheetComponents(
        angles_deg={"B01": 45.0},
        panels=tuple((s.name, s) for s in shapes),
        zones=(),
        expected_volume_mm3=sum(s.Volume for s in shapes),
    )


def screen(sheet, punch, die, *, conditional=True, volume=1e-4):
    return screen_native_sheet_components(
        sheet, object(), punch, die,
        volume_tolerance_mm3=volume,
        allow_end_zone_collision=conditional,
        end_zone_length_mm=10.0,
    )


class ComponentCollisionScreenTests(unittest.TestCase):
    def test_default_original_validator_remains_unmodified(self):
        self.assertFalse(AdaptiveSamplingConfig().component_collision_rejection)

    def test_separated_aabbs_never_invoke_common_and_never_prove_clear(self):
        sheet = components(FakeShape("P01", 0, 1))
        with patch(
            "smbs_core.kinematics2d.native_component_collision_3d._evaluate_common_volume"
        ) as common:
            result = screen(
                sheet, FakeShape("punch", 20, 30),
                FakeShape("die", -30, -20),
            )
        self.assertIsNone(result.hit)
        self.assertFalse(result.has_proven_rejection)
        self.assertEqual(result.aabb_rejected_pairs, 2)
        self.assertEqual(result.common_calls, 0)
        common.assert_not_called()

    def test_exact_die_overlap_rejects_before_full_sheet_union(self):
        original = FakeShape("P07", 0, 10)
        sheet = components(original)
        bbox = {"xmin": 2., "xmax": 3.}
        with patch(
            "smbs_core.kinematics2d.native_component_collision_3d._evaluate_common_volume",
            return_value=(1.25, bbox, object()),
        ) as common:
            got = screen(
                sheet, FakeShape("punch", 100, 200),
                FakeShape("die", 2, 3),
            )
        self.assertTrue(got.has_proven_rejection)
        self.assertEqual(got.hit.tool_label, "DIE")
        self.assertEqual(got.hit.component_id, "P07")
        self.assertAlmostEqual(got.hit.volume_mm3, 1.25)
        self.assertEqual(common.call_count, 1)
        self.assertFalse(original.transformed)
        rep = proven_component_report(got)
        self.assertEqual(rep.status, CollisionClass.PENETRATION)
        self.assertFalse(rep.is_safe)
        self.assertEqual(rep.die_result.status, CollisionClass.PENETRATION)
        self.assertEqual(rep.punch_result.status, CollisionClass.MISSING_EVIDENCE)
        self.assertIn("P07", rep.diagnostics[0])

    def test_small_component_overlap_is_not_independently_accepted_or_rejected(self):
        sheet = components(FakeShape("P01", 0, 3), FakeShape("P02", 1, 3))
        with patch(
            "smbs_core.kinematics2d.native_component_collision_3d._evaluate_common_volume",
            return_value=(6e-5, None, object()),
        ) as common:
            got = screen(
                sheet, FakeShape("punch", 100, 200),
                FakeShape("die", 1, 2),
            )
        self.assertFalse(got.has_proven_rejection)
        self.assertEqual(got.common_calls, 2)
        self.assertEqual(common.call_count, 2)

    def test_punch_end_only_conditional_is_not_rejected(self):
        sheet = components(FakeShape("bend_zone:B09", 0, 10))
        with patch(
            "smbs_core.kinematics2d.native_component_collision_3d._evaluate_common_volume",
            return_value=(6., None, object()),
        ), patch(
            "smbs_core.kinematics2d.native_component_collision_3d.classify_punch_overlap_zones",
            return_value=SimpleNamespace(
                classification=PunchCollisionZoneClassification.END_ZONE_ONLY,
                central_zone_overlap_volume_mm3=0.0,
            ),
        ) as classify:
            got = screen(
                sheet, FakeShape("punch", 1, 8),
                FakeShape("die", 20, 30),
            )
        self.assertIsNone(got.hit)
        classify.assert_called_once()
        self.assertEqual(got.fallback_reason, "needs_full_sheet_oracle")

    def test_punch_central_b09_is_proven_even_with_end_zone_enabled(self):
        sheet = components(FakeShape("bend_zone:B09", 0, 10))
        with patch(
            "smbs_core.kinematics2d.native_component_collision_3d._evaluate_common_volume",
            return_value=(2.75176, {"xmin": 1}, object()),
        ), patch(
            "smbs_core.kinematics2d.native_component_collision_3d.classify_punch_overlap_zones",
            return_value=SimpleNamespace(
                classification=PunchCollisionZoneClassification.CENTRAL_ZONE,
                central_zone_overlap_volume_mm3=2.75176,
            ),
        ):
            got = screen(
                sheet, FakeShape("punch", 1, 8),
                FakeShape("die", 20, 30),
            )
        self.assertTrue(got.has_proven_rejection)
        self.assertTrue(got.hit.central_zone_proved)
        rep = proven_component_report(got)
        self.assertFalse(rep.is_conditional_safe)
        self.assertEqual(rep.punch_result.status, CollisionClass.PENETRATION)
        self.assertEqual(rep.die_result.status, CollisionClass.MISSING_EVIDENCE)

    def test_uncertain_end_zone_does_not_turn_into_collision_or_clear(self):
        sheet = components(FakeShape("P01", 0, 10))
        with patch(
            "smbs_core.kinematics2d.native_component_collision_3d._evaluate_common_volume",
            return_value=(1.0, None, object()),
        ), patch(
            "smbs_core.kinematics2d.native_component_collision_3d.classify_punch_overlap_zones",
            return_value=SimpleNamespace(
                classification=PunchCollisionZoneClassification.UNCLASSIFIED,
                central_zone_overlap_volume_mm3=1.0,
            ),
        ):
            got = screen(
                sheet, FakeShape("punch", 2, 8),
                FakeShape("die", 20, 30),
            )
        self.assertIsNone(got.hit)

    def test_kernel_error_and_invalid_tool_always_fallback(self):
        sheet = components(FakeShape("P01", 0, 10))
        with patch(
            "smbs_core.kinematics2d.native_component_collision_3d._evaluate_common_volume",
            side_effect=RuntimeError("OCC failed"),
        ):
            got = screen(
                sheet, FakeShape("punch", 25, 30),
                FakeShape("die", 2, 8),
            )
        self.assertIsNone(got.hit)
        self.assertIn("component_screen_exception", got.fallback_reason)
        invalid = screen(
            sheet, FakeShape("punch", 2, 8, valid=False),
            FakeShape("die", 2, 8),
        )
        self.assertIsNone(invalid.hit)
        self.assertIn("invalid_tool", invalid.fallback_reason)

    def test_invalid_transformed_component_fails_closed(self):
        got = screen(
            components(FakeShape("P01", 0, 10, valid=False)),
            FakeShape("punch", 1, 4), FakeShape("die", 1, 4),
        )
        self.assertIsNone(got.hit)
        self.assertIn("invalid_component", got.fallback_reason)

    def test_no_fabricated_report_for_nonblocking_screen(self):
        got = screen(
            components(FakeShape("P01", 0, 1)),
            FakeShape("punch", 20, 30), FakeShape("die", -20, -10),
        )
        with self.assertRaisesRegex(ValueError, "no_proven_hit"):
            proven_component_report(got)


class FakeJoined:
    def __init__(self, *, volume=10.0, valid=True, closed=True):
        self.Volume = volume
        self.valid, self.closed = valid, closed
        self.calls = 0

    def isNull(self):
        return False

    def isValid(self):
        self.calls += 1
        return self.valid

    def isClosed(self):
        return self.closed

    @property
    def Solids(self):
        return [object()]

    def multiFuse(self, others):
        return self


class ComponentAssemblyQualificationTests(unittest.TestCase):
    def test_join_still_validates_native_topology_and_volume(self):
        joined = FakeJoined(volume=10)
        comps = NativeSheetComponents(
            angles_deg={"B01": 45.0},
            panels=(("P01", joined), ("P02", object())),
            zones=(),
            expected_volume_mm3=10.0,
        )
        result = join_multi_bend_sheet_components(comps)
        self.assertIs(result.shape, joined)
        self.assertEqual(joined.calls, 1)
        self.assertEqual(result.panels_count, 2)

    def test_volume_mismatch_cannot_be_screened_away(self):
        comps = NativeSheetComponents(
            angles_deg={"B01": 45.0},
            panels=(("P01", FakeJoined(volume=7.0)), ("P02", object())),
            zones=(),
            expected_volume_mm3=10.0,
        )
        with self.assertRaisesRegex(
            NativeBendSolidError, "panel_zone_overlap_or_missing_material",
        ):
            join_multi_bend_sheet_components(comps)

    def test_joined_invalid_shape_still_fails(self):
        comps = NativeSheetComponents(
            angles_deg={"B01": 45.0},
            panels=(("P01", FakeJoined(valid=False)), ("P02", object())),
            zones=(),
            expected_volume_mm3=10.0,
        )
        with self.assertRaisesRegex(
            NativeBendSolidError, "zones_and_panels_not_one_valid_closed_solid",
        ):
            join_multi_bend_sheet_components(comps)


if __name__ == "__main__":
    unittest.main()
