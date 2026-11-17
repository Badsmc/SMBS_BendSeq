"""CAD display layers for the Coin3D simulation scene."""
from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path

from freecad_smbs.resources.tooling_paths import (
    resolve_backgauge_fcstd,
    resolve_clamp_fcstd,
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


def _mesh_node(coin, shapes, point_in_view, color, transparency=0.0):
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


def _load_frame(coin, library_root):
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
        ram = doc.getObject(mount["ram_object"])
        bodies = [body for body in assembly.Group
                  if body.TypeId == "PartDesign::Body" and not body.Shape.isNull()]
        if bed not in bodies or ram not in bodies or bed == ram:
            raise ValueError("bed or ram is missing in the registered CAD assembly")
        shapes = {body.Name: _global_shape(body) for body in bodies}
        bed_origin = _mount_origin(shapes[bed.Name], mount["bed_face"], 1)
        ram_origin = _mount_origin(shapes[ram.Name], mount["ram_face"], -1)
        frame = _mesh_node(coin, (shapes[body.Name] for body in bodies if body != ram),
                           at_origin(bed_origin), (0.44, 0.49, 0.54), 0.47)
        beam = _mesh_node(coin, (shapes[ram.Name],), at_origin(ram_origin),
                          (0.50, 0.54, 0.60), 0.35)
        ram_width = shapes[ram.Name].getElement(mount["ram_face"]).BoundBox.XLength
    return frame, beam, float(ram_origin.z - bed_origin.z), float(ram_width)


def _load_gauge(coin, library_root):
    from smbs_core.kinematics2d.backgauge_cad_3d import BackgaugeCAD3D

    path = resolve_backgauge_fcstd(library_root)
    if path is None:
        raise FileNotFoundError("Backgauge.FCStd")
    adapter = BackgaugeCAD3D(library_dir=path.parent)
    shape, = adapter.preview_positioned_shapes(1.0, 0.0)
    # The picked point identifies the contact face. The X/Z station in Coin
    # refers to the centre of that face, regardless of where it was picked.
    centre = shape.Faces[adapter.stop_face_index - 1].CenterOfMass
    return _mesh_node(coin, (shape,),
                      lambda p: (centre.y - p.y, p.x - centre.x, p.z - centre.z),
                      (0.83, 0.49, 0.19))


def _load_clamp(coin, library_root, spans):
    path = resolve_clamp_fcstd(library_root)
    if path is None:
        raise FileNotFoundError("Clamp.FCStd")
    sidecar = json.loads(path.with_suffix(".tooling.json").read_text(encoding="utf-8"))
    x, y, z = sidecar["datums"]["working_plane"]["origin"]
    with _machine_document(path) as doc:
        bodies = [_global_shape(item) for item in doc.Objects
                  if item.TypeId == "PartDesign::Body" and not item.Shape.isNull()]
        if not bodies:
            raise ValueError("CAD-модель лапки пуста")
        width = max(s.BoundBox.XMax for s in bodies) - min(s.BoundBox.XMin for s in bodies)
        if width <= 0:
            raise ValueError("CAD-модель лапки имеет нулевую длину")
        height = max(s.BoundBox.ZMax for s in bodies) - z
        if height <= 0:
            raise ValueError("CAD-модель лапки расположена ниже рабочей плоскости")
        meshes = {}
        row = coin.SoSeparator()
        for left, right in spans:
            length = right - left
            if length not in meshes:
                meshes[length] = _mesh_node(
                    coin, bodies, lambda p: (y - p.y, (p.x - x) * length / width, p.z - z),
                    (0.64, 0.68, 0.76),
                )
            block = coin.SoSeparator()
            placement = coin.SoTranslation()
            placement.translation.setValue(0.0, (left + right) / 2.0, 0.0)
            block.addChild(placement)
            block.addChild(meshes[length])
            row.addChild(block)
    return row, height


class CoinMachineScene:
    def __init__(self, coin):
        self.coin = coin
        self.root = coin.SoSeparator()
        self.frame = coin.SoSwitch()
        self.gauges = coin.SoSwitch()
        self.clamps = coin.SoSwitch()
        self.clamps.setName("SMBS_Clamps")
        self.clamp_motion = coin.SoTranslation()
        self.clamp_key = None
        self.clamp_config_key = None
        self.clamp_spans = ()
        self.clamp_config_error = ""
        self.clamp_error = ""
        self.clamp_height = 0.0
        self.beam_z = 0.0
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
                self.clamp_spans = clamp_spans_for_centres(config.centres_mm, config.width_mm)
        except (OSError, ClampValidatorError) as exc:
            self.clamp_config_error = f"Лапки: {exc}"
        spans = self.clamp_spans if not self.clamp_config_error else ()
        if not spans:
            self.clamps.whichChild.setValue(self.coin.SO_SWITCH_NONE)
            return self.clamp_config_error
        key = (library_root, spans)
        if key != self.clamp_key:
            self.clamp_key = key
            self.clamps.removeAllChildren()
            self.clamp_error = ""
            try:
                row, self.clamp_height = _load_clamp(self.coin, library_root, spans)
                mounted = self.coin.SoSeparator()
                mounted.addChild(self.clamp_motion)
                mounted.addChild(row)
                self.clamps.addChild(mounted)
            except (OSError, ValueError, RuntimeError, TypeError, AttributeError, KeyError) as exc:
                self.clamp_error = f"Лапки: {exc}"
        if self.clamp_error:
            self.clamps.whichChild.setValue(self.coin.SO_SWITCH_NONE)
            return self.clamp_error
        if self.punch_top is not None:
            # The aligned punch seats against the lower clamp datum.
            clamp_z = self.beam_z
            self.ram_motion.translation.setValue(0.0, 0.0, clamp_z + self.clamp_height)
        else:
            # With the punch absent or hidden, keep clamps attached to the
            # actual mounting surface of the displayed ram.
            clamp_z = self.beam_z - self.clamp_height
        self.clamp_motion.translation.setValue(0.0, 0.0, clamp_z)
        self.clamps.whichChild.setValue(self.coin.SO_SWITCH_ALL)
        return ""

    def update_frame(self, library_root, tooling, ram_z, visible):
        if self.frame_key != library_root:
            self.frame.removeAllChildren()
            self.frame_key = library_root
            self.mount_key = None
            self.rest_z = self.ram_mount_width = 0.0
            try:
                frame, beam, self.rest_z, self.ram_mount_width = _load_frame(self.coin, library_root)
                for motion, item in ((self.frame_mount, frame), (self.ram_motion, beam)):
                    component = self.coin.SoSeparator()
                    component.addChild(motion)
                    component.addChild(item)
                    self.frame.addChild(component)
            except (OSError, ValueError, RuntimeError, TypeError, AttributeError, KeyError) as exc:
                self.frame_error = f"Станок: {exc}"
            else:
                self.frame_error = ""
        self.frame.whichChild.setValue(self.coin.SO_SWITCH_ALL if visible and not self.frame_error
                                      else self.coin.SO_SWITCH_NONE)
        key = (id(tooling.punch), id(tooling.die), tooling.aligned, tooling.visible,
               tooling.die_machine_transform.to_matrix_tuple())
        if key != self.mount_key:
            die_polygons = tooling.die_polygons_machine()
            punch_polygons = tooling.punch_polygons_machine() if tooling.visible else None
            die_outline = die_polygons[0] if die_polygons else ()
            punch_outline = punch_polygons[0] if punch_polygons else ()
            self.bed_z = min((z for _, z in die_outline), default=0.0)
            self.frame_mount.translation.setValue(0.0, 0.0, self.bed_z)
            self.punch_top = (max(z for _, z in punch_outline) - tooling.ram_z_mm
                              if punch_outline else None)
            self.mount_key = key
        self.beam_z = (float(ram_z) + self.punch_top if self.punch_top is not None
                       else self.bed_z + self.rest_z)
        self.ram_motion.translation.setValue(0.0, 0.0, self.beam_z)
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
                self.gauge_mesh = _load_gauge(self.coin, library_root)
            except (OSError, ValueError, RuntimeError, TypeError, AttributeError, KeyError) as exc:
                self.gauge_error = f"Задние упоры: {exc}"
            else:
                self.gauge_error = ""
        if self.gauge_error:
            return self.gauge_error
        if self.stations != stations:
            self.gauges.removeAllChildren()
            self.stations = stations
            fingers = []
            for _ in stations:
                motion = self.coin.SoTranslation()
                finger = self.coin.SoSeparator()
                finger.addChild(motion)
                finger.addChild(self.gauge_mesh)
                self.gauges.addChild(finger)
                fingers.append(motion)
            self.fingers = tuple(fingers)
        s, z = contact
        for finger, x in zip(self.fingers, stations):
            finger.translation.setValue(float(s), float(x), float(z))
        self.gauges.whichChild.setValue(self.coin.SO_SWITCH_ALL)
        return ""
