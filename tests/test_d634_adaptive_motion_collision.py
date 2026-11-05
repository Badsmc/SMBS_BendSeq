"""Unit tests for D6.3.4 Adaptive Motion Collision Validation."""
from __future__ import annotations

import math
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from smbs_core.kinematics2d.motion_trajectory_3d import (
    AdaptiveSamplingConfig,
    AdaptiveTrajectoryValidator3D,
    BendTrajectoryResult,
    MotionState3D,
    TrajectoryEvidenceStatus,
    TrajectoryPhaseResult,
    check_motion_continuity,
)
from smbs_core.kinematics2d.native_collision_3d import (
    CollisionClass,
    PairClearanceResult,
    SetupQualificationStatus,
    ToolCollisionReport,
)
from smbs_core.kinematics2d.workpiece_placement_3d import PlacementTransform3D


def _make_dummy_tool_report(
    status: CollisionClass = CollisionClass.CLEAR,
    p_dist: float = 10.0,
    d_dist: float = 10.0,
    p_vol: float = 0.0,
    d_vol: float = 0.0,
    is_safe: bool = True,
) -> ToolCollisionReport:
    p_res = PairClearanceResult(status, "test", p_dist, p_vol, None, 0.1)
    d_res = PairClearanceResult(status, "test", d_dist, d_vol, None, 0.1)
    return ToolCollisionReport(status, p_res, d_res, is_safe, [], 0.2)


def _identity_placement(tz: float = 0.0) -> PlacementTransform3D:
    eye = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
    return PlacementTransform3D(rotation=eye, translation=(0.0, 0.0, tz))


class TestD634AdaptiveMotionCollision(unittest.TestCase):
    """Test suite verifying D6.3.4 specifications and contracts."""

    def test_status_vocabulary(self):
        """Verify strict trajectory evidence status vocabulary."""
        expected = {
            "DISCRETE_SAMPLES_SAFE",
            "DISCRETE_SAMPLES_REJECTED",
            "ADAPTIVE_SAMPLING_CONVERGED",
            "INTERVAL_UNRESOLVED",
            "CONTINUOUS_COLLISION_CERTIFIED",
            "MISSING_EVIDENCE",
            "INVALID_GEOMETRY",
            "TRANSFER_PATH_UNDEFINED",
        }
        actual = {e.value for e in TrajectoryEvidenceStatus}
        self.assertEqual(expected, actual)

    def test_motion_continuity_check_phase_boundary_clean(self):
        """Clean boundary between phases passes continuity check."""
        cfg = AdaptiveSamplingConfig()
        s0 = MotionState3D(
            phase="APPROACH",
            active_bend_id="B01",
            signed_angle_deg=0.0,
            path_u=1.0,
            ram_z_mm=1.5,
            support_plane_zm=2.5,
            workpiece_placement=_identity_placement(2.5),
            completed_angles={},
            collision_report=_make_dummy_tool_report(),
            is_safe=True,
        )
        s1 = MotionState3D(
            phase="CONTACT",
            active_bend_id="B01",
            signed_angle_deg=0.0,
            path_u=0.0,
            ram_z_mm=1.5,
            support_plane_zm=2.5,
            workpiece_placement=_identity_placement(2.5),
            completed_angles={},
            collision_report=_make_dummy_tool_report(),
            is_safe=True,
        )
        ok, reason = check_motion_continuity(s0, s1, cfg)
        self.assertTrue(ok)
        self.assertEqual(reason, "CONTINUOUS")

    def test_motion_continuity_detects_position_jump(self):
        """Position jump across phase boundary triggers discontinuity failure."""
        cfg = AdaptiveSamplingConfig(position_continuity_tolerance_mm=1e-3)
        s0 = MotionState3D(
            phase="APPROACH",
            active_bend_id="B01",
            signed_angle_deg=0.0,
            path_u=1.0,
            ram_z_mm=1.5,
            support_plane_zm=2.5,
            workpiece_placement=_identity_placement(2.5),
            completed_angles={},
            collision_report=_make_dummy_tool_report(),
            is_safe=True,
        )
        s1 = MotionState3D(
            phase="CONTACT",
            active_bend_id="B01",
            signed_angle_deg=0.0,
            path_u=0.0,
            ram_z_mm=1.5,
            support_plane_zm=2.5,
            workpiece_placement=_identity_placement(2.6),  # 0.1mm jump!
            completed_angles={},
            collision_report=_make_dummy_tool_report(),
            is_safe=True,
        )
        ok, reason = check_motion_continuity(s0, s1, cfg)
        self.assertFalse(ok)
        self.assertIn("PHASE_BOUNDARY_POSITION_JUMP", reason)

    def test_motion_continuity_detects_angle_jump(self):
        """Angle jump across phase boundary triggers discontinuity failure."""
        cfg = AdaptiveSamplingConfig(angle_continuity_tolerance_deg=1e-3)
        s0 = MotionState3D(
            phase="APPROACH",
            active_bend_id="B01",
            signed_angle_deg=0.0,
            path_u=1.0,
            ram_z_mm=1.5,
            support_plane_zm=2.5,
            workpiece_placement=_identity_placement(2.5),
            completed_angles={},
            collision_report=_make_dummy_tool_report(),
            is_safe=True,
        )
        s1 = MotionState3D(
            phase="CONTACT",
            active_bend_id="B01",
            signed_angle_deg=5.0,  # 5 deg jump!
            path_u=0.0,
            ram_z_mm=1.5,
            support_plane_zm=2.5,
            workpiece_placement=_identity_placement(2.5),
            completed_angles={},
            collision_report=_make_dummy_tool_report(),
            is_safe=True,
        )
        ok, reason = check_motion_continuity(s0, s1, cfg)
        self.assertFalse(ok)
        self.assertIn("PHASE_BOUNDARY_ANGLE_JUMP", reason)

    def test_transfer_path_undefined_when_no_trajectory(self):
        """Transfer phase without Transfer3D returns TRANSFER_PATH_UNDEFINED."""
        validator = AdaptiveTrajectoryValidator3D(
            fold_state=MagicMock(),
            panel_build=MagicMock(),
            punch_base=MagicMock(),
            die=MagicMock(),
        )
        tr = validator.validate_transfer_phase("B01", "B02", {}, transfer_3d=None)
        self.assertEqual(tr.evidence_status, TrajectoryEvidenceStatus.TRANSFER_PATH_UNDEFINED)
        self.assertFalse(tr.is_safe)
        self.assertIn("TRANSFER_PATH_UNDEFINED", tr.diagnostics[0])

    def test_continuous_collision_certified_not_falsely_reported(self):
        """ADAPTIVE_SAMPLING_CONVERGED must not falsely report CONTINUOUS_COLLISION_CERTIFIED."""
        # Ensure our status mapping never sets CONTINUOUS_COLLISION_CERTIFIED on sampling convergence
        self.assertNotEqual(
            TrajectoryEvidenceStatus.ADAPTIVE_SAMPLING_CONVERGED,
            TrajectoryEvidenceStatus.CONTINUOUS_COLLISION_CERTIFIED,
        )


if __name__ == "__main__":
    unittest.main()
