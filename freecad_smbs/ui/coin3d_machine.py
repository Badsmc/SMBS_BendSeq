"""CAD display layers for the Coin3D simulation scene."""
from __future__ import annotations
from freecad_smbs.ui.localization import tr

import json
from contextlib import contextmanager
from pathlib import Path

from freecad_smbs.resources.tooling_paths import (
    resolve_backgauge_fcstd,
    resolve_machine_frame_fcstd,
)


@contextmanager
def _machine_document(path):
    import FreeCAD as App

    previous = getattr(App.ActiveDocument, "Name", None)
    opened = App.listDocuments()
    path = path.resolve()
    doc = next((document for document in opened.values()
                if document.FileName and Path(document.FileName).resolve() == path), None)
    if doc is None:
        doc = App.openDocument(str(path), True)
    try:
        yield doc
    finally:
        if doc.Name not in opened:
            App.closeDocument(doc.Name)
        if previous and previous in App.listDocuments():
            App.setActiveDocument(previous)


def _global_shape(body):
    shape = body.Shape.copy()
    parent_transform = body.getGlobalPlacement().multiply(body.Placement.inverse())
    shape.Placement = parent_transform.multiply(shape.Placement)
    return shape


def _mount_origin(shape, face_name, normal_z):
    face = shape.getElement(face_name)
    if (getattr(face.Surface, "TypeId", "") != "Part::GeomPlane"
            or float(face.normalAt(0, 0).z) * normal_z < 0.999):
        raise ValueError(f"invalid horizontal mounting face {face_name}")
    return face.CenterOfMass


def _cad_mesh(shapes, point_in_view):
    points = []
    indices = []
    for shape in shapes:
        vertices, facets = shape.tessellate(0.5)
        offset = len(points)
        points.extend(point_in_view(p) for p in vertices)
        for triangle in facets:
            indices.extend(i + offset for i in triangle)
            indices.append(-1)
    if not indices:
        raise ValueError("CAD mesh has no triangles")
    return points, indices


def _mesh_node(coin, mesh, color, transparency=0.0):
    points, indices = mesh
    root = coin.SoSeparator()
    material = coin.SoMaterial()
    material.diffuseColor.setValue(*color)
    material.transparency.setValue(transparency)
    coordinates = coin.SoCoordinate3()
    coordinates.point.setValues(0, len(points), points)
    face_set = coin.SoIndexedFaceSet()
    face_set.coordIndex.setValues(0, len(indices), indices)
    root.addChild(material)
    root.addChild(coordinates)
    root.addChild(face_set)
    return root


def _frame_file_key(library_root):
    """Reload edited CAD/metadata on the next scene update, not just on restart."""
    path = resolve_machine_frame_fcstd(library_root)
    if path is None:
        return (library_root, None)
    def stamp(file):
        try:
            stat = file.stat()
            return stat.st_mtime_ns, stat.st_size
        except OSError:
            return None
    return (library_root, path.resolve(), stamp(path), stamp(path.with_suffix(".tooling.json")))


def _moving_body(doc, bodies, mount):
    # Legacy sidecars incorrectly called the fixed 'up' body the ram.
    # Prefer an explicitly registered moving object; migrate old 'z' assemblies
    # by unique label rather than silently using the old ram_object field.
    name = mount.get("moving_object")
    if name:
        body = doc.getObject(name)
        if body not in bodies:
            raise ValueError("Registered moving object is missing")
        return body
    matches = [body for body in bodies if str(body.Label).strip().casefold() == "z"]
    if len(matches) != 1:
        raise ValueError("Register a unique moving_object (the z layer) in machine_frame.tooling.json")
    return matches[0]


