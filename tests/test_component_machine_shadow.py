"""Conservative machine-frame broad phase and read-only shadow evidence.

Fake BReps exercise contracts; a real FreeCAD/OCCT run is still required.
"""
import math
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from smbs_core.kinematics2d.component_collision_oracle import (
    ComponentVerdict,
    MachineComponentScreen,
    _machine_box,
    count_component_shadow_checks,
    record_component_shadow_probe,
    screen_machine_component_overlap,
)
from smbs_core.kinematics2d.native_collision_3d import CollisionClass


def box(x0, x1, y0=0, y1=1, z0=0, z1=1):
    return SimpleNamespace(
        XMin=x0, XMax=x1, YMin=y0, YMax=y1, ZMin=z0, ZMax=z1,
    )


def placement(dx=0, dy=0, angle=0):
    theta = math.radians(angle)
    c, s = math.cos(theta), math.sin(theta)
    return SimpleNamespace(
        rotation=((c, -s, 0), (s, c, 0), (0, 0, 1)),
        translation=(dx, dy, 0),
    )


class FakeShape:
    def __init__(self, bbox):
        self.BoundBox = bbox
        self.copy_calls = 0

    def isNull(self):
        return False

    def copy(self):
        self.copy_calls += 1
        return FakeShape(box(
            self.BoundBox.XMin, self.BoundBox.XMax,
            self.BoundBox.YMin, self.BoundBox.YMax,
            self.BoundBox.ZMin, self.BoundBox.ZMax,
        ))

    def transformShape(self, matrix):
        b = self.BoundBox
        self.BoundBox = box(
            b.XMin + matrix.dx, b.XMax + matrix.dx,
            b.YMin + matrix.dy, b.YMax + matrix.dy,
            b.ZMin, b.ZMax,
        )


