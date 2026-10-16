import os, sys, tempfile, shutil, json, math
from pathlib import Path
from smbs_core.coordinator.run_pipeline import Coordinator
import FreeCAD, Part
import SheetMetalBendSolid
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

def fold_branch(panels_subset, bends_subset, t, width, reverse_direction=False):
    # panels_subset: [P_fixed, P_1, P_2, ... P_m] where P_fixed is at the root
    # bends_subset: [B_0, ... B_{m-1}] where B_k is between P_k and P_{k+1}
    # Each bend in bends_subset has: R, ang, down, t1_x, ba
    # We fold from leaf P_m back to root P_fixed!
    curr = panels_subset[-1].copy()
    m = len(bends_subset)
    for k in range(m - 1, -1, -1):
        b = bends_subset[k]
        fixed = panels_subset[k].copy()
        R = b["R"]
        ang = b["ang"]
        down = b["down"]
        t1_x = b["t1_x"]
        ba = b["ba"]
        
        mov = curr.copy()
        if down:
            mov = mov.mirror(FreeCAD.Vector(0, 0, t/2.0), FreeCAD.Vector(0, 0, 1))
        
        ref_face = Part.makePlane(ba, width, FreeCAD.Vector(t1_x, 0, t), FreeCAD.Vector(0, 0, 1))
        b_sector = SheetMetalBendSolid.bend_solid(
            ref_face,
            radius=R,
            angle=ang,
            thickness=t,
            flipped=False,
            bend_direction=True
        )
        mov.translate(FreeCAD.Vector(-ba, 0, 0))
        mov.rotate(FreeCAD.Vector(t1_x, 0, t + R), FreeCAD.Vector(0, 1, 0), -ang)
        
        if down:
            mov = mov.mirror(FreeCAD.Vector(0, 0, t/2.0), FreeCAD.Vector(0, 0, 1))
            b_sector = b_sector.mirror(FreeCAD.Vector(0, 0, t/2.0), FreeCAD.Vector(0, 0, 1))
        
        fused = fixed.fuse(b_sector).fuse(mov)
        curr = fused.Solids[0] if fused.Solids else fused
    return curr

print("Fold branch function ready")
