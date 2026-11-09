"""TechDraw numbering contracts: no FreeCAD runtime or shape mocks required."""
from types import SimpleNamespace
import unittest

from smbs_core.kinematics2d.bend_drawing_plan import (
    BendDrawingPlanError,
    create_bend_drawing_plan,
    is_bend_drawing_plan_current,
)


def make_unfold(*, sketch_order=(1, 2, 0), angles=(90.0, -90.0, 45.0)):
    segments = (
        ((0.0, 0.0), (120.0, 0.0)),
        ((0.0, 20.0), (120.0, 20.0)),
        ((0.0, 40.0), (120.0, 40.0)),
    )
    bends = tuple(SimpleNamespace(
        bend_id=f"B{i+1:02d}",
        start_xyz=(*line[0], 0.0),
        end_xyz=(*line[1], 0.0),
        source_angle_deg=-angles[i],
        fold_angle_deg=angles[i],
        radius_mm=1.0,
        length_mm=120.0,
        bend_allowance_mm=3.2,
        thickness_mm=1.5,
        k_factor_ansi=0.31,
    ) for i, line in enumerate(segments))
    return SimpleNamespace(
        source_object="Bend002",
        source_face="Face5",
        unfold_object="Body_Unfold",
        bends=bends,
        k_factor_standard="ansi",
        thickness_mm=1.5,
        sketch_bend_lines_xy=tuple(segments[i] for i in sketch_order),
    )


class TechDrawSequenceNumberingTests(unittest.TestCase):
    def make_plan(self, unfold=None, sequence=("B03", "B01", "B02")):
        return create_bend_drawing_plan(
            unfold or make_unfold(), sequence, source_document="Z.FCStd",
            accepted_status="STRICT_VALIDATED", verified=True,
        )

    def test_labels_are_real_planner_order_not_source_order(self):
        plan = self.make_plan()
        self.assertEqual([b.bend_id for b in plan.operations],
                         ["B03", "B01", "B02"])
        self.assertEqual([b.operation_number for b in plan.operations],
                         [1, 2, 3])
        # Sketch has a different ordering than SheetMetal bend records.
        self.assertEqual([b.sketch_index for b in plan.operations], [1, 2, 0])
        self.assertEqual([b.signed_angle_deg for b in plan.operations],
                         [45.0, 90.0, -90.0])

    def test_stale_after_sequence_change(self):
        part = make_unfold()
        plan = self.make_plan(part)
        self.assertTrue(is_bend_drawing_plan_current(
            plan, part, ("B03", "B01", "B02"),
            source_document="Z.FCStd", accepted_status="STRICT_VALIDATED"))
        self.assertFalse(is_bend_drawing_plan_current(
            plan, part, ("B02", "B01", "B03"),
            source_document="Z.FCStd", accepted_status="STRICT_VALIDATED"))

    def test_stale_after_geometry_change(self):
        plan = self.make_plan()
        modified = make_unfold(angles=(90.0, -89.0, 45.0))
        self.assertFalse(is_bend_drawing_plan_current(
            plan, modified, ("B03", "B01", "B02"),
            source_document="Z.FCStd", accepted_status="STRICT_VALIDATED"))

    def test_refuse_unverified_and_incomplete(self):
        with self.assertRaisesRegex(BendDrawingPlanError, "verification"):
            create_bend_drawing_plan(make_unfold(), ("B03", "B01", "B02"),
                                     source_document="Z.FCStd",
                                     accepted_status="NO_FEASIBLE_SEQUENCE",
                                     verified=False)
        with self.assertRaisesRegex(BendDrawingPlanError, "one ordered"):
            self.make_plan(sequence=("B03", "B02"))

    def test_refuse_duplicate_and_unknown_bends(self):
        with self.assertRaises(BendDrawingPlanError):
            self.make_plan(sequence=("B01", "B01", "B03"))
        with self.assertRaises(BendDrawingPlanError):
            self.make_plan(sequence=("B01", "B02", "B09"))

    def test_refuse_ambiguous_bend_line_mapping(self):
        part = make_unfold()
        part.sketch_bend_lines_xy = (
            part.sketch_bend_lines_xy[0],
            part.sketch_bend_lines_xy[0],
            part.sketch_bend_lines_xy[2],
        )
        with self.assertRaisesRegex(BendDrawingPlanError, "mapping failed"):
            self.make_plan(part)

    def test_refuse_unjustified_sketch_offset(self):
        part = make_unfold()
        old = part.sketch_bend_lines_xy
        part.sketch_bend_lines_xy = (
            ((old[0][0][0], old[0][0][1] + 0.25),
             (old[0][1][0], old[0][1][1] + 0.25)), old[1], old[2],
        )
        with self.assertRaisesRegex(BendDrawingPlanError, "mapping failed"):
            self.make_plan(part)

    def test_serialization_is_stable(self):
        plan = self.make_plan()
        data = plan.to_dict()
        self.assertEqual(data["schema_version"], 1)
        self.assertEqual(data["operations"][0]["bend_id"], "B03")
        self.assertEqual(len(data["input_signature_sha256"]), 64)


if __name__ == "__main__":
    unittest.main()
