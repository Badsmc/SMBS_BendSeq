import os
import sys
import json
from pathlib import Path

root_dir = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(root_dir))
sys.path.append('/Applications/FreeCAD.app/Contents/Resources/lib')
sys.path.append(os.path.expanduser('~/Library/Application Support/FreeCAD/v1-1/Mod/sheetmetal'))

from tests.test_sheetmetal_4bend import create_4bend_stepped_busbar

import FreeCAD
import Part
import SheetMetalNewUnfolder
from OCC.Core.BRepTools import breptools
import tempfile

def main():
    shape_occ = create_4bend_stepped_busbar(l1=40.0, l2=30.0, l3=50.0, l4=30.0, l5=40.0)

    with tempfile.NamedTemporaryFile(suffix='.brep', delete=False) as tf:
        breptools.Write(shape_occ, tf.name)
        fc_shape = Part.Shape()
        fc_shape.read(tf.name)

    print(f"fc_shape faces count: {len(fc_shape.Faces)}")

    bac = SheetMetalNewUnfolder.BendAllowanceCalculator.from_single_value(0.44, "ansi")

    doc = FreeCAD.newDocument("InspectDoc")
    part_obj = doc.addObject("Part::Feature", "Busbar")
    part_obj.Shape = fc_shape
    doc.recompute()

    # Find largest planar face
    max_area = -1.0
    best_idx = 0
    for idx, face in enumerate(fc_shape.Faces):
        if face.ShapeType == 'Face' and getattr(face.Surface, 'TypeId', '') == 'Part::GeomPlane':
            if face.Area > max_area:
                max_area = face.Area
                best_idx = idx

    facename = f"Face{best_idx + 1}"
    print(f"Selected root face: {facename} (area={max_area})")

    res = SheetMetalNewUnfolder.getUnfold(bac, part_obj, facename)
    root_face, inplace_unbend, trimmed_bend_lines, root_normal, bend_lines_info = res

    print(f"root_face: {root_face}")
    print(f"inplace_unbend: {inplace_unbend} (type: {type(inplace_unbend)})")
    if inplace_unbend:
        print(f"inplace_unbend shape faces: {len(inplace_unbend.Faces)}")
    print(f"trimmed_bend_lines: {trimmed_bend_lines}")
    print(f"root_normal: {root_normal}")
    print(f"bend_lines_info count: {len(bend_lines_info) if bend_lines_info else 0}")

    raw_data = []
    if bend_lines_info:
        for idx, item in enumerate(bend_lines_info):
            verts = []
            if hasattr(item, "line") and hasattr(item.line, "Vertexes"):
                for v in item.line.Vertexes:
                    p = v.Point
                    verts.append([round(p.x, 4), round(p.y, 4), round(p.z, 4)])

            item_dict = {
                "idx": idx,
                "class": item.__class__.__name__,
                "angle": getattr(item, "angle", None),
                "radius": getattr(item, "radius", None),
                "line_length": round(item.line.Length, 4) if hasattr(item, "line") and hasattr(item.line, "Length") else None,
                "line_vertices": verts
            }
            raw_data.append(item_dict)

    out_json = root_dir / "tests" / "fixtures" / "sheetmetal_contract.json"
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(raw_data, f, indent=2)

    print(f"Saved raw bend_lines_info to {out_json}")
    print(json.dumps(raw_data, indent=2))

if __name__ == "__main__":
    main()
