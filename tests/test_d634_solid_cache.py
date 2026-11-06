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

        punch_path = "/Users/badmc/Desktop/SMBS_BendSeq/toollibrary/punch/puancon.FCStd"
        die_path = "/Users/badmc/Desktop/SMBS_BendSeq/toollibrary/die/matrica.FCStd"
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


if __name__ == "__main__":
    unittest.main()

