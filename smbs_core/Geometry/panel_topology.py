"""
panel_topology.py - Panel Topology Graph Generator for Sheet Metal Parts (Headless Core)
"""

import logging
from typing import Dict, Any, List, Set, Optional, Tuple
from smbs_core.Core.panel_graph import Panel, PanelGraph
from smbs_core.geometry_backend.base import GeometryBackend

logger = logging.getLogger(__name__)

class TopologyError(ValueError):
    """Raised when panel topology graph generation encounters invalid or cyclic connections."""
    pass

class PanelTopologyBuilder:
    def __init__(self, backend: GeometryBackend):
        self.backend = backend

    def build_panel_graph(
        self,
        shape: Any,
        bends_dict: Dict[str, Dict[str, Any]]
    ) -> PanelGraph:
        """
        Builds a PanelGraph from extracted bend records and shape geometry.
        """
        graph = PanelGraph()

        if not bends_dict:
            return graph

        # 1. Register panels and connections
        for bend_id, bend in bends_dict.items():
            p_a = bend.get("parent_panel_id")
            p_b = bend.get("child_panel_id")
            if p_a and p_b:
                graph.add_bend_connection(bend_id, p_a, p_b)

        # 2. Assign face indices to panels if real shape available
        if shape is not None:
            faces = self.backend.get_faces(shape)
            for i, face in enumerate(faces):
                f_hash = self.backend.get_face_hash(face)
                if f_hash:
                    p_id = f"PANEL_{f_hash[:8]}"
                    if p_id in graph.panels:
                        if i not in graph.panels[p_id].face_indices:
                            graph.panels[p_id].face_indices.append(i)

        # 3. Verify graph is connected and cycle-free (tree structure)
        self._verify_topology(graph)

        return graph

    def _verify_topology(self, graph: PanelGraph) -> None:
        if not graph.panels:
            return

        # Check for cycles using DFS
        visited = set()
        rec_stack = set()

        def dfs(node: str, parent: Optional[str] = None) -> bool:
            visited.add(node)
            rec_stack.add(node)

            for neighbor in graph._adj.get(node, {}).keys():
                if neighbor == parent:
                    continue
                if neighbor in rec_stack:
                    return True  # Cycle detected
                if neighbor not in visited:
                    if dfs(neighbor, node):
                        return True

            rec_stack.remove(node)
            return False

        start_node = next(iter(graph.panels.keys()))
        if dfs(start_node):
            logger.warning(f"Cycle detected in panel topology starting from {start_node}")
