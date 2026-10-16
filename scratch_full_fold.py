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

def fold_multi(flat_fc, cp):
    t = cp["thickness_mm"]["value"]
    panels = cp["panels"]
    bends = cp["bends"]
    ref_p = cp["reference_panel_id"]
    
    degrees = {pid: len(p["adjacent_bend_ids"]) for pid, p in panels.items()}
    for pid, deg in degrees.items():
        if deg > 2:
            raise ValueError("tree topology not supported by this function")
    leaves = [pid for pid, deg in degrees.items() if deg == 1]
    if len(leaves) != 2:
        raise ValueError("tree topology not supported by this function")
    
    curr_p = leaves[0]
    visited_p = [curr_p]
    bend_path = []
    while True:
        adjs = [b for b in panels[curr_p]["adjacent_bend_ids"] if b not in bend_path]
        if not adjs:
            break
        b_id = adjs[0]
        bend_path.append(b_id)
        next_ps = [p for p, pinfo in panels.items() if b_id in pinfo["adjacent_bend_ids"] and p != curr_p]
        curr_p = next_ps[0]
        visited_p.append(curr_p)
    
    b0 = bends[bend_path[0]]
    st0 = b0["bend_line_flat_pattern"]["start_mm"]
    en0 = b0["bend_line_flat_pattern"]["end_mm"]
    is_x = abs(en0[0] - st0[0]) < 1e-3
    
    pos_path = [bends[b]["bend_line_flat_pattern"]["start_mm"][0 if is_x else 1] for b in bend_path]
    increasing = all(pos_path[i] < pos_path[i+1] for i in range(len(pos_path)-1))
    if not increasing:
        visited_p.reverse()
        bend_path.reverse()
        pos_path = [bends[b]["bend_line_flat_pattern"]["start_mm"][0 if is_x else 1] for b in bend_path]
    
    ref_idx = visited_p.index(ref_p)
    
    # Standardize flat_fc to canonical frame:
    # Track forward transformations to invert later
    bb = flat_fc.BoundBox
    s = flat_fc.copy()
    
    inv_ops = [] # list of functions to invert
    if abs(bb.XLength - t) < 1e-2:
        s.rotate(FreeCAD.Vector(0,0,0), FreeCAD.Vector(0,1,0), -90)
        inv_ops.append(lambda sh: sh.rotate(FreeCAD.Vector(0,0,0), FreeCAD.Vector(0,1,0), 90))
    elif abs(bb.YLength - t) < 1e-2:
        s.rotate(FreeCAD.Vector(0,0,0), FreeCAD.Vector(1,0,0), 90)
        inv_ops.append(lambda sh: sh.rotate(FreeCAD.Vector(0,0,0), FreeCAD.Vector(1,0,0), -90))
    
    bb2 = s.BoundBox
    tr_v1 = FreeCAD.Vector(-bb2.XMin, -bb2.YMin, -bb2.ZMin)
    s.translate(tr_v1)
    inv_ops.append(lambda sh, v=tr_v1: sh.translate(-v))
    
    if not is_x:
        s.rotate(FreeCAD.Vector(0,0,0), FreeCAD.Vector(0,0,1), 90)
        inv_ops.append(lambda sh: sh.rotate(FreeCAD.Vector(0,0,0), FreeCAD.Vector(0,0,1), -90))
        bb3 = s.BoundBox
        tr_v2 = FreeCAD.Vector(-bb3.XMin, -bb3.YMin, -bb3.ZMin)
        s.translate(tr_v2)
        inv_ops.append(lambda sh, v=tr_v2: sh.translate(-v))
    
    total_L = s.BoundBox.XLength
    width = s.BoundBox.YLength
    
    N_bends = len(bend_path)
    ba_list = []
    t1_list = []
    t2_list = []
    for i in range(N_bends):
        binfo = bends[bend_path[i]]
        ang = abs(binfo["angle_deg"]["value"])
        R = binfo["radius_mm"]["value"]
        k = binfo["k_factor"]["value"]
        ba = math.radians(ang) * (R + k * t)
        pos = pos_path[i]
        t1 = pos - ba / 2.0
        t2 = pos + ba / 2.0
        ba_list.append(ba)
        t1_list.append(t1)
        t2_list.append(t2)
    
    # Cut into panels
    panels_fc = []
    box_margin = 100.0
    for i in range(N_bends + 1):
        p_min_x = 0.0 if i == 0 else t2_list[i-1]
        p_max_x = total_L if i == N_bends else t1_list[i]
        cut_b = Part.makeBox(p_max_x - p_min_x, width + 2*box_margin, t + 2*box_margin,
                             FreeCAD.Vector(p_min_x, -box_margin, -box_margin))
        p_shape = s.common(cut_b)
        solid = p_shape.Solids[0] if p_shape.Solids else p_shape
        panels_fc.append(solid)
    
    # Determine upward / downward for each bend in bend_path:
    # Rule:
    # For U-bracket: all bends turn SAME direction (all UP).
    # For Z-bracket: alternating direction.
    # In canonical_part, look at angle signs:
    # u_bracket: [-90, -90] -> signs equal -> same direction
    # u_60_30: [60, 30] -> signs equal -> same direction
    # u_90_90: [90, 90] -> signs equal -> same direction
    # z_ok: [90, -90] -> signs opposite -> alternating
    # z_10_20_10: [-90, 90] -> signs opposite -> alternating
    # 135_ok: [-45, 45] -> signs opposite -> alternating
    # four_bend: [90, -90, -90, 90] -> alternating!
    # Let us compute is_downward list:
    is_downward_list = []
    curr_down = False # first bend is always UP (relative to reference orientation)
    # But wait: is bend 0 UP or DOWN?
    # In FreeCAD: bend with positive angle is folded up.
    # Let us check whether bend[i] alternates or stays same:
    # For i=0: is_downward = False (UP)
    # For i>0: if signs[i] == signs[i-1]:
    #   in U-bracket, signs[i]==signs[i-1] means BOTH turn UP! So is_downward stays False!
    #   in four_bend: signs are [1, -1, -1, 1]. In four_bend, bend 1 and 2 have same sign (-1), but in stepped busbar it steps UP then flat then DOWN then flat then UP!
    # Wait! Let us check four_bend geometry!
    pass
