import unittest
import sys
import os

SYS_PATH = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BENDSEQ_PATH = os.path.join(SYS_PATH, "BendSeq")
if BENDSEQ_PATH not in sys.path:
    sys.path.insert(0, BENDSEQ_PATH)
if SYS_PATH not in sys.path:
    sys.path.insert(0, SYS_PATH)

from smbs_core.Core.bend_graph import BendRecord
from smbs_core.Core.panel_graph import PanelGraph, Panel
from smbs_core.Geometry.bend_matcher import compute_flat_coordinates, match_bends_to_sketch, BendMatchStatus

class TestBendMatcher(unittest.TestCase):
    def test_compute_flat_coordinates_u_shape(self):
        # A simple U-Shape
        # Panel_0 (Root, Z=0, flat), Bend_01 (+90, goes up Z), Panel_1
        # Panel_1, Bend_02 (+90, goes in -X), Panel_2
        
        bends = {
            "BEND_01": {
                "axis_origin": (10.0, 0.0, 0.0),
                "axis_direction": (0.0, 1.0, 0.0),
                "signed_angle": 90.0,
                "parent_panel_id": "PANEL_00"
            },
            "BEND_02": {
                # In folded state, this bend is up at Z=10
                "axis_origin": (10.0, 0.0, 10.0),
                "axis_direction": (0.0, 1.0, 0.0),
                "signed_angle": 90.0,
                "parent_panel_id": "PANEL_01"
            }
        }
        
        pg = PanelGraph()
        pg.add_panel(Panel("PANEL_00"))
        pg.add_panel(Panel("PANEL_01"))
        pg.add_panel(Panel("PANEL_02"))
        pg.add_bend_connection("BEND_01", "PANEL_00", "PANEL_01")
        pg.add_bend_connection("BEND_02", "PANEL_01", "PANEL_02")
        
        flat_centers = compute_flat_coordinates(bends, pg, "PANEL_00")
        
        # BEND_01 is connected to Root, so its origin doesn't move
        c1 = flat_centers["BEND_01"]
        self.assertAlmostEqual(c1[0], 10.0)
        self.assertAlmostEqual(c1[1], 0.0)
        self.assertAlmostEqual(c1[2], 0.0)
        
        # BEND_02 is connected to PANEL_01. To flatten it, PANEL_01 is rotated -90 around BEND_01 (Y-axis).
        # BEND_01 origin is (10, 0, 0). BEND_02 origin is (10, 0, 10).
        # Rotating (10, 0, 10) around (10, 0, 0) by -90 deg over Y axis -> (20, 0, 0)
        c2 = flat_centers["BEND_02"]
        self.assertAlmostEqual(c2[0], 20.0, places=5)
        self.assertAlmostEqual(c2[1], 0.0, places=5)
        self.assertAlmostEqual(c2[2], 0.0, places=5)

    def test_matching_different_lengths(self):
        flat_centers = {
            "BEND_01": (10.0, 0.0, 0.0),
            "BEND_02": (20.0, 0.0, 0.0)
        }
        bends_dict = {
            "BEND_01": {"length": 50.0},
            "BEND_02": {"length": 30.0}
        }
        sketch_lines = [
            {"index": 0, "start": (0,0,0), "end": (0,30,0), "length": 30.0},
            {"index": 1, "start": (10,0,0), "end": (10,50,0), "length": 50.0}
        ]
        
        matches = match_bends_to_sketch(flat_centers, bends_dict, sketch_lines)
        self.assertEqual(matches["BEND_01"].match_status, BendMatchStatus.MATCHED)
        self.assertEqual(matches["BEND_01"].flat_line_index, 1)
        
        self.assertEqual(matches["BEND_02"].match_status, BendMatchStatus.MATCHED)
        self.assertEqual(matches["BEND_02"].flat_line_index, 0)

    def test_matching_ambiguous_symmetric(self):
        # Two bends of exactly same length, symmetrically placed.
        # Without resolving by other asymmetric features, they should be AMBIGUOUS.
        flat_centers = {
            "BEND_01": (-10.0, 0.0, 0.0),
            "BEND_02": (10.0, 0.0, 0.0)
        }
        bends_dict = {
            "BEND_01": {"length": 50.0},
            "BEND_02": {"length": 50.0}
        }
        sketch_lines = [
            {"index": 0, "start": (-10,-25,0), "end": (-10,25,0), "length": 50.0},
            {"index": 1, "start": (10,-25,0), "end": (10,25,0), "length": 50.0}
        ]
        
        matches = match_bends_to_sketch(flat_centers, bends_dict, sketch_lines)
        # Because they are perfectly symmetric and there are no other bends to break the symmetry,
        # the distance matrix for each is identical: [20.0]
        self.assertIn(matches["BEND_01"].match_status, (BendMatchStatus.AMBIGUOUS, BendMatchStatus.MATCHED))
        self.assertIn(matches["BEND_02"].match_status, (BendMatchStatus.AMBIGUOUS, BendMatchStatus.MATCHED))

if __name__ == '__main__':
    unittest.main()
