import unittest
from smbs_core.Core.bend_transform import BendTransform
from smbs_core.Core.bend_graph import BendRecord, BendGraph
from smbs_core.Core.panel_graph import PanelGraph
from smbs_core.Core.fold_state import FoldState
from smbs_core.Core.state_cache import StateCache
from smbs_core.geometry_backend.backends.mock_backend import MockBackend
from smbs_core.geometry_backend.base import InvalidRotationError

class TestPhase5Kinematics(unittest.TestCase):
    def setUp(self):
        self.backend = MockBackend()
        self.bend_graph = BendGraph()
        self.b1 = BendRecord("BEND_01", "F1", "PA", "PB", (0, 0, 0), (1, 0, 0), 90.0, 2.0, 2.0)
        self.b2 = BendRecord("BEND_02", "F2", "PB", "PC", (0, 10, 0), (1, 0, 0), 45.0, 2.0, 2.0)
        self.bend_graph.add_bend(self.b1)
        self.bend_graph.add_bend(self.b2)

        self.panel_graph = PanelGraph()
        self.panel_graph.add_bend_connection("BEND_01", "PA", "PB")
        self.panel_graph.add_bend_connection("BEND_02", "PB", "PC")

        self.initial_shape = self.backend.make_box(50, 50, 2)

    def test_rotate_only_moving_panel(self):
        moving = self.panel_graph.moving_subtree("BEND_01")
        self.assertEqual(moving, {"PB", "PC"})
        
        rotated = self.backend.rotate_subtree(
            self.initial_shape,
            axis_origin=(0, 0, 0),
            axis_direction=(1, 0, 0),
            signed_angle_deg=90.0,
            moving_panels=moving
        )
        self.assertIsNotNone(rotated)
        self.assertEqual(rotated["last_rotation"], 90.0)
        self.assertEqual(set(rotated["moving_panels"]), {"PB", "PC"})

    def test_fixed_panels_unchanged_and_empty_moving_raises(self):
        with self.assertRaises(InvalidRotationError):
            self.backend.rotate_subtree(
                self.initial_shape,
                axis_origin=(0, 0, 0),
                axis_direction=(1, 0, 0),
                signed_angle_deg=90.0,
                moving_panels=set()
            )

    def test_fold_state_apply_unfold_and_fingerprint(self):
        root_state = FoldState.initial(self.initial_shape, self.bend_graph, self.panel_graph)
        self.assertFalse(root_state.is_goal())
        
        pred_fp = root_state.predict_successor_fingerprint("BEND_01", panel_graph=self.panel_graph)
        
        moving = self.panel_graph.moving_subtree("BEND_01")
        unfolded_shape = self.backend.rotate_subtree(
            root_state.shape,
            axis_origin=(0, 0, 0),
            axis_direction=(1, 0, 0),
            signed_angle_deg=-90.0,
            moving_panels=moving
        )
        next_state = root_state.apply_unfold("BEND_01", unfolded_shape, moving)
        
        self.assertIn("BEND_01", root_state.remaining_bends)
        self.assertNotIn("BEND_01", next_state.remaining_bends)
        self.assertEqual(next_state.fingerprint(), pred_fp)

    def test_state_cache_hits_misses_and_eviction(self):
        cache = StateCache(max_size=2)
        root_state = FoldState.initial(self.initial_shape, self.bend_graph, self.panel_graph)
        fp1 = root_state.fingerprint()
        
        self.assertFalse(cache.contains(fp1))
        cache.put(fp1, {"data": 1})
        self.assertTrue(cache.contains(fp1))
        
        cached_val = cache.get(fp1)
        self.assertEqual(cached_val, {"data": 1})
        self.assertEqual(cache.stats["hits"], 1)

        # Test LRU eviction
        cache.put("key2", {"data": 2})
        cache.put("key3", {"data": 3})  # Exceeds max_size=2, evicts fp1
        
        self.assertFalse(cache.contains(fp1))
        self.assertTrue(cache.contains("key2"))
        self.assertTrue(cache.contains("key3"))

if __name__ == "__main__":
    unittest.main()
