"""
test_real_freecad_shape.py - Test BendSeq on actual FreeCAD 3D Sheet Metal TopoShape
"""

import sys
import os

SYS_PATH = "/Users/badmc/Desktop/SMBS"
BENDSEQ_PATH = "/Users/badmc/Desktop/SMBS/BendSeq"
if BENDSEQ_PATH not in sys.path:
    sys.path.insert(0, BENDSEQ_PATH)
if SYS_PATH not in sys.path:
    sys.path.insert(0, SYS_PATH)

FREECAD_LIB = '/Applications/FreeCAD.app/Contents/Resources/lib'
if os.path.exists(FREECAD_LIB) and FREECAD_LIB not in sys.path:
    sys.path.append(FREECAD_LIB)

import FreeCAD
import Part
from smbs_core.Core.orchestrator import BendSeqOrchestrator
from smbs_core.Geometry.feature_extractor import FeatureExtractor
from adapters.freecad.freecad_backend import FreeCADBackend


def test_uchannel_shape():
    print("==================================================")
    print(" Testing BendSeq on Real FreeCAD TopoShape        ")
    print("==================================================")

    # Build a U-channel sheet metal TopoShape with 2 cylindrical bends
    b1 = Part.makeBox(100, 100, 2)
    
    # Create cylindrical bend wall 1
    c1 = Part.makeCylinder(4, 100) # inner r=2, T=2 -> outer r=4
    c1.rotate(FreeCAD.Vector(0,0,0), FreeCAD.Vector(1,0,0), 90)
    c1.translate(FreeCAD.Vector(0, 100, 2))

    b2 = Part.makeBox(100, 50, 2)
    b2.rotate(FreeCAD.Vector(0,0,0), FreeCAD.Vector(1,0,0), 90)
    b2.translate(FreeCAD.Vector(0, 100, 2))

    uchannel = b1.fuse(c1).fuse(b2)

    print(f"Generated U-Channel shape with {len(uchannel.Faces)} faces.")

    # Test FeatureExtractor
    backend = FreeCADBackend()
    extractor = FeatureExtractor(backend)
    features = extractor.extract_features(uchannel)
    print(f"Extracted Thickness: {features.get('thickness')} mm")
    print(f"Extracted Bends Count: {len(features.get('bends', {}))}")
    for b_id, b_info in features.get("bends", {}).items():
        print(f" - {b_id}: Radius={b_info['radius']}mm, Angle={b_info['angle']}deg, Axis={b_info['axis']}")

    # Execute Backward DFS Planning with Physical Oracle
    backend = FreeCADBackend()
    orchestrator = BendSeqOrchestrator(backend)
    result = orchestrator.run_planning(
        target_shape=uchannel,
        algorithm="dfs",
        output_file="uchannel_bendseq_output.json"
    )

    print(f"\nPlanning Result: Success = {result.get('success')}")
    if result.get("success"):
        print(f"Planning Time: {result['planning_time']:.3f}s, Explored Nodes: {result['nodes_explored']}")
        for step in result["sequence"]:
            print(f"Step {step['step']}: Bend {step['bend_id']} | Backgauge X={step['backgauge']['X_mm']}mm")
    else:
        print(f"Error: {result.get('error')}")


if __name__ == "__main__":
    test_uchannel_shape()
