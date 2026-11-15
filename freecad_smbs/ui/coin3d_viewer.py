"""GPU-backed SMBS preview using FreeCAD's Coin3D/Quarter scenegraph.

This viewer is visualization only. It never alters FoldState, FreeCAD Shapes or
native OCC collision geometry. Mesh topology is retained across frames; only
SoCoordinate3 point arrays change for bending panels. Tools are rebuilt only
when the tool configuration changes; punch stroke uses SoTranslation.
"""
from __future__ import annotations

from dataclasses import dataclass

# Lazily imported from the UI constructor: headless imports remain supported.
COLORS = {
    "panel_top": (0.55, 0.72, 0.84),
    "panel_bottom": (0.43, 0.53, 0.61),
    "panel_edge": (0.38, 0.51, 0.62),
    "zone_inner": (0.91, 0.77, 0.48),
    "zone_outer": (0.86, 0.70, 0.40),
    "zone_end": (0.76, 0.62, 0.38),
    "punch_cap": (0.92, 0.40, 0.34),
    "punch_side": (0.79, 0.28, 0.23),
    "die_cap": (0.40, 0.51, 0.60),
    "die_side": (0.25, 0.36, 0.45),
}


@dataclass
class _FaceNode:
    root: object
    coordinates: object
    topology: object
    material: object
    kind: str
    vertex_count: int


def _face_node(coin, face):
    if len(face.vertices) < 3:
        return None
    # Tool end caps with inner contours must not be shown as solid-filled
    # polygons. Their real inner/outer walls remain visible in this mesh.
    # A later triangulated-cap implementation may fill the annular region.
    if face.holes:
        return None
    node = coin.SoSeparator()
    material = coin.SoMaterial()
    material.diffuseColor.setValue(*COLORS.get(face.kind, (0.55, 0.65, 0.75)))
    coordinates = coin.SoCoordinate3()
    coordinates.point.setValues(0, len(face.vertices), list(face.vertices))
    facets = coin.SoFaceSet()
    facets.numVertices.setValues(0, 1, [len(face.vertices)])
    node.addChild(material)
    node.addChild(coordinates)
    node.addChild(facets)
    return _FaceNode(node, coordinates, facets, material, face.kind,
                     len(face.vertices))


