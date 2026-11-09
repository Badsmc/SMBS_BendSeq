"""Continuous planar swept-area regression tests."""
import unittest

from smbs_core.kinematics2d.swept_translation_2d import (
    screen_swept_translation, screen_lift_then_withdraw_swept,
    swept_translation_patches,
)


def rect(x0, z0, x1, z1):
    return ((x0, z0), (x1, z0), (x1, z1), (x0, z1))


class SweptTranslationTests(unittest.TestCase):
    def test_catches_middle_obstacle_between_clear_endpoints(self):
        part = (("sheet", rect(0, 0, 1, 1)),)
        punch = (rect(4, 0, 5, 1), ())
        die = (rect(30, -10, 31, -9), ())
        result = screen_swept_translation(
            part, punch, die, dx=10,
        )
        self.assertEqual(result["status"], "SWEPT_2D_INTERFERENCE")
        self.assertEqual(result["tool"], "PUNCH")
        self.assertEqual(result["component"], "sheet")

    def test_clear_sweep_past_distant_obstacles(self):
        part = (("sheet", rect(0, 0, 1, 1)),)
        punch = (rect(20, 20, 21, 21), ())
        die = (rect(-20, -20, -19, -19), ())
        result = screen_swept_translation(part, punch, die, dx=10)
        self.assertEqual(result["status"],
                         "SWEPT_2D_CLEAR_NOT_3D_CERTIFIED")

    def test_lift_changes_obstruction_to_clear(self):
        part = (("sheet", rect(0, 0, 1, 1)),)
        punch = (rect(-10, 20, -9, 21), ())
        die = (rect(4, 0, 5, 1), ())
        direct = screen_swept_translation(part, punch, die, dx=10)
        self.assertEqual(direct["status"], "SWEPT_2D_INTERFERENCE")
        lifted = screen_lift_then_withdraw_swept(
            part, punch, die, lift_mm=3, distance_mm=10, direction=1,
        )
        self.assertEqual(lifted["status"],
                         "SWEPT_2D_CLEAR_NOT_3D_CERTIFIED")
        self.assertEqual(lifted["phase"], "COMPLETE")

    def test_punch_blocks_lift_before_lateral(self):
        part = (("sheet", rect(0, 0, 1, 1)),)
        punch = (rect(0, 3, 1, 5), ())
        die = (rect(20, -10, 21, -9), ())
        result = screen_lift_then_withdraw_swept(
            part, punch, die, lift_mm=5, distance_mm=10, direction=1,
        )
        self.assertEqual(result["status"], "SWEPT_2D_INTERFERENCE")
        self.assertEqual(result["phase"], "LIFT")

    def test_concave_sheet_not_convex_hull(self):
        concave = ((0,0),(4,0),(4,1),(1,1),(1,4),(0,4))
        patches = tuple(swept_translation_patches(concave, 1, 0))
        self.assertEqual(patches[0], concave)
        # A distant tool inside the concave notch must remain CLEAR.
        part = (("concave", concave),)
        punch = (rect(3, 3, 3.5, 3.5), ())
        die = (rect(20, 20, 21, 21), ())
        result = screen_swept_translation(part, punch, die, dx=1)
        self.assertEqual(result["status"],
                         "SWEPT_2D_CLEAR_NOT_3D_CERTIFIED")

    def test_deadline_is_unverified(self):
        from unittest.mock import patch
        part = (("sheet", rect(0,0,1,1)),)
        punch = (rect(5,0,6,1), ())
        die = (rect(20,-10,21,-9), ())
        with patch("smbs_core.kinematics2d.swept_translation_2d.perf_counter",
                   return_value=10.0):
            result = screen_swept_translation(
                part,punch,die,dx=10,deadline_monotonic=5.0
            )
        self.assertEqual(result["status"],
                         "UNVERIFIED_SEARCH_LIMIT_REACHED")


if __name__ == "__main__":
    unittest.main()