def _moving_mount_face(shape, preferred):
    """Find the lower mounting plane even after a face-number change."""
    bottom = shape.BoundBox.ZMin
    candidates = []
    for index, face in enumerate(shape.Faces, 1):
        if (getattr(face.Surface, "TypeId", "") == "Part::GeomPlane"
                and float(face.normalAt(0, 0).z) < -0.999
                and abs(face.CenterOfMass.z-bottom) < 0.1):
            candidates.append((f"Face{index}", face))
    if not candidates:
        raise ValueError("Moving z layer has no horizontal downward mounting face")
    for name, face in candidates:
        if name == preferred:
            return face
    # Fall back only to an unambiguous largest lower plane.
    candidates.sort(key=lambda item: item[1].Area, reverse=True)
    if len(candidates)>1 and abs(candidates[0][1].Area-candidates[1][1].Area)<1e-6:
        raise ValueError("Ambiguous moving z mounting plane; register moving_face")
    return candidates[0][1]


def load_frame_meshes(library_root):
    path = resolve_machine_frame_fcstd(library_root)
    if path is None:
        raise FileNotFoundError("machine_frame.FCStd")
    sidecar = path.with_suffix(".tooling.json")
    mount = json.loads(sidecar.read_text(encoding="utf-8"))["visual_mount"]
    sign = mount["operator_y_sign"]
    if sign not in (-1, 1):
        raise ValueError("operator_y_sign must be -1 or +1")

    def at_origin(origin):
        # Source Z is up. The same proper Z rotation applies to both X and Y
        # so the open throat faces the operator (+Y_m, negative S in Coin).
        return lambda p: (-sign * (p.y - origin.y),
                          sign * (p.x - origin.x), p.z - origin.z)

    with _machine_document(path) as doc:
        assembly = doc.getObject(mount["assembly_object"])
        bed = doc.getObject(mount["bed_object"])
        bodies = [body for body in assembly.Group
                  if body.TypeId == "PartDesign::Body" and not body.Shape.isNull()]
        ram = _moving_body(doc, bodies, mount)
        if bed not in bodies or bed == ram:
            raise ValueError("bed or moving layer is missing in the registered CAD assembly")
        shapes = {body.Name: _global_shape(body) for body in bodies}
        bed_origin = _mount_origin(shapes[bed.Name], mount["bed_face"], 1)
        face = _moving_mount_face(shapes[ram.Name], mount.get("moving_face", "Face1"))
        ram_origin = face.CenterOfMass
        rest_z = float(ram_origin.z-bed_origin.z)
        common = at_origin(bed_origin)
        # Both fixed and moving meshes use one XY origin. Only the moving
        # mesh's Z is expressed relative to its lower attachment plane.
        # Editing any fixed body, including up, cannot change that datum.
        frame = _cad_mesh((shapes[body.Name] for body in bodies if body != ram), common)
        def moving_point(point):
            x,y,z = common(point)
            return x,y,z-rest_z
        beam = _cad_mesh((shapes[ram.Name],), moving_point)
        ram_width = face.BoundBox.XLength
    return frame, beam, rest_z, float(ram_width)


def load_gauge_mesh(library_root):
    from smbs_core.kinematics2d.backgauge_cad_3d import BackgaugeCAD3D

    path = resolve_backgauge_fcstd(library_root)
    if path is None:
        raise FileNotFoundError("Backgauge.FCStd")
    adapter = BackgaugeCAD3D(library_dir=path.parent)
    shape, = adapter.preview_positioned_shapes(1.0, 0.0)
    # The picked point identifies the contact face. The X/Z station in Coin
    # refers to the centre of that face, regardless of where it was picked.
    centre = shape.Faces[adapter.stop_face_index - 1].CenterOfMass
    return _cad_mesh((shape,),
                     lambda p: (centre.y - p.y, p.x - centre.x, p.z - centre.z))


CLAMP_HEIGHT_MM = 25.0
CLAMP_DEPTH_MM = 50.0


