"""PDF-only appearance options; no mutation of the live machine or collision results."""
from dataclasses import dataclass, field
from math import isfinite

LAYERS = ("part", "punch", "die", "frame", "clamps", "gauge", "collision")
DEFAULT_TRANSPARENCY = dict(part=0, punch=0, die=0, frame=65, clamps=0, gauge=0, collision=30)
DEFAULT_VISIBLE = dict.fromkeys(LAYERS, True)
DEFAULT_VISIBLE["collision"] = False
CONTENT = ("overview", "flat_pattern", "schedule", "tool_details", "segment_text",
           "before", "after", "part_inset", "bend_axis", "gauge_text")
DEFAULT_CONTENT = dict.fromkeys(CONTENT, True)


@dataclass(frozen=True)
class PdfOptions:
    visible: dict = field(default_factory=lambda: DEFAULT_VISIBLE.copy())
    transparency: dict = field(default_factory=lambda: DEFAULT_TRANSPARENCY.copy())
    content: dict = field(default_factory=lambda: DEFAULT_CONTENT.copy())
    positions_per_page: int = 1
    large_first: bool = False
    punch_segments: bool = True
    die_segments: bool = True
    fit_machine: bool = False
    landscape: bool = False
    group_collisions: bool = True
    right_view: str = "3d"

    def __post_init__(self):
        for mapping, keys, predicate in (
            (self.visible, LAYERS, lambda v: type(v) is bool),
            (self.transparency, LAYERS, lambda v: type(v) is int and 0 <= v <= 100),
            (self.content, CONTENT, lambda v: type(v) is bool),
        ):
            if set(mapping) != set(keys) or not all(predicate(v) for v in mapping.values()):
                raise ValueError("Invalid PDF display settings")
        if type(self.positions_per_page) is not int or not 1 <= self.positions_per_page <= 10:
            raise ValueError("Invalid operations per page")
        if self.right_view not in ("3d", "section"):
            raise ValueError("Invalid PDF right image mode")
        for key in ("large_first", "punch_segments", "die_segments", "fit_machine", "landscape", "group_collisions"):
            if type(getattr(self, key)) is not bool:
                raise ValueError("Invalid PDF layout flag")

    def shows(self, layer):
        return self.visible[layer] and self.transparency[layer] < 100

    def alpha(self, layer):
        return round(255 * (100 - self.transparency[layer]) / 100)

    def includes(self, key):
        return self.content[key]


def face_layer(face):
    prefix = face.kind.split("_", 1)[0]
    return prefix if prefix in LAYERS else "part"


def visible_faces(faces, options):
    return tuple(face for face in faces if options.shows(face_layer(face)))


def current_collision_hits(workspace):
    """Only evidence from the unchanged current setup; never reuse stale hits."""
    if workspace.session is None or getattr(workspace, "_mesh_collision_identity", None) is None:
        return ()
    if workspace._mesh_collision_identity != workspace._mesh_preview_identity():
        return ()
    return tuple(getattr(workspace, "_mesh_collision_hits", ()))


def collision_box_faces(bounds):
    """Reported overlap AABB in machine XYZ -> preview (-Y, X, Z). Not exact overlap."""
    from smbs_core.kinematics2d.preview_mesh_3d import PreviewFace3D
    if not bounds:
        return ()
    try:
        lo = tuple(float(bounds[k+"min"]) for k in "xyz")
        hi = tuple(float(bounds[k+"max"]) for k in "xyz")
    except (KeyError, TypeError, ValueError):
        return ()
    if not all(isfinite(v) for v in (*lo, *hi)) or any(a > b for a,b in zip(lo,hi)):
        return ()
    v = [(-y,x,z) for x in (lo[0],hi[0]) for y in (lo[1],hi[1]) for z in (lo[2],hi[2])]
    return tuple(PreviewFace3D(tuple(v[i] for i in ids), "collision", "overlap_aabb")
                 for ids in ((0,1,3,2),(4,6,7,5),(0,4,5,1),(2,3,7,6),(0,2,6,4),(1,5,7,3)))
