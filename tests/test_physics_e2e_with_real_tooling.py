"""Physics e2e with real tooling (puancon.FCStd + matrica.FCStd).

Acceptance for KL-8 (not 'VERIFIED' — that needs machine_frame, KL-16):
- Every physically-verifiable stage (APPROACH, SELF, TOOL, TRAJECTORY, RETRACT)
  MUST return VALID on compatible parts.
- MACHINE stage returns honest UNKNOWN (no machine_frame.FCStd in toollibrary/;
  KL-16 backlog).
- Orchestrator under EXPLORATORY: steps_invalid == 0, steps_unknown > 0
  (honest, invariant #1: UNKNOWN != VALID).
- Sequence is found (PARTIAL, not FAILED).

If these tests start passing with VERIFIED (after KL-16 adds machine_frame),
update assertions to strict all_steps_valid and enable PRODUCTION policy.
"""
import sys, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from smbs_core.Core.orchestrator import BendSeqOrchestrator
from smbs_core.Physics.machine_kinematics import PressBrakeMachine
from smbs_core.Physics.collision_detector import CollisionDetector
from smbs_core.Physics.tooling_assembly import ToolingKind
from smbs_core.Physics.validator import PhysicalValidator
from smbs_core.geometry_backend.backends.pythonocc_backend import PythonOCCBackend
from smbs_core.Geometry.feature_extractor import FeatureExtractor
from smbs_core.Core.bend_graph import BendGraph, BendRecord
from smbs_core.Core.panel_graph import PanelGraph
from smbs_core.Core.bend_transform import BendTransform
from smbs_core.geometry_backend.panel_face_resolver import resolve_panel_faces
from smbs_core.models.tooling import GeometryStatus
from smbs_core.Physics.collision_detector import CollisionStatus
from tests.test_phase3_cad_models import build_l_profile
from tests.test_technological_unfold_single_bend import create_l_bracket_shape


PUNCH = ROOT / "toollibrary/punch/puancon.FCStd"
DIE = ROOT / "toollibrary/die/matrica.FCStd"


