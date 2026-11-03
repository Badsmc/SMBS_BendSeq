"""Validated rooted panel topology for a SheetMetal flat-pattern bend graph.

Pure Python. Edges are undirected until a user explicitly chooses a fixed
panel. A connected tree is required; ambiguous or cyclic graphs fail closed.
"""
from __future__ import annotations
from dataclasses import dataclass
from math import isfinite

class PanelGraphError(ValueError):
    pass

@dataclass(frozen=True)
class GraphEdge:
    bend_id: str
    panel_a: str
    panel_b: str
    angle_deg: float
    axis_xyz: tuple
    bend_zone: object = None

@dataclass(frozen=True)
class RootedBend:
    bend_id: str
    fixed_panel: str
    moving_panel: str
    moving_subtree: tuple
    angle_deg: float
    axis_xyz: tuple
    bend_zone: object = None
    moving_left: bool = True

class PanelGraph:
    def __init__(self, panel_ids, edges):
        self.panels = tuple(panel_ids)
        self.edges = tuple(edges)
        if not self.panels or len(set(self.panels)) != len(self.panels):
            raise PanelGraphError("Panel IDs must be unique and nonempty")
        if len(self.edges) != len(self.panels) - 1:
            raise PanelGraphError("Expected a tree: edges must equal panels minus one")
        self.adj = {p: [] for p in self.panels}
        bends = set()
        for e in self.edges:
            if not e.bend_id or e.bend_id in bends:
                raise PanelGraphError("Duplicate or empty bend ID")
            bends.add(e.bend_id)
            if e.panel_a not in self.adj or e.panel_b not in self.adj or e.panel_a == e.panel_b:
                raise PanelGraphError("Invalid panel endpoints for " + e.bend_id)
            if not isfinite(e.angle_deg) or not 0 < abs(e.angle_deg) < 180:
                raise PanelGraphError("Invalid signed bend angle for " + e.bend_id)
            if len(e.axis_xyz) != 2 or any(len(p) != 3 for p in e.axis_xyz):
                raise PanelGraphError("Axis requires two 3D endpoints")
            if not all(isfinite(float(v)) for p in e.axis_xyz for v in p):
                raise PanelGraphError("Nonfinite axis")
            a, b = e.axis_xyz
            if sum((b[i]-a[i])**2 for i in range(3)) <= 1e-16:
                raise PanelGraphError("Degenerate bend axis")
            self.adj[e.panel_a].append((e.panel_b, e))
            self.adj[e.panel_b].append((e.panel_a, e))
        visited = set()
        def walk(p, parent):
            if p in visited:
                raise PanelGraphError("Cycle in panel graph")
            visited.add(p)
            for neighbor, _ in self.adj[p]:
                if neighbor != parent:
                    walk(neighbor, p)
        walk(self.panels[0], None)
        if len(visited) != len(self.panels):
            raise PanelGraphError("Disconnected panel graph")

    def rooted(self, fixed_panel):
        """Orient each bend away from fixed_panel; return subtree per bend."""
        if fixed_panel not in self.adj:
            raise PanelGraphError("Unknown fixed panel: " + str(fixed_panel))
        parent = {fixed_panel: None}
        order = [fixed_panel]
        edge_for = {}
        for p in order:
            for neighbor, edge in self.adj[p]:
                if neighbor not in parent:
                    parent[neighbor] = p
                    edge_for[neighbor] = edge
                    order.append(neighbor)
        children = {p: [] for p in self.panels}
        for p, par in parent.items():
            if par is not None:
                children[par].append(p)
        def subtree(p):
            return (p,) + tuple(n for c in children[p] for n in subtree(c))
        return tuple(RootedBend(
            edge_for[p].bend_id, parent[p], p, subtree(p),
            edge_for[p].angle_deg, edge_for[p].axis_xyz,
            edge_for[p].bend_zone,
            p == edge_for[p].panel_a,
        ) for p in order[1:])

def graph_from_panel_build(result):
    """Adapter from FreeCAD PanelBuildResult without importing FreeCAD."""
    panels = [p.panel_id for p in result.panels]
    edges = [GraphEdge(c.bend_id, c.panel_a, c.panel_b, c.angle_deg,
                       ((c.axis_xy[0][0], c.axis_xy[0][1], 0.0),
                        (c.axis_xy[1][0], c.axis_xy[1][1], 0.0)),
                       getattr(c, "bend_zone", None))
             for c in result.connections]
    return PanelGraph(panels, edges)
