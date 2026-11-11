"""FreeCAD/OpenCASCADE adapter for sampled 3D rear-stop interference.

This imports no FreeCAD modules at module load time. A true source BRep from
Backgauge.FCStd, placed using the verified stop_face datum, is compared with
rigid panel and sampled bend-zone solids derived from authoritative FoldState.
It never treats an absent 3D check as CLEAR and never certifies swept paths.
"""
from __future__ import annotations

from pathlib import Path
import json
import math


class BackgaugeCADError(ValueError):
    pass


def _v(app, point):
    return app.Vector(*[float(x) for x in point])


def _closed_face(part, app, points):
    if len(points) < 3:
        raise BackgaugeCADError("sheet polygon has fewer than three vertices")
    vertices = [_v(app, p) for p in points]
    if (vertices[0] - vertices[-1]).Length > 1e-7:
        vertices.append(vertices[0])
    face = part.Face(part.makePolygon(vertices))
    if face.isNull() or face.Area < 1e-7:
        raise BackgaugeCADError("sheet polygon face is null or degenerate")
    return face


class BackgaugeCAD3D:
    """One explicitly mounted real CAD stop at an X_m station (default 0).

    The source stop-face outward normal -X is mapped to machine +Y_m, i.e.
    toward the operator/left (-s). The CAD body extends toward rear/right.
    A second finger requires a second independently configured instance;
    this class does not invent its station or machine axis range.
    """

    def __init__(self, library_dir=None, *, station_xm_mm=0.0,
                 second_station_xm_mm=None):
        import FreeCAD as App

        self.app = App
        from . import backgauge_2d  # core boundary; no synthetic CAD fallback
        del backgauge_2d
        if not math.isfinite(float(station_xm_mm)):
            raise BackgaugeCADError("nonfinite X_m gauge station")
        self.station_xm_mm = float(station_xm_mm)
        if second_station_xm_mm is not None:
            if (not math.isfinite(float(second_station_xm_mm))
                    or abs(float(second_station_xm_mm)
                           - self.station_xm_mm) < 1e-6):
                raise BackgaugeCADError("invalid second CAD stop X station")
        self.second_station_xm_mm = (
            float(second_station_xm_mm)
            if second_station_xm_mm is not None else None
        )
        directory = (
            Path(library_dir).resolve() if library_dir is not None
            else Path(__file__).resolve().parents[2] / "toollibrary" / "Backgauge"
        )
        path = directory / "Backgauge.FCStd"
        metadata = directory / "Backgauge.tooling.json"
        if not path.is_file() or not metadata.is_file():
            raise BackgaugeCADError("Backgauge CAD and tooling.json required")
        raw = json.loads(metadata.read_text(encoding="utf-8"))
        if raw.get("kind") != "backgauge" or raw.get("source") != path.name:
            raise BackgaugeCADError("Backgauge kind/source mismatch")
        datum = raw.get("datums", {}).get("stop_face", {})
        if datum.get("kind") != "plane":
            raise BackgaugeCADError("Real stop_face plane datum required")
        point = datum.get("origin")
        normal = datum.get("normal")
        if (not isinstance(point, list) or not isinstance(normal, list)
                or len(point) != 3 or len(normal) != 3
                or any(not math.isfinite(float(v)) for v in point + normal)):
            raise BackgaugeCADError("Invalid contact datum coordinates")
        if math.dist(normal, (-1, 0, 0)) > 1e-4:
            raise BackgaugeCADError(
                "unsupported source stop_face normal; expected (-1,0,0)"
            )
        tol = float(datum.get("tolerance_mm", 0.05))
        if not math.isfinite(tol) or tol <= 0:
            raise BackgaugeCADError("invalid CAD face tolerance")

        import Part
        active = getattr(App.ActiveDocument, "Name", None)
        doc = None
        try:
            doc = App.openDocument(str(path), True)
            bodies = [o for o in doc.Objects
                      if o.TypeId == "PartDesign::Body"
                      and hasattr(o, "Shape") and not o.Shape.isNull()
                      and o.Shape.isValid() and len(o.Shape.Solids) == 1]
            if len(bodies) != 1:
                raise BackgaugeCADError(
                    "Expected exactly one valid solid PartDesign Body in Backgauge.FCStd"
                )
            shape = bodies[0].Shape.copy()
            origin = _v(App, point)
            target_faces = []
            for idx, face in enumerate(shape.Faces, 1):
                if not isinstance(face.Surface, Part.Plane):
                    continue
                bounds = face.ParameterRange
                outward = face.normalAt(
                    0.5*(bounds[0]+bounds[1]),
                    0.5*(bounds[2]+bounds[3]),
                )
                if outward.dot(_v(App, normal)) < 0.999:
                    continue
                distance, _, _ = face.distToShape(Part.Vertex(origin))
                if distance <= tol:
                    target_faces.append(idx)
            if len(target_faces) != 1:
                raise BackgaugeCADError(
                    "CAD contact face not uniquely verified: " + str(target_faces)
                )
            self.source_shape = shape
            self.stop_origin = origin
            self.stop_face_index = target_faces[0]
        finally:
            if doc is not None:
                App.closeDocument(doc.Name)
            if active and active in App.listDocuments():
                App.setActiveDocument(active)
        # Source -X normal rotates to machine +Y, operator/left.
        self.source_to_machine_rotation = App.Rotation(App.Vector(0,0,1), -90)
        self._placement_cache = {}

    def positioned_shape(self, contact_s_mm, contact_z_mm):
        """Return primary real CAD stop at its configured X station."""
        return self._positioned_at(
            contact_s_mm, contact_z_mm, self.station_xm_mm
        )

    def positioned_shapes(self, contact_s_mm, contact_z_mm):
        """Return 1 or 2 independent, explicitly mounted CAD stop solids."""
        shapes = [self.positioned_shape(contact_s_mm, contact_z_mm)]
        if self.second_station_xm_mm is not None:
            shapes.append(self._positioned_at(
                contact_s_mm, contact_z_mm, self.second_station_xm_mm
            ))
            # Reject finger bodies physically overlapping one another.
            ga, sb = shapes[0].BoundBox, shapes[1].BoundBox
            if not (ga.XMax < sb.XMin or sb.XMax < ga.XMin or
                    ga.YMax < sb.YMin or sb.YMax < ga.YMin or
                    ga.ZMax < sb.ZMin or sb.ZMax < ga.ZMin):
                common = shapes[0].common(shapes[1])
                if not common.isNull() and float(common.Volume) > 1e-6:
                    # Same rigid CAD model at each X station. The projected
                    # width along machine X is the minimum separation that
                    # guarantees non-overlapping X extents (regardless of
                    # any concavity elsewhere in the true BRep).
                    actual_width = float(shapes[0].BoundBox.XLength)
                    actual_gap = abs(
                        self.second_station_xm_mm - self.station_xm_mm
                    )
                    raise BackgaugeCADError(
                        "two_backgauge_cad_bodies_intersect: "
                        "X1={:.2f} mm, X2={:.2f} mm, "
                        "separation={:.2f} mm, "
                        "actual CAD X-width={:.2f} mm, "
                        "minimum guaranteed non-overlap separation={:.2f} mm. "
                        "Uncheck 'Добавить второй CAD упор' to use one, "
                        "or set real machine X positions with sufficient "
                        "clearance. No safety margin is assumed."
                        .format(
                            self.station_xm_mm,
                            self.second_station_xm_mm,
                            actual_gap, actual_width, actual_width,
                        )
                    )
        return tuple(shapes)

    def _positioned_at(self, contact_s_mm, contact_z_mm, station_xm_mm):
        """Position source Face3 at exact machine stop contact point."""
        if not all(math.isfinite(float(v)) for v in
                   (contact_s_mm, contact_z_mm)):
            raise BackgaugeCADError("invalid stop-face target")
        if contact_s_mm <= 0:
            raise BackgaugeCADError("rear stop face must be right of bend axis")
        key = (float(contact_s_mm), float(contact_z_mm),
               float(station_xm_mm))
        if key not in self._placement_cache:
            app = self.app
            target = app.Vector(
                float(station_xm_mm), -float(contact_s_mm), float(contact_z_mm)
            )
            translation = (
                target - self.source_to_machine_rotation.multVec(self.stop_origin)
            )
            shp = self.source_shape.copy()
            shp.Placement = app.Placement(
                translation, self.source_to_machine_rotation
            ).multiply(shp.Placement)
            # Backgauge has a stepped/nonconvex profile: some CAD faces
            # can project in front of the selected contact patch. Do NOT
            # reject by the global bounding box; evaluate actual 3D solids.
            if not shp.isValid() or not shp.Solids:
                raise BackgaugeCADError("placed CAD stop is invalid")
            self._placement_cache[key] = shp
        return self._placement_cache[key]

    def _panel_solids(self, state, panel_vertices_xy, placement, thickness_mm):
        import Part
        app = self.app
        if not math.isfinite(thickness_mm) or thickness_mm <= 0:
            raise BackgaugeCADError("authoritative sheet thickness missing")
        solids = []
        for panel_id in sorted(state.graph.panels):
            vertices = panel_vertices_xy.get(panel_id)
            if vertices is None or len(vertices) < 3:
                raise BackgaugeCADError(
                    "panel vertices missing: " + str(panel_id)
                )
            tr = state.transforms[panel_id]
            top = [placement.apply(tr.point((x,y,0.0)))
                   for x,y in vertices]
            bot = placement.apply(tr.point(
                (vertices[0][0], vertices[0][1], -thickness_mm)
            ))
            extrusion = _v(app, bot) - _v(app, top[0])
            solid = _closed_face(Part, app, top).extrude(extrusion)
            if solid.isNull() or not solid.isValid() or not solid.Solids:
                raise BackgaugeCADError(
                    "cannot build rigid panel solid: " + str(panel_id)
                )
            solids.append(("panel:" + str(panel_id), solid))
        return solids

    def _bend_solids(self, state, placement):
        """Create sampled finite-radius bend-zone solids, not arbitrary boxes."""
        import Part
        app = self.app
        solids = []
        for bid, bend in sorted(state.bends.items()):
            zone = bend.bend_zone
            if zone is None:
                raise BackgaugeCADError(
                    "bend zone geometry missing: " + str(bid)
                )
            profile = state.zone_section(bid, segments=24)
            inner, outer = profile["inner"], profile["outer"]
            if len(inner) < 2 or len(outer) < 2:
                raise BackgaugeCADError(
                    "bend section insufficient: " + str(bid)
                )
            # Make a cross-section at one end of the bend axis, then
            # extrude along its actually transformed 3D axis.
            ax0, ax1 = zone.axis_xy
            side = 1.0 if bend.moving_left else -1.0
            ux = zone.left_xy[0] * side
            uy = zone.left_xy[1] * side
            parent = state.transforms[bend.fixed_panel]

            def to_machine(pair):
                s, z = pair
                return placement.apply(parent.point((
                    ax0[0] + ux*s, ax0[1] + uy*s, z
                )))
            section = list(inner) + list(reversed(outer))
            poly = [to_machine(pair) for pair in section]
            start_axis = placement.apply(parent.point(
                (ax0[0], ax0[1], 0.0)
            ))
            end_axis = placement.apply(parent.point(
                (ax1[0], ax1[1], 0.0)
            ))
            direction = _v(app, end_axis) - _v(app, start_axis)
            solid = _closed_face(Part, app, poly).extrude(direction)
            if solid.isNull() or not solid.isValid() or not solid.Solids:
                raise BackgaugeCADError("invalid finite-radius zone:" + bid)
            solids.append(("bend_zone:" + str(bid), solid))
        return solids

    def check_phase(self, state, panel_vertices_xy, placement, *,
                    active_bend_id, contact_s_mm, contact_z_mm,
                    tolerance_mm=0.02):
        """Return material intersections against the real positioned CAD BRep.

        CLEAR is sampled-only (no inter-frame sweep, whole-press geometry or
        multiple fingers); uncertainty/errors propagate to UNVERIFIED.
        """
        if not math.isfinite(float(tolerance_mm)) or tolerance_mm <= 0:
            raise BackgaugeCADError("invalid CAD collision tolerance")
        bend = state.bends[active_bend_id]
        if bend.bend_zone is None:
            raise BackgaugeCADError("active thickness unavailable")
        thickness = float(bend.bend_zone.thickness_mm)
        gauges = self.positioned_shapes(contact_s_mm, contact_z_mm)
        components = (
            self._panel_solids(
                state, panel_vertices_xy, placement, thickness
            ) + self._bend_solids(state, placement)
        )
        for finger_no, gauge in enumerate(gauges, 1):
            for label, solid in components:
                ga, sb = gauge.BoundBox, solid.BoundBox
                if (ga.XMax < sb.XMin or sb.XMax < ga.XMin or
                        ga.YMax < sb.YMin or sb.YMax < ga.YMin or
                        ga.ZMax < sb.ZMin or sb.ZMax < ga.ZMin):
                    continue
                common = gauge.common(solid)
                if common.isNull():
                    continue
                volume = float(common.Volume)
                if not math.isfinite(volume):
                    raise BackgaugeCADError(
                        "nonfinite CAD intersection volume"
                    )
                if volume > max(1e-6, float(tolerance_mm)**3):
                    return {
                        "status": "INTERFERENCE",
                        "component": "finger{}:{}".format(finger_no, label),
                        "volume_mm3": volume,
                        "evidence": "SAMPLED_REAL_CAD_STOP_VS_FOLDSTATE_SOLIDS",
                    }
        return {
            "status": "SAMPLED_CLEAR_OR_TOUCH",
            "component": "",
            "volume_mm3": 0.0,
            "evidence": "NOT_SWEPT_OR_MANUFACTURING_CERTIFIED",
        }
