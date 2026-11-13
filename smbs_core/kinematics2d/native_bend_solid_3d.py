"""D6.3 / D6.3.2: real FreeCAD Part solid for SheetMetal bend zones and full multi-bend sheets.

Consumes authoritative FoldState + BendZone; does not introduce new bend
angles, K-factors, or transformations. The solid is a polygonal approximation
of the existing finite-radius section, extruded along the actual bend line.

No FreeCAD import until build is invoked. No document objects are created.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from math import isfinite, hypot

from .panel_kinematics import FoldState
from .native_geometry_build_metrics import (
    measure_native_geometry_region,
    record_native_geometry_cache_event,
)


class NativeBendSolidError(ValueError):
    pass


def zone_outline(zone, angle_deg, segments=64):
    """Ordered (s,z) perimeter with adjacent duplicate points removed.

    Retains the exact existing BendZone section contract. This step is pure
    Python; Part.Face/solid extrusion happens only in FreeCAD.
    """
    if not isinstance(segments, int) or segments < 8:
        raise NativeBendSolidError("segments must be integer >= 8")
    profile = zone.section_profile(angle_deg, segments=segments)
    # Inner and outer are equally sampled along the same source bend, and
    # their endpoints may coincide along the arc when angle=0 or fully bent.
    source = tuple(profile["inner"]) + tuple(reversed(profile["outer"]))
    clean = []
    for raw in source:
        s, z = float(raw[0]), float(raw[1])
        if not isfinite(s) or not isfinite(z):
            raise NativeBendSolidError("nonfinite_section")
        if not clean or hypot(s-clean[-1][0], z-clean[-1][1]) > 1e-8:
            clean.append((s,z))
    if len(clean) > 1 and hypot(clean[0][0]-clean[-1][0],clean[0][1]-clean[-1][1]) <= 1e-8:
        clean.pop()
    if len(clean) < 4:
        raise NativeBendSolidError("degenerate_section_outline")
    signed_area = 0.5 * sum(
        clean[i][0]*clean[(i+1)%len(clean)][1] -
        clean[(i+1)%len(clean)][0]*clean[i][1]
        for i in range(len(clean))
    )
    if not isfinite(signed_area) or abs(signed_area) < 1e-7:
        raise NativeBendSolidError("invalid_section_area")
    return tuple(clean)


@dataclass(frozen=True)
class NativeBendSolid:
    bend_id: str
    angle_deg: float
    shape: object
    volume_mm3: float
    bend_line_length_mm: float
    section_area_mm2: float
    facets_approximation: int
    is_entire_sheet: bool = False
    physical_certified: bool = False


def build_bend_zone_solid(state: FoldState, bend_id: str, *,
                          segments=64) -> NativeBendSolid:
    """Build one true closed FreeCAD Part.Shape for a FoldState bend zone.

    Positive and negative angle signs retain BendZone convention. Uses the
    transform of the fixed panel, preserving preceding completed folds.
    This geometry is independent of and never modifies a FreeCAD Document.
    """
    if not isinstance(state, FoldState) or bend_id not in state.bends:
        raise NativeBendSolidError("missing_fold_state_or_bend")
    bend = state.bends[bend_id]
    zone = getattr(bend, "bend_zone", None)
    if zone is None or zone.bend_id != bend_id:
        raise NativeBendSolidError("missing_or_mismatched_bend_zone")
    if abs(float(state.angles[bend_id])) > abs(float(bend.angle_deg))+1e-8:
        raise NativeBendSolidError("angle_outside_target")
    outline = zone_outline(zone, state.angles[bend_id], segments=segments)
    try:
        import FreeCAD as App
        import Part
    except ImportError as exc:
        raise NativeBendSolidError("FreeCAD_Part_not_available") from exc

    # Local (s,z) lies in a plane perpendicular to the actual bend axis.
    side = 1.0 if bend.moving_left else -1.0
    lx,ly = zone.left_xy
    ox,oy = zone.origin_xy
    tf = state.transforms[bend.fixed_panel]
    (ax,ay),(bx,by) = zone.axis_xy
    v0 = tf.point((ax,ay,0.0))
    v1 = tf.point((bx,by,0.0))
    length = ((v1[0]-v0[0])**2+(v1[1]-v0[1])**2+(v1[2]-v0[2])**2)**0.5
    if not isfinite(length) or length < 1e-6:
        raise NativeBendSolidError("invalid_bend_axis")

    points=[]
    for s,z in outline:
        x = ox + lx*side*s
        y = oy + ly*side*s
        p = tf.point((x,y,z))
        points.append(App.Vector(*p))
    try:
        wire=Part.makePolygon(points+[points[0]])
        face=Part.Face(wire)
        extrusion=App.Vector(v1[0]-v0[0],v1[1]-v0[1],v1[2]-v0[2])
        solid=face.extrude(extrusion)
        if (solid.isNull() or not solid.isValid() or not solid.isClosed()
                or len(solid.Solids)!=1 or not isfinite(float(solid.Volume))
                or float(solid.Volume)<=0):
            raise NativeBendSolidError("invalid_or_open_part_solid")
    except NativeBendSolidError:
        raise
    except Exception as exc:
        raise NativeBendSolidError("occ_zone_build_failed: "+str(exc)) from exc

    area=float(face.Area)
    if abs(float(solid.Volume)-area*length)>max(1e-4,area*length*1e-6):
        raise NativeBendSolidError("inconsistent_extrusion_volume")
    return NativeBendSolid(
        bend_id, float(state.angles[bend_id]), solid,
        float(solid.Volume), length, area, len(outline),
    )


@dataclass(frozen=True)
class NativeSingleBendSheet:
    """Validated joined OCC solid for a two-panel, one-bend sheet only."""
    bend_id: str
    angle_deg: float
    shape: object
    volume_mm3: float
    components: int
    physical_certified: bool = False


@dataclass(frozen=True)
class NativeMultiBendSheet:
    """Validated joined OCC solid for an N-panel, M-bend sheet."""
    angles_deg: dict[str, float]
    shape: object
    volume_mm3: float
    panels_count: int
    bends_count: int
    is_valid: bool = True
    is_closed: bool = True
    physical_certified: bool = False


def _apply_rigid_transform(shape, transform, App):
    """Apply existing FoldState panel transform to an isolated Part.Shape."""
    mat = App.Matrix()
    for i in range(3):
        for j in range(3):
            setattr(mat, "A{}{}".format(i+1,j+1), float(transform.rotation[i][j]))
        setattr(mat, "A{}4".format(i+1), float(transform.translation[i]))
    independent = shape.copy()
    transformed = independent.transformShape(mat, True)
    # FreeCAD's transformShape API returns a Shape in supported versions.
    if transformed is None:
        raise NativeBendSolidError("FreeCAD_transformShape_did_not_return_shape")
    if not transformed.isValid() or transformed.isNull():
        raise NativeBendSolidError("invalid_transformed_panel")
    return transformed


def build_single_bend_sheet_solid(state: FoldState, panel_build, bend_id: str, *,
                                  segments=64) -> NativeSingleBendSheet:
    """Attempt full-sheet solid from authoritative two-panel SheetMetal faces.

    Exactly ONE bend and TWO rigid faces are required. Other geometries fail
    closed; there is NO implicit dropping of neighboring bends or panel faces.
    Rigid panel faces are those returned by the existing PanelBuilder, cut
    at the finite-width BendZone boundaries, not a second 2D decomposition.

    Geometric union must be one valid connected closed OCC solid. A non-fused
    compound, a gap or a self-intersection fails rather than being accepted.
    Physical tool contact and intermediate full-cycle motion are not tested.
    """
    if not isinstance(state, FoldState) or bend_id not in state.bends:
        raise NativeBendSolidError("missing_state")
    if len(state.ordered_bends) != 1 or len(state.graph.panels) != 2:
        raise NativeBendSolidError("only_single_bend_two_panel_parts_supported")
    multi = build_multi_bend_sheet_solid(state, panel_build, segments=segments)
    return NativeSingleBendSheet(
        bend_id, float(state.angles[bend_id]), multi.shape, multi.volume_mm3,
        multi.panels_count + multi.bends_count,
    )


def _source_panel_prism(panel_id, face, thickness_mm, App, cache=None):
    """Return one unchanged source prism, validating source and cached solid.

    Caller owns geometry-aware cache invalidation. Never transform the cached
    object in place; _apply_rigid_transform always copies it.
    """
    if face.isNull() or not face.isValid() or face.Area <= 0:
        raise NativeBendSolidError(f"invalid_rigid_panel_face_{panel_id}")
    prism = cache.get(panel_id) if cache is not None else None
    if prism is None:
        with measure_native_geometry_region("panel_extrude"):
            prism = face.extrude(App.Vector(0, 0, -thickness_mm))
    if prism.isNull() or not prism.isValid() or len(prism.Solids) != 1:
        raise NativeBendSolidError(f"invalid_rigid_panel_solid_{panel_id}")
    if cache is not None:
        cache[panel_id] = prism
    return prism


def _cached_zone_solid(
    state: FoldState, bend, *, segments: int, zone_shape_cache=None,
    max_cache_entries: int = 128,
) -> NativeBendSolid:
    """Reuse EXACT immutable bend-zone solids at identical native poses.

    Unlike the active bend, adjacent bends are usually stationary throughout
    its 30+ sampled poses. The zone's 3D shape depends ONLY on the immutable
    BendZone, signed angle, orientation of its fixed panel, moving_left and
    segment count. The key includes all of these; changing any one rebuilds.

    Values are never returned directly from the cache: every hit supplies an
    independent Part.Shape copy, and a miss stores an independent shape copy.
    LRU is limited to protect FreeCAD memory during multi-bend validation.
    The complete sheet multiFuse and its topology/volume qualification still
    execute on EVERY un-cached full-sheet pose; no collision is skipped.
    """
    if zone_shape_cache is None:
        return build_bend_zone_solid(
            state, bend.bend_id, segments=segments,
        )
    zone = bend.bend_zone
    transform = state.transforms[bend.fixed_panel]
    key = (
        zone, bend.bend_id, bool(bend.moving_left),
        float(state.angles[bend.bend_id]), transform, int(segments),
    )
    if key in zone_shape_cache:
        cached = zone_shape_cache.pop(key)
        zone_shape_cache[key] = cached
        record_native_geometry_cache_event("bend_zone_cache_hit")
        return replace(cached, shape=cached.shape.copy())

    record_native_geometry_cache_event("bend_zone_cache_miss")
    built = build_bend_zone_solid(
        state, bend.bend_id, segments=segments,
    )
    # Keep only private owned shapes inside the cache. FreeCAD OCCT booleans
    # must never be handed the same mutable shape instance as the cached one.
    zone_shape_cache[key] = replace(
        built, shape=built.shape.copy(),
    )
    while len(zone_shape_cache) > max_cache_entries:
        zone_shape_cache.pop(next(iter(zone_shape_cache)))
        record_native_geometry_cache_event("bend_zone_cache_eviction")
    return built


def _validated_joined_sheet_volume(joined) -> float:
    """Validate exact native OCC sheet topology once and retain its volume.

    Called immediately after multiFuse, before any further shape mutation.
    isValid()/isClosed() can be expensive on a multi-face native sheet;
    the result is trusted only within this synchronous, immutable snapshot.
    This is not a replacement for the original strict qualification.
    """
    if (joined.isNull() or not joined.isValid() or not joined.isClosed()
            or len(joined.Solids) != 1):
        raise NativeBendSolidError(
            "zones_and_panels_not_one_valid_closed_solid"
        )
    volume = float(joined.Volume)
    if not isfinite(volume) or volume <= 0:
        raise NativeBendSolidError(
            "zones_and_panels_not_one_valid_closed_solid"
        )
    return volume


@dataclass(frozen=True)
class NativeSheetComponents:
    """Authoritative isolated, positioned OCC solids, not a joined sheet.

    panel/zone shapes are independent of cache-owned source shapes and remain
    in FoldState part coordinates. Consumers must rigidly place them in the
    machine frame before comparison with punch/die.
    """
    panels: tuple
    zones: tuple
    zone_volume_mm3: float

    def named_shapes(self):
        return self.panels + self.zones


def build_multi_bend_sheet_components(state: FoldState, panel_build, *,
                                      segments=64, panel_prism_cache=None,
                                      zone_shape_cache=None) -> NativeSheetComponents:
    """Build the SAME native panel/zone solids used by full-sheet multiFuse.

    No 3D collision or full-sheet integrity certificate follows from this
    intermediate representation. Cache invalidation is caller-owned and must
    match the existing native validator's source identity.
    """
    if not isinstance(state, FoldState):
        raise NativeBendSolidError("missing_fold_state")
    panels = getattr(panel_build, "panels", None)
    faces = getattr(panel_build, "panel_faces", None)
    if panels is None or faces is None:
        raise NativeBendSolidError("missing_panel_build")
    if len(panels) != len(faces) or len(panels) < 2:
        raise NativeBendSolidError("invalid_panel_build_count")
    if {p.panel_id for p in panels} != set(state.graph.panels):
        raise NativeBendSolidError("panel_build_graph_mismatch")
    if len(state.ordered_bends) < 1:
        raise NativeBendSolidError("no_bends_in_fold_state")

    # Authoritative sheet thickness from first bend zone
    first_bend = state.ordered_bends[0]
    if first_bend.bend_zone is None:
        raise NativeBendSolidError("missing_bend_zone")
    t = float(first_bend.bend_zone.thickness_mm)


    try:
        import FreeCAD as App

        # Optional per-validator cache stores only UNTRANSFORMED panel prisms.
        # The caller must invalidate it when source panel geometry changes.
        # Every pose still receives an independent copy, rigid transform,
        # complete OCC union and the original volume/topology qualification.
        if panel_prism_cache is not None and len(panel_prism_cache) != len(panels):
            panel_prism_cache.clear()
        panel_solids = []
        panel_ids = []
        for panel, face in zip(panels, faces):
            panel_ids.append(panel.panel_id)
            panel_solid = _source_panel_prism(
                panel.panel_id, face, t, App, panel_prism_cache
            )
            tf = state.transforms[panel.panel_id]
            with measure_native_geometry_region("panel_transform"):
                panel_solids.append(_apply_rigid_transform(panel_solid, tf, App))

        zone_solids = []
        zone_ids = []
        zone_volumes = 0.0
        for bend in state.ordered_bends:
            with measure_native_geometry_region("bend_zone_build"):
                zone_res = _cached_zone_solid(
                    state, bend, segments=segments,
                    zone_shape_cache=zone_shape_cache,
                )
            zone_ids.append('bend_zone:' + str(bend.bend_id))
            zone_solids.append(zone_res.shape)
            zone_volumes += zone_res.volume_mm3


    except NativeBendSolidError:
        raise
    except Exception as exc:
        raise NativeBendSolidError("occ_multi_bend_component_build_failed: " + str(exc)) from exc

    return NativeSheetComponents(
        tuple(zip(panel_ids, panel_solids)),
        tuple(zip(zone_ids, zone_solids)),
        float(zone_volumes),
    )


def build_multi_bend_sheet_solid(state: FoldState, panel_build, *,
                                 segments=64, panel_prism_cache=None,
                                 zone_shape_cache=None,
                                 component_observer=None) -> NativeMultiBendSheet:
    """Attempt full-sheet solid from authoritative N-panel, M-bend SheetMetal faces.

    Uses all rigid panel faces from panel_build transformed by their FoldState
    transforms, builds every bend zone at its active angle via build_bend_zone_solid(),
    and joins them into a single connected, closed OCC solid.

    Topological and volumetric integrity:
    - Every panel and every bend zone must be present.
    - Geometric union must be exactly 1 valid closed OCC solid.
    - Total volume must match the sum of transformed panels and bend zones
      within numerical tolerance (rejecting gaps or self-intersections).
    """
    components = build_multi_bend_sheet_components(
        state, panel_build, segments=segments,
        panel_prism_cache=panel_prism_cache,
        zone_shape_cache=zone_shape_cache,
    )
    panel_solids = [shape for _, shape in components.panels]
    zone_solids = [shape for _, shape in components.zones]
    zone_volumes = components.zone_volume_mm3

    # An optional experimental observer sees the EXACT isolated
    # BReps being passed to this native multiFuse; no second geometry build.
    # The default and every non-experimental caller retain identical behavior.
    # The caller must catch its own diagnostic errors so production geometry
    # qualification is never affected by a failed shadow probe. The opt-in
    # reject-only D6 observer may deliberately raise ComponentBlockerFound
    # BEFORE fusion; its caller handles that positive-only proof separately.
    if component_observer is not None:
        component_observer(components)

    try:
        # Join all panel and zone solids together via OCC multiFuse
        all_solids = panel_solids + zone_solids
        with measure_native_geometry_region("sheet_union"):
            if len(all_solids) == 1:
                joined = all_solids[0]
            elif hasattr(all_solids[0], "multiFuse"):
                joined = all_solids[0].multiFuse(all_solids[1:])
            else:
                joined = all_solids[0]
                for s in all_solids[1:]:
                    joined = joined.fuse(s)

        with measure_native_geometry_region("sheet_qualification"):
            # One OCC validity/closure/volume evaluation per new joined
            # sheet. Do not repeat expensive BRep topology checking solely
            # to populate an immutable report after validation succeeded.
            joined_volume = _validated_joined_sheet_volume(joined)
            expected = sum(float(x.Volume) for x in panel_solids) + zone_volumes
            if abs(joined_volume - expected) > max(1e-3, expected * 1e-5):
                raise NativeBendSolidError("panel_zone_overlap_or_missing_material")

    except NativeBendSolidError:
        raise
    except Exception as exc:
        raise NativeBendSolidError("occ_multi_bend_assembly_failed: " + str(exc)) from exc

    return NativeMultiBendSheet(
        angles_deg=dict(state.angles),
        shape=joined,
        volume_mm3=joined_volume,
        panels_count=len(components.panels),
        bends_count=len(state.ordered_bends),
        is_valid=True,  # exactly checked by _validated_joined_sheet_volume
        is_closed=True,  # exactly checked by _validated_joined_sheet_volume
        physical_certified=False,
    )
