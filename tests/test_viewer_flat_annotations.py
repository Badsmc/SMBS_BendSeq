"""
test_viewer_flat_annotations.py - Test suite for flat_annotations.py (S.18a).
"""
from pathlib import Path
import unittest

from smbs_core.viewer.flat_annotations import (
    BendAnnotation,
    FlatAnnotationsError,
    VERIFICATION_FAILED,
    VERIFICATION_UNVERIFIED,
    VERIFICATION_VERIFIED,
    compute_annotations,
)
from smbs_core.viewer.graph_builder import ViewerGraphs, build_graphs
from smbs_core.viewer.run_loader import RunArtifacts, load_run


def _make_bend(
    parent_panel_id="P1",
    child_panel_id="P2",
    direction="UP",
    axis_point=None,
    axis_direction=None,
    angle_deg=90.0,
    radius_mm=2.0,
    length_mm=100.0,
    bend_line_flat_pattern=None,
):
    b = {
        "parent_panel_id": parent_panel_id,
        "child_panel_id": child_panel_id,
        "axis_point": axis_point if axis_point is not None else [0.0, 0.0, 0.0],
        "axis_direction": axis_direction if axis_direction is not None else [1.0, 0.0, 0.0],
        "angle_deg": {"value": angle_deg},
        "radius_mm": {"value": radius_mm},
        "length_mm": {"value": length_mm},
        "direction": {"value": direction},
    }
    if bend_line_flat_pattern is not None:
        b["bend_line_flat_pattern"] = bend_line_flat_pattern
    return b


def _make_artifacts(
    candidate_sequence,
    bends,
    panels=None,
    verification_payload=None,
    verification_diagnostics=None,
) -> RunArtifacts:
    if panels is None:
        panels = {
            "P1": {"centroid": [0.0, 0.0, 0.0], "normal": [0.0, 0.0, 1.0]},
            "P2": {"centroid": [10.0, 0.0, 0.0], "normal": [0.0, 0.0, 1.0]},
        }

    canonical_part = {
        "payload": {
            "panels": panels,
            "bends": bends,
            "thickness_mm": {"value": 2.0},
        }
    }
    search_result = {
        "payload": {
            "planning_status": "SUCCESS",
            "candidate_sequence": candidate_sequence,
        }
    }
    verification_result = None
    if verification_payload is not None or verification_diagnostics is not None:
        verification_result = {
            "payload": verification_payload if verification_payload is not None else {},
            "diagnostics": verification_diagnostics if verification_diagnostics is not None else [],
        }

    return RunArtifacts(
        run_dir=Path("/fake/run"),
        canonical_part=canonical_part,
        search_result=search_result,
        verification_result=verification_result,
        geometry_shape_path=Path("/fake/geometry.brep"),
        flat_pattern_path=Path("/fake/flat_pattern.brep"),
    )