class MachineComponentShadowTests(unittest.TestCase):
    def test_machine_bbox_corners_include_rotation_and_placement(self):
        out = _machine_box(box(0, 2, 0, 1), placement(dx=10, dy=-2, angle=90))
        self.assertLess(out.XMin, 9.0)
        self.assertGreater(out.XMax, 10.0)
        self.assertLess(out.YMin, -2)
        self.assertGreater(out.YMax, 0.0)

    def test_far_component_does_not_call_occ_and_never_claims_clear(self):
        part = FakeShape(box(0, 1))
        tool = FakeShape(box(20, 21))
        with patch("smbs_core.kinematics2d.component_collision_oracle._evaluate_common_volume") as common:
            got = screen_machine_component_overlap(
                (("P01", part),), placement(dx=10),
                SimpleNamespace(dx=10, dy=0),
                "PUNCH", tool, volume_tolerance_mm3=1e-4,
            )
        self.assertEqual(got.verdict, ComponentVerdict.FALLBACK_REQUIRED)
        self.assertEqual(got.aabb_rejected, 1)
        self.assertEqual(got.candidate_count, 0)
        self.assertEqual(got.transformed_count, 0)
        common.assert_not_called()
        self.assertEqual(part.copy_calls, 0)

    def test_machine_transform_applied_to_exact_common_candidate(self):
        part = FakeShape(box(0, 2))
        tool = FakeShape(box(11, 12))
        def evaluate(workpiece, real_tool):
            self.assertEqual(workpiece.BoundBox.XMin, 10)
            self.assertEqual(workpiece.BoundBox.XMax, 12)
            self.assertIs(real_tool, tool)
            return 2.5
        with patch("smbs_core.kinematics2d.component_collision_oracle._evaluate_common_volume",
                   side_effect=evaluate) as common:
            got = screen_machine_component_overlap(
                (("bend_zone:B09", part),), placement(dx=10),
                SimpleNamespace(dx=10, dy=0), "PUNCH", tool,
                volume_tolerance_mm3=1e-4,
            )
        self.assertEqual(got.verdict, ComponentVerdict.PROVEN_PENETRATION)
        self.assertEqual(got.hit.component_id, "bend_zone:B09")
        self.assertEqual(got.hit.volume_mm3, 2.5)
        self.assertEqual(got.common_calls, 1)
        self.assertEqual(common.call_count, 1)
        self.assertEqual(part.BoundBox.XMin, 0)  # source never mutated

    def test_candidate_budget_falls_back_without_occ(self):
        comps = tuple((f"P{i:02d}", FakeShape(box(0, 2))) for i in range(1, 9))
        with patch("smbs_core.kinematics2d.component_collision_oracle._evaluate_common_volume") as common:
            got = screen_machine_component_overlap(
                comps, placement(), SimpleNamespace(dx=0, dy=0),
                "DIE", FakeShape(box(1, 3)),
                volume_tolerance_mm3=1e-4, max_exact_candidates=4,
            )
        self.assertEqual(got.reason, "candidate_budget_fallback")
        self.assertEqual(got.candidate_count, 8)
        self.assertEqual(got.transformed_count, 0)
        common.assert_not_called()

    def test_invalid_nonrigid_matrix_fails_closed(self):
        bad = placement()
        bad.rotation = ((2, 0, 0), (0, 1, 0), (0, 0, 1))
        got = screen_machine_component_overlap(
            (("P01", FakeShape(box(0, 1))),), bad,
            SimpleNamespace(dx=0, dy=0), "DIE", FakeShape(box(0, 1)),
            volume_tolerance_mm3=1e-4,
        )
        self.assertEqual(got.verdict, ComponentVerdict.FALLBACK_REQUIRED)
        self.assertTrue(got.reason.startswith("component_machine_screen_error"))

    def test_nan_tool_bbox_never_becomes_clear(self):
        got = screen_machine_component_overlap(
            (("P01", FakeShape(box(0, 1))),), placement(),
            SimpleNamespace(dx=0, dy=0), "DIE",
            FakeShape(box(float("nan"), float("nan"))),
            volume_tolerance_mm3=1e-4,
        )
        self.assertEqual(got.verdict, ComponentVerdict.FALLBACK_REQUIRED)

    def test_shadow_metrics_capture_disagreement_but_not_change_oracle(self):
        screen = MachineComponentScreen(
            ComponentVerdict.PROVEN_PENETRATION, "exact_positive",
            "PUNCH", 21, 18, 3, 1, 1, 15.0,
            hit=SimpleNamespace(component_id="P09", volume_mm3=25.0),
        )
        oracle = SimpleNamespace(
            status=CollisionClass.CLEAR,
            punch_result=SimpleNamespace(status=CollisionClass.CLEAR),
            die_result=SimpleNamespace(status=CollisionClass.CLEAR),
        )
        with count_component_shadow_checks() as stats:
            record_component_shadow_probe({"PUNCH": screen}, oracle, "B07", "RETRACT", 0.4)
        self.assertEqual(stats.proven_component_hits, 1)
        self.assertEqual(stats.disagreements, 1)
        self.assertEqual(stats.exact_candidates, 3)
        self.assertEqual(stats.to_dict()["qualification"],
                         "NOT_A_CLEARANCE_OR_CONTINUOUS_MOTION_CERTIFICATE")
        self.assertEqual(oracle.status, CollisionClass.CLEAR)

    def test_conditional_punch_overlap_is_not_shadow_disagreement(self):
        screen = MachineComponentScreen(
            ComponentVerdict.PROVEN_PENETRATION, "exact_positive",
            "PUNCH", 21, 20, 1, 1, 1, 5.0,
            hit=SimpleNamespace(component_id="P07", volume_mm3=11.5),
        )
        oracle = SimpleNamespace(
            status=CollisionClass.PENETRATION,
            is_conditional_safe=True,
            punch_result=SimpleNamespace(status=CollisionClass.PENETRATION),
            die_result=SimpleNamespace(status=CollisionClass.CLEAR),
        )
        with count_component_shadow_checks() as stats:
            record_component_shadow_probe({"PUNCH": screen}, oracle, "B08", "BENDING", 0.9)
        self.assertEqual(stats.disagreements, 0)
        self.assertEqual(stats.proven_component_hits, 1)
        actual = stats.to_dict()["examples"][0]["results"][0]
        self.assertTrue(actual["full_oracle_conditional"])
        self.assertEqual(actual["full_oracle_tool_status"], "PENETRATION")
        self.assertEqual(actual["hit_volume_mm3"], screen.hit.volume_mm3)


if __name__ == "__main__":
    unittest.main()
