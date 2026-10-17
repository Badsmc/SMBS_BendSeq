"""
Unit tests for smbs_core.viewer.graph_builder (Stage 4, S.12).
Pure Python tests without OCC / FreeCAD dependencies.
"""
import copy
import unittest
from pathlib import Path

from smbs_core.Core.bend_graph import IncompleteBendError
from smbs_core.viewer.run_loader import RunArtifacts
from smbs_core.viewer.graph_builder import build_graphs, ViewerGraphs, GraphBuilderError


def _make_valid_artifacts(cp_override=None, sr_override=None) -> RunArtifacts:
    cp = {
        "schema_name": "canonical_part",
        "payload": {
            "input_mode": "CANONICAL_PART",
            "thickness_mm": {"value": 1.5, "source": "USER_INPUT", "confidence": "CONFIRMED"},
            "reference_panel_id": "P01",
            "panels": {
                "P01": {"panel_id": "P01", "area_mm2": 100.0, "centroid": [0, 0, 0], "normal": [0.0, 0.0, 1.0]},
                "P02": {"panel_id": "P02", "area_mm2": 50.0, "centroid": [0, 10, 0], "normal": [0.0, 1.0, 0.0]},
                "P03": {"panel_id": "P03", "area_mm2": 50.0, "centroid": [0, -10, 0], "normal": [0.0, -1.0, 0.0]}
            },
            "bends": {
                "B01": {
                    "bend_id": "B01",
                    "parent_panel_id": "P01",
                    "child_panel_id": "P02",
                    "angle_deg": {"value": 90.0, "source": "GEOMETRIC_INFERENCE", "confidence": "CONFIRMED"},
                    "radius_mm": {"value": 1.0, "source": "GEOMETRIC_INFERENCE", "confidence": "CONFIRMED"},
                    "length_mm": {"value": 50.0, "source": "GEOMETRIC_INFERENCE", "confidence": "CONFIRMED"},
                    "direction": {"value": "UP", "source": "GEOMETRIC_INFERENCE", "confidence": "CONFIRMED"},
                    "k_factor": {"value": 0.44, "source": "SHEETMETAL_NATIVE", "confidence": "CONFIRMED"},
                    "axis_point": [0.0, 0.0, 0.0],
                    "axis_direction": [1.0, 0.0, 0.0]
                },
                "B02": {
                    "bend_id": "B02",
                    "parent_panel_id": "P01",
                    "child_panel_id": "P03",
                    "angle_deg": {"value": -90.0, "source": "GEOMETRIC_INFERENCE", "confidence": "CONFIRMED"},
                    "radius_mm": {"value": 1.0, "source": "GEOMETRIC_INFERENCE", "confidence": "CONFIRMED"},
                    "length_mm": {"value": 50.0, "source": "GEOMETRIC_INFERENCE", "confidence": "CONFIRMED"},
                    "direction": {"value": "DOWN", "source": "GEOMETRIC_INFERENCE", "confidence": "CONFIRMED"},
                    "k_factor": {"value": 0.44, "source": "SHEETMETAL_NATIVE", "confidence": "CONFIRMED"},
                    "axis_point": [0.0, 20.0, 0.0],
                    "axis_direction": [1.0, 0.0, 0.0]
                }
            },
            "geometry_artifact": {
                "file_path": "geometry.brep",
                "format": "BREP",
                "content_hash": "a" * 64
            }
        }
    }
    sr = {
        "schema_name": "search_result",
        "status": "SUCCESS",
        "payload": {
            "planning_status": "SUCCESS",
            "candidate_sequence": [
                {
                    "step": 1,
                    "bend_id": "B01",
                    "direction": {"value": "UP"},
                    "angle_deg": {"value": 90.0},
                    "radius_mm": {"value": 1.0},
                    "backgauge": {"x_mm": 10.0, "r_mm": 0.0, "confidence": "CONFIRMED"},
                    "tool_id": "PUNCH_STD",
                    "planner_status": "VALID",
                    "collision_status": "VALID",
                    "tooling_status": "REAL",
                    "alignment_status": "OK"
                },
                {
                    "step": 2,
                    "bend_id": "B02",
                    "direction": {"value": "DOWN"},
                    "angle_deg": {"value": -90.0},
                    "radius_mm": {"value": 1.0},
                    "backgauge": {"x_mm": 10.0, "r_mm": 0.0, "confidence": "CONFIRMED"},
                    "tool_id": "PUNCH_STD",
                    "planner_status": "VALID",
                    "collision_status": "VALID",
                    "tooling_status": "REAL",
                    "alignment_status": "OK"
                }
            ]
        }
    }

    if cp_override:
        cp = cp_override
    if sr_override:
        sr = sr_override

    return RunArtifacts(
        run_dir=Path("/tmp/dummy_run"),
        canonical_part=cp,
        search_result=sr,
        verification_result=None,
        geometry_shape_path=Path("/tmp/dummy_run/geometry.brep"),
        flat_pattern_path=None
    )