class TestPhysicsE2EWithRealTooling(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.backend = PythonOCCBackend()
        cls.machine_config = {
            "punch_path": str(PUNCH),
            "die_path": str(DIE),
            "punch_target_object_name": "Body",
            "die_target_object_name": "Body",
        }

    def _orchestrator(self):
        return BendSeqOrchestrator(backend=self.backend, machine_config=self.machine_config)

    def _detector(self):
        machine = PressBrakeMachine(
            punch_path=str(PUNCH), die_path=str(DIE),
            punch_target_object_name="Body", die_target_object_name="Body")
        return CollisionDetector(machine, self.backend)

    def _setup_panel_graph(self, shape):
        feats = FeatureExtractor(self.backend).extract_features(shape)
        bid = list(feats["bends"].keys())[0]
        binfo = feats["bends"][bid]
        bg = BendGraph()
        rec = BendRecord(
            bend_id=bid, feature_id=f"FEAT_{bid}",
            parent_panel_id=binfo["parent_panel_id"],
            child_panel_id=binfo["child_panel_id"],
            axis_origin=binfo["origin"], axis_direction=binfo["axis"],
            signed_angle=binfo["signed_angle"], radius=binfo["radius"],
            thickness=feats["thickness"], length=binfo["length"],
        )
        bg.add_bend(rec)
        pg = PanelGraph()
        pg.add_bend_connection(bid, rec.parent_panel_id, rec.child_panel_id)
        payload = {
            "thickness_mm": {"value": feats["thickness"]},
            "panels": {pid: {"normal": pd["normal"], "centroid": pd["centroid"]}
                       for pid, pd in feats["panels"].items()},
            "bends": {bid: {"axis_point": binfo["origin"],
                            "child_panel_id": binfo["child_panel_id"]}},
        }
        resolve_panel_faces(shape, pg, payload)
        return feats, bid, binfo, rec, bg, pg

    # ------------------------------------------------------------------
    # Orchestrator-level tests (planner + validator under EXPLORATORY)
    # ------------------------------------------------------------------

    def test_orchestrator_L40_real_tooling_partial_honest(self):
        """Orchestrator on L40 (compatible with puancon) under EXPLORATORY:
        finds sequence, no INVALID steps, honest UNKNOWN for MACHINE."""
        orch = self._orchestrator()
        shape = build_l_profile(40, 40, 2.0, 2.0, 80.0)
        res = orch.run_planning(
            target_shape=shape, algorithm="dfs", unknown_policy="EXPLORATORY"
        )
        self.assertEqual(res["status"], "PARTIAL",
                         f"Real tooling under EXPLORATORY must yield PARTIAL "
                         f"(not VERIFIED without machine_frame, KL-16), got {res['status']}")
        self.assertGreater(len(res["sequence"]), 0, "sequence must be found")
        v = res["validation"]
        self.assertEqual(v["steps_invalid"], 0,
                         f"No INVALID steps allowed on compatible L40, got {v}")
        self.assertGreater(v["steps_unknown"], 0,
                         f"Expected UNKNOWN steps (MACHINE, KL-16). If zero, machine_frame "
                         f"was added — update this test to assert all_steps_valid instead. Got {v}")
        self.assertFalse(v["all_steps_valid"],
                         f"all_steps_valid must be False with UNKNOWN present (invariant #1). Got {v}")

    def test_orchestrator_fixture_l_real_tooling_partial_honest(self):
        """Orchestrator on fixture_l (compatible, Q21 proof) under EXPLORATORY:
        finds sequence, no INVALID steps, honest UNKNOWN for MACHINE."""
        orch = self._orchestrator()
        shape = create_l_bracket_shape(90.0, 1.0, 1.5, 50.0, 95.5, 47.5)
        res = orch.run_planning(
            target_shape=shape, algorithm="dfs", unknown_policy="EXPLORATORY"
        )
        self.assertEqual(res["status"], "PARTIAL")
        self.assertGreater(len(res["sequence"]), 0)
        v = res["validation"]
        self.assertEqual(v["steps_invalid"], 0)
        self.assertGreater(v["steps_unknown"], 0)
        self.assertFalse(v["all_steps_valid"])

    # ------------------------------------------------------------------
    # Per-stage direct tests (isolate MACHINE from physics)
    # ------------------------------------------------------------------

    def _run_per_stage(self, shape, label):
        detector = self._detector()
        feats, bid, binfo, rec, bg, pg = self._setup_panel_graph(shape)
        moving = pg.moving_subtree(bid)
        unfolded = BendTransform.rotate_subtree(
            self.backend, shape, binfo["origin"], binfo["axis"],
            -rec.signed_angle, moving, pg,
        )
        return {
            "APPROACH": detector.approach_collision(shape, binfo),
            "SELF": detector.self_collision(unfolded, bid, pg),
            "TOOL": detector.tool_collision(unfolded, binfo, bid, pg),
            "TRAJECTORY": detector.trajectory_collision(
                shape, binfo, bend_id=bid, panel_graph=pg, dtheta=5.0),
            "RETRACT": detector.retract_collision(unfolded, binfo),
            "MACHINE": detector.machine_collision(unfolded, binfo, bid, pg),
        }, label

    def test_per_stage_L40_real_tooling(self):
        """Every physically-verifiable stage on L40 must be VALID; MACHINE is
        honest UNKNOWN (no machine_frame in toollibrary, KL-16)."""
        shape = build_l_profile(40, 40, 2.0, 2.0, 80.0)
        results, _ = self._run_per_stage(shape, "L40")
        for stage in ("APPROACH", "SELF", "TOOL", "TRAJECTORY", "RETRACT"):
            self.assertEqual(
                results[stage].status, CollisionStatus.VALID,
                f"{stage} on L40 with real tooling must be VALID, "
                f"got {results[stage].status}: {results[stage].message}")
        self.assertEqual(
            results["MACHINE"].status, CollisionStatus.UNKNOWN,
            f"MACHINE must be honest UNKNOWN (KL-16: no machine_frame), "
            f"got {results['MACHINE'].status}: {results['MACHINE'].message}")

    def test_per_stage_fixture_l_real_tooling(self):
        """Every physically-verifiable stage on fixture_l must be VALID; MACHINE
        is honest UNKNOWN."""
        shape = create_l_bracket_shape(90.0, 1.0, 1.5, 50.0, 95.5, 47.5)
        results, _ = self._run_per_stage(shape, "fixture_l")
        for stage in ("APPROACH", "SELF", "TOOL", "TRAJECTORY", "RETRACT"):
            self.assertEqual(
                results[stage].status, CollisionStatus.VALID,
                f"{stage} on fixture_l with real tooling must be VALID, "
                f"got {results[stage].status}: {results[stage].message}")
        self.assertEqual(
            results["MACHINE"].status, CollisionStatus.UNKNOWN,
            f"MACHINE must be honest UNKNOWN (KL-16), "
            f"got {results['MACHINE'].status}")


if __name__ == "__main__":
    unittest.main()
