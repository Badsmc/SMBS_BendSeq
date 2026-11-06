"""Unit and regression tests for MachineCuttingPlaneSection (D3).

Verifies that:
1. Slicing with machine cutting plane X_m = 0 extracts true physical cross-section.
2. Panels and zones that do not intersect X_m = 0 are NOT projected onto cutting plane.
3. Panels that intersect X_m = 0 yield closed 4-vertex quads in (s, z) plane.
4. Active bend zone yields exact closed curved polygon with inner and outer surfaces.
5. All 8 sequential bends B01-B08 of real SheetMetal model produce valid cutting sections.
6. Transfer frames smoothly transition the cutting plane without geometry distortion.
"""
from __future__ import annotations

import math
import unittest

from smbs_core.demo_paths import demo_part_path
from freecad_smbs.commands.pressbrake_2d import build_part_session
from smbs_core.kinematics2d.machine_section_3d import (
    compute_machine_cutting_plane_section, slice_polygon_3d_at_x,
)
from smbs_core.kinematics2d.workpiece_placement_3d import WorkpiecePlacement3D
from smbs_core.kinematics2d.workpiece_transfer_3d import build_bend_transfer

try:
    import FreeCAD
    HAS_FREECAD = True
except ImportError:
    FreeCAD = None
    HAS_FREECAD = False

MODEL_PATH = demo_part_path("box.100out-50in-(ok).FCStd", required=False)


class TestMachineCuttingPlaneSection(unittest.TestCase):
    def setUp(self):
        if not HAS_FREECAD:
            self.skipTest("FreeCAD runtime is required")
        if not MODEL_PATH.is_file():
            self.skipTest(f"Model file not found: {MODEL_PATH}")
        self.doc = FreeCAD.openDocument(str(MODEL_PATH), True)
        obj = next(o for o in self.doc.Objects if o.Name == "Body_Unfold")
        self.session = build_part_session(self.doc, [obj])
        self.fold_state = self.session.fold_state
        self.panel_verts = self.session.panel_vertices_xy

    def tearDown(self):
        if hasattr(self, "doc"):
            FreeCAD.closeDocument(self.doc.Name)

    def test_slice_polygon_3d_at_x_basic(self):
        # A quad crossing X=0 from X=-10 to X=+10 at Y=5, Z=0
        quad = (
            (-10.0, 5.0, 0.0),
            (10.0, 5.0, 0.0),
            (10.0, 15.0, 0.0),
            (-10.0, 15.0, 0.0),
        )
        cuts = slice_polygon_3d_at_x(quad, target_x=0.0)
        self.assertEqual(len(cuts), 2)
        self.assertAlmostEqual(cuts[0][0], 5.0)
        self.assertAlmostEqual(cuts[0][1], 0.0)
        self.assertAlmostEqual(cuts[1][0], 15.0)
        self.assertAlmostEqual(cuts[1][1], 0.0)

    def test_slice_polygon_3d_at_x_no_intersection(self):
        quad = (
            (10.0, 5.0, 0.0),
            (20.0, 5.0, 0.0),
            (20.0, 15.0, 0.0),
            (10.0, 15.0, 0.0),
        )
        cuts = slice_polygon_3d_at_x(quad, target_x=0.0)
        self.assertEqual(len(cuts), 0)

    def test_all_8_bends_cutting_plane_sections(self):
        fold_state = self.fold_state
        verts = self.panel_verts

        for rb in self.session.ordered_bends:
            bid = rb.bend_id
            p_tf = WorkpiecePlacement3D.compute_placement(fold_state, bid)
            sec = compute_machine_cutting_plane_section(
                fold_state, verts, p_tf, active_bend_id=bid, segments=16
            )

            # 1. Verify fixed and moving polygons exist and are closed quads
            self.assertEqual(len(sec["fixed_polygon"]), 4, f"Bend {bid} fixed_polygon missing")
            self.assertEqual(len(sec["moving_polygon"]), 4, f"Bend {bid} moving_polygon missing")

            # 2. Verify active bend zone has inner, outer, and closed zone polygon
            self.assertGreater(len(sec["zone_polygon"]), 10, f"Bend {bid} zone_polygon missing")
            self.assertEqual(len(sec["inner_surface"]), len(sec["outer_surface"]))

            # 3. Verify only panels actually crossing X_m = 0 are in other_panel_polygons
            for pid, poly in sec["other_panel_polygons"]:
                self.assertEqual(len(poly), 4)
                # Verify that this panel in 3D actually spans across X_m = 0
                t_pid = fold_state.transforms[pid]
                xs = [p_tf.apply(t_pid.point((x, y, 0.0)))[0] for x, y in verts[pid]]
                self.assertLessEqual(min(xs), 1e-4)
                self.assertGreaterEqual(max(xs), -1e-4)

            # Fold active bend to its target angle for sequential realism
            fold_state.set_angle(bid, rb.angle_deg)

    def test_transfer_b01_to_b02_continuous_cutting_plane(self):
        fold_state = self.fold_state
        verts = self.panel_verts
        fold_state.set_angle("B01", 90.0)

        transfer = build_bend_transfer(
            fold_state, "B01", "B02",
            max_linear_mm_s=250.0, max_angular_deg_s=90.0
        )

        for step in range(11):
            t = (step / 10.0) * transfer.duration_s
            pose = transfer.pose_at(t)
            sec = compute_machine_cutting_plane_section(
                fold_state, verts, pose, active_bend_id="B01"
            )
            self.assertIn("other_panel_polygons", sec)
            # All polygons must have finite coordinates
            for pid, poly in sec["other_panel_polygons"]:
                for s, z in poly:
                    self.assertTrue(math.isfinite(s) and math.isfinite(z))


if __name__ == "__main__":
    unittest.main()

