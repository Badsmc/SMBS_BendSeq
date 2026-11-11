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

    def resolve_contact_for_bend(self, state, panel_vertices_xy, bend_id,
                                 backgauge_model, *,
                                 desired_midpoint_xm=0.0,
                                 tolerance_mm=0.02):
        """Resolve edge at the actual finger X station and find OCC contact.

        The bend pose stays fixed at the die; only the gauge's rear
        translational axis is varied. Never move the bend line to force
        contact. Returns the actual front-face target and diagnostics.
        """
        from .backgauge_2d import BackgaugePosition2D
        from .workpiece_placement_3d import WorkpiecePlacement3D

        target, _depth, source = backgauge_model.resolve_for_state(
            state, panel_vertices_xy, bend_id,
            desired_midpoint_xm=float(desired_midpoint_xm),
            cutting_plane_xm=self.station_xm_mm,
        )
        if self.second_station_xm_mm is not None:
            other, _, _ = backgauge_model.resolve_for_state(
                state, panel_vertices_xy, bend_id,
                desired_midpoint_xm=float(desired_midpoint_xm),
                cutting_plane_xm=self.second_station_xm_mm,
            )
            if (abs(other.s_mm - target.s_mm) > 0.05 or
                    abs(other.z_mm - target.z_mm) > 0.05):
                raise BackgaugeCADError(
                    "TWO_FINGERS_REQUIRE_INDEPENDENT_TARGETS:"
                    "fixed_panel_edge_diff_s_mm={:.4f} diff_z_mm={:.4f}"
                    .format(other.s_mm-target.s_mm,
                            other.z_mm-target.z_mm)
                )
        if backgauge_model.automatic:
            # Automatic 2D position is a rectangle centre. Subtract only
            # half of its width to obtain the intended CAD stop-face s.
            hint_s = target.s_mm - backgauge_model.width_s_mm / 2
        else:
            hint_s = target.s_mm - backgauge_model.width_s_mm / 2
        zone = state.bends[bend_id].bend_zone
        if zone is None:
            raise BackgaugeCADError("no authoritative bend zone")
        pose = WorkpiecePlacement3D.compute_placement(
            state, bend_id,
            desired_midpoint_xm=float(desired_midpoint_xm),
            support_plane_zm=float(zone.outer_radius_mm),
            align_bisector=False,
            align_bend_arc_center=True,
            rear_panel_vertices_xy=panel_vertices_xy,
        )
        result = self.solve_first_contact(
            state, panel_vertices_xy, pose,
            active_bend_id=bend_id,
            hint_s_mm=hint_s, contact_z_mm=target.z_mm,
            tolerance_mm=tolerance_mm,
        )
        actual_s = float(result["contact_s_mm"])
        actual_z = float(result["contact_z_mm"])
        display = BackgaugePosition2D(
            actual_s + backgauge_model.width_s_mm / 2, actual_z
        )
        return (
            display, actual_s,
            source + (
                "|CAD_FIRST_CONTACT:stop_s_mm={:.4f}:shift_rear_mm={:.4f}:"
                "actual_faces={}:datum_face={}:{}"
            ).format(
                actual_s, result["shift_rearward_mm"],
                ",".join(map(str, result["actual_faces"])),
                self.stop_face_index, result["note"],
            ), result
        )

    def solve_first_contact(self, state, panel_vertices_xy, placement, *,
                            active_bend_id, hint_s_mm, contact_z_mm,
                            tolerance_mm=0.02):
        """Locate first nonpenetrating CAD stop contact without moving the bend.

        The actual CAD stop is withdrawn towards machine +s if the suggested
        Face3 station overlaps the workpiece. A bounded OCC binary search
        returns a nonpenetrating placement, then *independently* verifies that
        the designated fixed panel is physically contacted by the CAD body.
        Contact with another panel or an unverified source face is not a
        substitute for the intended stop; such cases raise an explicit
        BackgaugeCADError and must NOT be treated as collision-free.
        """
        import Part

        if (not math.isfinite(float(hint_s_mm))
                or not math.isfinite(float(contact_z_mm))
                or float(hint_s_mm) <= 0.0
                or not 0.001 <= float(tolerance_mm) <= 0.2):
            raise BackgaugeCADError("invalid CAD contact search arguments")
        bend = state.bends[active_bend_id]
        if bend.bend_zone is None:
            raise BackgaugeCADError("active BendZone required for CAD contact")
        thickness_mm = float(bend.bend_zone.thickness_mm)
        components = (
            self._panel_solids(state, panel_vertices_xy, placement, thickness_mm)
            + self._bend_solids(state, placement)
        )
        fixed_id = "panel:" + str(bend.fixed_panel)
        fixed = next((solid for label, solid in components
                      if label == fixed_id), None)
        if fixed is None:
            raise BackgaugeCADError(
                "fixed panel CAD solid missing:" + fixed_id
            )
        volume_tol = max(1e-6, float(tolerance_mm)**3)

        def probe(s_mm):
            shapes = self.positioned_shapes(s_mm, contact_z_mm)
            for index, gauge in enumerate(shapes, 1):
                ga = gauge.BoundBox
                for label, sheet in components:
                    sb = sheet.BoundBox
                    if (ga.XMax < sb.XMin or sb.XMax < ga.XMin or
                            ga.YMax < sb.YMin or sb.YMax < ga.YMin or
                            ga.ZMax < sb.ZMin or sb.ZMax < ga.ZMin):
                        continue
                    common = gauge.common(sheet)
                    if not common.isNull():
                        volume = float(common.Volume)
                        if not math.isfinite(volume):
                            raise BackgaugeCADError(
                                "nonfinite OCC CAD contact volume"
                            )
                        if volume > volume_tol:
                            return (index, label, volume), shapes
            return None, shapes

        hint_s = float(hint_s_mm)
        interference, shapes = probe(hint_s)
        if interference is None:
            # Existing gauge location may already be at first contact.
            nearest = min(float(gauge.distToShape(fixed)[0])
                          for gauge in shapes)
            if nearest <= float(tolerance_mm) + 0.03:
                return self._qualified_contact(
                    shapes, fixed, hint_s, contact_z_mm, hint_s,
                    tolerance_mm, fixed_id,
                )

        # Calculate a provably Y-disjoint rearward bound from actual CAD
        # bounding boxes of ALL sheet solids, not an arbitrary travel limit.
        # This is purely geometric bracketing, NOT machine axis qualification.
        sheet_rearmost_s = max(
            -float(shape.BoundBox.YMin) for _, shape in components
        )
        forward_protrusion = max(
            float(shape.BoundBox.YMax) + hint_s for shape in shapes
        )
        far_s = max(hint_s + 1.0,
                    sheet_rearmost_s + forward_protrusion + 1.0)
        if not math.isfinite(far_s):
            raise BackgaugeCADError("nonfinite geometric search bound")

        if interference is not None:
            low_s = hint_s
            high_s = far_s
            far_interference, _ = probe(high_s)
            if far_interference is not None:
                raise BackgaugeCADError(
                    "gauge did not retract to Y-disjoint position"
                )
        else:
            # Hint is clear but may not touch any panel: approach from
            # operator side only until real material contact first occurs.
            high_s = hint_s
            low_s = max(0.001, hint_s - 1.0)
            step = 1.0
            for _ in range(18):
                collision, _ = probe(low_s)
                if collision is not None:
                    break
                if low_s <= 0.001:
                    raise BackgaugeCADError(
                        "NO_PHYSICAL_CONTACT_IN_REAR_GAUGE_TRAVEL:"
                        "fixed_panel=" + fixed_id
                    )
                step *= 2.0
                low_s = max(0.001, hint_s - step)
            else:
                raise BackgaugeCADError(
                    "contact bracketing exhausted for fixed panel"
                )

        # Bisection preserves an OCC-clear upper bound and an interfering
        # lower bound. Do not accept an intersecting final placement.
        for _ in range(28):
            if high_s - low_s <= max(0.005, float(tolerance_mm) / 2.0):
                break
            mid_s = (low_s + high_s) / 2.0
            collision, _ = probe(mid_s)
            if collision is None:
                high_s = mid_s
            else:
                low_s = mid_s
        collision, shapes = probe(high_s)
        if collision is not None:
            raise BackgaugeCADError(
                "OCC contact solver failed to find nonpenetrating position"
            )
        return self._qualified_contact(
            shapes, fixed, high_s, contact_z_mm, hint_s,
            tolerance_mm, fixed_id,
        )

    def _qualified_contact(self, gauges, fixed, contact_s, contact_z,
                           hint_s, tolerance_mm, fixed_id):
        """Verify contact against the required panel AND real oriented face."""
        import Part

        matched = []
        gap_limit = max(0.05, float(tolerance_mm) + 0.03)
        for finger_index, gauge in enumerate(gauges, 1):
            distance = float(gauge.distToShape(fixed)[0])
            if distance > gap_limit:
                raise BackgaugeCADError(
                    "FIRST_CONTACT_WRONG_PANEL_OR_NO_FIXED_CONTACT:"
                    "finger={} fixed={} distance_mm={:.4f} "
                    "stop_s_mm={:.4f} hint_s_mm={:.4f}".format(
                        finger_index, fixed_id, distance,
                        contact_s, hint_s,
                    )
                )
            face_hits = []
            for face_idx, face in enumerate(gauge.Faces, 1):
                if not isinstance(face.Surface, Part.Plane):
                    continue
                b = face.ParameterRange
                normal = face.normalAt(
                    0.5 * (b[0] + b[1]), 0.5 * (b[2] + b[3])
                )
                # Operator-facing surface of the stop points +Y_m.
                if normal.dot(self.app.Vector(0, 1, 0)) < 0.999:
                    continue
                gap = float(face.distToShape(fixed)[0])
                if gap <= gap_limit:
                    face_hits.append((face_idx, gap))
            if not face_hits:
                raise BackgaugeCADError(
                    "NO_OPERATOR_FACING_STOP_FACE_CONTACT:"
                    "finger={} fixed={} stop_s_mm={:.4f}".format(
                        finger_index, fixed_id, contact_s
                    )
                )
            matched.append(min(face_hits, key=lambda item: item[1]))

        actual_face_indices = [i for i, _gap in matched]
        return {
            "contact_s_mm": float(contact_s),
            "contact_z_mm": float(contact_z),
            "hint_s_mm": float(hint_s),
            "shift_rearward_mm": float(contact_s - hint_s),
            "actual_faces": actual_face_indices,
            "datum_face_indices": [self.stop_face_index] * len(gauges),
            "face3_is_contact": all(
                index == self.stop_face_index for index in actual_face_indices
            ),
            "status": "FIRST_NONPENETRATING_CAD_FACE_CONTACT_SAMPLED",
            "note": (
                "CONTACT_FACE3" if all(
                    index == self.stop_face_index for index in actual_face_indices
                ) else "CONTACT_ON_DIFFERENT_CAD_FACE_NEEDS_TOOLING_REVIEW"
            ),
            "swept_path_verified": False,
            "axis_limits_verified": False,
        }

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
                    # The selected Face3 is not necessarily the physical
                    # foremost surface of this nonconvex part. Report the
                    # actual body penetration toward the operator so a
                    # genuine overlap can be distinguished from incorrect
                    # stop-datum selection or mounting.
                    contact_machine_y = -float(contact_s_mm)
                    protrusion_operator_mm = max(
                        0.0, float(ga.YMax) - contact_machine_y
                    )
                    return {
                        "status": "INTERFERENCE",
                        "component": "finger{}:{}".format(finger_no, label),
                        "volume_mm3": volume,
                        "evidence": "SAMPLED_REAL_CAD_STOP_VS_FOLDSTATE_SOLIDS",
                        "station_xm_mm": (
                            self.station_xm_mm if finger_no == 1
                            else self.second_station_xm_mm
                        ),
                        "stop_face_s_mm": float(contact_s_mm),
                        "body_protrusion_ahead_of_stop_face_mm": (
                            protrusion_operator_mm
                        ),
                        "finger_bbox_xyz_mm": [
                            float(ga.XMin), float(ga.XMax),
                            float(ga.YMin), float(ga.YMax),
                            float(ga.ZMin), float(ga.ZMax),
                        ],
                        "sheet_component_bbox_xyz_mm": [
                            float(sb.XMin), float(sb.XMax),
                            float(sb.YMin), float(sb.YMax),
                            float(sb.ZMin), float(sb.ZMax),
                        ],
                    }
        return {
            "status": "SAMPLED_CLEAR_OR_TOUCH",
            "component": "",
            "volume_mm3": 0.0,
            "evidence": "NOT_SWEPT_OR_MANUFACTURING_CERTIFIED",
        }
