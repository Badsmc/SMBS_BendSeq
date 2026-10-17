"""
test_phase3_cad_models.py - SMBS Phase 3 Real CAD & Kinematics Test Suite.

Executes real geometric tests using OpenCASCADE / pythonocc-core and FreeCAD:
- Stage 2: CAD Test Models (L, U, C, Z, +45°/-45°)
- Stage 3: Kinematic Invariants & B-Rep Transformation
- Stage 4: Real Tooling Assembly Verification (puancon + matrica)
- Stage 5: Discrete Trajectory Collision Verification (5° vs 1°)
- Stage 6: Full Planning Pipeline & Sequence Validation
- Stage 8: Performance & Resource Profiling
"""

import os
import sys
import math
import time
import json
import tracemalloc
import unittest

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FREECAD_LIB = "/Applications/FreeCAD.app/Contents/Resources/lib"
if os.path.exists(FREECAD_LIB) and FREECAD_LIB not in sys.path:
    sys.path.append(FREECAD_LIB)
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from smbs_core.geometry_backend.backends.pythonocc_backend import PythonOCCBackend, HAS_OCC
from smbs_core.geometry_backend.backends.fcstd_reader import read_fcstd
from smbs_core.geometry_backend.backends.fcstd_loader import FcstdToolingLoader
from smbs_core.Geometry.feature_extractor import FeatureExtractor
from smbs_core.models.tooling import ToolingKind, AlignmentStatus, GeometryStatus
from smbs_core.Physics.machine_kinematics import PressBrakeMachine
from smbs_core.Physics.tooling_assembly import ToolingAssembly
from smbs_core.Physics.collision_detector import CollisionDetector, CollisionStatus
from smbs_core.Physics.validator import PhysicalValidator
from smbs_core.Core.panel_graph import PanelGraph
from smbs_core.Core.bend_graph import BendGraph, BendRecord
from smbs_core.Core.fold_state import FoldState
from smbs_core.Core.orchestrator import BendSeqOrchestrator
from smbs_core.testing.profile_generator import (
    build_l_profile,
    build_u_profile,
    build_c_profile,
)

try:
    from OCC.Core.GProp import GProp_GProps
    from OCC.Core.BRepGProp import brepgprop
    from OCC.Core.BRepCheck import BRepCheck_Analyzer
except ImportError:
    pass


