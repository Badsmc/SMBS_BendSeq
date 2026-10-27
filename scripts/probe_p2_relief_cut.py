"""
probe_p2_relief_cut.py - Geometric probe for KL-9 (2_1_3_4_ok relief-cut edge offset).

Tests the hypothesis:
1. Cutting relief regions from the bend sector eliminates spurious corner facet faces.
2. Identifies the 0.2 mm delta between canonical bend axis_point and flat_pattern.brep relief edge.
"""
from __future__ import annotations

import math
from pathlib import Path

from smbs_core.geometry_backend.freecad_bootstrap import ensure_freecad_paths
ensure_freecad_paths()

import FreeCAD as App
import Part
from OCC.Core.BRep import BRep_Builder
from OCC.Core.BRepTools import breptools
from OCC.Core.TopoDS import TopoDS_Shape
from OCC.Core.TopAbs import TopAbs_FACE, TopAbs_SOLID
from OCC.Core.TopExp import TopExp_Explorer
from OCC.Core.BRepCheck import BRepCheck_Analyzer
from OCC.Core.GProp import GProp_GProps
from OCC.Core.BRepGProp import brepgprop
from smbs_core.coordinator.run_pipeline import Coordinator


def counts(shape):
    if shape is None:
        return {'solids': 0, 'faces': 0}
    if hasattr(shape, "ShapeType") and not hasattr(shape, "IsNull"):
        return {'solids': len(shape.Solids), 'faces': len(shape.Faces)}
    if hasattr(shape, "IsNull") and shape.IsNull():
        return {'solids': 0, 'faces': 0}
    out = {}
    for t, name in [(TopAbs_SOLID, 'solids'), (TopAbs_FACE, 'faces')]:
        exp = TopExp_Explorer(shape, t)
        n = 0
        while exp.More():
            n += 1
            exp.Next()
        out[name] = n
    return out


def main():
    print("=== CHECK 1: Sector generation + cut feasibility ===")
    r_in, t, length, deg = 1.0, 1.5, 50.0, 90.0

    ax_p = App.Vector(0, 0, 0)
    ax_d = App.Vector(0, 1, 0)  # bend along Y
    start_v = App.Vector(0, 0, 1)  # radial offset
    p1 = ax_p + start_v * r_in
    p2 = ax_p + start_v * (r_in + t)
    p3 = p2 + ax_d * length
    p4 = p1 + ax_d * length
    face = Part.Face(Part.makePolygon([p1, p2, p3, p4, p1]))
    sector = face.revolve(ax_p, ax_d, deg)

    print(f"Base sector: {counts(sector)}")

    # Cutting prism at one end of sector (simulating relief notch at Y in [0, 2.5])
    cut_box = Part.makeBox(10, 2.5, 10, App.Vector(-2, 0, -2))
    cut_result = sector.cut(cut_box)

    print(f"Cut result counts: {counts(cut_result)}")
    cut_occ = cut_result._Shape if hasattr(cut_result, "_Shape") else None
    if cut_occ is None:
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".brep") as tf:
            cut_result.exportBrep(tf.name)
            cut_occ = TopoDS_Shape()
            breptools.Read(cut_occ, tf.name, BRep_Builder())

    print(f"BRepCheck valid: {BRepCheck_Analyzer(cut_occ).IsValid()}")

    print("\n=== CHECK 2: Coordinator execution on 2_1_3_4 ===")
    fixture_path = Path("SMBendTests/2-1-3-4-(ok).FCStd")
    if not fixture_path.exists():
        print(f"Fixture {fixture_path} not found.")
        return

    coord = Coordinator()
    res = coord.start_run(
        source_file_path=fixture_path,
        part_id="part_2134",
        search_algorithm="dfs",
        unknown_policy="EXPLORATORY",
    )
    bm = res.get("baseline_metrics", {})
    print(f"pass: {res.get('pass')}, faces ref: {bm.get('faces_count_reference')}, faces rest: {bm.get('faces_count_restored')}")
    print(f"counts_match: {bm.get('raw_comparison_result', {}).get('counts_match')}")
    print(f"topology_matches: {bm.get('raw_comparison_result', {}).get('topology_matches')}")
    print(f"hausdorff: {bm.get('hausdorff_distance_mm')}")


if __name__ == "__main__":
    main()
