"""Regression gate for supplied Backgauge.FCStd stop-face CAD datum.

A passing preflight validates a real oriented BRep face, NOT actual machine
installation, collision freedom, two-finger arrangement, or travel.
"""
import unittest


class BackgaugeCADInspectorTests(unittest.TestCase):
    def test_real_cad_stop_face_matches_sidecar(self):
        try:
            import FreeCAD  # noqa: F401
            import Part  # noqa: F401
        except ImportError:
            self.skipTest("Needs FreeCAD and Part runtime")
        from freecad_smbs.commands.backgauge_cad_inspector import (
            inspect_backgauge_cad,
        )
        result = inspect_backgauge_cad()
        self.assertEqual(result["status"],
                         "CAD_SOLID_AND_STOP_FACE_VALIDATED_ONLY")
        self.assertEqual(result["stop_face_origin_mm"], [25.0, 137.5, 7.0])
        self.assertEqual(result["stop_face_normal"], [-1.0, 0.0, 0.0])
        self.assertEqual(result["solids"], 1)
        self.assertFalse(result["collision_model_active"])


if __name__ == "__main__":
    unittest.main()
