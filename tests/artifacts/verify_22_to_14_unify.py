"""
Artifact script demonstrating that FreeCAD sheet metal models have 22 faces
in their raw state (due to lack of UnifySameDomain between sequential Bend features),
and collapse into exactly 14 faces after ShapeUpgrade_UnifySameDomain.
"""
from pathlib import Path
from smbs_core.workers.baseline.worker import BaselineWorker
from OCC.Core.TopExp import TopExp_Explorer
from OCC.Core.TopAbs import TopAbs_FACE
from OCC.Core.BRepAdaptor import BRepAdaptor_Surface
from OCC.Core.GeomAbs import GeomAbs_Cylinder
from OCC.Core.ShapeUpgrade import ShapeUpgrade_UnifySameDomain

def inspect_fixture(brep_path: Path):
    bw = BaselineWorker()
    shape_raw = bw._load_brep(brep_path)

    unif = ShapeUpgrade_UnifySameDomain(shape_raw, True, True, True)
    unif.Build()
    shape_canonical = unif.Shape()

    def count(s):
        exp = TopExp_Explorer(s, TopAbs_FACE)
        total = 0
        cyl = 0
        while exp.More():
            total += 1
            f = exp.Current()
            if BRepAdaptor_Surface(f, True).GetType() == GeomAbs_Cylinder:
                cyl += 1
            exp.Next()
        return total, cyl

    raw_total, raw_cyl = count(shape_raw)
    can_total, can_cyl = count(shape_canonical)
    return raw_total, can_total, raw_cyl, can_cyl

if __name__ == "__main__":
    fixtures = [
        ("z_ok", Path("fixtures/freecad/z_ok/part_3d.brep")),
        ("z_10_20_10_ok", Path("fixtures/freecad/z_10_20_10_ok/part_3d.brep")),
        ("135_ok", Path("fixtures/freecad/135_ok/part_3d.brep")),
        ("u_60deg_30deg_ok", Path("fixtures/freecad/u_60deg_30deg_ok/part_3d.brep")),
        ("u_90deg_90deg_ok", Path("fixtures/freecad/u_90deg_90deg_ok/part_3d.brep")),
        ("u_bracket", Path("fixtures/synthetic/u_bracket.brep")),
        ("four_bend", Path("fixtures/synthetic/four_bend.brep")),
    ]
    print(f"{'Fixture':20} | Raw Faces | Canonical Faces | Raw Cyl | Can Cyl")
    print("-" * 65)
    for name, path in fixtures:
        if path.exists():
            rf, cf, rc, cc = inspect_fixture(path)
            print(f"{name:20} | {rf:9} | {cf:15} | {rc:7} | {cc:7}")
