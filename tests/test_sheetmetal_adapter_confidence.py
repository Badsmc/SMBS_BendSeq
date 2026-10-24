"""
Unit tests for SheetMetalAdapter confidence tracking and strict parameter validation (P0-CODE-5).
"""

import sys
import unittest
from unittest.mock import MagicMock, patch

from smbs_core.workers.sheetmetal.adapter import SheetMetalAdapter
from smbs_core.protocols.file_store import RunStatus, ConfidenceLevel
from smbs_core.Core.bend_graph import IncompleteBendError


class FakeVertex:
    def __init__(self, x, y, z):
        self.Point = MagicMock(x=x, y=y, z=z)


class FakeLine:
    def __init__(self, p1=(0.0, 0.0, 0.0), p2=(10.0, 0.0, 0.0)):
        self.Vertexes = [FakeVertex(*p1), FakeVertex(*p2)]


class TestSheetMetalAdapterConfidence(unittest.TestCase):
    def setUp(self):
        self.adapter = SheetMetalAdapter()

    def _run_mock_unfold(
        self,
        bend_lines_info,
        k_factor_override=None,
        pref_get_float_val=0.44,
        pref_raises=False,
        estimated_t=1.5,
    ):
        """Helper to run SheetMetalAdapter.unfold with controlled mocks."""
        fake_shape = MagicMock()
        fake_face = MagicMock()
        fake_face.ShapeType = "Face"
        fake_face.Surface.TypeId = "Part::GeomPlane"
        fake_face.Area = 100.0
        fake_shape.Faces = [fake_face]

        fake_freecad = MagicMock()
        fake_params = MagicMock()
        if pref_raises:
            fake_params.GetFloat.side_effect = RuntimeError("Preferences unavailable")
        else:
            fake_params.GetFloat.return_value = pref_get_float_val
            fake_params.GetString.return_value = "ansi"
        fake_freecad.ParamGet.return_value = fake_params

        fake_doc = MagicMock()
        fake_part_obj = MagicMock()
        fake_doc.addObject.return_value = fake_part_obj
        fake_freecad.newDocument.return_value = fake_doc

        fake_unfolder = MagicMock()
        fake_unfolder.EstimateThickness.using_best_method.return_value = estimated_t
        fake_unfolder.getUnfold.return_value = (
            MagicMock(),  # root_face
            MagicMock(),  # inplace_unbend
            [],           # trimmed_bend_lines
            MagicMock(x=0.0, y=0.0, z=1.0),  # root_normal
            bend_lines_info,
        )

        with patch.dict(
            sys.modules,
            {
                "FreeCAD": fake_freecad,
                "SheetMetalNewUnfolder": fake_unfolder,
            },
        ):
            return self.adapter.unfold(
                fake_shape,
                k_factor_override=k_factor_override,
            )

    def test_01_k_factor_override_marks_confirmed(self):
        res = self._run_mock_unfold(bend_lines_info=[], k_factor_override=0.45)
        self.assertEqual(res.status, RunStatus.SUCCESS)
        self.assertEqual(res.k_factor, 0.45)
        self.assertEqual(res.k_factor_confidence, ConfidenceLevel.CONFIRMED)

    def test_02_no_override_pref_marks_estimated(self):
        res = self._run_mock_unfold(bend_lines_info=[], k_factor_override=None, pref_get_float_val=0.44)
        self.assertEqual(res.status, RunStatus.SUCCESS)
        self.assertEqual(res.k_factor, 0.44)
        self.assertEqual(res.k_factor_confidence, ConfidenceLevel.ESTIMATED)

    def test_03_no_override_pref_exception_marks_estimated_with_diagnostic(self):
        res = self._run_mock_unfold(bend_lines_info=[], k_factor_override=None, pref_raises=True)
        self.assertEqual(res.status, RunStatus.SUCCESS)
        self.assertEqual(res.k_factor, 0.44)
        self.assertEqual(res.k_factor_confidence, ConfidenceLevel.ESTIMATED)
        codes = [d.code for d in res.diagnostics]
        self.assertIn("PREFERENCES_UNAVAILABLE", codes)

    def test_04_thickness_estimation_marks_estimated(self):
        res = self._run_mock_unfold(bend_lines_info=[], estimated_t=2.0)
        self.assertEqual(res.status, RunStatus.SUCCESS)
        self.assertEqual(res.thickness_mm, 2.0)
        self.assertEqual(res.thickness_confidence, ConfidenceLevel.ESTIMATED)

    def test_05_missing_radius_raises_incomplete_bend_error(self):
        # b_info missing mandatory radius
        bad_b_info = [
            {"angle": 90.0, "direction": "UP", "line": FakeLine()}
        ]
        with self.assertRaises(IncompleteBendError) as ctx:
            self._run_mock_unfold(bend_lines_info=bad_b_info)
        self.assertIn("missing mandatory radius", str(ctx.exception))

    def test_06_defaulted_k_marks_bend_confidence_estimated(self):
        # k_factor is defaulted from preferences (no override) -> bend confidence ESTIMATED
        b_info = [
            {"angle": 90.0, "radius": 1.5, "direction": "UP", "line": FakeLine()}
        ]
        res = self._run_mock_unfold(bend_lines_info=b_info, k_factor_override=None)
        self.assertEqual(res.status, RunStatus.SUCCESS)
        self.assertEqual(len(res.bend_lines), 1)
        self.assertEqual(res.bend_lines[0].confidence, ConfidenceLevel.ESTIMATED)

    def test_07_override_k_with_line_marks_bend_confidence_confirmed(self):
        # H5 dual-access test: dict b_info with line + k_factor_override -> CONFIRMED
        b_info = [
            {"angle": 90.0, "radius": 1.5, "direction": "UP", "line": FakeLine()}
        ]
        res = self._run_mock_unfold(bend_lines_info=b_info, k_factor_override=0.44)
        self.assertEqual(res.status, RunStatus.SUCCESS)
        self.assertEqual(len(res.bend_lines), 1)
        self.assertEqual(res.bend_lines[0].confidence, ConfidenceLevel.CONFIRMED)


if __name__ == "__main__":
    unittest.main()
