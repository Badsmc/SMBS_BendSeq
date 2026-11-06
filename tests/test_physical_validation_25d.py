"""Read-only physical check contract: no false 3D qualification and no GUI dependencies."""
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from smbs_core.kinematics2d.contact_oracle_2d import (
    ContactReport, ContactFinding, ContactStatus,
)
from smbs_core.kinematics2d.physical_validation_25d import (
    PhysicalEvidence, check_current_25d_pose,
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
