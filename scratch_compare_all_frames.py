import os, sys, tempfile, shutil, json, math
from pathlib import Path
from smbs_core.coordinator.run_pipeline import Coordinator
import FreeCAD, Part
from OCC.Core.BRepTools import breptools
from OCC.Core.TopoDS import TopoDS_Shape
from OCC.Core.BRep import BRep_Builder
from OCC.Core.Bnd import Bnd_Box
from OCC.Core.BRepBndLib import brepbndlib
from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_Transform
from OCC.Core.ShapeUpgrade import ShapeUpgrade_UnifySameDomain
from smbs_core.Geometry.sheetmetal_folder import SheetMetalFolder
from smbs_core.Geometry.frame_aligner import FrameAligner
from smbs_core.workers.baseline.worker import BaselineWorker

fixtures = [
    ("u_bracket", "fixtures/synthetic/u_bracket.brep"),
    ("four_bend", "fixtures/synthetic/four_bend.brep"),
    ("z_ok", "fixtures/freecad/z_ok/source.FCStd"),
    ("z_10_20_10_ok", "fixtures/freecad/z_10_20_10_ok/source.FCStd"),
    ("135_ok", "fixtures/freecad/135_ok/source.FCStd"),
    ("u_60deg_30deg_ok", "fixtures/freecad/u_60deg_30deg_ok/source.FCStd"),
    ("u_90deg_90deg_ok", "fixtures/freecad/u_90deg_90deg_ok/source.FCStd")
]

for name, fpath in fixtures:
    td = tempfile.mkdtemp()
    try:
        coord = Coordinator(workspace_root=td)
        coord.start_run(
            source_file_path=Path(fpath).resolve(),
            part_id=name,
            k_factor_override=0.44
        )
        runs_dir = Path(td) / "runs"
        for r in runs_dir.iterdir():
            if r.is_dir():
                with open(r / "04_canonical_part.json") as f:
                    cp = json.load(f)["payload"]
                rest_file = r / "restored.brep"
                geom_file = r / "geometry.brep"
                rest_s = TopoDS_Shape()
                geom_s = TopoDS_Shape()
                breptools.Read(rest_s, str(rest_file), BRep_Builder())
                breptools.Read(geom_s, str(geom_file), BRep_Builder())
                
                box_r = Bnd_Box()
                box_g = Bnd_Box()
                brepbndlib.Add(rest_s, box_r)
                brepbndlib.Add(geom_s, box_g)
                print(name)
                print("  restored bbox: ", [round(x, 3) for x in box_r.Get()])
                print("  geometry bbox: ", [round(x, 3) for x in box_g.Get()])
    finally:
        shutil.rmtree(td, ignore_errors=True)