def _virtual_clamps(coin, spans, height, depth):
    row = coin.SoSeparator()
    material = coin.SoMaterial()
    material.diffuseColor.setValue(0.64, 0.68, 0.76)
    row.addChild(material)
    for left, right in spans:
        block = coin.SoSeparator()
        position = coin.SoTranslation()
        position.translation.setValue(0.0, (left + right) / 2, -height / 2)
        cube = coin.SoCube()
        cube.width.setValue(depth)
        cube.height.setValue(right - left)
        cube.depth.setValue(height)
        block.addChild(position)
        block.addChild(cube)
        row.addChild(block)
    return row


class CoinMachineScene:
    def __init__(self, coin):
        self.coin = coin
        self.root = coin.SoSeparator()
        self.frame = coin.SoSwitch()
        self.gauges = coin.SoSwitch()
        self.clamps = coin.SoSwitch()
        self.clamps.setName("SMBS_Clamps")
        self.clamp_key = None
        self.clamp_config_key = None
        self.clamp_spans = ()
        self.clamp_config_error = ""
        self.clamp_height = CLAMP_HEIGHT_MM
        self.clamp_depth = CLAMP_DEPTH_MM
        self.beam_z = 0.0
        self.bed_z = 0.0
        self.last_ram_z = 0.0
        self.ram_mount_width = 0.0
        self.frame.setName("SMBS_MachineFrame")
        self.gauges.setName("SMBS_Backgauges")
        self.root.addChild(self.frame)
        self.root.addChild(self.gauges)
        self.root.addChild(self.clamps)
        self.frame_mount = coin.SoTranslation()
        self.ram_motion = coin.SoTranslation()
        self.frame_key = None
        self.mount_key = None
        self.gauge_key = None
        self.frame_error = ""
        self.gauge_error = ""
        self.rest_z = 0.0
        self.punch_top = None
        self.gauge_mesh = None
        self.stations = ()
        self.fingers = ()
        self.clear()

    def clear(self):
        self.frame.whichChild.setValue(self.coin.SO_SWITCH_NONE)
        self.gauges.whichChild.setValue(self.coin.SO_SWITCH_NONE)
        self.clamps.whichChild.setValue(self.coin.SO_SWITCH_NONE)

    def update_clamps(self, library_root):
        from dataclasses import replace

        from smbs_core.geometry_backend.io.clamp_validator import ClampValidatorError, load_clamp_config
        from smbs_core.kinematics2d.tooling_assembly import clamp_spans_for_centres

        config_path = library_root / "config.json"
        try:
            try:
                stamp = config_path.stat().st_mtime_ns
            except FileNotFoundError:
                stamp = None
            key = (library_root, stamp, self.ram_mount_width)
            if key != self.clamp_config_key:
                self.clamp_config_key = key
                self.clamp_spans = ()
                self.clamp_config_error = ""
                config = load_clamp_config(library_root)
                if not config.centres_mm and self.ram_mount_width > 0:
                    # Unconfigured library: fill the CAD ram with a centred
                    # nominal row for display, independently of the tools.
                    count = int((self.ram_mount_width + config.gap_between_mm)
                                / (config.width_mm + config.gap_between_mm))
                    config = replace(config, clamp_count=count)
                self.clamp_height, self.clamp_depth = config.height_mm, config.depth_mm
                self._position_ram()
                self.clamp_spans = clamp_spans_for_centres(config.centres_mm, config.width_mm)
        except (OSError, ClampValidatorError) as exc:
            self.clamp_config_error = tr("clamps_baec283a", error=exc)
        spans = self.clamp_spans if not self.clamp_config_error else ()
        if not spans:
            self.clamps.whichChild.setValue(self.coin.SO_SWITCH_NONE)
            return self.clamp_config_error
        mesh_key = (spans, self.clamp_height, self.clamp_depth)
        if mesh_key != self.clamp_key:
            self.clamp_key = mesh_key
            self.clamps.removeAllChildren()
            mounted = self.coin.SoSeparator()
            mounted.addChild(self.ram_motion)
            mounted.addChild(_virtual_clamps(self.coin, spans, self.clamp_height, self.clamp_depth))
            self.clamps.addChild(mounted)
        self.clamps.whichChild.setValue(self.coin.SO_SWITCH_ALL)
        return ""

    def _position_ram(self):
        # Absolute placement, never accumulate offsets on successive frames.
        self.beam_z = (self.last_ram_z + self.punch_top + self.clamp_height
                       if self.punch_top is not None else self.bed_z + self.rest_z)
        self.ram_motion.translation.setValue(0.0, 0.0, self.beam_z)

    def update_frame(self, library_root, tooling, ram_z, visible):
        source_key = _frame_file_key(library_root)
        if self.frame_key != source_key:
            self.frame.removeAllChildren()
            self.frame_key = source_key
            self.mount_key = None
            self.rest_z = self.ram_mount_width = 0.0
            try:
                frame_mesh, beam_mesh, self.rest_z, self.ram_mount_width = load_frame_meshes(library_root)
                frame = _mesh_node(self.coin, frame_mesh, (0.44, 0.49, 0.54), 0.47)
                beam = _mesh_node(self.coin, beam_mesh, (0.50, 0.54, 0.60), 0.35)
                for motion, item in ((self.frame_mount, frame), (self.ram_motion, beam)):
                    component = self.coin.SoSeparator()
                    component.addChild(motion)
                    component.addChild(item)
                    self.frame.addChild(component)
            except (OSError, ValueError, RuntimeError, TypeError, AttributeError, KeyError) as exc:
                self.frame_error = tr("machine_89c98c9c", error=exc)
            else:
                self.frame_error = ""
        self.frame.whichChild.setValue(self.coin.SO_SWITCH_ALL if visible and not self.frame_error
                                      else self.coin.SO_SWITCH_NONE)
        key = (id(tooling.punch), id(tooling.die), tooling.aligned,
               tooling.die_machine_transform.to_matrix_tuple())
        if key != self.mount_key:
            die_polygons = tooling.die_polygons_machine()
            punch_polygons = tooling.punch_polygons_machine() if tooling.punch is not None else None
            die_outline = die_polygons[0] if die_polygons else ()
            punch_outline = punch_polygons[0] if punch_polygons else ()
            self.bed_z = min((z for _, z in die_outline), default=0.0)
            self.frame_mount.translation.setValue(0.0, 0.0, self.bed_z)
            self.punch_top = (max(z for _, z in punch_outline) - tooling.ram_z_mm
                              if punch_outline else None)
            self.mount_key = key
        self.last_ram_z = float(ram_z)
        self._position_ram()
        return self.frame_error if visible else ""

    def update_gauges(self, library_root, stations, contact):
        if not stations or contact is None:
            self.gauges.whichChild.setValue(self.coin.SO_SWITCH_NONE)
            return ""
        if self.gauge_key != library_root:
            self.gauges.removeAllChildren()
            self.stations = ()
            self.gauge_key = library_root
            try:
                self.gauge_mesh = _mesh_node(self.coin, load_gauge_mesh(library_root), (0.83, 0.49, 0.19))
            except (OSError, ValueError, RuntimeError, TypeError, AttributeError, KeyError) as exc:
                self.gauge_error = tr("backgauges_9460a8d0", error=exc)
            else:
                self.gauge_error = ""
        if self.gauge_error:
            return self.gauge_error
        if len(self.stations) != len(stations):
            self.gauges.removeAllChildren()
            fingers = []
            for _ in stations:
                motion = self.coin.SoTranslation()
                finger = self.coin.SoSeparator()
                finger.addChild(motion)
                finger.addChild(self.gauge_mesh)
                self.gauges.addChild(finger)
                fingers.append(motion)
            self.fingers = tuple(fingers)
        self.stations = stations
        s, z = contact
        for finger, x in zip(self.fingers, stations):
            finger.translation.setValue(float(s), float(x), float(z))
        self.gauges.whichChild.setValue(self.coin.SO_SWITCH_ALL)
        return ""
