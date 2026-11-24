"""GPU-backed SMBS preview using FreeCAD's Coin3D/Quarter scenegraph.

This viewer is visualization only. It never alters FoldState, FreeCAD Shapes or
native OCC collision geometry. Mesh topology is retained across frames; only
SoCoordinate3 point arrays change for bending panels. Tools are rebuilt only
when the tool configuration changes; punch stroke uses SoTranslation.
"""
from __future__ import annotations

from dataclasses import dataclass

from freecad_smbs.ui.localization import tr
from freecad_smbs.ui.tool_segment_display import (
    native_display_sections, segment_color, section_boundary_edges,
)


def _document_is_open(app, name):
    """Check FreeCAD documents without getDocument's missing-name exception.

    FreeCAD 1.1 macOS raises on App.getDocument("nonexistent") rather
    than returning None. listDocuments() is a mapping of *open* documents
    and can be checked without triggering that exception.
    """
    return bool(name) and str(name) in app.listDocuments()

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
    # A cap is often a nonconvex punch/die cross-section. Coin's SoFaceSet
    # may fill across the concavity (the dark end blocks reported on macOS).
    # Use deterministic ear-clipping triangles derived from the REAL profile.
    # Profiles with holes are not sealed: never falsely cover the openings.
    if face.holes:
        return None
    triangles = None
    if face.kind in ("punch_cap", "die_cap"):
        from freecad_smbs.ui.tool_cap_triangulation import (
            CapTriangulationError, triangulate_tool_cap,
        )
        try:
            triangles = triangulate_tool_cap(face.vertices)
        except CapTriangulationError:
            # Refuse an inaccurate filled surface. The actual sidewalls
            # and the OCC tooling stay unaffected and still display.
            return None

    node = coin.SoSeparator()
    material = coin.SoMaterial()
    color = COLORS.get(face.kind, (0.55, 0.65, 0.75))
    if face.kind.endswith("_seam"):
        color = (0.18, 0.12, 0.12)
        material.emissiveColor.setValue(*color)
    elif face.kind.startswith(("punch_", "die_")):
        color = segment_color(face.kind.split("_", 1)[0], face.section_index)
    material.diffuseColor.setValue(*color)
    coordinates = coin.SoCoordinate3()
    coordinates.point.setValues(0, len(face.vertices), list(face.vertices))
    if triangles is not None:
        facets = coin.SoIndexedFaceSet()
        indices = []
        for triangle in triangles:
            indices.extend(triangle)
            indices.append(-1)
        facets.coordIndex.setValues(0, len(indices), indices)
    else:
        facets = coin.SoLineSet() if face.kind.endswith("_seam") else coin.SoFaceSet()
        facets.numVertices.setValues(0, 1, [len(face.vertices)])
    binding = coin.SoMaterialBinding()
    binding.value = coin.SoMaterialBinding.OVERALL
    node.addChild(binding)
    material.setOverride(True)
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
    except (ImportError, OSError) as exc:
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
                    "Управление порядком гибов доступно на развёртке. "
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

            def reset_camera(self, *, whole_scene=False):
                pass

        return UnavailableCoinViewer(parent)
    try:
        from pivy.quarter import QuarterWidget
    except (ImportError, OSError):
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
            self._native_source_doc = None
            self._native_notice = None
            if self._native:
                layout = QtWidgets.QVBoxLayout(self)
                self._native_notice = QtWidgets.QLabel(
                    tr("Coin3D работает в штатном 3D-окне FreeCAD. Это окно показывает состояние и управление симуляцией; вращение и масштабирование выполняются в 3D-окне детали.")
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
            from freecad_smbs.ui.coin3d_machine import CoinMachineScene
            self._machine = CoinMachineScene(coin)
            self._collision_count = 0
            self._root.addChild(self._machine.root)
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
            """Create a dedicated temporary FreeCAD 3D document for animation.

            Do not insert Coin nodes into the source FCStd scenegraph. Each
            simulation owns its separate native Inventor view and its single
            ephemeral SoSeparator. This does not add model objects or save
            anything to the source or temporary document.
            """
            if not self._native:
                return
            import FreeCAD as App
            import FreeCADGui as Gui
            source_name = str(name or "")
            if not _document_is_open(App, source_name):
                raise RuntimeError("Исходный FCStd документ закрыт")
            if self._native_doc is not None:
                existing = _document_is_open(App, self._native_doc)
                if (self._native_source_doc == source_name
                        and existing
                        and self._native_root is not None):
                    return
                self.detach_document()

            # A normal, empty document creates its own FreeCAD Coin3D MDI
            # window; hidden documents cannot supply an interactive view.
            # Reserve the prefix and avoid overriding any user's document.
            number = 1
            while _document_is_open(App, "SMBS_Animation_{:02d}".format(number)):
                number += 1
            temporary_name = "SMBS_Animation_{:02d}".format(number)
            created = None
            root = None
            try:
                created = App.newDocument(temporary_name)
                temporary_name = str(created.Name)
                gui_doc = Gui.getDocument(temporary_name)
                if gui_doc is None:
                    raise RuntimeError(
                        "FreeCAD не открыл временный 3D-документ"
                    )
                view = gui_doc.activeView()
                if view is None or not hasattr(view, "getSceneGraph"):
                    raise RuntimeError(
                        "Временный документ не имеет Coin3D вида"
                    )
                root = view.getSceneGraph()
                if root is None:
                    raise RuntimeError(
                        "Coin3D граф временного документа недоступен"
                    )
                root.addChild(self._root)
                self._native_root = root
                self._native_view = view
                self._native_doc = temporary_name
                self._native_source_doc = source_name
                self._initial_fit_done = False
                self._native_notice.setText(
                    "3D-анимация открыта в отдельном окне FreeCAD: {}. "
                    "Все движения и настройки остаются в SMBS; "
                    "исходный FCStd не изменяется.".format(temporary_name)
                )
            except Exception:
                if root is not None:
                    try:
                        root.removeChild(self._root)
                    except (RuntimeError, AttributeError, ValueError):
                        pass
                if created is not None:
                    try:
                        App.closeDocument(temporary_name)
                    except (RuntimeError, AttributeError):
                        pass
                raise
            finally:
                # FreeCAD makes newly created documents active. Restore the
                # actual source so subsequent commands and Ctrl+S target FCStd.
                try:
                    if _document_is_open(App, source_name):
                        App.setActiveDocument(source_name)
                except (RuntimeError, AttributeError):
                    pass

        def detach_document(self):
            if not self._native:
                return
            import FreeCAD as App
            name = self._native_doc
            source_name = self._native_source_doc
            native_still_open = bool(name and _document_is_open(App, name))
            # Closing SMBS_Animation_XX from FreeCAD destroys its native
            # scenegraph. Never dereference the old Coin root afterwards:
            # keep the reusable SMBS scene and create a fresh document on
            # the next render instead.
            if self._native_root is not None and native_still_open:
                try:
                    self._native_root.removeChild(self._root)
                except (RuntimeError, AttributeError, ValueError):
                    pass
            self._native_root = None
            self._native_view = None
            self._native_doc = None
            self._native_source_doc = None
            try:
                # Only close a document that we created, never source.
                if name != source_name and native_still_open:
                    App.closeDocument(name)
            finally:
                if _document_is_open(App, source_name):
                    App.setActiveDocument(source_name)

        def show_view(self):
            if self._native and self._native_doc:
                import FreeCAD as App
                if _document_is_open(App, self._native_doc):
                    App.setActiveDocument(self._native_doc)

        def release(self):
            self.detach_document()

        def clear_preview(self):
            self._sheet_root.removeAllChildren()
            self._tool_root.removeAllChildren()
            self._collision_root.removeAllChildren()
            self._collision_count = 0
            self._machine.clear()
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

        def update_tools(self, tooling, ram_z, *, active_bend_length_mm=None, section_preview=None):
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
                round(float((tooling.active_bend_length_mm if active_bend_length_mm is None
                             else active_bend_length_mm) or 0.0), 5)
                if tooling.match_active_bend_length else None,
                tuple(tooling.punch_section_offsets_xm),
                tuple(tooling.die_section_offsets_xm),
                tooling.punch_section_spans_xm,
                tooling.die_section_spans_xm,
                tuple((section_preview or {}).items()),
                round(float(punch_transform.tx), 5),
                tuple(die_transform.to_matrix_tuple()),
            )
            if key != self._tool_key:
                from smbs_core.kinematics2d.physical_validation_25d import _source_path

                base_ram_z = float(tooling.ram_z_mm)
                tool_faces = ()
                native_meshes = ()
                if (tooling.visible and section_preview is None
                        and _source_path(punch) is not None and _source_path(die) is not None):
                    from smbs_core.kinematics2d.mesh_sequence_screen import native_tool_meshes
                    length = (tooling.active_bend_length_mm if active_bend_length_mm is None
                              else active_bend_length_mm)
                    virtual_length = float(length) if tooling.match_active_bend_length else None
                    native_meshes = tuple(native_tool_meshes(tooling, virtual_length))
                    base_ram_z = 0.0
                elif tooling.visible:
                    tool_faces = build_tool_preview_faces(
                        tooling,
                        active_bend_length_mm=active_bend_length_mm,
                        section_preview=section_preview,
                    )
                self._tool_root.removeAllChildren()
                lighting = coin.SoLightModel()
                lighting.model = coin.SoLightModel.BASE_COLOR
                lighting.setOverride(True)
                self._tool_root.addChild(lighting)
                self._punch_motion = coin.SoTranslation()
                punch_root = coin.SoSeparator()
                punch_root.addChild(self._punch_motion)
                die_root = coin.SoSeparator()
                for kind, tool, triangles in native_meshes:
                    root = punch_root if kind == "punch" else die_root
                    for section_index, span, facets in native_display_sections(tool, triangles):
                        group = coin.SoSeparator()
                        material = coin.SoMaterial()
                        material.diffuseColor.setValue(*segment_color(kind, section_index))
                        coordinates = coin.SoCoordinate3()
                        points = [(-y, x, z) for triangle in facets for x, y, z in triangle]
                        coordinates.point.setValues(0, len(points), points)
                        geometry = coin.SoFaceSet()
                        geometry.numVertices.setValues(0, len(facets), [3] * len(facets))
                        binding = coin.SoMaterialBinding()
                        binding.value = coin.SoMaterialBinding.OVERALL
                        group.addChild(binding)
                        material.setOverride(True)
                        group.addChild(material)
                        group.addChild(coordinates)
                        group.addChild(geometry)
                        root.addChild(group)
                        edges = section_boundary_edges(span, facets)
                        if edges:
                            outline = coin.SoSeparator()
                            ink = coin.SoMaterial()
                            ink.diffuseColor.setValue(0.08, 0.08, 0.10)
                            ink.setOverride(True)
                            outline.addChild(ink)
                            style = coin.SoDrawStyle()
                            style.lineWidth = 2
                            outline.addChild(style)
                            coords = coin.SoCoordinate3()
                            vertices = [(-y, x, z) for edge in edges for x, y, z in edge]
                            coords.point.setValues(0, len(vertices), vertices)
                            lines = coin.SoLineSet()
                            lines.numVertices.setValues(0, len(edges), [2] * len(edges))
                            outline.addChild(coords)
                            outline.addChild(lines)
                            root.addChild(outline)
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
                self._tool_base_ram_z = base_ram_z
            self._punch_motion.translation.setValue(
                0.0, 0.0, float(ram_z) - self._tool_base_ram_z
            )

        def update_machine(self, library_root, tooling, ram_z, *, frame_visible,
                           gauge_stations, gauge_contact):
            frame_error = self._machine.update_frame(library_root, tooling, ram_z, frame_visible)
            clamp_error = self._machine.update_clamps(library_root)
            gauge_error = self._machine.update_gauges(library_root, gauge_stations, gauge_contact)
            return tuple(error for error in (frame_error, clamp_error, gauge_error) if error)

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

        def reset_camera(self, *, whole_scene=False):
            """Fit current sheet explicitly, independent of Quarter navigation.

            Quarter.viewAll() can be a no-op if no navigation state machine
            is registered. Fit once after initial mesh, then leave the camera
            completely under user control until the next explicit Fit action.
            """
            points = [
                p for face in self._last_faces for p in face.vertices
            ]
            if whole_scene and not self._native:
                bounds = self._coin.SoGetBoundingBoxAction(self._coin.SbViewportRegion(640, 480))
                bounds.apply(self._root)
                box = bounds.getBoundingBox()
                if not box.isEmpty():
                    points = [box.getMin().getValue(), box.getMax().getValue()]
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
