"""Headless contracts for exact FreeCAD OCC collision inspection.

The real FreeCAD native-view acceptance is still required; mocks here only
check source-pose invariants, conservative failure and data provenance.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from smbs_core.kinematics2d.native_collision_inspector import (
    NativeCollisionInspectionError,
    _validated_collision_pose,
    _check_replay_volume,
    _common_positive,
)


def native_b03_blocker():
    return {
        "phase": "BENDING",
        "tool": "PUNCH",
        "angle_deg": 52.03125,
        "first_sample_punch_overlap_mm3": 17.048115,
        "sampled_machine_pose": {
            "bend_id": "B03",
            "phase": "BENDING",
            "signed_angle_deg": 52.03125,
            "ram_z_mm": -1.406211,
            "rotation": [
                [1.0, 0.0, 0.0],
                [0.0, 1.0, 0.0],
                [0.0, 0.0, 1.0],
            ],
            "translation": [0.0, 0.0, 1.5],
            "completed_angles": {"B07": 90.0},
        },
    }


class NativeCollisionInspectorContractTests(TestCase):
    def test_accepts_only_specific_sampled_bend_phase_and_pose(self):
        blocker = native_b03_blocker()
        self.assertEqual(
            _validated_collision_pose("B03", blocker)["ram_z_mm"], -1.406211,
        )
        for key, value in (
            ("tool", "UNKNOWN"),
            ("angle_deg", 90.0),
        ):
            bad = dict(blocker, **{key: value})
            with self.assertRaises(NativeCollisionInspectionError):
                _validated_collision_pose("B03", bad)
        with self.assertRaises(NativeCollisionInspectionError):
            _validated_collision_pose("B07", blocker)
        bad = native_b03_blocker()
        bad["sampled_machine_pose"] = {
            **bad["sampled_machine_pose"], "translation": None,
        }
        with self.assertRaises(NativeCollisionInspectionError):
            _validated_collision_pose("B03", bad)

    def test_replay_volume_must_match_authoritative_d6_measurement(self):
        blocker = native_b03_blocker()
        _check_replay_volume(blocker, "PUNCH", 17.048114)
        with self.assertRaisesRegex(
            NativeCollisionInspectionError, "native_replay_overlap_mismatch",
        ):
            _check_replay_volume(blocker, "PUNCH", 22.0)
        with self.assertRaisesRegex(
            NativeCollisionInspectionError, "missing_sampled_volume",
        ):
            _check_replay_volume(blocker, "DIE", 1.0)

    def test_exact_intersection_uses_occ_common_not_overlapping_bbox(self):
        sheet = SimpleNamespace()
        punch = SimpleNamespace()
        exact = SimpleNamespace(
            isNull=lambda: False, isValid=lambda: True, Volume=17.048114,
        )
        scope = "smbs_core.kinematics2d.native_collision_3d."
        with patch(scope + "check_solid_validity",
                   return_value=(True, "valid")) as valid:
            with patch(scope + "measured_common",
                       return_value=exact) as common:
                got, vol = _common_positive(sheet, punch)
        self.assertIs(got, exact)
        self.assertEqual(vol, exact.Volume)
        common.assert_called_once_with(
            sheet, punch, label="collision_inspector_common",
        )
        self.assertEqual(valid.call_count, 2)

    def test_no_measurable_common_is_never_drawn_as_contact(self):
        empty = SimpleNamespace(
            isNull=lambda: False, isValid=lambda: True, Volume=1e-6,
        )
        scope = "smbs_core.kinematics2d.native_collision_3d."
        with patch(scope + "check_solid_validity",
                   return_value=(True, "valid")):
            with patch(scope + "measured_common", return_value=empty):
                shape, vol = _common_positive(object(), object())
        self.assertIsNone(shape)
        self.assertAlmostEqual(vol, 1e-6)


if __name__ == "__main__":
    import unittest
    unittest.main()
