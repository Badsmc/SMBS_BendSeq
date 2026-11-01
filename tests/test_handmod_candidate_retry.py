"""Headless HandMod regression tests for the full-stroke mounting candidate patch.

Run from the SMBS_BendSeq repo with the Python environment that supports the
project's imports (normally bundled FreeCAD Python on macOS).
"""
import unittest
from unittest.mock import patch

from smbs_core.Physics.validator import PhysicalValidator
from smbs_core.Physics.collision_detector import CollisionDetector, CollisionStatus, CollisionResult


def check(status, message=None):
    return CollisionResult(status, message or status.value)


class FakeState:
    def __init__(self):
        self.shape = object()
        self.remaining_bends = {"B_NEG"}
        self.bend_axes = {}

    def fingerprint(self):
        return "stable-state"

    def apply_unfold(self, **kwargs):
        return ("NEXT_STATE", kwargs["bend_id"])


class FakeBend:
    id = "B_NEG"
    axis_origin = (0, 0, 0)
    axis_direction = (0, 1, 0)
    signed_angle = -45.0

    def to_dict(self):
        return {
            "id": self.id, "angle": 45.0, "signed_angle": -45.0,
            "origin": self.axis_origin, "axis": self.axis_direction,
        }


class FakeGraph:
    def moving_subtree(self, _bend_id):
        return {"P_MOVING"}


class FakeDetector:
    def __init__(self, plans):
        self.plans = plans
        self.current = 0
        self.active_placement_key = None
        self.calls = []
        self.ended = 0

    def begin_step(self):
        self.calls.append(("BEGIN", None))
        self.current = 0
        self.active_placement_key = None

    def end_step(self):
        self.ended += 1
        self.calls.append(("END", None))
        self.active_placement_key = None

    def reject_active_placement(self):
        self.calls.append(("REJECT", self.current))
        self.current += 1
        self.active_placement_key = None

    def self_collision(self, *_args):
        self.calls.append(("SELF", None))
        return check(CollisionStatus.VALID)

    def _stage(self, stage):
        self.calls.append((stage, self.current))
        return check(self.plans[self.current].get(stage, CollisionStatus.VALID))

    def approach_collision(self, *_args):
        if self.current >= len(self.plans):
            self.calls.append(("EXHAUSTED", self.current))
            return check(CollisionStatus.UNKNOWN, "No viable placement")
        self.active_placement_key = ("candidate", self.current)
        return self._stage("APPROACH")

    def tool_collision(self, *_args):
        return self._stage("TOOL")

    def machine_collision(self, *_args):
        return self._stage("MACHINE")

    def trajectory_collision(self, *_args, **_kwargs):
        return self._stage("BEND_TRAJECTORY")

    def retract_collision(self, *_args):
        return self._stage("RETRACT")


class FullStrokeCandidateTests(unittest.TestCase):
    def make_validator(self, plans):
        validator = PhysicalValidator.__new__(PhysicalValidator)
        validator.machine = None
        validator.backend = object()
        validator.assembly = None
        validator.invalid_transitions = {}
        detector = FakeDetector(plans)
        validator.detector = detector
        return validator, detector

    def run_step(self, plans, policy="EXPLORATORY"):
        validator, detector = self.make_validator(plans)
        # The actual geometric unfold is orthogonal to mounting-candidate search.
        with patch("smbs_core.Physics.validator.BendTransform.rotate_subtree", return_value=object()):
            result = validator.validate_step(FakeState(), FakeBend(), FakeGraph(), unknown_policy=policy)
        return result, detector

    def test_later_candidate_passes_entire_stroke(self):
        result, detector = self.run_step([
            {"TOOL": CollisionStatus.INVALID},
            {},
        ])
        self.assertTrue(result.valid)
        self.assertEqual(result.diagnostics["mounting_candidates_tried"], 2)
        self.assertEqual(result.diagnostics["mounting_key"], ("candidate", 1))
        self.assertEqual(detector.ended, 1)
        self.assertIn(("REJECT", 0), detector.calls)
        self.assertEqual([i for stage, i in detector.calls if stage in
                          ("TOOL", "MACHINE", "BEND_TRAJECTORY", "RETRACT")],
                         [0, 1, 1, 1, 1])

    def test_trajectory_collision_retries_next_candidate(self):
        result, detector = self.run_step([
            {"BEND_TRAJECTORY": CollisionStatus.INVALID},
            {},
        ])
        self.assertTrue(result.valid)
        self.assertEqual(result.diagnostics["mounting_candidates_tried"], 2)
        self.assertIn(("BEND_TRAJECTORY", 0), detector.calls)
        self.assertIn(("BEND_TRAJECTORY", 1), detector.calls)

    def test_production_unknown_does_not_pass(self):
        result, detector = self.run_step([
            {"MACHINE": CollisionStatus.UNKNOWN},
            {"TOOL": CollisionStatus.INVALID},
        ], policy="PRODUCTION")
        self.assertFalse(result.valid)
        self.assertEqual(result.collision_status, CollisionStatus.UNKNOWN)
        self.assertTrue(result.has_unknown)
        self.assertEqual(detector.ended, 1)

    def test_exploratory_unknown_not_production_eligible(self):
        result, _ = self.run_step([
            {"MACHINE": CollisionStatus.UNKNOWN},
        ], policy="EXPLORATORY")
        self.assertTrue(result.valid)
        self.assertTrue(result.has_unknown)
        self.assertEqual(result.collision_status, CollisionStatus.UNKNOWN)

    def test_no_placement_does_not_claim_feasible(self):
        result, detector = self.run_step([], policy="PRODUCTION")
        self.assertFalse(result.valid)
        self.assertEqual(result.collision_status, CollisionStatus.UNKNOWN)
        self.assertEqual(detector.ended, 1)

    def test_all_eight_invalid_candidates_fail_without_unknown(self):
        result, detector = self.run_step(
            [{"TOOL": CollisionStatus.INVALID} for _ in range(8)],
            policy="PRODUCTION")
        self.assertFalse(result.valid)
        self.assertEqual(result.collision_status, CollisionStatus.INVALID)
        self.assertEqual(result.diagnostics["mounting_candidates_tried"], 8)
        self.assertEqual(detector.ended, 1)

    def test_repeated_placement_does_not_loop_forever(self):
        validator, detector = self.make_validator([{"TOOL": CollisionStatus.INVALID}])
        def broken_rejection():
            detector.active_placement_key = None
        detector.reject_active_placement = broken_rejection
        with patch("smbs_core.Physics.validator.BendTransform.rotate_subtree", return_value=object()):
            result = validator.validate_step(
                FakeState(), FakeBend(), FakeGraph(), unknown_policy="PRODUCTION")
        self.assertFalse(result.valid)
        self.assertEqual(result.collision_status, CollisionStatus.UNKNOWN)
        self.assertEqual(detector.ended, 1)

    def test_retract_failure_retries_next_candidate(self):
        result, detector = self.run_step([
            {"RETRACT": CollisionStatus.INVALID},
            {},
        ])
        self.assertTrue(result.valid)
        self.assertIn(("RETRACT", 1), detector.calls)


