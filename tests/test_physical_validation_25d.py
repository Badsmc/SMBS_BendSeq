"""Read-only physical check contract: no false 3D qualification and no GUI dependencies."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from smbs_core.kinematics2d.contact_oracle_2d import (
    ContactReport, ContactFinding, ContactStatus,
)
from smbs_core.kinematics2d.physical_validation_25d import (
    PhysicalEvidence, _source_path, check_current_25d_pose,
    check_active_25d_bend_trajectory,
)

MOD = "smbs_core.kinematics2d.physical_validation_25d"


class PhysicalIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.angles = {"B01": 45.0}
        self.state = SimpleNamespace(
            angles=self.angles,
            bends={"B01": SimpleNamespace(axis_xyz=((0., 0., 0.), (100., 0., 0.)))},
        )
        self.session = SimpleNamespace(
            fold_state=self.state, panel_vertices_xy={"P01": ((0.,0.),(1.,0.),(1.,1.))},
            active_bend_id="B01", panel_build=object(),
        )
        self.pose = SimpleNamespace(
            rotation=((1,0,0),(0,1,0),(0,0,1)),
            translation=(0,0,0),
        )
        self.cycle = SimpleNamespace(
            phase="BENDING", ram_z_mm=2.0, transfer_fault=None,
            current_workpiece_placement_3d=lambda: self.pose,
        )
        self.tooling = SimpleNamespace(aligned=True, punch=None, die=None)
        self.section = {"fixed_polygon": ((0.,0.),(1.,0.),(1.,1.))}
        self.contact = ContactReport(
            ContactStatus.CLEAR,
            (ContactFinding("PUNCH","P01",ContactStatus.CLEAR,"no_overlap"),),
            0.02,
        )

    def _check(self, contact=None, native_3d=True):
        with patch(MOD + ".compute_machine_cutting_plane_section", return_value=self.section), \
             patch(MOD + ".check_sheet_tooling_preview", return_value=contact or self.contact):
            return check_current_25d_pose(self.session, self.cycle, self.tooling, native_3d=native_3d)

    def test_section_collision_still_runs_independent_native_clear_and_flags_conflict(self):
        """A 2D/3D disagreement must never be a false physical PASS."""
        import sys
        from unittest.mock import MagicMock
        from smbs_core.kinematics2d.native_collision_3d import CollisionClass

        cut = ContactReport(
            ContactStatus.INTERFERENCE,
            (ContactFinding("DIE", "active_fixed",
                            ContactStatus.INTERFERENCE,
                            "proper_material_boundary_crossing"),),
            0.02,
        )
        shape = MagicMock()
        shape.copy.return_value = shape
        punch = SimpleNamespace(span_xm=(-100.,100.), sections=(), machine_shape=shape)
        die = SimpleNamespace(span_xm=(-100.,100.), sections=(), machine_shape=shape)
        coverage = SimpleNamespace(is_covered=True, reason="covered")
        collision = SimpleNamespace(
            status=CollisionClass.CLEAR, is_safe=True, diagnostics=[],
        )
        app = SimpleNamespace(Matrix=lambda *a: object(), ActiveDocument=None,
                              getDocument=lambda name: None)
        with (
            patch.dict(sys.modules, {"FreeCAD": app}),
            patch(MOD + "._source_path", side_effect=[
                Path("puancon.FCStd"), Path("matrica.FCStd"),
            ]),
            patch(MOD + ".compute_machine_cutting_plane_section",
                  return_value=self.section),
            patch(MOD + ".check_sheet_tooling_preview", return_value=cut),
            patch("smbs_core.kinematics2d.native_bend_solid_3d.build_multi_bend_sheet_solid",
                  return_value=SimpleNamespace(shape=shape)),
            patch("smbs_core.kinematics2d.native_tool_loader.load_native_punch",
                  return_value=punch),
            patch("smbs_core.kinematics2d.native_tool_loader.load_native_die",
                  return_value=die),
            patch("smbs_core.kinematics2d.native_tool_loader.set_punch_ram_z",
                  return_value=punch),
            patch("smbs_core.kinematics2d.native_tool_loader.check_tool_coverage",
                  return_value=coverage),
            patch("smbs_core.kinematics2d.native_collision_3d.validate_workpiece_tooling_pose",
                  return_value=collision) as native_check,
        ):
            result = check_current_25d_pose(
                self.session, self.cycle, self.tooling, native_3d=True,
            )
        native_check.assert_called_once()
        self.assertEqual(result.section_status, "INTERFERENCE")
        self.assertEqual(result.native_status, "CLEAR")
        self.assertEqual(result.status, PhysicalEvidence.UNVERIFIED)
        self.assertIn("disagreement", result.reason)
        self.assertFalse(result.is_fully_qualified)
        self.assertEqual(self.angles, {"B01": 45.0})

    def test_native_source_must_match_selected_verified_profile(self):
        from smbs_core.kinematics2d.tooling_25d import ValidationStatus
        with tempfile.TemporaryDirectory() as tmp:
            fp = Path(tmp) / "punch123.FCStd"
            fp.write_bytes(b"fixture for path contract")
            obj = SimpleNamespace(
                validation_status=ValidationStatus.GEOMETRY_VERIFIED,
                source_file=str(fp), tool_id="other_tool",
            )
            self.assertIsNone(_source_path(obj))
            obj.tool_id = "punch123"
            self.assertEqual(_source_path(obj), fp)
            obj.validation_status = ValidationStatus.IMPORTED
            self.assertIsNone(_source_path(obj))

    def _trajectory(self, status, *, qualified=True, colliding=False, mutate=False):
        import sys
        from smbs_core.kinematics2d.motion_trajectory_3d import (
            TrajectoryEvidenceStatus,
        )
        from smbs_core.kinematics2d.tooling_25d import ValidationStatus
        self.session.bend_order = ("B01",)
        self.state.bends["B01"].angle_deg = 90.0
        self.state.transforms = {"P01": ("original",)}
        self.tooling.punch = SimpleNamespace(
            validation_status=ValidationStatus.GEOMETRY_VERIFIED,
            tool_id="puancon", source_file="puancon.FCStd",
        )
        self.tooling.die = SimpleNamespace(
            validation_status=ValidationStatus.GEOMETRY_VERIFIED,
            tool_id="matrica", source_file="matrica.FCStd",
        )
        def result(*args):
            if mutate:
                self.state.angles["B01"] = 12.
            return SimpleNamespace(
                evidence_status=status,
                is_qualified=qualified,
                fold_state_restored=True,
                total_states_evaluated=21,
                continuous_certification_status=TrajectoryEvidenceStatus.NOT_CONTINUOUSLY_CERTIFIED,
                first_collision_state=object() if colliding else None,
                diagnostics=["controlled_test"],
            )
        fake_app = SimpleNamespace(ActiveDocument=None, getDocument=lambda n: None)
        with (
            patch.dict(sys.modules, {"FreeCAD": fake_app}),
            patch(MOD + "._source_path", side_effect=[
                Path("puancon.FCStd"), Path("matrica.FCStd"),
            ]),
            patch("smbs_core.kinematics2d.native_tool_loader.load_native_punch",
                  return_value=object()),
            patch("smbs_core.kinematics2d.native_tool_loader.load_native_die",
                  return_value=object()),
            patch("smbs_core.kinematics2d.motion_trajectory_3d.AdaptiveTrajectoryValidator3D") as validator,
        ):
            validator.return_value.validate_bend_trajectory.side_effect = result
            return check_active_25d_bend_trajectory(self.session, self.tooling)

    def test_d634_converged_still_not_continuously_certified(self):
        from smbs_core.kinematics2d.motion_trajectory_3d import TrajectoryEvidenceStatus
        report = self._trajectory(TrajectoryEvidenceStatus.SAMPLED_INTERVAL_CONVERGED)
        self.assertEqual(report.status, PhysicalEvidence.SAMPLED_3D_CLEAR_NOT_CONTINUOUS)
        self.assertFalse(report.is_fully_qualified)
        self.assertEqual(self.angles, {"B01": 45.0})
        self.assertIn("NOT_CONTINUOUSLY_CERTIFIED", report.reason)

    def test_d634_positive_collision_rejected(self):
        from smbs_core.kinematics2d.motion_trajectory_3d import TrajectoryEvidenceStatus
        report = self._trajectory(
            TrajectoryEvidenceStatus.DISCRETE_SAMPLES_REJECTED,
            qualified=False, colliding=True,
        )
        self.assertEqual(report.status, PhysicalEvidence.COLLISION)
        self.assertFalse(report.is_fully_qualified)

    def test_d634_unevaluated_result_is_unverified(self):
        from smbs_core.kinematics2d.motion_trajectory_3d import TrajectoryEvidenceStatus
        report = self._trajectory(
            TrajectoryEvidenceStatus.INTERVAL_UNRESOLVED, qualified=False,
        )
        self.assertEqual(report.status, PhysicalEvidence.UNVERIFIED)

    def test_d634_detects_unrestored_fold_state(self):
        from smbs_core.kinematics2d.motion_trajectory_3d import TrajectoryEvidenceStatus
        with self.assertRaisesRegex(RuntimeError, "FOLDSTATE_RESTORATION_FAILED"):
            self._trajectory(
                TrajectoryEvidenceStatus.SAMPLED_INTERVAL_CONVERGED,
                mutate=True,
            )

    def test_missing_part_and_alignment_fail_closed(self):
        self.assertEqual(check_current_25d_pose(None, self.cycle, self.tooling).status,
                         PhysicalEvidence.UNVERIFIED)
        self.tooling.aligned = False
        self.assertEqual(self._check().status, PhysicalEvidence.UNVERIFIED)

    def test_transfer_cannot_be_certified_by_stationary_section(self):
        self.cycle.phase = "TRANSFER"
        report = self._check()
        self.assertEqual(report.status, PhysicalEvidence.UNVERIFIED)
        self.assertIn("swept", report.reason)
        self.assertFalse(report.is_fully_qualified)

    def test_2d_collision_rejected_without_invoking_3d(self):
        report = self._check(ContactReport(
            ContactStatus.INTERFERENCE,
            (ContactFinding("DIE","P01",ContactStatus.INTERFERENCE,"overlap"),),
            0.02,
        ))
        self.assertEqual(report.status, PhysicalEvidence.COLLISION)
        self.assertIn("DIE/P01", report.reason)
        self.assertEqual(report.native_status, "NOT_CHECKED")
        self.assertEqual(self.angles, {"B01": 45.0})

    def test_ambiguous_2d_contact_is_unverified(self):
        report = self._check(ContactReport(
            ContactStatus.UNVERIFIED,
            (ContactFinding("DIE","P01",ContactStatus.UNVERIFIED,"boundary"),),
            0.02,
        ))
        self.assertEqual(report.status, PhysicalEvidence.UNVERIFIED)
        self.assertFalse(report.is_fully_qualified)

    def test_clear_section_is_not_3d_qualified(self):
        report = self._check(native_3d=False)
        self.assertEqual(report.status, PhysicalEvidence.SECTION_CLEAR_3D_UNVERIFIED)
        self.assertEqual(report.native_status, "NOT_REQUESTED")
        self.assertFalse(report.is_fully_qualified)

    def test_missing_real_3d_tooling_cannot_report_pass(self):
        report = self._check()
        self.assertEqual(report.status, PhysicalEvidence.SECTION_CLEAR_3D_UNVERIFIED)
        self.assertEqual(report.native_status, "MISSING_SOURCE_FCSTD")
        self.assertNotIn("3D_PUNCH_DIE_CURRENT_POSE", report.checked_scope)


if __name__ == "__main__":
    unittest.main()
