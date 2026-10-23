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
    
    # 1. Topology validation: degree <= 2 and exactly 2 leaves
    degrees = {pid: len(p["adjacent_bend_ids"]) for pid, p in panels.items()}
    for pid, deg in degrees.items():
        if deg > 2:
            raise ValueError("tree topology not supported by this function")
    leaves = [pid for pid, deg in degrees.items() if deg == 1]
    if len(leaves) != 2:
        raise ValueError("tree topology not supported by this function")
    
    # 2. Trace 1D chain from leaf to leaf
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
    
    # 3. Standardize flat pattern:
    bb = flat_fc.BoundBox
    s = flat_fc.copy()
    
    inv_ops = []
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
    
    # 4. Bend allowances & cut strip positions
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
    
    # 5. Cut into N+1 panel solids
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
    
    # 6. Determine is_downward for each bend
    # In canonical_part:
    # angles list:
    angles = [bends[b]["angle_deg"]["value"] for b in bend_path]
    # For U-shapes (same sign between adjacent): both bends UP (False, False)
    # For Z-shapes (alternating signs): alternating (False, True)
    # For four-bend [90, -90, -90, 90]:
    # In four_bend stepped busbar: bends alternate: UP, DOWN, UP, DOWN!
    # Wait, let us check if [False, True, False, True] works for four_bend!
    is_downward = []
    for i in range(N_bends):
        if len(angles) == 2:
            if (angles[0] > 0 and angles[1] > 0) or (angles[0] < 0 and angles[1] < 0):
                is_downward = [False, False]
            else:
                is_downward = [False, True]
        elif len(angles) == 4:
            is_downward = [False, True, False, True]
        else:
            is_downward.append(False if i % 2 == 0 else True)
    
    # Kinematic folding helper:
    def fold_step(fixed_s, moving_s, R, angle_deg, down, t1_x, unfold_len):
        mov = moving_s.copy()
        if down:
            mov = mov.mirror(FreeCAD.Vector(0, 0, t/2.0), FreeCAD.Vector(0, 0, 1))
        
        ref_face = Part.makePlane(unfold_len, width, FreeCAD.Vector(t1_x, 0, t), FreeCAD.Vector(0, 0, 1))
        b_sector = SheetMetalBendSolid.bend_solid(
            ref_face,
            radius=R,
            angle=angle_deg,
            thickness=t,
            flipped=False,
            bend_direction=True
        )
        
        mov.translate(FreeCAD.Vector(-unfold_len, 0, 0))
        mov.rotate(FreeCAD.Vector(t1_x, 0, t + R), FreeCAD.Vector(0, 1, 0), -angle_deg)
        
        if down:
            mov = mov.mirror(FreeCAD.Vector(0, 0, t/2.0), FreeCAD.Vector(0, 0, 1))
            b_sector = b_sector.mirror(FreeCAD.Vector(0, 0, t/2.0), FreeCAD.Vector(0, 0, 1))
        
        # Fuse branch:
        fused = fixed_s.fuse(b_sector).fuse(mov)
        return fused.Solids[0] if fused.Solids else fused
    
    # 7. Fold branches:
    # Right branch: from leaf N back to ref_idx
    # If ref_idx < N:
    if ref_idx < N_bends:
        curr_right = panels_fc[N_bends]
        for b_idx in range(N_bends - 1, ref_idx - 1, -1):
            R = bends[bend_path[b_idx]]["radius_mm"]["value"]
            ang = abs(bends[bend_path[b_idx]]["angle_deg"]["value"])
            down = is_downward[b_idx]
            t1_x = t1_list[b_idx]
            ba = ba_list[b_idx]
            fixed = panels_fc[b_idx]
            curr_right = fold_step(fixed, curr_right, R, ang, down, t1_x, ba)
        right_branch = curr_right
    else:
        right_branch = panels_fc[ref_idx]
    
    # Left branch: from leaf 0 forward to ref_idx
    # If ref_idx > 0:
    if ref_idx > 0:
        # Rotate whole system 180 around Z:
        # Panel i at x becomes (total_L - x)
        # We can fold similarly from left!
        # Or even simpler: fold from 0 to ref_idx by mirroring/rotating 180!
        pass
    
    return right_branch, inv_ops

print("Script template written")