class SignedTrajectoryTests(unittest.TestCase):
    def test_negative_bend_uses_positive_reverse_rotation(self):
        detector = CollisionDetector.__new__(CollisionDetector)
        detector.assembly = type("Tooling", (), {"get_world_shape": lambda self, kind: None})()

        class Backend:
            def __init__(self):
                self.rotations = []
            def rotate_subtree(self, _shape, _origin, _axis, angle, _moving, _pg):
                self.rotations.append(angle)
                return object()

        backend = Backend()
        detector.backend = backend
        detector.self_collision = lambda *_args: check(CollisionStatus.VALID)
        detector.tool_collision = lambda *_args: check(CollisionStatus.VALID)
        detector.machine_collision = lambda *_args: check(CollisionStatus.VALID)
        graph = FakeGraph()
        args = {"origin": (0, 0, 0), "axis": (0, 1, 0), "angle": 45.0, "signed_angle": -45.0}
        result = detector.trajectory_collision(object(), args, bend_id="B_NEG", panel_graph=graph, dtheta=20.0)
        self.assertEqual(result.status, CollisionStatus.VALID)
        self.assertEqual(backend.rotations, [15.0, 30.0, 45.0])

    def test_invalid_sampling_returns_unknown(self):
        detector = CollisionDetector.__new__(CollisionDetector)
        detector.assembly = object()
        result = detector.trajectory_collision(
            object(), {"signed_angle": -45.0, "origin": (0, 0, 0), "axis": (0, 1, 0)},
            dtheta=0.0)
        self.assertEqual(result.status, CollisionStatus.UNKNOWN)

    def test_positive_bend_uses_negative_reverse_rotation(self):
        detector = CollisionDetector.__new__(CollisionDetector)
        detector.assembly = type("Tooling", (), {"get_world_shape": lambda self, kind: None})()

        class Backend:
            def __init__(self):
                self.rotations = []
            def rotate_subtree(self, _shape, _origin, _axis, angle, _moving, _pg):
                self.rotations.append(angle)
                return object()

        backend = Backend()
        detector.backend = backend
        detector.self_collision = lambda *_args: check(CollisionStatus.VALID)
        detector.tool_collision = lambda *_args: check(CollisionStatus.VALID)
        detector.machine_collision = lambda *_args: check(CollisionStatus.VALID)
        args = {"origin": (0, 0, 0), "axis": (0, 1, 0), "angle": 45.0, "signed_angle": +45.0}
        result = detector.trajectory_collision(object(), args, bend_id="B_POS", panel_graph=FakeGraph(), dtheta=20.0)
        self.assertEqual(result.status, CollisionStatus.VALID)
        self.assertEqual(backend.rotations, [-15.0, -30.0, -45.0])


if __name__ == "__main__":
    unittest.main()