class TestViewerFlatAnnotations(unittest.TestCase):
    def test_real_failed_run_annotations(self):
        run_dir = Path("runs/run_20261017_203828_511545")
        if not run_dir.exists():
            self.skipTest(f"Run dir not found: {run_dir}")

        artifacts = load_run(run_dir)
        graphs = build_graphs(artifacts)
        annotations = compute_annotations(artifacts, graphs)

        seq = artifacts.search_result["payload"]["candidate_sequence"]
        self.assertEqual(len(annotations), len(seq))
        for ann in annotations:
            self.assertEqual(ann.verification, VERIFICATION_FAILED)
            self.assertIsInstance(ann.midpoint, tuple)
            self.assertEqual(len(ann.midpoint), 3)
            for coord in ann.midpoint:
                self.assertIsInstance(coord, float)

        # Check steps in ascending order
        steps = [ann.step for ann in annotations]
        self.assertEqual(steps, sorted(steps))

    def test_real_old_verified_run_raises_missing_bend_line(self):
        run_dir = Path("runs/run_20261012_034042_cf4c34")
        if not run_dir.exists():
            self.skipTest(f"Run dir not found: {run_dir}")

        artifacts = load_run(run_dir)
        graphs = build_graphs(artifacts)

        with self.assertRaises(FlatAnnotationsError) as ctx:
            compute_annotations(artifacts, graphs)

        err_msg = str(ctx.exception)
        self.assertIn("bend_line_flat_pattern", err_msg)
        self.assertIn("axis_point is NOT used as fallback", err_msg)
        self.assertIn("Regenerate", err_msg)

    def test_synthetic_verified_annotations(self):
        bends = {
            "B1": _make_bend(
                parent_panel_id="P1",
                child_panel_id="P2",
                direction="UP",
                bend_line_flat_pattern={
                    "start_mm": [0.0, 0.0, 0.0],
                    "end_mm": [10.0, 0.0, 0.0],
                },
            ),
            "B2": _make_bend(
                parent_panel_id="P2",
                child_panel_id="P1",
                direction="DOWN",
                bend_line_flat_pattern={
                    "start_mm": [0.0, 20.0, 0.0],
                    "end_mm": [10.0, 20.0, 0.0],
                },
            ),
        }
        seq = [
            {"step": 1, "bend_id": "B1"},
            {"step": 2, "bend_id": "B2"},
        ]
        artifacts = _make_artifacts(
            candidate_sequence=seq,
            bends=bends,
            verification_payload={"verification_status": "VERIFIED"},
        )
        graphs = build_graphs(artifacts)
        annotations = compute_annotations(artifacts, graphs)

        self.assertEqual(len(annotations), 2)
        self.assertEqual(annotations[0].step, 1)
        self.assertEqual(annotations[0].bend_id, "B1")
        self.assertEqual(annotations[0].verification, VERIFICATION_VERIFIED)
        self.assertEqual(annotations[1].step, 2)
        self.assertEqual(annotations[1].bend_id, "B2")
        self.assertEqual(annotations[1].verification, VERIFICATION_VERIFIED)

    def test_midpoint_is_start_end_average(self):
        bends = {
            "B1": _make_bend(
                parent_panel_id="P1",
                child_panel_id="P2",
                direction="UP",
                bend_line_flat_pattern={
                    "start_mm": [0.0, 0.0, 0.0],
                    "end_mm": [10.0, 20.0, 30.0],
                },
            )
        }
        seq = [{"step": 1, "bend_id": "B1"}]
        artifacts = _make_artifacts(seq, bends)
        graphs = build_graphs(artifacts)
        annotations = compute_annotations(artifacts, graphs)

        self.assertEqual(len(annotations), 1)
        self.assertEqual(annotations[0].midpoint, (5.0, 10.0, 15.0))

    def test_step_comes_from_candidate_sequence(self):
        bends = {
            "B1": _make_bend(
                parent_panel_id="P1",
                child_panel_id="P2",
                direction="UP",
                bend_line_flat_pattern={"start_mm": [0.0, 0.0, 0.0], "end_mm": [1.0, 0.0, 0.0]},
            ),
            "B2": _make_bend(
                parent_panel_id="P2",
                child_panel_id="P1",
                direction="DOWN",
                bend_line_flat_pattern={"start_mm": [0.0, 1.0, 0.0], "end_mm": [1.0, 1.0, 0.0]},
            ),
        }
        # Inverted order in candidate sequence
        seq = [
            {"step": 2, "bend_id": "B2"},
            {"step": 1, "bend_id": "B1"},
        ]
        artifacts = _make_artifacts(seq, bends)
        graphs = build_graphs(artifacts)
        annotations = compute_annotations(artifacts, graphs)

        self.assertEqual(len(annotations), 2)
        self.assertEqual(annotations[0].step, 1)
        self.assertEqual(annotations[0].bend_id, "B1")
        self.assertEqual(annotations[1].step, 2)
        self.assertEqual(annotations[1].bend_id, "B2")

    def test_ui_direction_from_graph_builder(self):
        bends = {
            "B1": _make_bend(
                parent_panel_id="P1",
                child_panel_id="P2",
                direction="DOWN",
                bend_line_flat_pattern={"start_mm": [0.0, 0.0, 0.0], "end_mm": [1.0, 0.0, 0.0]},
            )
        }
        seq = [{"step": 1, "bend_id": "B1"}]
        artifacts = _make_artifacts(seq, bends)
        graphs = build_graphs(artifacts)
        annotations = compute_annotations(artifacts, graphs)

        self.assertEqual(annotations[0].ui_direction, graphs.direction_by_bend["B1"])
        self.assertEqual(annotations[0].ui_direction, "DOWN")

    def test_missing_bend_line_flat_pattern_raises(self):
        bends = {
            "B1": _make_bend(
                parent_panel_id="P1",
                child_panel_id="P2",
                direction="UP",
                bend_line_flat_pattern=None,
            )
        }
        seq = [{"step": 1, "bend_id": "B1"}]
        artifacts = _make_artifacts(seq, bends)
        graphs = build_graphs(artifacts)

        with self.assertRaises(FlatAnnotationsError) as ctx:
            compute_annotations(artifacts, graphs)

        err_msg = str(ctx.exception)
        self.assertIn("bend_line_flat_pattern", err_msg)
        self.assertIn("axis_point is NOT used as fallback", err_msg)
        self.assertIn("Regenerate", err_msg)

    def test_missing_start_mm_raises(self):
        bends = {
            "B1": _make_bend(
                parent_panel_id="P1",
                child_panel_id="P2",
                direction="UP",
                bend_line_flat_pattern={
                    "end_mm": [1.0, 1.0, 1.0],
                },
            )
        }
        seq = [{"step": 1, "bend_id": "B1"}]
        artifacts = _make_artifacts(seq, bends)
        graphs = build_graphs(artifacts)

        with self.assertRaises(FlatAnnotationsError) as ctx:
            compute_annotations(artifacts, graphs)
        self.assertIn("missing required field", str(ctx.exception))

    def test_start_end_wrong_length_raises(self):
        bends = {
            "B1": _make_bend(
                parent_panel_id="P1",
                child_panel_id="P2",
                direction="UP",
                bend_line_flat_pattern={
                    "start_mm": [0.0, 0.0],
                    "end_mm": [1.0, 1.0, 1.0],
                },
            )
        }
        seq = [{"step": 1, "bend_id": "B1"}]
        artifacts = _make_artifacts(seq, bends)
        graphs = build_graphs(artifacts)

        with self.assertRaises(FlatAnnotationsError) as ctx:
            compute_annotations(artifacts, graphs)
        self.assertIn("3-element vectors", str(ctx.exception))

    def test_duplicate_step_raises(self):
        bends = {
            "B1": _make_bend(
                parent_panel_id="P1",
                child_panel_id="P2",
                direction="UP",
                bend_line_flat_pattern={"start_mm": [0.0, 0.0, 0.0], "end_mm": [1.0, 0.0, 0.0]},
            ),
            "B2": _make_bend(
                parent_panel_id="P2",
                child_panel_id="P1",
                direction="DOWN",
                bend_line_flat_pattern={"start_mm": [0.0, 1.0, 0.0], "end_mm": [1.0, 1.0, 0.0]},
            ),
        }
        seq = [
            {"step": 1, "bend_id": "B1"},
            {"step": 1, "bend_id": "B2"},
        ]
        artifacts = _make_artifacts(seq, bends)
        graphs = build_graphs(artifacts)

        with self.assertRaises(FlatAnnotationsError) as ctx:
            compute_annotations(artifacts, graphs)
        self.assertIn("Duplicate step", str(ctx.exception))

    def test_missing_step_raises(self):
        bends = {
            "B1": _make_bend(
                parent_panel_id="P1",
                child_panel_id="P2",
                direction="UP",
                bend_line_flat_pattern={"start_mm": [0.0, 0.0, 0.0], "end_mm": [1.0, 0.0, 0.0]},
            )
        }
        seq = [{"bend_id": "B1"}]
        artifacts = _make_artifacts(seq, bends)
        graphs = build_graphs(artifacts)

        with self.assertRaises(FlatAnnotationsError) as ctx:
            compute_annotations(artifacts, graphs)
        self.assertIn("missing required field", str(ctx.exception))

    def test_missing_bend_id_raises(self):
        bends = {
            "B1": _make_bend(
                parent_panel_id="P1",
                child_panel_id="P2",
                direction="UP",
                bend_line_flat_pattern={"start_mm": [0.0, 0.0, 0.0], "end_mm": [1.0, 0.0, 0.0]},
            )
        }
        valid_seq = [{"step": 1, "bend_id": "B1"}]
        artifacts_valid = _make_artifacts(valid_seq, bends)
        graphs = build_graphs(artifacts_valid)

        bad_seq = [{"step": 1}]
        artifacts_bad = _make_artifacts(bad_seq, bends)

        with self.assertRaises(FlatAnnotationsError) as ctx:
            compute_annotations(artifacts_bad, graphs)
        self.assertIn("missing required field", str(ctx.exception))

    def test_unknown_bend_id_raises(self):
        bends = {
            "B1": _make_bend(
                parent_panel_id="P1",
                child_panel_id="P2",
                direction="UP",
                bend_line_flat_pattern={"start_mm": [0.0, 0.0, 0.0], "end_mm": [1.0, 0.0, 0.0]},
            )
        }
        valid_seq = [{"step": 1, "bend_id": "B1"}]
        artifacts_valid = _make_artifacts(valid_seq, bends)
        graphs = build_graphs(artifacts_valid)

        bad_seq = [{"step": 1, "bend_id": "B_UNKNOWN"}]
        artifacts_bad = _make_artifacts(bad_seq, bends)

        with self.assertRaises(FlatAnnotationsError) as ctx:
            compute_annotations(artifacts_bad, graphs)
        self.assertIn("not in 04_canonical_part", str(ctx.exception))

    def test_empty_candidate_sequence_returns_empty_list(self):
        bends = {
            "B1": _make_bend(
                parent_panel_id="P1",
                child_panel_id="P2",
                direction="UP",
                bend_line_flat_pattern={"start_mm": [0.0, 0.0, 0.0], "end_mm": [1.0, 0.0, 0.0]},
            )
        }
        seq = []
        artifacts = _make_artifacts(seq, bends)
        graphs = build_graphs(artifacts)

        result = compute_annotations(artifacts, graphs)
        self.assertEqual(result, [])

    def test_unverified_when_09_missing(self):
        bends = {
            "B1": _make_bend(
                parent_panel_id="P1",
                child_panel_id="P2",
                direction="UP",
                bend_line_flat_pattern={"start_mm": [0.0, 0.0, 0.0], "end_mm": [1.0, 0.0, 0.0]},
            )
        }
        seq = [{"step": 1, "bend_id": "B1"}]
        artifacts = _make_artifacts(seq, bends, verification_payload=None)
        graphs = build_graphs(artifacts)
        annotations = compute_annotations(artifacts, graphs)

        self.assertEqual(len(annotations), 1)
        self.assertEqual(annotations[0].verification, VERIFICATION_UNVERIFIED)

    def test_09_present_but_missing_verification_status_raises(self):
        bends = {
            "B1": _make_bend(
                parent_panel_id="P1",
                child_panel_id="P2",
                direction="UP",
                bend_line_flat_pattern={"start_mm": [0.0, 0.0, 0.0], "end_mm": [1.0, 0.0, 0.0]},
            )
        }
        seq = [{"step": 1, "bend_id": "B1"}]
        # 09 exists (payload is empty dict, no verification_status)
        artifacts = _make_artifacts(seq, bends, verification_payload={})
        graphs = build_graphs(artifacts)

        with self.assertRaises(FlatAnnotationsError) as ctx:
            compute_annotations(artifacts, graphs)
        self.assertIn("payload.verification_status missing", str(ctx.exception))


    def test_empty_candidate_sequence_with_malformed_09_returns_empty(self):
        """
        Empty candidate_sequence short-circuits before 09 validation.
        No annotations are needed, so missing verification_status is irrelevant.
        This locks in the documented early-return contract in compute_annotations.
        """
        bends = {
            "B1": _make_bend(
                parent_panel_id="P1",
                child_panel_id="P2",
                direction="UP",
                bend_line_flat_pattern={"start_mm": [0.0, 0.0, 0.0], "end_mm": [1.0, 0.0, 0.0]},
            )
        }
        seq = []
        # 09 present but payload empty (no verification_status) — this would raise
        # if not for the early return on empty candidate_sequence.
        artifacts = _make_artifacts(seq, bends, verification_payload={})
        graphs = build_graphs(artifacts)

        result = compute_annotations(artifacts, graphs)
        self.assertEqual(result, [])


if __name__ == "__main__":
    unittest.main()