def create_coin_viewer(parent=None):
    """Create a single native Quarter widget, no QGraphicsScene 3D fallback."""
    try:
        from pivy import coin
        from pivy.quarter import QuarterWidget
    except ImportError as exc:
        raise RuntimeError(
            "Для 3D SMBS требуется FreeCAD с Pivy/Quarter (Coin3D). "
            "Проверьте установленную сборку FreeCAD."
        ) from exc

    class CoinPreviewWidget(QuarterWidget):
        def __init__(self, parent=None):
            super().__init__(parent=parent)
            self.setObjectName("SMBS_Coin3D_Viewer")
            self.setMinimumHeight(240)
            self._coin = coin
            self._root = coin.SoSeparator()
            # An explicit camera is owned by this scene, not by the FCStd
            # document or a second FreeCAD 3D view.
            self._camera = coin.SoPerspectiveCamera()
            self._camera.orientation.setValue(coin.SbRotation(
                coin.SbVec3f(0.0, 0.0, -1.0),
                coin.SbVec3f(-1.0, 1.0, -0.7),
            ))
            self._root.addChild(self._camera)
            self._sheet_root = coin.SoSeparator()
            self._tool_root = coin.SoSeparator()
            self._collision_root = coin.SoSeparator()
            self._collision_count = 0
            self._root.addChild(self._sheet_root)
            self._root.addChild(self._tool_root)
            self._root.addChild(self._collision_root)
            self.setSceneGraph(self._root)
            self._sheet_nodes = []
            self._tool_key = None
            self._tool_base_ram_z = 0.0
            self._punch_motion = None
            self._collision_count = 0
            self._initial_fit_done = False
            self._last_sheet_face_count = 0
            self._sheet_topology = None

        def clear_preview(self):
            self._sheet_root.removeAllChildren()
            self._tool_root.removeAllChildren()
            self._collision_root.removeAllChildren()
            self._sheet_nodes.clear()
            self._sheet_topology = None
            self._tool_key = None
            self._punch_motion = None
            self._initial_fit_done = False

        def set_sheet_faces(self, faces, highlighted=()):
            """Update coordinates, not Qt polygons or a painter path."""
            highlighted = set(highlighted)
            # Keep Coin nodes and their topology until the bend mesh structure
            # changes (for example when a different source model is loaded).
            topology = tuple((f.kind, f.identifier, len(f.vertices))
                             for f in faces if len(f.vertices) >= 3)
            if getattr(self, "_sheet_topology", None) != topology:
                self._sheet_root.removeAllChildren()
                self._sheet_nodes = []
                for face in faces:
                    node = _face_node(coin, face)
                    if node is not None:
                        self._sheet_root.addChild(node.root)
                        self._sheet_nodes.append(node)
                self._sheet_topology = topology
            else:
                for node, face in zip(self._sheet_nodes,
                                      (f for f in faces if len(f.vertices) >= 3)):
                    node.coordinates.point.setValues(
                        0, len(face.vertices), list(face.vertices)
                    )
            for node, (kind, ident, _count) in zip(self._sheet_nodes, topology):
                node.material.diffuseColor.setValue(
                    *( (0.91, 0.14, 0.12) if ident in highlighted
                       else COLORS.get(kind, (0.55, 0.65, 0.75)) )
                )
            self._last_sheet_face_count = len(self._sheet_nodes)
            if not self._initial_fit_done and faces:
                self.viewAll()
                self._initial_fit_done = True

        def update_tools(self, tooling, preview_length, ram_z, *, inspection=False):
            from smbs_core.kinematics2d.preview_mesh_3d import (
                build_tool_preview_faces,
            )
            punch = tooling.punch
            die = tooling.die
            # Never use the variable punch Z as part of the cache key.
            # Only punch geometry is driven by the Z stroke.
            punch_transform = tooling.punch_machine_transform
            die_transform = tooling.die_machine_transform
            key = (
                id(punch), id(die), bool(tooling.visible),
                bool(tooling.match_active_bend_length),
                round(float(tooling.active_bend_length_mm or 0.0), 5),
                tuple(tooling.punch_section_offsets_xm),
                tuple(tooling.die_section_offsets_xm),
                round(float(punch_transform.tx), 5),
                tuple(die_transform.to_matrix_tuple()),
                None if preview_length is None else round(float(preview_length), 5),
                bool(inspection),
            )
            if key != self._tool_key:
                tool_faces = build_tool_preview_faces(
                    tooling,
                    preview_punch_length_mm=preview_length,
                    punch_ram_z_mm=ram_z if inspection else None,
                )
                self._tool_root.removeAllChildren()
                self._punch_motion = coin.SoTranslation()
                punch_root = coin.SoSeparator()
                punch_root.addChild(self._punch_motion)
                die_root = coin.SoSeparator()
                for face in tool_faces:
                    node = _face_node(coin, face)
                    if node is None:
                        continue
                    if face.kind.startswith("punch"):
                        punch_root.addChild(node.root)
                    else:
                        die_root.addChild(node.root)
                self._tool_root.addChild(die_root)
                self._tool_root.addChild(punch_root)
                self._tool_key = key
                self._tool_base_ram_z = (
                    float(ram_z) if inspection
                    else float(tooling.ram_z_mm)
                )
            self._punch_motion.translation.setValue(
                0.0, 0.0, float(ram_z) - self._tool_base_ram_z
            )

        def set_collision_boxes(self, boxes):
            if not boxes and self._collision_count == 0:
                return
            self._collision_root.removeAllChildren()
            self._collision_count = len(boxes)
            if not boxes:
                return
            for bound in boxes:
                # Native OCC AABB (machine XYZ) -> preview (-Ym, Xm, Zm).
                lo = (bound["xmin"], bound["ymin"], bound["zmin"])
                hi = (bound["xmax"], bound["ymax"], bound["zmax"])
                corners = [
                    (-y, x, z)
                    for x in (lo[0], hi[0])
                    for y in (lo[1], hi[1])
                    for z in (lo[2], hi[2])
                ]
                vertices = []
                for i in range(8):
                    for j in range(i + 1, 8):
                        if sum(corners[i][k] != corners[j][k]
                               for k in range(3)) == 1:
                            vertices.extend((corners[i], corners[j]))
                root = coin.SoSeparator()
                color = coin.SoBaseColor()
                color.rgb.setValue(1.0, 0.08, 0.08)
                coords = coin.SoCoordinate3()
                coords.point.setValues(0, len(vertices), vertices)
                segments = coin.SoLineSet()
                segments.numVertices.setValues(
                    0, len(vertices) // 2, [2] * (len(vertices) // 2)
                )
                root.addChild(color)
                root.addChild(coords)
                root.addChild(segments)
                self._collision_root.addChild(root)

        def reset_camera(self):
            self.viewAll()

    return CoinPreviewWidget(parent)