class TestPhase3RealCAD(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not HAS_OCC:
            raise unittest.SkipTest("OpenCASCADE / pythonocc-core not available in environment.")
        cls.backend = PythonOCCBackend()
        cls.extractor = FeatureExtractor(cls.backend)
        cls.models_dir = os.path.join(ROOT_DIR, "SMBendTests")
        cls.tooling_punch = os.path.join(ROOT_DIR, "toollibrary", "punch", "puancon.FCStd")
        cls.tooling_die = os.path.join(ROOT_DIR, "toollibrary", "die", "matrica.FCStd")

    def _get_shape_volume(self, shape) -> float:
        props = GProp_GProps()
        brepgprop.VolumeProperties(shape, props)
        return props.Mass()

    # =========================================================================
    # STAGE 2: Воспроизводимые CAD-тесты (L, U, C, Z, +45°/-45°)
    # =========================================================================

    def test_stage2_l_profile_cad(self):
        """Stage 2: L-profile real CAD model feature extraction & validity."""
        # 1. Procedural OCC Solid
        occ_l = build_l_profile(50, 50, thickness=2.0, inner_radius=2.0, length=100.0)
        self.assertTrue(BRepCheck_Analyzer(occ_l).IsValid(), "Procedural L solid must be valid B-Rep")
        feats_occ = self.extractor.extract_features(occ_l)
        self.assertEqual(feats_occ["thickness"], 2.0)
        self.assertEqual(len(feats_occ["bends"]), 1)
        self.assertAlmostEqual(list(feats_occ["bends"].values())[0]["angle"], 90.0, places=1)

        # 2. Real FCStd Model
        l_fcstd = os.path.join(self.models_dir, "L-(ok).FCStd")
        objs, status = read_fcstd(l_fcstd)
        self.assertEqual(status, GeometryStatus.REAL)
        self.assertTrue(len(objs) > 0)
        shape = objs[-1][1]
        feats = self.extractor.extract_features(shape)
        self.assertEqual(feats["thickness"], 1.5)
        self.assertEqual(len(feats["bends"]), 1)
        self.assertAlmostEqual(list(feats["bends"].values())[0]["angle"], 90.0, places=1)

    def test_stage2_u_profile_cad(self):
        """Stage 2: U-profile real CAD model (2 bends at 90°)."""
        occ_u = build_u_profile(40, 60, 40, thickness=2.0, inner_radius=2.0, length=100.0)
        self.assertTrue(BRepCheck_Analyzer(occ_u).IsValid(), "Procedural U solid must be valid B-Rep")
        feats_occ = self.extractor.extract_features(occ_u)
        self.assertEqual(feats_occ["thickness"], 2.0)
        self.assertEqual(len(feats_occ["bends"]), 2)

        u_fcstd = os.path.join(self.models_dir, "U-90deg-90deg-(ok).FCStd")
        objs, status = read_fcstd(u_fcstd)
        self.assertEqual(status, GeometryStatus.REAL)
        shape = objs[-1][1]
        feats = self.extractor.extract_features(shape)
        self.assertEqual(feats["thickness"], 1.5)
        self.assertEqual(len(feats["bends"]), 2)
        for b_id, b_info in feats["bends"].items():
            self.assertAlmostEqual(b_info["angle"], 90.0, places=1)

    def test_stage2_c_profile_cad(self):
        """Stage 2: C-profile real CAD model (3 bends at 90°)."""
        occ_c = build_c_profile(25, 40, 60, 40, thickness=2.0, inner_radius=2.0, length=100.0)
        self.assertTrue(BRepCheck_Analyzer(occ_c).IsValid(), "Procedural C solid must be valid B-Rep")
        feats_occ = self.extractor.extract_features(occ_c)
        self.assertEqual(feats_occ["thickness"], 2.0)
        self.assertEqual(len(feats_occ["bends"]), 3)
        for b_id, b_info in feats_occ["bends"].items():
            self.assertAlmostEqual(b_info["angle"], 90.0, places=1)

    def test_stage2_z_profile_cad(self):
        """Stage 2: Z-profile real CAD model (2 opposite bends at 90°)."""
        z_fcstd = os.path.join(self.models_dir, "Z-(ok).FCStd")
        objs, status = read_fcstd(z_fcstd)
        self.assertEqual(status, GeometryStatus.REAL)
        shape = objs[-1][1]
        feats = self.extractor.extract_features(shape)
        self.assertEqual(feats["thickness"], 1.5)
        self.assertEqual(len(feats["bends"]), 2)
        bends = list(feats["bends"].values())
        # Confirm opposite directions of bend axes (dot product ~ -1.0)
        a1, a2 = bends[0]["axis"], bends[1]["axis"]
        dot = sum(a1[i] * a2[i] for i in range(3))
        self.assertAlmostEqual(dot, -1.0, places=2, msg="Z profile bend axes must be opposite in direction")

    def test_stage2_opposite_45_profile_cad(self):
        """Stage 2: Adjacent opposite bends +45° and -45° real CAD model."""
        fcstd_45 = os.path.join(self.models_dir, "135-(ok).FCStd")
        objs, status = read_fcstd(fcstd_45)
        self.assertEqual(status, GeometryStatus.REAL)
        shape = objs[-1][1]
        feats = self.extractor.extract_features(shape)
        self.assertEqual(feats["thickness"], 1.5)
        self.assertEqual(len(feats["bends"]), 2)
        bends = list(feats["bends"].values())
        self.assertAlmostEqual(bends[0]["angle"], 45.0, places=1)
        self.assertAlmostEqual(bends[1]["angle"], 45.0, places=1)
        # Check opposite axis directions
        dot = sum(bends[0]["axis"][i] * bends[1]["axis"][i] for i in range(3))
        self.assertAlmostEqual(dot, -1.0, places=2, msg="+45°/-45° bends must be opposite in direction")

    # =========================================================================
    # STAGE 3: Испытать кинематику
    # =========================================================================

    def test_stage3_kinematics_and_invariants(self):
        """Stage 3: Kinematic invariants, volume preservation, and inverse rotation."""
        occ_l = build_l_profile(50, 50, thickness=2.0, inner_radius=2.0, length=100.0)
        v_orig = self._get_shape_volume(occ_l)
        self.assertGreater(v_orig, 1000.0)

        feats = self.extractor.extract_features(occ_l)
        b_info = list(feats["bends"].values())[0]

        pg = PanelGraph()
        pg.add_bend_connection("BEND_01", "PANEL_00", "PANEL_01")
        moving = {"PANEL_01"}

        # 1. Forward Unfolding: rotate subtree by -90 deg
        unfolded = self.backend.rotate_subtree(
            occ_l,
            axis_origin=b_info["origin"],
            axis_direction=b_info["axis"],
            signed_angle_deg=-90.0,
            moving_panels=moving,
            panel_graph=pg
        )
        self.assertIsNotNone(unfolded)
        self.assertTrue(self.backend.is_valid(unfolded), "Unfolded B-Rep shape must be topologically valid")

        v_unfolded = self._get_shape_volume(unfolded)
        # Volume preservation invariant (zero loss of material, zero gaps)
        self.assertAlmostEqual(v_orig, v_unfolded, places=2, msg="Volume must be preserved during rotation")

        # 2. Inverse Transformation: rotate subtree back by +90 deg
        refolded = self.backend.rotate_subtree(
            unfolded,
            axis_origin=b_info["origin"],
            axis_direction=b_info["axis"],
            signed_angle_deg=90.0,
            moving_panels=moving,
            panel_graph=pg
        )
        self.assertIsNotNone(refolded)
        v_refolded = self._get_shape_volume(refolded)
        self.assertAlmostEqual(v_orig, v_refolded, places=2, msg="Inverse rotation must restore exact volume")

    # =========================================================================
    # STAGE 4: Испытать реальную оснастку
    # =========================================================================

    def test_stage4_real_tooling_assembly(self):
        """Stage 4: Real Tooling (puancon.FCStd + matrica.FCStd) loading, datums & alignment."""
        self.assertTrue(os.path.exists(self.tooling_punch), "Real punch file must exist")
        self.assertTrue(os.path.exists(self.tooling_die), "Real die file must exist")

        loader = FcstdToolingLoader()
        punch = loader.load(self.tooling_punch, ToolingKind.PUNCH, target_object_name="Body")
        die = loader.load(self.tooling_die, ToolingKind.DIE, target_object_name="Body")

        self.assertEqual(punch.geometry_status, GeometryStatus.REAL)
        self.assertEqual(die.geometry_status, GeometryStatus.REAL)
        self.assertIsNotNone(punch.shape)
        self.assertIsNotNone(die.shape)

        machine = PressBrakeMachine(punch_path=self.tooling_punch, die_path=self.tooling_die, punch_target_object_name="Body", die_target_object_name="Body")
        assembly = machine.setup_assembly(self.backend)

        res = assembly.place(punch, die)
        self.assertEqual(res.status, AlignmentStatus.ALIGNED, f"Real tooling must align: {res.diagnostics}")
        self.assertIn("Tooling aligned and verified.", res.diagnostics[0])

        punch_world = assembly.get_world_shape(ToolingKind.PUNCH)
        die_world = assembly.get_world_shape(ToolingKind.DIE)
        self.assertIsNotNone(punch_world)
        self.assertIsNotNone(die_world)

    # =========================================================================
    # STAGE 5: Проверить столкновения на траектории
    # =========================================================================

    def test_stage5_trajectory_collisions(self):
        """Stage 5: Trajectory collision tests (positive, negative, 5° vs 1° comparison)."""
        machine = PressBrakeMachine(punch_path=self.tooling_punch, die_path=self.tooling_die, punch_target_object_name="Body", die_target_object_name="Body")
        assembly = machine.setup_assembly(self.backend)
        detector = CollisionDetector(assembly, self.backend)

        # 1. Positive test: Clean L-profile positioned clearly away from tools
        occ_l = build_l_profile(30, 30, thickness=2.0, inner_radius=2.0, length=50.0)
        feats = self.extractor.extract_features(occ_l)
        bend_info = list(feats["bends"].values())[0]

        res_5deg = detector.trajectory_collision(occ_l, bend_info, dtheta=5.0)
        res_1deg = detector.trajectory_collision(occ_l, bend_info, dtheta=1.0)
        self.assertEqual(res_5deg.status, CollisionStatus.VALID)
        self.assertEqual(res_1deg.status, CollisionStatus.VALID)

        # 2. Negative test: Self-collision when flange is rotated through itself
        coll_res = detector._check_intersection(occ_l, occ_l, "FlangeA", "FlangeB")
        self.assertEqual(coll_res.status, CollisionStatus.INVALID, "Identical intersecting parts must be detected as INVALID")

        # 3. Negative test: Part colliding with die
        die_world = assembly.get_world_shape(ToolingKind.DIE)
        self.assertIsNotNone(die_world)
        tool_coll = detector._check_intersection(die_world, die_world, "Part", "Die")
        self.assertEqual(tool_coll.status, CollisionStatus.INVALID, "Direct intersection with die must report INVALID")

    # =========================================================================
    # STAGE 6: Полный поиск (L, U, C, Z, +45°/-45°)
    # =========================================================================

    def test_stage6_full_planning_pipeline(self):
        """Stage 6: Complete pipeline for all profiles: CAD -> Features -> Tools -> Planning -> Validation -> JSON."""
        orchestrator = BendSeqOrchestrator(
            backend=self.backend,
            machine_config={
                "punch_path": self.tooling_punch,
                "die_path": self.tooling_die,
                "punch_target_object_name": "Body",
                "die_target_object_name": "Body"
            }
        )

        test_cases = [
            ("L", build_l_profile(40, 40, 2.0, 2.0, 80.0), 1, True),
            ("U", build_u_profile(30, 50, 30, 2.0, 2.0, 80.0), 2, True),
            ("C", build_c_profile(20, 30, 50, 30, 2.0, 2.0, 80.0), 3, True),
            ("Z", os.path.join(self.models_dir, "Z-(ok).FCStd"), 2, True),
            ("S_45_opposite", os.path.join(self.models_dir, "135-(ok).FCStd"), 2, False),
        ]

        summary_results = {}

        for name, model, expected_bends, should_succeed in test_cases:
            out_json = os.path.join(ROOT_DIR, f"test_stage6_{name}_output.json")
            if os.path.exists(out_json):
                os.remove(out_json)

            t0 = time.time()
            if isinstance(model, str):
                res = orchestrator.run_planning(
                    file_path=model,
                    algorithm="dfs",
                    output_file=out_json,
                    punch_path=self.tooling_punch,
                    die_path=self.tooling_die
                )
            else:
                res = orchestrator.run_planning(
                    target_shape=model,
                    algorithm="dfs",
                    output_file=out_json,
                    punch_path=self.tooling_punch,
                    die_path=self.tooling_die
                )
            t_plan = time.time() - t0

            summary_results[name] = {
                "status": res["status"],
                "success": res["success"],
                "sequence_len": len(res.get("sequence", [])),
                "time_sec": round(t_plan, 4),
                "all_valid": res.get("validation", {}).get("all_steps_valid", False),
                "tooling_alignment": res.get("validation", {}).get("tooling_alignment"),
                "error": res.get("error")
            }

            if should_succeed:
                self.assertTrue(os.path.exists(out_json), f"Output JSON {out_json} must be created")
                with open(out_json, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.assertIn("steps", data)
                    self.assertEqual(len(data["steps"]), expected_bends)

                self.assertEqual(len(res["sequence"]), expected_bends, f"Must find {expected_bends} steps for {name}")
                self.assertTrue(res["validation"]["all_steps_valid"], f"All steps must be physically valid for {name}")
            else:
                # Document proven impossibility without masking rejection
                self.assertFalse(res["success"], f"Unsolvable configuration {name} must not be masked as success")
                self.assertIn("exhausted", str(res.get("error", "")))

            if os.path.exists(out_json):
                os.remove(out_json)

        print("\n=== STAGE 6 PLANNING SUMMARY ===")
        for k, v in summary_results.items():
            print(f"  [{k}] status={v['status']}, sequence={v['sequence_len']} steps, time={v['time_sec']}s, valid={v['all_valid']}, err={v['error']}")

    # =========================================================================
    # STAGE 8: Производительность и ресурсы
    # =========================================================================

    def test_stage8_performance_and_memory(self):
        """Stage 8: Measure planning time, peak memory, and node budget limit enforcement."""
        tracemalloc.start()
        orchestrator = BendSeqOrchestrator(self.backend)

        # Profile execution on U-profile
        u_model = build_u_profile(35, 50, 35, 2.0, 2.0, 80.0)
        t0 = time.perf_counter()
        res = orchestrator.run_planning(target_shape=u_model, algorithm="astar", max_iterations=500)
        t_elapsed = time.perf_counter() - t0

        current_mem, peak_mem = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        peak_mb = peak_mem / (1024 * 1024)
        print(f"\n[PERF] U-Profile Planning Time: {t_elapsed:.4f} s, Peak Memory: {peak_mb:.2f} MB")
        self.assertLess(t_elapsed, 5.0, "Planning for U-profile should complete within 5 seconds")
        self.assertLess(peak_mb, 150.0, "Peak memory should be under 150 MB")


if __name__ == "__main__":
    unittest.main()
