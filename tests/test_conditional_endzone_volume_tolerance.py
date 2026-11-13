"""Regression for tiny OCC Common central slivers in punch Conditional end zones.

Numerical example reproduced from SMBS_debug_log(6).json: B08 after
B07/B03/B05/B01 with explicit 180-degree loading and virtual AP1017.
Use lightweight fake Part solids only for the *classification* contract.
FreeCAD/OCC integration on the real FCStd remains a separate runtime test.
"""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from smbs_core.kinematics2d.native_collision_3d import (
    classify_punch_overlap_zones,
    PunchCollisionZoneClassification as Zone,
)


def bbox(xmin, xmax):
    return SimpleNamespace(
        XMin=xmin, XMax=xmax,
        YMin=-20.0, YMax=-10.0,
        ZMin=12.0, ZMax=15.0,
    )


def solid(vol, xmin, xmax):
    return SimpleNamespace(
        Volume=vol,
        BoundBox=bbox(xmin, xmax),
        isNull=lambda: False,
        isValid=lambda: True,
    )


def classify(central_fragment_volumes, *, end_total_each=67.052432):
    """Full punch +/-148.177582mm, Conditional end zones are 10mm each."""
    punch = SimpleNamespace(BoundBox=bbox(-148.177582, 148.177582))
    workpiece = SimpleNamespace()
    components = [
        solid(end_total_each, -148.177582, -140.0),
        *[
            # Submicroscopic OCC artifacts with a full-length BBox.
            solid(vol, -148.177582, 148.177582)
            for vol in central_fragment_volumes
        ],
        solid(end_total_each, 140.0, 148.177582),
    ]
    common = SimpleNamespace(
        Solids=components,
        Volume=sum(x.Volume for x in components),
        isNull=lambda: False,
        isValid=lambda: True,
    )
    with patch(
        "smbs_core.kinematics2d.native_collision_3d.check_solid_validity",
        return_value=(True, "valid"),
    ), patch(
        "smbs_core.kinematics2d.native_collision_3d.measured_common",
        return_value=common,
    ):
        return classify_punch_overlap_zones(
            workpiece, punch, 10.0, volume_tolerance_mm3=1e-4,
        )


class ConditionalEndZoneVolumeToleranceTests(unittest.TestCase):
    def test_b08_22_5_deg_ignored_subtolerance_central_while_end_stays_conditional(self):
        result = classify([0.0000005, 0.0000005])
        self.assertEqual(result.classification, Zone.END_ZONE_ONLY)
        self.assertAlmostEqual(result.central_zone_overlap_volume_mm3, 1e-6)
        self.assertEqual(result.affected_sides, ("LEFT", "RIGHT"))
        self.assertIn("within_tolerance", result.reason)
        self.assertGreater(result.end_zone_overlap_volume_mm3, 100)

    def test_b08_90_deg_aggregate_micro_slivers_below_native_tolerance(self):
        result = classify([0.000029, 0.000029], end_total_each=595.081707)
        self.assertEqual(result.classification, Zone.END_ZONE_ONLY)
        self.assertAlmostEqual(result.central_zone_overlap_volume_mm3, 5.8e-5)
        self.assertAlmostEqual(result.end_zone_overlap_volume_mm3, 1190.163414)
        self.assertEqual(result.affected_sides, ("LEFT", "RIGHT"))

    def test_multiple_individually_small_central_slivers_over_limit_block(self):
        result = classify([0.00006, 0.00006])
        self.assertEqual(result.classification, Zone.MIXED_CENTRAL_AND_END_ZONE)
        self.assertAlmostEqual(result.central_zone_overlap_volume_mm3, .00012)
        self.assertIn("CENTRAL", result.affected_sides)

    def test_one_real_central_overlap_still_blocks_even_without_end_hits(self):
        punch = SimpleNamespace(BoundBox=bbox(-148.177582, 148.177582))
        middle = solid(1.0, -25.0, 25.0)
        common = SimpleNamespace(
            Solids=[middle], Volume=1.0,
            isNull=lambda: False, isValid=lambda: True,
        )
        with patch(
            "smbs_core.kinematics2d.native_collision_3d.check_solid_validity",
            return_value=(True, "valid"),
        ), patch(
            "smbs_core.kinematics2d.native_collision_3d.measured_common",
            return_value=common,
        ):
            result = classify_punch_overlap_zones(
                object(), punch, 10.0, volume_tolerance_mm3=1e-4,
            )
        self.assertEqual(result.classification, Zone.CENTRAL_ZONE)

    def test_normal_end_collision_is_still_identified(self):
        result = classify([])
        self.assertEqual(result.classification, Zone.END_ZONE_ONLY)
        self.assertEqual(result.central_zone_overlap_volume_mm3, 0.0)

    def test_central_above_tolerance_not_waived_by_end_zones(self):
        result = classify([0.005, 0.002])
        self.assertEqual(result.classification, Zone.MIXED_CENTRAL_AND_END_ZONE)


if __name__ == "__main__":
    unittest.main()
