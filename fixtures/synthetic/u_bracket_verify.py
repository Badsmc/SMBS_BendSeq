"""
Cross-check script for U-bracket synthetic fixture.
Reads u_bracket.brep, measures OCC metrics, and compares against u_bracket.expected.json.
Tolerances:
  ΔV < 1e-3 mm³
  ΔBBox < 1e-3 mm
  Face count exact match
  ΔSurface Area < 1e-2 mm²
  Solid count == 1
  Shell count == 1
  BRepCheck_Analyzer.IsValid() == True
"""

import json
import sys
from pathlib import Path

from OCC.Core.BRepTools import breptools
from OCC.Core.BRep import BRep_Builder
from OCC.Core.TopoDS import TopoDS_Shape, topods
from OCC.Core.GProp import GProp_GProps
from OCC.Core.BRepGProp import brepgprop
from OCC.Core.Bnd import Bnd_Box
from OCC.Core.BRepBndLib import brepbndlib
from OCC.Core.TopExp import TopExp_Explorer
from OCC.Core.TopAbs import TopAbs_FACE, TopAbs_SOLID, TopAbs_SHELL
from OCC.Core.BRepAdaptor import BRepAdaptor_Surface
from OCC.Core.GeomAbs import GeomAbs_Cylinder
from OCC.Core.BRepCheck import BRepCheck_Analyzer

def verify_u_bracket():
    base_dir = Path(__file__).parent
    expected_path = base_dir / "u_bracket.expected.json"
    brep_path = base_dir / "u_bracket.brep"

    if not expected_path.exists():
        raise FileNotFoundError(f"Expected metrics file not found: {expected_path}")
    if not brep_path.exists():
        raise FileNotFoundError(f"BREP file not found: {brep_path}")

    with open(expected_path, "r", encoding="utf-8") as f:
        expected = json.load(f)

    shape = TopoDS_Shape()
    builder = BRep_Builder()
    if not breptools.Read(shape, str(brep_path), builder):
        raise ValueError(f"Failed to read BREP from {brep_path}")

    # 1. Volume
    vol_props = GProp_GProps()
    brepgprop.VolumeProperties(shape, vol_props)
    act_vol = vol_props.Mass()

    # 2. Surface Area
    surf_props = GProp_GProps()
    brepgprop.SurfaceProperties(shape, surf_props)
    act_area = surf_props.Mass()

    # 3. Bounding Box
    bbox = Bnd_Box()
    brepbndlib.Add(shape, bbox)
    xmin, ymin, zmin, xmax, ymax, zmax = bbox.Get()
    act_bbox = [xmax - xmin, ymax - ymin, zmax - zmin]

    # 4. Faces
    exp = TopExp_Explorer(shape, TopAbs_FACE)
    act_faces = 0
    act_cyl = 0
    while exp.More():
        f = topods.Face(exp.Current())
        act_faces += 1
        surf = BRepAdaptor_Surface(f, True)
        if surf.GetType() == GeomAbs_Cylinder:
            act_cyl += 1
        exp.Next()

    # 5. Solids and Shells count
    solids_cnt = 0
    exp_sol = TopExp_Explorer(shape, TopAbs_SOLID)
    while exp_sol.More():
        solids_cnt += 1
        exp_sol.Next()

    shells_cnt = 0
    exp_sh = TopExp_Explorer(shape, TopAbs_SHELL)
    while exp_sh.More():
        shells_cnt += 1
        exp_sh.Next()

    # 6. BRepCheck Validity
    analyzer = BRepCheck_Analyzer(shape)
    is_brep_valid = bool(analyzer.IsValid())

    # Comparisons
    delta_vol = abs(act_vol - expected["volume_mm3"])
    delta_bbox = [abs(act_bbox[i] - expected["bbox_mm"][i]) for i in range(3)]
    delta_max_bbox = max(delta_bbox)
    delta_area = abs(act_area - expected["surface_area_mm2"])
    delta_faces = abs(act_faces - expected["face_count"])
    delta_cyl = abs(act_cyl - expected["cylindrical_face_count"])

    tol_vol = 1e-3
    tol_bbox = 1e-3
    tol_area = 1e-2

    pass_vol = delta_vol < tol_vol
    pass_bbox = delta_max_bbox < tol_bbox
    pass_area = delta_area < tol_area
    pass_faces = (delta_faces == 0) and (delta_cyl == 0)
    pass_solids = (solids_cnt == 1)
    pass_shells = (shells_cnt == 1)
    pass_validity = is_brep_valid
    all_pass = pass_vol and pass_bbox and pass_area and pass_faces and pass_solids and pass_shells and pass_validity

    print("=========================================================================================")
    print(" U-BRACKET CROSS-CHECK REPORT")
    print("=========================================================================================")
    print(f"{'Metric':<25} | {'Analytical':<15} | {'Actual BRep':<15} | {'Delta':<12} | {'Status'}")
    print("-----------------------------------------------------------------------------------------")
    print(f"{'Volume (mm³)':<25} | {expected['volume_mm3']:<15.6f} | {act_vol:<15.6f} | {delta_vol:<12.6e} | {'PASS' if pass_vol else 'FAIL'}")
    print(f"{'BBox dx (mm)':<25} | {expected['bbox_mm'][0]:<15.6f} | {act_bbox[0]:<15.6f} | {delta_bbox[0]:<12.6e} | {'PASS' if delta_bbox[0] < tol_bbox else 'FAIL'}")
    print(f"{'BBox dy (mm)':<25} | {expected['bbox_mm'][1]:<15.6f} | {act_bbox[1]:<15.6f} | {delta_bbox[1]:<12.6e} | {'PASS' if delta_bbox[1] < tol_bbox else 'FAIL'}")
    print(f"{'BBox dz (mm)':<25} | {expected['bbox_mm'][2]:<15.6f} | {act_bbox[2]:<15.6f} | {delta_bbox[2]:<12.6e} | {'PASS' if delta_bbox[2] < tol_bbox else 'FAIL'}")
    print(f"{'Surface Area (mm²)':<25} | {expected['surface_area_mm2']:<15.6f} | {act_area:<15.6f} | {delta_area:<12.6e} | {'PASS' if pass_area else 'FAIL'}")
    print(f"{'Total Faces':<25} | {expected['face_count']:<15} | {act_faces:<15} | {delta_faces:<12} | {'PASS' if delta_faces == 0 else 'FAIL'}")
    print(f"{'Cylindrical Faces':<25} | {expected['cylindrical_face_count']:<15} | {act_cyl:<15} | {delta_cyl:<12} | {'PASS' if delta_cyl == 0 else 'FAIL'}")
    print(f"{'Solids Count':<25} | {1:<15} | {solids_cnt:<15} | {abs(solids_cnt - 1):<12} | {'PASS' if pass_solids else 'FAIL'}")
    print(f"{'Shells Count':<25} | {1:<15} | {shells_cnt:<15} | {abs(shells_cnt - 1):<12} | {'PASS' if pass_shells else 'FAIL'}")
    print(f"{'BRepCheck IsValid':<25} | {'True':<15} | {str(is_brep_valid):<15} | {'0':<12} | {'PASS' if pass_validity else 'FAIL'}")
    print("=========================================================================================")
    print(f"OVERALL RESULT: {'PASS' if all_pass else 'FAIL'}")
    print("=========================================================================================")

    if not all_pass:
        sys.exit(1)

if __name__ == "__main__":
    verify_u_bracket()
