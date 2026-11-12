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
                 second_station_xm_mm=None, trace_path=None):
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
        from .backgauge_cad_datum import (
            resolve_stop_face, StopDatumError, z_rotation_to_operator_deg,
        )

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
            selected_body = bodies[0]
            try:
                selected_index, origin, outward = resolve_stop_face(
                    doc, selected_body, datum, App, Part
                )
            except StopDatumError as exc:
                raise BackgaugeCADError(
                    "SELECTED_BACKGAUGE_FACE_UNVERIFIED:"
                    + str(exc)
                ) from exc
            self.source_shape = selected_body.Shape.copy()
            self.stop_origin = origin
            self.stop_source_normal = outward
            self.stop_source_object = str(datum["source_object"])
            self.stop_source_subelement = str(datum["subelement"])
            self.stop_face_index = selected_index
            self.stop_rotation_z_deg = z_rotation_to_operator_deg(outward)
        finally:
            if doc is not None:
                App.closeDocument(doc.Name)
            if active and active in App.listDocuments():
                App.setActiveDocument(active)
        # Align the actual Pad.Face6 normal with operator-facing +Y while
        # preserving world Z-up; never guess the orientation from Body.Face3.
        self.source_to_machine_rotation = App.Rotation(
            App.Vector(0, 0, 1), self.stop_rotation_z_deg
        )
        mapped_normal = self.source_to_machine_rotation.multVec(
            self.stop_source_normal
        )
        if mapped_normal.dot(App.Vector(0, 1, 0)) < .999:
            raise BackgaugeCADError(
                "selected source face cannot align to operator-facing +Y"
            )
        self._placement_cache = {}
        self._trace_writer = None
        if trace_path is not None:
            from .backgauge_cad_trace import (
                BackgaugeCADTrace, bbox_xyz, point_xyz,
            )
            self._trace_writer = BackgaugeCADTrace(trace_path)
            face_info = []
            for index, face in enumerate(self.source_shape.Faces, 1):
                if not isinstance(face.Surface, Part.Plane):
                    continue
                u0, u1, v0, v1 = face.ParameterRange
                normal = face.normalAt((u0 + u1) / 2, (v0 + v1) / 2)
                center = face.CenterOfMass
                face_info.append({
                    "face": "Face{}".format(index),
                    "area_mm2": round(float(face.Area), 6),
                    "center_source_xyz_mm": point_xyz(center),
                    "normal_source_xyz": point_xyz(normal),
                    "bbox_source_xyz_mm": bbox_xyz(face),
                    "normal_machine_xyz": point_xyz(
                        self.source_to_machine_rotation.multVec(normal)
                    ),
                })
            self._trace_writer.emit(
                "SOURCE_CAD_VERIFIED",
                cad_path=str(path),
                datum_path=str(metadata),
                stop_face="Face{}".format(self.stop_face_index),
                selected_source_object=self.stop_source_object,
                selected_source_subelement=self.stop_source_subelement,
                stop_face_origin_source_xyz_mm=point_xyz(self.stop_origin),
                stop_face_normal_source_xyz=point_xyz(self.stop_source_normal),
                stop_rotation_about_z_deg=self.stop_rotation_z_deg,
                stop_face_normal_machine_xyz=point_xyz(mapped_normal),
                first_station_xm_mm=self.station_xm_mm,
                second_station_xm_mm=self.second_station_xm_mm,
                source_body_bbox_xyz_mm=bbox_xyz(self.source_shape),
                source_solid_volume_mm3=round(float(self.source_shape.Volume), 6),
                planar_faces=face_info,
            )

    def _trace(self, event, **fields):
        writer = getattr(self, "_trace_writer", None)
        if writer is not None:
            writer.emit(event, **fields)

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

    def preview_positioned_shapes(self, contact_s_mm, contact_z_mm):
        """Place real FCStd finger solids for display, WITHOUT collision tests.

        No common(), distToShape(), stop-face contact search, overlap
        rejection or other manufacturing feasibility logic is invoked.
        """
        shapes = [self._positioned_at(
            contact_s_mm, contact_z_mm, self.station_xm_mm,
        )]
        if self.second_station_xm_mm is not None:
            shapes.append(self._positioned_at(
                contact_s_mm, contact_z_mm, self.second_station_xm_mm,
            ))
        return tuple(shapes)

    def solve_accessible_rear_contact(self, state, panel_vertices_xy,
                                      placement, *, active_bend_id,
                                      tolerance_mm=0.02,
                                      z_min_mm=-20.0, z_max_mm=20.0):
        """Find an external, physically reachable panel edge at the mounted X.

        This is a sampled *static* CAD setup solver. Unlike fixed_panel,
        which defines bend kinematics, the stop may contact any exposed
        rear-facing sheet edge. Generate depths from actual placed panel
        solids, then verify Pad.Face6-to-panel contact AND BRep clearance
        against every panel and finite bend zone. Never assume that a BBox
        coincidence is a valid contact, never omit the 200mm rear stop body.
        Machine-axis limits and transfer sweeps remain UNVERIFIED.
        """
        import Part
        if (not math.isfinite(float(tolerance_mm)) or
                not 0.001 <= float(tolerance_mm) <= 0.2):
            raise BackgaugeCADError("invalid external contact tolerance")
        bend = state.bends[active_bend_id]
        if bend.bend_zone is None:
            raise BackgaugeCADError("authoritative bend thickness missing")
        if self.second_station_xm_mm is not None:
            # Two X stations generally contact distinct external boundaries
            # and need independently driven depth / height. No such axis
            # model is available, so do not manufacture a shared setup.
            raise BackgaugeCADError(
                "TWO_CAD_FINGERS_REQUIRE_INDEPENDENT_REAR_EDGE_SOLVER"
            )
        thickness = float(bend.bend_zone.thickness_mm)
        components = (
            self._panel_solids(state, panel_vertices_xy, placement, thickness)
            + self._bend_solids(state, placement)
        )
        panels = [(name, solid) for name, solid in components
                  if name.startswith("panel:")]
        if not panels:
            raise BackgaugeCADError("no 3D panel solids for rear stop")
        volume_limit = max(1e-6, float(tolerance_mm) ** 3)
        gap_limit = max(0.05, float(tolerance_mm) + 0.03)
        fixed_id = "panel:" + str(bend.fixed_panel)
        # Stable ordering: the physical backgauge approaches the *outer*
        # rear boundary first, not the internal edge of fixed_panel.
        options = sorted(
            ((-float(part.BoundBox.YMin), name, part)
             for name, part in panels
             if math.isfinite(float(part.BoundBox.YMin)) and
             -float(part.BoundBox.YMin) > 0.001),
            key=lambda item: (-item[0], item[1]),
        )
        from .backgauge_contact_height import (
            contact_height_candidates, rear_edge_at_face_height,
        )
        from .machine_section_3d import compute_machine_cutting_plane_section
        if (not math.isfinite(float(z_min_mm))
                or not math.isfinite(float(z_max_mm))
                or z_min_mm >= z_max_mm):
            raise BackgaugeCADError("invalid configured backgauge Z travel")
        attempted = []
        solutions = []
        # The target Z is the actual datum origin (Pad.Face6), NOT the
        # midpoint of the sheet panel. The face spans z+relative offsets.
        datum_at_zero = self.positioned_shapes(1.0, 0.0)[0]
        datum_face = datum_at_zero.Faces[self.stop_face_index - 1]
        dz0 = float(datum_face.BoundBox.ZMin)
        dz1 = float(datum_face.BoundBox.ZMax)
        # Probe the real face's centre and quarter-width X positions.  The
        # 2.5D sections only generate candidate S values; actual Part checks
        # are still required to verify contact and body clearance.
        from .backgauge_contact_height import working_face_section_stations
        face_stations = working_face_section_stations(
            datum_face.BoundBox.XMin, datum_face.BoundBox.XMax,
            self.station_xm_mm,
        )
        station_sections = []
        for station_xm in face_stations:
            try:
                cut = compute_machine_cutting_plane_section(
                    state, panel_vertices_xy, placement,
                    active_bend_id=active_bend_id,
                    segments=24, target_xm=station_xm,
                )
                section_panels = {
                    "panel:" + str(bend.fixed_panel): cut.get("fixed_polygon"),
                    "panel:" + str(bend.moving_panel): cut.get("moving_polygon"),
                }
                section_panels.update({
                    "panel:" + str(pid): poly
                    for pid, poly in cut.get("other_panel_polygons", ())
                })
                station_sections.append((station_xm, section_panels))
            except (ValueError, TypeError, KeyError, AttributeError) as exc:
                # A missing section leaves candidate contact UNVERIFIED.
                # It may call for a bounded CAD fallback, never a hard prune.
                self._trace(
                    "EXTERNAL_REAR_SECTION_UNVERIFIED",
                    bend_id=str(active_bend_id),
                    station_xm_mm=float(station_xm),
                    reason=type(exc).__name__ + ":" + str(exc),
                )
        # Limit expensive OCC confirmation trials; geometric prefilter below
        # always runs first. This is NOT proof that other setups are impossible.
        max_occ_trials = 24
        max_solutions = 4
        occ_trials = 0
        for bbox_depth, panel_id, panel in options:
            if occ_trials >= max_occ_trials or len(solutions) >= max_solutions:
                break
            heights = contact_height_candidates(
                float(panel.BoundBox.ZMin), float(panel.BoundBox.ZMax),
                dz0, dz1, z_min_mm, z_max_mm,
            )
            if not heights:
                item = {
                    "panel": panel_id,
                    "depth_s_mm": round(bbox_depth, 6),
                    "station_xm_mm": float(self.station_xm_mm),
                    "candidate_z_mm": None,
                    "allowed_z_mm": [float(z_min_mm), float(z_max_mm)],
                    "rejection": "BACKGAUGE_Z_OUT_OF_RANGE",
                    "blocking_component": None,
                    "overlap_mm3": 0.0,
                    "face_gap_mm": None,
                }
                attempted.append(item)
                self._trace("EXTERNAL_REAR_CONTACT_TRIAL",
                            bend_id=str(active_bend_id), **item)
                continue
            # Use only section-derived targets when actual local geometry
            # yields candidates. The global CAD BBox fallback is considered
            # ONLY when none of the inspected X sections resolves a target.
            # This eliminates the low-yield duplicate fallback trials.
            trial_targets = []
            for z_candidate in heights:
                for station_xm, panels_at_x in station_sections:
                    section_poly = panels_at_x.get(panel_id) or ()
                    local_depth = (
                        rear_edge_at_face_height(
                            section_poly, z_candidate + dz0, z_candidate + dz1,
                        ) if section_poly else None
                    )
                    if local_depth is not None and local_depth > 0.001:
                        if not any(
                            abs(local_depth - old_depth) < 0.02
                            and abs(z_candidate - old_z) < 0.02
                            for old_depth, old_z, _source in trial_targets
                        ):
                            trial_targets.append((
                                float(local_depth), z_candidate, "2D_SECTION",
                            ))
            if not trial_targets:
                # Inconclusive 2.5D cross-sections cannot exclude physical
                # contact between the sampled X planes. Limit the expensive
                # fallback to one Z proposal per panel.
                trial_targets.append((
                    bbox_depth, heights[0], "CAD_BBOX_FALLBACK",
                ))
            self._trace(
                "EXTERNAL_REAR_PREFILTER_SUMMARY",
                bend_id=str(active_bend_id),
                panel=panel_id,
                section_stations_xm_mm=list(face_stations),
                proposed_2d_targets=sum(
                    source == "2D_SECTION" for _s, _z, source in trial_targets
                ),
                proposed_cad_fallbacks=sum(
                    source == "CAD_BBOX_FALLBACK"
                    for _s, _z, source in trial_targets
                ),
            )
            # One confirmed placement per panel before checking a distinct
            # panel. Probe a few heights on each before spending the entire
            # CAD budget on one unsuitable tall panel.
            panel_trials = 0
            for depth, z, depth_source in trial_targets:
                if panel_trials >= 3:
                    break
                panel_trials += 1
                if occ_trials >= max_occ_trials:
                    break
                occ_trials += 1
                gauge = self.positioned_shapes(depth, z)[0]
                face = gauge.Faces[self.stop_face_index - 1]
                u0, u1, v0, v1 = face.ParameterRange
                n = face.normalAt((u0 + u1)/2, (v0 + v1)/2)
                if n.dot(self.app.Vector(0, 1, 0)) < 0.999:
                    raise BackgaugeCADError(
                        "SELECTED_WORKING_FACE_NOT_OPERATOR_FACING"
                    )
                # Only an AABB exclusion, never proof of contact.
                fbb, pbb = face.BoundBox, panel.BoundBox
                if (fbb.XMax < pbb.XMin - gap_limit or
                        pbb.XMax < fbb.XMin - gap_limit or
                        fbb.ZMax < pbb.ZMin - gap_limit or
                        pbb.ZMax < fbb.ZMin - gap_limit):
                    gap, reason = float("inf"), "WORKING_FACE_OUTSIDE_PANEL_SPAN"
                else:
                    gap = float(face.distToShape(panel)[0])
                    if not math.isfinite(gap):
                        raise BackgaugeCADError(
                            "nonfinite selected face contact gap"
                        )
                    reason = (
                        "WORKING_FACE_DOES_NOT_REACH_EDGE"
                        if gap > gap_limit else None
                    )
                blocked_by = None
                overlap_volume = 0.0
                if reason is None:
                    gbb = gauge.BoundBox
                    for name, solid in components:
                        sb = solid.BoundBox
                        if (gbb.XMax < sb.XMin or sb.XMax < gbb.XMin or
                                gbb.YMax < sb.YMin or sb.YMax < gbb.YMin or
                                gbb.ZMax < sb.ZMin or sb.ZMax < gbb.ZMin):
                            continue
                        common = gauge.common(solid)
                        if common.isNull():
                            continue
                        overlap = float(common.Volume)
                        if not math.isfinite(overlap):
                            raise BackgaugeCADError(
                                "nonfinite OCC external stop overlap"
                            )
                        if overlap > volume_limit:
                            reason, blocked_by, overlap_volume = (
                                "CAD_VOLUME_INTERFERENCE", name, overlap,
                            )
                            break
                item = {
                    "panel": panel_id,
                    "depth_s_mm": round(depth, 6),
                    "station_xm_mm": float(self.station_xm_mm),
                    "candidate_z_mm": round(z, 6),
                    "candidate_depth_source": depth_source,
                    "allowed_z_mm": [float(z_min_mm), float(z_max_mm)],
                    "face_gap_mm": (
                        round(gap, 6) if math.isfinite(gap) else None
                    ),
                    "rejection": reason,
                    "blocking_component": blocked_by,
                    "overlap_mm3": round(overlap_volume, 6),
                }
                attempted.append(item)
                self._trace("EXTERNAL_REAR_CONTACT_TRIAL",
                            bend_id=str(active_bend_id), **item)
                if reason is not None:
                    continue
                solutions.append({
                    "contact_s_mm": float(depth),
                    "contact_z_mm": float(z),
                    "support_panel_id": panel_id.removeprefix("panel:"),
                    "support_panel_matches_fixed": panel_id == fixed_id,
                    "face_gap_mm": float(gap),
                })
                break

        if solutions:
            # Preserve physically exterior-first depth order. A smaller S
            # alone cannot establish first contact during forward feeding.
            # Do not promote an inner contact merely to improve cost.
            solutions.sort(key=lambda s: (
                -s["contact_s_mm"],
                abs(s["contact_z_mm"]),
                not s["support_panel_matches_fixed"],
                s["support_panel_id"],
            ))
            chosen = solutions[0]
            result = {
                "contact_s_mm": chosen["contact_s_mm"],
                "contact_z_mm": chosen["contact_z_mm"],
                "hint_s_mm": chosen["contact_s_mm"],
                "shift_rearward_mm": 0.0,
                "actual_faces": [self.stop_face_index],
                "datum_face_indices": [self.stop_face_index],
                "selected_datum_is_contact": True,
                "source_datum": "{}.{}".format(
                    self.stop_source_object, self.stop_source_subelement
                ),
                "support_panel_id": chosen["support_panel_id"],
                "support_panel_matches_fixed":
                    chosen["support_panel_matches_fixed"],
                "contact_station_xm_mm": float(self.station_xm_mm),
                "face_gap_mm": chosen["face_gap_mm"],
                "status": "EXTERNAL_REAR_FACE6_STATIC_CAD_CONTACT_SAMPLED",
                "note": (
                    "EXTERNAL_REAR_PANEL_panel:{}".format(
                        chosen["support_panel_id"]
                    ) + ("_MATCHES_FIXED" if chosen["support_panel_matches_fixed"]
                         else "_NOT_FIXED_PANEL_NEEDS_SETUP_REVIEW")
                ),
                # Independent choices are exposed, not silently marked as
                # verified complete multi-setup search branches.
                "alternative_contacts": solutions[1:],
                "candidate_z_interval_checked": True,
                "candidate_trials_count": len(attempted),
                "candidate_occ_trials": occ_trials,
                "candidate_trial_limit_reached": (
                    occ_trials >= max_occ_trials or
                    len(solutions) >= max_solutions
                ),
                "swept_path_verified": False,
                "axis_limits_verified": False,
            }
            self._trace("EXTERNAL_REAR_CONTACT_SOLVED",
                        bend_id=str(active_bend_id), **result)
            return result
        self._trace(
            "EXTERNAL_REAR_CONTACT_EXHAUSTED",
            bend_id=str(active_bend_id),
            configured_station_xm_mm=self.station_xm_mm,
            fixed_panel=fixed_id,
            attempted=attempted,
        )
        summary = ";".join(
            "{}@{:.3f}:{}:{}".format(
                item["panel"], item["depth_s_mm"], item["rejection"],
                item["blocking_component"] or "-"
            ) for item in attempted[:8]
        )
        # Sampling / bounded OCC trial exhaustion can never prove that
        # every reachable height or panel has been ruled out. Do not prune
        # an entire DFS branch on an uncompleted geometric search.
        if (occ_trials >= max_occ_trials or
                any(item.get("rejection") != "BACKGAUGE_Z_OUT_OF_RANGE"
                    for item in attempted)):
            raise BackgaugeCADError(
                "CAD_CONTACT_SEARCH_INCOMPLETE:"
                "station_xm_mm={:.3f} occ_trials={} max_trials={} "
                "remaining_setup_requires_verification".format(
                    self.station_xm_mm, occ_trials, max_occ_trials
                )
            )

        raise BackgaugeCADError(
            "NO_ACCESSIBLE_REAR_EDGE_AT_CONFIGURED_X:"
            "station_xm_mm={:.3f} fixed={} trials={}"
            .format(self.station_xm_mm, fixed_id, summary)
        )

    def resolve_contact_for_bend(self, state, panel_vertices_xy, bend_id,
                                 backgauge_model, *,
                                 desired_midpoint_xm=0.0,
                                 tolerance_mm=0.02,
                                 reverse_axis=False):
        """Resolve edge at the actual finger X station and find OCC contact.

        The bend pose stays fixed at the die; only the gauge's rear
        translational axis is varied. Never move the bend line to force
        contact. Returns the actual front-face target and diagnostics.
        """
        from .backgauge_2d import BackgaugePosition2D
        from .workpiece_placement_3d import WorkpiecePlacement3D

        if backgauge_model.automatic:
            if self.second_station_xm_mm is not None:
                raise BackgaugeCADError(
                    "TWO_CAD_FINGERS_REQUIRE_INDEPENDENT_REAR_EDGE_SOLVER"
                )
            if float(backgauge_model.edge_clearance_mm) > tolerance_mm:
                # A deliberate gap cannot be silently classified as genuine
                # Face6-to-sheet contact.
                raise BackgaugeCADError(
                    "AUTO_CAD_CONTACT_REQUIRES_ZERO_EDGE_CLEARANCE:"
                    "specified_mm={:.4f}".format(
                        float(backgauge_model.edge_clearance_mm)
                    )
                )
            zone = state.bends[bend_id].bend_zone
            if zone is None:
                raise BackgaugeCADError("no authoritative bend zone")
            placement = WorkpiecePlacement3D.compute_placement(
                state, bend_id,
                desired_midpoint_xm=float(desired_midpoint_xm),
                support_plane_zm=float(zone.outer_radius_mm),
                align_bisector=False,
                align_bend_arc_center=True,
                reverse_axis=bool(reverse_axis),
                rear_panel_vertices_xy=panel_vertices_xy,
            )
            if getattr(self, "_trace_writer", None) is not None:
                self._trace(
                    "EXTERNAL_REAR_AUTO_REQUEST",
                    bend_id=str(bend_id),
                    kinematic_fixed_panel=str(state.bends[bend_id].fixed_panel),
                    reverse_axis=bool(reverse_axis),
                    station_xm_mm=float(self.station_xm_mm),
                    workpiece_pose_rotation=placement.rotation,
                    workpiece_pose_translation_xyz_mm=placement.translation,
                )
            result = self.solve_accessible_rear_contact(
                state, panel_vertices_xy, placement,
                active_bend_id=bend_id, tolerance_mm=tolerance_mm,
                z_min_mm=backgauge_model.z_min_mm,
                z_max_mm=backgauge_model.z_max_mm,
            )
            actual_s = float(result["contact_s_mm"])
            actual_z = float(result["contact_z_mm"])
            display = BackgaugePosition2D(
                actual_s + backgauge_model.width_s_mm / 2, actual_z
            )
            return (
                display, actual_s,
                "AUTO_EXTERNAL_REAR_CAD:"
                "support_panel={}:fixed_panel={}:stop_s_mm={:.4f}:"
                "station_xm_mm={:.4f}:{}".format(
                    result["support_panel_id"],
                    state.bends[bend_id].fixed_panel,
                    actual_s, self.station_xm_mm, result["note"],
                ), result,
            )

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
            reverse_axis=bool(reverse_axis),
            rear_panel_vertices_xy=panel_vertices_xy,
        )
        if getattr(self, "_trace_writer", None) is not None:
            self._trace(
                "BEND_CONTACT_REQUEST",
                bend_id=str(bend_id),
                fixed_panel=str(state.bends[bend_id].fixed_panel),
                desired_midpoint_xm_mm=float(desired_midpoint_xm),
                contact_station_xm_mm=self.station_xm_mm,
                second_station_xm_mm=self.second_station_xm_mm,
                nominal_stop_s_mm=round(float(hint_s), 6),
                target_center_s_mm=round(float(target.s_mm), 6),
                target_z_mm=round(float(target.z_mm), 6),
                legacy_rectangle_width_s_mm=float(backgauge_model.width_s_mm),
                requested_edge_clearance_mm=float(backgauge_model.edge_clearance_mm),
                workpiece_pose_rotation=pose.rotation,
                workpiece_pose_translation_xyz_mm=pose.translation,
            )
        result = self.solve_first_contact(
            state, panel_vertices_xy, pose,
            active_bend_id=bend_id,
            hint_s_mm=hint_s, contact_z_mm=target.z_mm,
            tolerance_mm=tolerance_mm,
        )
        self._trace(
            "BEND_CONTACT_SOLVED",
            bend_id=str(bend_id),
            **result,
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
        if getattr(self, "_trace_writer", None) is not None:
            from .backgauge_cad_trace import bbox_xyz
            self._trace(
                "CONTACT_GEOMETRY_PRECHECK",
                bend_id=str(active_bend_id),
                required_fixed_panel=fixed_id,
                hint_s_mm=round(float(hint_s_mm), 6),
                contact_z_mm=round(float(contact_z_mm), 6),
                tolerance_mm=float(tolerance_mm),
                volume_threshold_mm3=volume_tol,
                part_panels_and_bend_zones=[
                    {"component": label, "bbox_xyz_mm": bbox_xyz(shape),
                     "volume_mm3": round(float(shape.Volume), 6)}
                    for label, shape in components
                ],
                selected_fixed_bbox_xyz_mm=bbox_xyz(fixed),
                stop_at_hint_bbox_xyz_mm=[
                    bbox_xyz(g) for g in self.positioned_shapes(
                        float(hint_s_mm), float(contact_z_mm)
                    )
                ],
            )

        def probe(s_mm, *, target_only):
            """Volume interference for the required fixed panel or entire sheet.

            A near-side collision with an unrelated panel must not shift the
            stop past the designated fixed panel. The full part is checked
            independently at the resulting fixed-panel contact.
            """
            shapes = self.positioned_shapes(s_mm, contact_z_mm)
            checked = ((fixed_id, fixed),) if target_only else components
            for index, gauge in enumerate(shapes, 1):
                ga = gauge.BoundBox
                for label, sheet in checked:
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
        fixed_interference, shapes = probe(hint_s, target_only=True)
        if fixed_interference is None:
            # The nominal stop may already contact the intended panel.
            if all(float(g.distToShape(fixed)[0]) <=
                   max(0.05, float(tolerance_mm) + 0.03)
                   for g in shapes):
                all_collision, _ = probe(hint_s, target_only=False)
                if all_collision is None:
                    return self._qualified_contact(
                        shapes, fixed, hint_s, contact_z_mm, hint_s,
                        tolerance_mm, fixed_id,
                    )

        # Only the fixed panel defines the search bracket. Other sheet
        # panels are never a target and cannot drive the stop 500mm away.
        rear_s = -float(fixed.BoundBox.YMin)
        max_forward_protrusion = max(
            float(shape.BoundBox.YMax) + hint_s for shape in shapes
        )
        far_s = max(
            hint_s + 1.0, rear_s + max_forward_protrusion + 1.0
        )
        if not math.isfinite(far_s):
            raise BackgaugeCADError("nonfinite geometric fixed-panel bound")

        if fixed_interference is not None:
            low_s, high_s = hint_s, far_s
            far_interference, _ = probe(high_s, target_only=True)
            if far_interference is not None:
                raise BackgaugeCADError(
                    "fixed panel contact cannot be cleared rearwards"
                )
        else:
            # Hint is clear of intended panel. Move the stop toward
            # operator, decreasing +s, until actual fixed-panel overlap.
            high_s = hint_s
            low_s = max(0.001, hint_s - 1.0)
            step = 1.0
            for _ in range(18):
                collision, _ = probe(low_s, target_only=True)
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
                    "fixed panel contact bracketing exhausted"
                )

        # Binary search approaches the true contact with the selected
        # fixed panel from its rear/clear side.
        for _ in range(28):
            if high_s - low_s <= max(0.005, float(tolerance_mm) / 2):
                break
            mid_s = (low_s + high_s) / 2
            fixed_hit, _ = probe(mid_s, target_only=True)
            if fixed_hit is None:
                high_s = mid_s
            else:
                low_s = mid_s
        fixed_hit, shapes = probe(high_s, target_only=True)
        if getattr(self, "_trace_writer", None) is not None:
            from .backgauge_cad_trace import bbox_xyz
            self._trace(
                "CONTACT_BRACKET_RESULT",
                bend_id=str(active_bend_id),
                required_fixed_panel=fixed_id,
                hint_s_mm=round(hint_s, 6),
                bracket_low_s_mm=round(low_s, 6),
                first_nonpenetrating_stop_s_mm=round(high_s, 6),
                fixed_panel_distance_mm=[
                    round(float(g.distToShape(fixed)[0]), 6)
                    for g in shapes
                ],
                stop_bboxes_xyz_mm=[bbox_xyz(g) for g in shapes],
            )
        if fixed_hit is not None:
            raise BackgaugeCADError(
                "OCC fixed-panel contact solver did not converge"
            )

        # Independent full-workpiece 3D test AT the fixed-panel target.
        # If another panel is obstructing the intended stop, report
        # obstruction rather than retreating to an unrelated outer edge.
        other_hit, _ = probe(high_s, target_only=False)
        if other_hit is not None:
            finger, component, volume = other_hit
            # The OCC intersection is physical material overlap at this
            # *specific* mounted CAD configuration. Include its spatial
            # location and any part of the gauge projecting ahead of Face3.
            gauge_body = shapes[finger - 1]
            obstructing_solid = next(
                shape for label, shape in components if label == component
            )
            common = gauge_body.common(obstructing_solid)
            if common.isNull() or not common.isValid():
                raise BackgaugeCADError(
                    "OCC_CAD_OBSTRUCTION_GEOMETRY_UNVERIFIED:"
                    "finger={} component={}".format(finger, component)
                )
            gb = gauge_body.BoundBox
            cb = common.BoundBox
            ahead = max(0.0, float(gb.YMax) + float(high_s))
            if getattr(self, "_trace_writer", None) is not None:
                from .backgauge_cad_trace import bbox_xyz
                operator_faces = []
                for face_index, face in enumerate(gauge_body.Faces, 1):
                    if not isinstance(face.Surface, Part.Plane):
                        continue
                    uv = face.ParameterRange
                    normal = face.normalAt(
                        (uv[0] + uv[1]) / 2,
                        (uv[2] + uv[3]) / 2,
                    )
                    if normal.dot(self.app.Vector(0, 1, 0)) < .999:
                        continue
                    operator_faces.append({
                        "face": "Face{}".format(face_index),
                        "bbox_xyz_mm": bbox_xyz(face),
                        "distance_to_fixed_mm": round(
                            float(face.distToShape(fixed)[0]), 6
                        ),
                        "distance_to_obstruction_mm": round(
                            float(face.distToShape(obstructing_solid)[0]), 6
                        ),
                    })
                self._trace(
                    "CONTACT_OBSTRUCTED",
                    bend_id=str(active_bend_id),
                    required_fixed_panel=fixed_id,
                    obstructing_component=component,
                    finger_number=int(finger),
                    station_xm_mm=(
                        self.station_xm_mm if finger == 1
                        else self.second_station_xm_mm
                    ),
                    source_datum_face="{}.{}->Body.Face{}".format(
                        self.stop_source_object, self.stop_source_subelement,
                        self.stop_face_index
                    ),
                    candidate_stop_s_mm=round(float(high_s), 6),
                    initial_hint_s_mm=round(float(hint_s), 6),
                    front_protrusion_mm=round(ahead, 6),
                    overlap_volume_mm3=round(float(volume), 6),
                    overlap_bbox_xyz_mm=bbox_xyz(common),
                    stop_body_bbox_xyz_mm=bbox_xyz(gauge_body),
                    fixed_panel_bbox_xyz_mm=bbox_xyz(fixed),
                    obstructing_panel_bbox_xyz_mm=bbox_xyz(obstructing_solid),
                    operator_facing_planar_faces=operator_faces,
                    contact_verified=False,
                    continuous_path_verified=False,
                )
            raise BackgaugeCADError(
                "OTHER_PANEL_BLOCKS_REQUIRED_GAUGE_CONTACT:"
                "finger={} obstructing_component={} "
                "volume_mm3={:.6f} fixed={} "
                "required_stop_s_mm={:.4f} hint_s_mm={:.4f} "
                "front_protrusion_mm={:.4f} "
                "overlap_bbox_xyz_mm=[{:.3f},{:.3f},{:.3f},{:.3f},"
                "{:.3f},{:.3f}]"
                .format(
                    finger, component, volume, fixed_id, high_s, hint_s,
                    ahead,
                    float(cb.XMin), float(cb.XMax),
                    float(cb.YMin), float(cb.YMax),
                    float(cb.ZMin), float(cb.ZMax),
                )
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
            "selected_datum_is_contact": all(
                index == self.stop_face_index for index in actual_face_indices
            ),
            "source_datum": "{}.{}".format(
                self.stop_source_object, self.stop_source_subelement
            ),
            "status": "FIRST_NONPENETRATING_CAD_FACE_CONTACT_SAMPLED",
            "note": (
                "CONTACT_SELECTED_CAD_FACE" if all(
                    index == self.stop_face_index for index in actual_face_indices
                ) else "CONTACT_ON_DIFFERENT_CAD_FACE_NEEDS_TOOLING_REVIEW"
            ),
            "swept_path_verified": False,
            "axis_limits_verified": False,
        }

    def _positioned_at(self, contact_s_mm, contact_z_mm, station_xm_mm):
        """Position explicitly selected CAD source face at machine point."""
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