class TestViewerGraphBuilder(unittest.TestCase):

    def test_build_valid_two_bend_run(self):
        """Build graphs from valid two-bend run; verify types, counts, and UI directions."""
        art = _make_valid_artifacts()
        vg = build_graphs(art)
        self.assertIsInstance(vg, ViewerGraphs)
        self.assertEqual(len(vg.bend_graph.all_bends()), 2)
        self.assertEqual(len(vg.panel_graph.panels), 3)
        self.assertEqual(vg.direction_by_bend, {"B01": "UP", "B02": "DOWN"})

    def test_bend_graph_bends_match_canonical(self):
        """All fields in BendRecords match canonical_part exactly."""
        art = _make_valid_artifacts()
        vg = build_graphs(art)
        b01 = vg.bend_graph.get_bend("B01")
        self.assertIsNotNone(b01)
        self.assertEqual(b01.id, "B01")
        self.assertEqual(b01.parent_panel_id, "P01")
        self.assertEqual(b01.child_panel_id, "P02")
        self.assertEqual(b01.signed_angle, 90.0)
        self.assertEqual(b01.axis_origin, (0.0, 0.0, 0.0))
        self.assertEqual(b01.axis_direction, (1.0, 0.0, 0.0))
        self.assertEqual(b01.radius, 1.0)
        self.assertEqual(b01.thickness, 1.5)
        self.assertEqual(b01.length, 50.0)

        b02 = vg.bend_graph.get_bend("B02")
        self.assertIsNotNone(b02)
        self.assertEqual(b02.id, "B02")
        self.assertEqual(b02.signed_angle, -90.0)

    def test_panel_graph_has_all_panels(self):
        """All panels registered in panel_graph with proper normals and connectivity."""
        art = _make_valid_artifacts()
        vg = build_graphs(art)
        self.assertEqual(set(vg.panel_graph.panels.keys()), {"P01", "P02", "P03"})
        self.assertEqual(vg.panel_graph.panels["P01"].normal, (0.0, 0.0, 1.0))
        self.assertEqual(vg.panel_graph.panels["P02"].normal, (0.0, 1.0, 0.0))

    def test_missing_angle_deg_value_raises(self):
        """Missing angle_deg.value raises GraphBuilderError."""
        art = _make_valid_artifacts()
        del art.canonical_part["payload"]["bends"]["B01"]["angle_deg"]["value"]
        with self.assertRaises(GraphBuilderError) as ctx:
            build_graphs(art)
        self.assertIn("B01", str(ctx.exception))

    def test_missing_axis_point_raises(self):
        """Missing axis_point raises GraphBuilderError."""
        art = _make_valid_artifacts()
        del art.canonical_part["payload"]["bends"]["B01"]["axis_point"]
        with self.assertRaises(GraphBuilderError) as ctx:
            build_graphs(art)
        self.assertIn("axis_point", str(ctx.exception))

    def test_missing_axis_direction_raises(self):
        """Missing axis_direction raises GraphBuilderError."""
        art = _make_valid_artifacts()
        del art.canonical_part["payload"]["bends"]["B01"]["axis_direction"]
        with self.assertRaises(GraphBuilderError) as ctx:
            build_graphs(art)
        self.assertIn("axis_direction", str(ctx.exception))

    def test_missing_parent_panel_id_raises(self):
        """Missing parent_panel_id raises GraphBuilderError."""
        art = _make_valid_artifacts()
        del art.canonical_part["payload"]["bends"]["B01"]["parent_panel_id"]
        with self.assertRaises(GraphBuilderError) as ctx:
            build_graphs(art)
        self.assertIn("parent_panel_id", str(ctx.exception))

    def test_missing_child_panel_id_raises(self):
        """Missing child_panel_id raises GraphBuilderError."""
        art = _make_valid_artifacts()
        del art.canonical_part["payload"]["bends"]["B01"]["child_panel_id"]
        with self.assertRaises(GraphBuilderError) as ctx:
            build_graphs(art)
        self.assertIn("child_panel_id", str(ctx.exception))

    def test_missing_length_mm_raises(self):
        """Missing length_mm.value raises GraphBuilderError."""
        art = _make_valid_artifacts()
        del art.canonical_part["payload"]["bends"]["B01"]["length_mm"]["value"]
        with self.assertRaises(GraphBuilderError) as ctx:
            build_graphs(art)
        self.assertIn("length_mm", str(ctx.exception))

    def test_missing_radius_mm_raises(self):
        """Missing radius_mm.value raises GraphBuilderError."""
        art = _make_valid_artifacts()
        del art.canonical_part["payload"]["bends"]["B01"]["radius_mm"]["value"]
        with self.assertRaises(GraphBuilderError) as ctx:
            build_graphs(art)
        self.assertIn("radius_mm", str(ctx.exception))

    def test_missing_thickness_mm_raises(self):
        """Missing thickness_mm.value raises GraphBuilderError."""
        art = _make_valid_artifacts()
        del art.canonical_part["payload"]["thickness_mm"]["value"]
        with self.assertRaises(GraphBuilderError) as ctx:
            build_graphs(art)
        self.assertIn("thickness_mm.value", str(ctx.exception))

    def test_missing_panel_normal_raises(self):
        """Missing panel normal raises GraphBuilderError (no default (0,0,1))."""
        art = _make_valid_artifacts()
        del art.canonical_part["payload"]["panels"]["P01"]["normal"]
        with self.assertRaises(GraphBuilderError) as ctx:
            build_graphs(art)
        self.assertIn("normal", str(ctx.exception))

    def test_invalid_direction_value_raises(self):
        """direction.value not in {'UP', 'DOWN'} raises GraphBuilderError."""
        art = _make_valid_artifacts()
        art.canonical_part["payload"]["bends"]["B01"]["direction"]["value"] = "SIDEWAYS"
        with self.assertRaises(GraphBuilderError) as ctx:
            build_graphs(art)
        self.assertIn("SIDEWAYS", str(ctx.exception))

    def test_signed_angle_out_of_range_raises(self):
        """signed_angle > 180 raises GraphBuilderError wrapping IncompleteBendError."""
        art = _make_valid_artifacts()
        art.canonical_part["payload"]["bends"]["B01"]["angle_deg"]["value"] = 200.0
        with self.assertRaises(GraphBuilderError) as ctx:
            build_graphs(art)
        self.assertIsInstance(ctx.exception.__cause__, IncompleteBendError)

    def test_empty_candidate_sequence_valid(self):
        """Empty candidate_sequence is valid (search failure scenario)."""
        art = _make_valid_artifacts()
        art.search_result["payload"]["candidate_sequence"] = []
        vg = build_graphs(art)
        self.assertEqual(len(vg.bend_graph.all_bends()), 2)
        self.assertEqual(len(vg.direction_by_bend), 2)

    def test_candidate_sequence_bend_id_not_in_bend_graph_raises(self):
        """Step referring to non-existent bend_id raises GraphBuilderError."""
        art = _make_valid_artifacts()
        art.search_result["payload"]["candidate_sequence"].append({
            "step": 3,
            "bend_id": "BEND_UNKNOWN_99",
            "direction": {"value": "UP"}
        })
        with self.assertRaises(GraphBuilderError) as ctx:
            build_graphs(art)
        self.assertIn("BEND_UNKNOWN_99", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
