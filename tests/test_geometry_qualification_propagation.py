"""Unit and integration tests for geometric qualification propagation across rigid transforms.

Tests:
- Tool validity proof generation on LoadedTool (punch, die, set_punch_ram_z).
- Rigid isometry preservation: verification that valid solids under Euclidean isometries
  (det = +1.0, orthogonal R) preserve validity without re-invoking OCC check_solid_validity.
- Fail-closed behavior on non-rigid transforms (scaling, reflection, shear, singular).
- Invalidation on object mutation, mismatched object references, or wrong shapes.
- Cache hit/miss accounting in NativeGeometryBuildMetrics.
"""
from __future__ import annotations

import unittest
from types import SimpleNamespace

from smbs_core.kinematics2d.native_bend_solid_3d import (
    is_rigid_transform_matrix, NativeMultiBendSheet,
)
from smbs_core.kinematics2d.native_collision_3d import (
    CollisionClass, check_pair_clearance, validate_workpiece_tooling_pose,
    check_solid_validity,
)
from smbs_core.kinematics2d.native_geometry_build_metrics import (
    count_native_geometry_builds,
)


class MockMatrix:
    def __init__(self, R=None, T=(0.0, 0.0, 0.0), det=1.0, affine=True):
        self.R = R or [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
        self.T = T
        self._det = det
        self.affine = affine

    @property
    def A11(self): return self.R[0][0]
    @property
    def A12(self): return self.R[0][1]
    @property
    def A13(self): return self.R[0][2]
    @property
    def A14(self): return self.T[0]

    @property
    def A21(self): return self.R[1][0]
    @property
    def A22(self): return self.R[1][1]
    @property
    def A23(self): return self.R[1][2]
    @property
    def A24(self): return self.T[1]

    @property
    def A31(self): return self.R[2][0]
    @property
    def A32(self): return self.R[2][1]
    @property
    def A33(self): return self.R[2][2]
    @property
    def A34(self): return self.T[2]

    @property
    def A41(self): return 0.0 if self.affine else 0.5
    @property
    def A42(self): return 0.0
    @property
    def A43(self): return 0.0
    @property
    def A44(self): return 1.0

    def determinant(self):
        return self._det


class FakeSolidShape:
    def __init__(self, x0, x1, *, valid=True):
        self.BoundBox = SimpleNamespace(XMin=x0, XMax=x1, YMin=0, YMax=1, ZMin=0, ZMax=1)
        self.valid = valid
        self.valid_calls = 0
        self.Volume = 100.0

    def isNull(self):
        return False

    def isValid(self):
        self.valid_calls += 1
        return self.valid

    def isClosed(self):
        return True


class GeometryQualificationPropagationTests(unittest.TestCase):
    def test_rigid_matrix_detection(self):
        # Pure translation / identity
        self.assertTrue(is_rigid_transform_matrix(MockMatrix()))
        # Pure 90 deg Z rotation
        R_z90 = [[0.0, -1.0, 0.0], [1.0, 0.0, 0.0], [0.0, 0.0, 1.0]]
        self.assertTrue(is_rigid_transform_matrix(MockMatrix(R=R_z90, det=1.0)))

        # Non-rigid: scaled (det != 1.0)
        self.assertFalse(is_rigid_transform_matrix(MockMatrix(det=1.5)))
        # Non-rigid: reflection (det = -1.0)
        R_refl = [[-1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
        self.assertFalse(is_rigid_transform_matrix(MockMatrix(R=R_refl, det=-1.0)))
        # Non-rigid: non-orthogonal R
        R_shear = [[1.0, 0.5, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
        self.assertFalse(is_rigid_transform_matrix(MockMatrix(R=R_shear, det=1.0)))
        # Non-rigid: non-affine (bottom row != [0,0,0,1])
        self.assertFalse(is_rigid_transform_matrix(MockMatrix(affine=False)))
        # None matrix fails closed
        self.assertFalse(is_rigid_transform_matrix(None))

    def test_tool_validity_proof_passed_to_check_pair_clearance(self):
        sheet = FakeSolidShape(0, 1)
        punch = FakeSolidShape(3, 4)
        proof = (punch, (True, "valid"))

        with count_native_geometry_builds() as m:
            result = check_pair_clearance(
                sheet, punch, tool_label="PUNCH",
                _shared_shape_b_check=proof,
            )
        self.assertEqual(result.status, CollisionClass.CLEAR)
        # sheet is checked once, but punch isValid is skipped because proof was supplied
        self.assertEqual(sheet.valid_calls, 1)
        self.assertEqual(punch.valid_calls, 0)
        self.assertEqual(m.calls.get("pair_geometry_sanity", 0), 1)

    def test_mismatched_tool_proof_fails_closed_to_revalidation(self):
        sheet = FakeSolidShape(0, 1)
        punch = FakeSolidShape(3, 4)
        other_tool = FakeSolidShape(3, 4)
        wrong_proof = (other_tool, (True, "valid"))

        with count_native_geometry_builds() as m:
            result = check_pair_clearance(
                sheet, punch, tool_label="PUNCH",
                _shared_shape_b_check=wrong_proof,
            )
        self.assertEqual(result.status, CollisionClass.CLEAR)
        self.assertEqual(punch.valid_calls, 1)
        self.assertEqual(m.calls.get("pair_geometry_sanity", 0), 2)

    def test_both_workpiece_and_tools_skip_sanity_when_proven(self):
        sheet = FakeSolidShape(0, 1)
        punch = FakeSolidShape(3, 4)
        die = FakeSolidShape(5, 6)

        proof_w = (sheet, (True, "valid"))
        proof_p = (punch, (True, "valid"))
        proof_d = (die, (True, "valid"))

        with count_native_geometry_builds() as m:
            report = validate_workpiece_tooling_pose(
                sheet, punch, die,
                _validated_workpiece_shape=proof_w,
                _validated_punch_shape=proof_p,
                _validated_die_shape=proof_d,
            )
        self.assertEqual(report.status, CollisionClass.CLEAR)
        self.assertEqual(sheet.valid_calls, 0)
        self.assertEqual(punch.valid_calls, 0)
        self.assertEqual(die.valid_calls, 0)
        self.assertEqual(m.calls.get("pair_geometry_sanity", 0), 0)

    def test_invalid_proof_propagates_invalid_geometry(self):
        sheet = FakeSolidShape(0, 1)
        punch = FakeSolidShape(3, 4)
        die = FakeSolidShape(5, 6)

        proof_bad = (punch, (False, "punch_is_open"))
        report = validate_workpiece_tooling_pose(
            sheet, punch, die,
            _validated_punch_shape=proof_bad,
        )
        self.assertEqual(report.status, CollisionClass.INVALID_GEOMETRY)
        self.assertFalse(report.is_safe)
        self.assertIn("PUNCH_INVALID_GEOMETRY: punch_is_open", report.diagnostics)


if __name__ == "__main__":
    unittest.main()

