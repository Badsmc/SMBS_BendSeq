import unittest
from typing import Dict, List, Set, Any

def compute_moving_panels_for_bends(
    panels: Dict[str, Dict[str, Any]],
    bends: Dict[str, Dict[str, Any]],
    root_panel_id: str
) -> Dict[str, Set[str]]:
    """
    Computes the set of moving panels for each bend in a tree topology graph.
    In a rooted tree, removing an edge (bend) partitions the graph into two components:
    1. The stationary component containing root_panel_id.
    2. The moving subtree hanging behind the bend (away from root).

    Note: In Task 1.0.1b-3a, this subset logic is geometrically equivalent to the
    per-branch extraction and rotation implemented in SheetMetalFolder.fold_tree_from_flat_pattern.
    The generalized recursive BFS/DFS moving panel engine will be formalized in Task 1.0.1b-3c.
    """
    # 1. Build adjacency list: panel -> list of (neighbor_panel, bend_id)
    bend_to_panels: Dict[str, List[str]] = {}
    for pid, p in panels.items():
        for bid in p.get("adjacent_bend_ids", []):
            bend_to_panels.setdefault(bid, []).append(pid)

    adj: Dict[str, List[tuple]] = {pid: [] for pid in panels}
    for bid, pids in bend_to_panels.items():
        if len(pids) == 2:
            p1, p2 = pids
            adj[p1].append((p2, bid))
            adj[p2].append((p1, bid))

    moving_panels: Dict[str, Set[str]] = {}

    for bid, pids in bend_to_panels.items():
        if len(pids) != 2:
            continue
        p1, p2 = pids
        # Find which side of the cut edge does NOT contain root_panel_id
        # Traverse component from p1 without crossing bid
        def get_component(start_panel: str, blocked_bend: str) -> Set[str]:
            visited = set([start_panel])
            queue = [start_panel]
            while queue:
                curr = queue.pop(0)
                for neighbor, edge_bend in adj.get(curr, []):
                    if edge_bend == blocked_bend:
                        continue
                    if neighbor not in visited:
                        visited.add(neighbor)
                        queue.append(neighbor)
            return visited

        comp_p1 = get_component(p1, bid)
        comp_p2 = get_component(p2, bid)

        if root_panel_id in comp_p1:
            moving_panels[bid] = comp_p2
        else:
            moving_panels[bid] = comp_p1

    return moving_panels


class TestMovingPanelsFromGraph(unittest.TestCase):
    """
    Unit test (§3, Task 1.0.1b-3a):
    Tests computation of moving panel subsets for each bend in a synthetic tree graph.
    Structure: 1 root hub ('HUB') + 3 leaf panels ('LEAF_1', 'LEAF_2', 'LEAF_3').
    Also tests a 2-level branch: HUB -> WALL_1 -> FLANGE_1.
    """

    def test_synthetic_tree_star_topology(self):
        # 1-hub + 3 leaves
        panels = {
            "HUB": {"panel_id": "HUB", "adjacent_bend_ids": ["B1", "B2", "B3"]},
            "LEAF_1": {"panel_id": "LEAF_1", "adjacent_bend_ids": ["B1"]},
            "LEAF_2": {"panel_id": "LEAF_2", "adjacent_bend_ids": ["B2"]},
            "LEAF_3": {"panel_id": "LEAF_3", "adjacent_bend_ids": ["B3"]},
        }
        bends = {
            "B1": {"bend_id": "B1"},
            "B2": {"bend_id": "B2"},
            "B3": {"bend_id": "B3"},
        }

        moving = compute_moving_panels_for_bends(panels, bends, root_panel_id="HUB")

        self.assertEqual(moving["B1"], {"LEAF_1"})
        self.assertEqual(moving["B2"], {"LEAF_2"})
        self.assertEqual(moving["B3"], {"LEAF_3"})

    def test_multi_level_tree_branch_topology(self):
        # HUB connected via B1 to WALL_1, which is connected via B2 to FLANGE_1
        # HUB connected via B3 to WALL_2
        panels = {
            "HUB": {"panel_id": "HUB", "adjacent_bend_ids": ["B1", "B3"]},
            "WALL_1": {"panel_id": "WALL_1", "adjacent_bend_ids": ["B1", "B2"]},
            "FLANGE_1": {"panel_id": "FLANGE_1", "adjacent_bend_ids": ["B2"]},
            "WALL_2": {"panel_id": "WALL_2", "adjacent_bend_ids": ["B3"]},
        }
        bends = {
            "B1": {"bend_id": "B1"},
            "B2": {"bend_id": "B2"},
            "B3": {"bend_id": "B3"},
        }

        moving = compute_moving_panels_for_bends(panels, bends, root_panel_id="HUB")

        # Removing B1 moves both WALL_1 and FLANGE_1 (subtree)
        self.assertEqual(moving["B1"], {"WALL_1", "FLANGE_1"})
        # Removing B2 moves only FLANGE_1
        self.assertEqual(moving["B2"], {"FLANGE_1"})
        # Removing B3 moves only WALL_2
        self.assertEqual(moving["B3"], {"WALL_2"})

if __name__ == "__main__":
    unittest.main()
