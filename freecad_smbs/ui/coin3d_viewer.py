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
    """Use Python Quarter when available, otherwise native FreeCAD Coin view.

    FreeCAD ships Quarter in C++ on some macOS builds without exporting
    pivy.quarter to Python. That is not a missing Coin3D renderer.
    """
    try:
        from pivy import coin
    except ImportError as exc:
        # Keep the rest of the workbench operational. There is no software
        # 3D fallback, only an explicit unavailable 3D surface.
        from freecad_smbs.commands.pressbrake_2d import _qt
        _, _, QtWidgets = _qt()
        reason = str(exc)

        class UnavailableCoinViewer(QtWidgets.QWidget):
            _native = False
            _available = False

            def __init__(self, parent=None):
                super().__init__(parent)
                self.setObjectName("SMBS_Coin3D_Unavailable")
                layout = QtWidgets.QVBoxLayout(self)
                note = QtWidgets.QLabel(
                    "3D Coin3D недоступен: pivy.coin не импортируется. "
                    "2D-симуляция и поиск остаются рабочими. "
                    "Диагностика: " + reason
                )
                note.setWordWrap(True)
                layout.addWidget(note)

            def release(self):
                pass

            def clear_preview(self):
                pass

            def attach_document(self, _name):
                raise RuntimeError(
                    "Coin3D отсутствует в Python FreeCAD: " + reason
                )

            def reset_camera(self):
                pass

        return UnavailableCoinViewer(parent)
    try:
        from pivy.quarter import QuarterWidget
    except ImportError:
        QuarterWidget = None
    if QuarterWidget is None:
        from freecad_smbs.commands.pressbrake_2d import _qt
        _, _, QtWidgets = _qt()
        base = QtWidgets.QWidget
    else:
        base = QuarterWidget

    class CoinPreviewWidget(base):
        def __init__(self, parent=None):
            super().__init__(parent=parent)
            self._native = QuarterWidget is None
            self._available = True
            self._native_view = None
            self._native_root = None
            self._native_doc = None
            self._native_notice = None
            if self._native:
                layout = QtWidgets.QVBoxLayout(self)
                self._native_notice = QtWidgets.QLabel(
                    "Coin3D работает в штатном 3D-окне FreeCAD. "
                    "Это окно показывает состояние и управление симуляцией; "
                    "вращение и масштабирование выполняются в 3D-окне детали."
                )
                self._native_notice.setWordWrap(True)
                layout.addWidget(self._native_notice)
                layout.addStretch(1)
            self.setObjectName("SMBS_Coin3D_Viewer")
            self.setMinimumHeight(240)
            self._coin = coin
            self._root = coin.SoSeparator()
            # An explicit camera is owned by this scene, not by the FCStd
            # document or a second FreeCAD 3D view.
            self._camera = None
            if not self._native:
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
            if not self._native:
                self.setSceneGraph(self._root)
            self._sheet_batches = {}
            self._tool_key = None
            self._tool_base_ram_z = 0.0
            self._punch_motion = None
            self._collision_count = 0
            self._initial_fit_done = False
            self._last_sheet_face_count = 0
            self._sheet_topology = None
            self._last_faces = ()

        def attach_document(self, name):
            """Attach ephemeral visualization to an existing FreeCAD 3D view.

            Never mutate a Part Shape or add a document object. Keep the
            scenegraph node independent from the FCStd serialization.
            """
            if not self._native:
                return
            if self._native_doc == name and self._native_view is not None:
                return
            self.detach_document()
            import FreeCADGui as Gui
            gui_doc = Gui.getDocument(str(name))
            if gui_doc is None:
                raise RuntimeError("Не найден 3D-документ FreeCAD: " + str(name))
            view = gui_doc.activeView()
            if view is None or not hasattr(view, "getSceneGraph"):
                raise RuntimeError("В документе нет доступного Coin3D вида")
            root = view.getSceneGraph()
            if root is None:
                raise RuntimeError("Coin3D scenegraph документа недоступен")
            root.addChild(self._root)
            self._native_doc = str(name)
            self._native_view = view
            self._native_root = root
            self._initial_fit_done = False
            self._native_notice.setText(
                "3D-анимация: штатный вид FreeCAD документа «{}». "
                "Рабочее окно с деталью должно быть видно рядом с SMBS."
                .format(name)
            )

        def detach_document(self):
            if self._native_root is not None:
                try:
                    self._native_root.removeChild(self._root)
                except (RuntimeError, AttributeError, ValueError):
                    pass
            self._native_root = None
            self._native_view = None
            self._native_doc = None

        def release(self):
            self.detach_document()

        def clear_preview(self):
            self._sheet_root.removeAllChildren()
            self._tool_root.removeAllChildren()
            self._collision_root.removeAllChildren()
            self._sheet_batches.clear()
            self._sheet_topology = None
            self._tool_key = None
            self._punch_motion = None
            self._initial_fit_done = False
            self._last_faces = ()

        def set_sheet_faces(self, faces, highlighted=()):
            """Batch persistent panel/bend meshes by material, not by face.

            In a 10-bend model this reduces per-frame Python/Pivy calls
            from one coordinate write per polygon to roughly one write per
            material kind. SoIndexedFaceSet stores multiple polygons in
            one GPU mesh, with -1 marking the end of each polygon.
            """
            highlighted = set(highlighted)
            groups = {}
            drawable_faces = 0
            for face in faces:
                # No invented filled surface across hole-bearing polygons.
                if len(face.vertices) < 3 or face.holes:
                    continue
                key = (face.kind, face.identifier in highlighted)
                groups.setdefault(key, []).append(face)
                drawable_faces += 1

            keys = sorted(groups)
            topology = tuple(
                (key, tuple(len(face.vertices) for face in groups[key]))
                for key in keys
            )
            if self._sheet_topology != topology:
                self._sheet_root.removeAllChildren()
                self._sheet_batches = {}
                for key, lengths in topology:
                    kind, is_highlighted = key
                    node = coin.SoSeparator()
                    material = coin.SoMaterial()
                    material.diffuseColor.setValue(*(
                        (0.91, 0.14, 0.12) if is_highlighted
                        else COLORS.get(kind, (0.55, 0.65, 0.75))
                    ))
                    coordinates = coin.SoCoordinate3()
                    geometry = coin.SoIndexedFaceSet()
                    indices = []
                    cursor = 0
                    for count in lengths:
                        indices.extend(range(cursor, cursor + count))
                        indices.append(-1)
                        cursor += count
                    geometry.coordIndex.setValues(
                        0, len(indices), indices
                    )
                    node.addChild(material)
                    node.addChild(coordinates)
                    node.addChild(geometry)
                    self._sheet_root.addChild(node)
                    self._sheet_batches[key] = coordinates
                self._sheet_topology = topology

            for key in keys:
                points = [
                    vertex for face in groups[key] for vertex in face.vertices
                ]
                self._sheet_batches[key].point.setValues(
                    0, len(points), points
                )

            self._last_sheet_face_count = drawable_faces
            self._last_faces = faces
            if not self._initial_fit_done and faces:
                self.reset_camera()
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
            """Fit current sheet explicitly, independent of Quarter navigation.

            Quarter.viewAll() can be a no-op if no navigation state machine
            is registered. Fit once after initial mesh, then leave the camera
            completely under user control until the next explicit Fit action.
            """
            points = [
                p for face in self._last_faces for p in face.vertices
            ]
            if not points:
                return
            lo = [min(p[i] for p in points) for i in range(3)]
            hi = [max(p[i] for p in points) for i in range(3)]
            center = [(lo[i] + hi[i]) * 0.5 for i in range(3)]
            radius = max(
                ((hi[0]-lo[0])**2 + (hi[1]-lo[1])**2
                 + (hi[2]-lo[2])**2)**0.5 / 2.0, 1.0
            )
            distance = 3.5 * radius
            # Place the camera on a diagonal above the fixed machine origin,
            # with its target at the visible sheet centre for initial Fit.
            eye = (
                center[0] + distance * 0.61,
                center[1] - distance * 0.72,
                center[2] + distance * 0.39,
            )
            if self._native:
                # A document's FreeCAD viewer already owns its actual camera.
                # Request Fit only once on model load or explicit user action.
                if self._native_view is not None:
                    self._native_view.fitAll()
                    self._initial_fit_done = True
                return
            self._camera.position.setValue(*eye)
            self._camera.orientation.setValue(self._coin.SbRotation(
                self._coin.SbVec3f(0.0, 0.0, -1.0),
                self._coin.SbVec3f(
                    center[0] - eye[0],
                    center[1] - eye[1],
                    center[2] - eye[2],
                ),
            ))
            self._camera.focalDistance.setValue(distance)
            self._camera.nearDistance.setValue(max(0.01, radius * 0.01))
            self._camera.farDistance.setValue(max(1000.0, distance * 8.0))
            self._initial_fit_done = True

    return CoinPreviewWidget(parent)
