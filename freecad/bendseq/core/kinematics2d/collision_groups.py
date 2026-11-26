"""Group sampled Mesh evidence without changing the collision solver.

A grouped image is a material-attached map, not simultaneous intersections.
Rigid panels use the exact source-to-reference rigid transform. Deforming
bend zones with differing angles are marked as whole components instead.
"""
from math import isfinite
from freecad.bendseq.core.kinematics2d.panel_kinematics import FoldState
from freecad.bendseq.core.kinematics2d.workpiece_placement_3d import PlacementTransform3D

BOX_EDGES = tuple((i,j) for i in range(8) for j in range(i+1,8)
                  if (i ^ j) in (1,2,4))
BOX_FACES = ((0,1,3,2),(4,6,7,5),(0,4,5,1),(2,3,7,6),(0,2,6,4),(1,5,7,3))


def collision_groups(hits, *, grouped=True, operation_order=()):
    hits = tuple(hits)
    if not grouped:
        return tuple((hit,) for hit in hits)
    groups = {}
    for hit in hits:
        groups.setdefault(hit["operation"], []).append(hit)
    order = tuple(dict.fromkeys((*operation_order,*groups)))
    return tuple(tuple(groups[k]) for k in order if k in groups)


def representative_hit(group):
    # Prefer confirmed evidence, then largest recorded intersection.
    return max(group,key=lambda h:(not h.get("conditional",False),
                                   float(h.get("occ_overlap_volume_mm3",0))))


def sample_state(graph, fixed_panel, hit):
    state = FoldState(graph,fixed_panel)
    pose = hit["sampled_machine_pose"]
    for bid, angle in pose["angles_deg"].items():
        state.set_angle(bid,angle)
    return state, PlacementTransform3D(tuple(tuple(row) for row in pose["rotation"]),
                                      tuple(pose["translation"]))


def bbox_corners(bounds):
    if not bounds:
        return ()
    try:
        lo = tuple(float(bounds[k+"min"]) for k in "xyz")
        hi = tuple(float(bounds[k+"max"]) for k in "xyz")
    except (KeyError,ValueError,TypeError):
        return ()
    if not all(isfinite(x) for x in (*lo,*hi)) or any(a>b for a,b in zip(lo,hi)):
        return ()
    return tuple((x,y,z) for x in (lo[0],hi[0]) for y in (lo[1],hi[1]) for z in (lo[2],hi[2]))


def panel_machine_transform(state, placement, panel):
    tf = state.transforms[panel]
    return PlacementTransform3D(tf.rotation,tf.translation).then(placement)


def remap_corners(corners, source, target):
    mapping = source.inverse().then(target)
    return tuple(mapping.apply(p) for p in corners)


def projected_zones(graph, fixed_panel, group, reference=None, *, grouped=True):
    reference = reference or representative_hit(group)
    target, target_place = sample_state(graph,fixed_panel,reference)
    result = []
    for hit in group:
        component = hit["component"]
        corners = bbox_corners(hit.get("overlap_bbox_xyz_mm"))
        mapped = False
        if hit is not reference and grouped and corners:
            source,source_place = sample_state(graph,fixed_panel,hit)
            if component in target.transforms and component in source.transforms:
                panel = component
            elif (component in target.bends and component in source.bends
                  and abs(source.angles[component]-target.angles[component])<1e-7):
                panel = target.bends[component].fixed_panel
            else:
                panel = None
            if panel is None:
                corners = ()
            else:
                corners = remap_corners(corners,panel_machine_transform(source,source_place,panel),
                                        panel_machine_transform(target,target_place,panel))
                mapped = True
        result.append(dict(component=component, conditional=bool(hit.get("conditional")),
                           corners_xyz=corners, mapped=mapped, component_only=not bool(corners),
                           operation=hit["operation"], phase=hit["phase"]))
    return tuple(result)


def component_colors(zones, *, all_components=False):
    colors = {}
    for zone in zones:
        if all_components or zone["component_only"]:
            key = zone["component"]
            # confirmed takes precedence over conditional
            colors[key] = min(colors.get(key,2),2 if zone["conditional"] else 1)
    return colors
