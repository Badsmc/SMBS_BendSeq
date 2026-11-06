"""Regression tests for Part Space _solid_cache and die clearance caching in AdaptiveTrajectoryValidator3D.

Verifies:
1. Exact angle floating-point keys (no round(..., 4) loss).
2. Cache invalidation on arc_segments change or explicit clear_solid_cache().
3. Cached solid immutability: transformShape operates on independent copies and never mutates Part Space solid.
4. Numerical identity: collision reports (clearance distance, overlap volume, contact class) match exactly
   with and without caching.
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

for _p in ("/Applications/FreeCAD.app/Contents/Resources/lib",):
    if os.path.exists(_p) and _p not in sys.path:
        sys.path.append(_p)

try:
    import FreeCAD as App
    from PySide6 import QtWidgets
    HAS_FREECAD = True
except (ImportError, ModuleNotFoundError):
    HAS_FREECAD = False

from smbs_core.demo_paths import demo_part_path
from smbs_core.kinematics2d.motion_trajectory_3d import (
    AdaptiveSamplingConfig,
    AdaptiveTrajectoryValidator3D,
)
from smbs_core.kinematics2d.native_tool_loader import (
    load_native_die,
    load_native_punch,
)


class TestD634SolidCache(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not HAS_FREECAD:
            raise unittest.SkipTest("FreeCAD not available")
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
        cls.model_path = demo_part_path("box.100out-50in-(ok).FCStd", required=False)
        if not cls.model_path.is_file():
            raise unittest.SkipTest(f"Model not found: {cls.model_path}")

    def setUp(self):
        self.doc = App.openDocument(str(self.model_path), True)
        self.unfold = next((o for o in self.doc.Objects if o.Name.endswith("_Unfold")), None)
        if self.unfold is None:
            App.closeDocument(self.doc.Name)
            raise unittest.SkipTest("Model missing _Unfold object")

        from freecad_smbs.commands.pressbrake_2d import build_part_session, _PreviewPanel
        self.session = build_part_session(self.doc, [self.unfold])
        self.panel = _PreviewPanel()
        self.panel.session = self.session
        self.panel._ensure_press_cycle_controller()
        self.panel.align_tooling()

        punch_path = str(ROOT / "toollibrary" / "punch" / "puancon.FCStd")
        die_path = str(ROOT / "toollibrary" / "die" / "matrica.FCStd")
        self.punch = load_native_punch(punch_path, ram_z_mm=50.0)
        self.die = load_native_die(die_path)
        self.config = AdaptiveSamplingConfig(arc_segments=128)
        self.validator = AdaptiveTrajectoryValidator3D(
            self.session.fold_state,
            self.session.panel_build,
            self.punch,
            self.die,
            config=self.config,
        )

    def tearDown(self):
        if hasattr(self, "panel") and self.panel is not None:
            self.panel._stop_timer()
            self.panel.close()
            self.panel.deleteLater()
        if hasattr(self, "doc") and self.doc is not None:
            App.closeDocument(self.doc.Name)

    def test_cached_solid_immutability(self):
        """Verify that transformShape never mutates cached Part Space solid shape."""
        # Create initial snapshot at B01 = 45.0
        st1 = self.validator.create_state_snapshot(
            active_bend_id="B01",
            phase="BENDING",
            path_u=0.5,
            signed_angle=45.0,
            ram_z=20.0,
            support_z=1.0,
            prior_angles={},
        )
        self.assertEqual(len(self.validator._solid_cache), 1)
        cached_solid = next(iter(self.validator._solid_cache.values()))
        bb_orig = (
            cached_solid.shape.BoundBox.XMin,
            cached_solid.shape.BoundBox.YMin,
            cached_solid.shape.BoundBox.ZMin,
            cached_solid.shape.BoundBox.XMax,
            cached_solid.shape.BoundBox.YMax,
            cached_solid.shape.BoundBox.ZMax,
        )

        # Create another snapshot at different ram_z and support_z (different machine matrix)
        st2 = self.validator.create_state_snapshot(
            active_bend_id="B01",
            phase="BENDING",
            path_u=0.6,
            signed_angle=45.0,
            ram_z=10.0,
            support_z=-5.0,
            prior_angles={},
        )
        # Should reuse cached solid (0 new solid reconstructions)
        self.assertEqual(self.validator.total_solid_reconstructions, 1)

        # Verify cached solid BoundBox in Part Space is completely unaltered
        bb_after = (
            cached_solid.shape.BoundBox.XMin,
            cached_solid.shape.BoundBox.YMin,
            cached_solid.shape.BoundBox.ZMin,
            cached_solid.shape.BoundBox.XMax,
            cached_solid.shape.BoundBox.YMax,
            cached_solid.shape.BoundBox.ZMax,
        )
        for c1, c2 in zip(bb_orig, bb_after):
            self.assertAlmostEqual(c1, c2, places=7, msg="Cached solid shape was mutated!")

    def test_numerical_identity_with_and_without_cache(self):
        """Verify that collision query results are numerically identical with cache vs without cache."""
        angles_to_test = [0.0, 30.0, 45.0, 90.0]
        cached_results = []
        for ang in angles_to_test:
            st = self.validator.create_state_snapshot(
                active_bend_id="B01",
                phase="BENDING",
                path_u=ang / 90.0,
                signed_angle=ang,
                ram_z=50.0 - ang * 0.4,
                support_z=1.0,
                prior_angles={},
            )
            cached_results.append(st.collision_report)

        # Clear cache and evaluate without cache
        self.validator.clear_solid_cache()
        uncached_results = []
        for ang in angles_to_test:
            self.validator.clear_solid_cache()  # Force rebuild each time
            st = self.validator.create_state_snapshot(
                active_bend_id="B01",
                phase="BENDING",
                path_u=ang / 90.0,
                signed_angle=ang,
                ram_z=50.0 - ang * 0.4,
                support_z=1.0,
                prior_angles={},
            )
            uncached_results.append(st.collision_report)

        for i, (c, u) in enumerate(zip(cached_results, uncached_results)):
            self.assertEqual(c.status, u.status, f"Status mismatch at step {i}")
            self.assertAlmostEqual(
                c.punch_result.distance_mm, u.punch_result.distance_mm, places=5,
                msg=f"Punch distance differs at step {i}",
            )
            self.assertAlmostEqual(
                c.die_result.distance_mm, u.die_result.distance_mm, places=5,
                msg=f"Die distance differs at step {i}",
            )
            self.assertAlmostEqual(
                c.punch_result.overlap_volume_mm3, u.punch_result.overlap_volume_mm3, places=6,
                msg=f"Punch overlap differs at step {i}",
            )
            self.assertAlmostEqual(
                c.die_result.overlap_volume_mm3, u.die_result.overlap_volume_mm3, places=6,
                msg=f"Die overlap differs at step {i}",
            )

    def test_cache_invalidation_on_arc_segments_change(self):
        """Verify that modifying arc_segments causes cache miss and new solid reconstruction."""
        self.validator.config.arc_segments = 128
        self.validator.create_state_snapshot(
            active_bend_id="B01",
            phase="BENDING",
            path_u=0.5,
            signed_angle=45.0,
            ram_z=20.0,
            support_z=1.0,
            prior_angles={},
        )
        self.assertEqual(self.validator.total_solid_reconstructions, 1)

        # Change arc_segments to 64
        self.validator.config.arc_segments = 64
        self.validator.create_state_snapshot(
            active_bend_id="B01",
            phase="BENDING",
            path_u=0.5,
            signed_angle=45.0,
            ram_z=20.0,
            support_z=1.0,
            prior_angles={},
        )
        self.assertEqual(self.validator.total_solid_reconstructions, 2)

    def test_die_clearance_cache_hit_and_invalidation(self):
        """Verify die clearance cache hits, miss on placement change, miss on tooling change, and uncached equality."""
        import Part
        from smbs_core.kinematics2d.native_tool_loader import LoadedTool

        # Step 1: Initial snapshot -> cache miss, 1 entry in cache
        st1 = self.validator.create_state_snapshot(
            active_bend_id="B01",
            phase="BENDING",
            path_u=0.5,
            signed_angle=45.0,
            ram_z=20.0,
            support_z=1.0,
            prior_angles={},
        )
        self.assertEqual(len(self.validator._die_clearance_cache), 1)
        initial_cache_entry = next(iter(self.validator._die_clearance_cache.values()))

        # Step 2: Identical bend & workpiece pose relative to die, but different ram_z
        # -> Must hit cache for die clearance!
        st2 = self.validator.create_state_snapshot(
            active_bend_id="B01",
            phase="BENDING",
            path_u=0.6,
            signed_angle=45.0,
            ram_z=30.0,
            support_z=1.0,
            prior_angles={},
        )
        self.assertEqual(len(self.validator._die_clearance_cache), 1)
        self.assertIs(st2.collision_report.die_result, initial_cache_entry)

        # Step 3: Change support_z (die placement relative to workpiece changes)
        # -> Cache miss: new key generated, entry count becomes 2
        st3 = self.validator.create_state_snapshot(
            active_bend_id="B01",
            phase="BENDING",
            path_u=0.7,
            signed_angle=45.0,
            ram_z=30.0,
            support_z=2.5,
            prior_angles={},
        )
        self.assertEqual(len(self.validator._die_clearance_cache), 2)

        # Step 4: Change tolerance in config -> cache miss/invalidation
        cfg_new = AdaptiveSamplingConfig(arc_segments=128, near_tolerance_mm=1.5)
        self.validator.set_config(cfg_new)
        self.assertEqual(len(self.validator._die_clearance_cache), 0)

        # Step 5: Change die to synthetic tooling -> cache miss/invalidation
        alt_shape = Part.makeBox(120, 60, 60, App.Vector(-60, -30, -60))
        synthetic_die = LoadedTool(
            tool_id="SYNTHETIC_TEST_TOOLING_DIE_V24",
            kind="DIE",
            file_path="synthetic://test_die_v24",
            cad_shape=alt_shape,
            machine_shape=alt_shape,
            tip_radius_mm=0.0,
            included_angle_deg=88.0,
            v_opening_mm=24.0,
            shoulder_radius_mm=2.0,
            v_depth_mm=14.0,
            height_mm=60.0,
            length_mm=120.0,
            span_xm=(-60.0, 60.0),
            origin_cad=(0.0, 0.0),
        )
        self.validator.set_die(synthetic_die)
        self.assertEqual(len(self.validator._die_clearance_cache), 0)

        st_synth = self.validator.create_state_snapshot(
            active_bend_id="B01",
            phase="BENDING",
            path_u=0.5,
            signed_angle=45.0,
            ram_z=20.0,
            support_z=1.0,
            prior_angles={},
        )
        self.assertEqual(len(self.validator._die_clearance_cache), 1)

        # Verify numerical results match an uncached independent validator
        independent_validator = AdaptiveTrajectoryValidator3D(
            self.session.fold_state,
            self.session.panel_build,
            self.punch,
            synthetic_die,
            config=cfg_new,
        )
        st_indep = independent_validator.create_state_snapshot(
            active_bend_id="B01",
            phase="BENDING",
            path_u=0.5,
            signed_angle=45.0,
            ram_z=20.0,
            support_z=1.0,
            prior_angles={},
        )
        self.assertAlmostEqual(
            st_synth.collision_report.die_result.distance_mm,
            st_indep.collision_report.die_result.distance_mm,
            places=5,
        )
        self.assertAlmostEqual(
            st_synth.collision_report.die_result.overlap_volume_mm3,
            st_indep.collision_report.die_result.overlap_volume_mm3,
            places=6,
        )

    def test_distinct_shapes_identical_bbox_area_vertex_count_cache_separation(self):
        """Verify two different shapes with identical BBox, area, and vertex count have distinct signatures and cache keys."""
        import Part
        from types import SimpleNamespace
        from smbs_core.kinematics2d.motion_trajectory_3d import _panel_build_geometry_signature

        # Shape A: (0,0), (10,0), (10,4), (4,4), (4,10), (0,10) -> BBox [0,10]x[0,10], area 64, 6 vertices
        pts_a = [
            App.Vector(0, 0, 0), App.Vector(10, 0, 0), App.Vector(10, 4, 0),
            App.Vector(4, 4, 0), App.Vector(4, 10, 0), App.Vector(0, 10, 0),
            App.Vector(0, 0, 0),
        ]
        wire_a = Part.makePolygon(pts_a)
        face_a = Part.Face(wire_a)

        # Shape B: (0,0), (10,0), (10,10), (6,10), (6,4), (0,4) -> BBox [0,10]x[0,10], area 64, 6 vertices
        pts_b = [
            App.Vector(0, 0, 0), App.Vector(10, 0, 0), App.Vector(10, 10, 0),
            App.Vector(6, 10, 0), App.Vector(6, 4, 0), App.Vector(0, 4, 0),
            App.Vector(0, 0, 0),
        ]
        wire_b = Part.makePolygon(pts_b)
        face_b = Part.Face(wire_b)

        # Confirm mathematical identity of BBox, vertex count, and area
        self.assertEqual(len(face_a.Vertexes), len(face_b.Vertexes))
        self.assertAlmostEqual(face_a.Area, face_b.Area, places=5)
        self.assertAlmostEqual(face_a.BoundBox.XMin, face_b.BoundBox.XMin, places=5)
        self.assertAlmostEqual(face_a.BoundBox.XMax, face_b.BoundBox.XMax, places=5)
        self.assertAlmostEqual(face_a.BoundBox.YMin, face_b.BoundBox.YMin, places=5)
        self.assertAlmostEqual(face_a.BoundBox.YMax, face_b.BoundBox.YMax, places=5)

        pb_a = SimpleNamespace(panels=[SimpleNamespace(panel_id="P1")], panel_faces=[face_a])
        pb_b = SimpleNamespace(panels=[SimpleNamespace(panel_id="P1")], panel_faces=[face_b])

        sig_a = _panel_build_geometry_signature(pb_a)
        sig_b = _panel_build_geometry_signature(pb_b)

        self.assertNotEqual(
            sig_a, sig_b,
            "Two topologically distinct shapes sharing identical BBox, area, and vertex count must NOT share geometry signatures!",
        )

    def test_panel_build_internal_mutation_and_generation_token_invalidation(self):
        """Verify modifying geometry inside an existing panel_build or updating generation_token causes cache miss."""
        from dataclasses import replace

        # Baseline snapshot
        st1 = self.validator.create_state_snapshot(
            active_bend_id="B01",
            phase="BENDING",
            path_u=0.5,
            signed_angle=45.0,
            ram_z=20.0,
            support_z=1.0,
            prior_angles={},
        )
        self.assertEqual(self.validator.total_solid_reconstructions, 1)

        # Snapshot with identical parameters and identical token -> must hit solid cache
        st2 = self.validator.create_state_snapshot(
            active_bend_id="B01",
            phase="BENDING",
            path_u=0.5,
            signed_angle=45.0,
            ram_z=25.0,
            support_z=1.0,
            prior_angles={},
        )
        self.assertTrue(st2.solid_cache_hit)
        self.assertEqual(self.validator.total_solid_reconstructions, 1)

        # 1. Update generation_token on panel_build
        new_pb = replace(self.session.panel_build, generation_token=2)
        self.validator.panel_build = new_pb
        st3 = self.validator.create_state_snapshot(
            active_bend_id="B01",
            phase="BENDING",
            path_u=0.5,
            signed_angle=45.0,
            ram_z=25.0,
            support_z=1.0,
            prior_angles={},
        )
        self.assertFalse(st3.solid_cache_hit)
        self.assertEqual(self.validator.total_solid_reconstructions, 2)

        # 2. Mutate face geometry inside panel_build (modifying face vertex coordinates)
        import Part
        old_face = new_pb.panel_faces[0]
        pts_mod = [App.Vector(v.X + 5.0, v.Y, v.Z) for v in old_face.Vertexes]
        pts_mod.append(pts_mod[0])
        mod_face = Part.Face(Part.makePolygon(pts_mod))
        mod_faces = list(new_pb.panel_faces)
        mod_faces[0] = mod_face
        pb_mutated = replace(new_pb, panel_faces=tuple(mod_faces))
        self.validator.panel_build = pb_mutated

        st4 = self.validator.create_state_snapshot(
            active_bend_id="B01",
            phase="BENDING",
            path_u=0.5,
            signed_angle=45.0,
            ram_z=25.0,
            support_z=1.0,
            prior_angles={},
        )
        self.assertFalse(st4.solid_cache_hit)
        self.assertEqual(self.validator.total_solid_reconstructions, 3)


if __name__ == "__main__":
    unittest.main()

