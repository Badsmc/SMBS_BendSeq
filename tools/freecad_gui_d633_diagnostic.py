"""freecad_gui_d633_diagnostic.py - Diagnostic script to run directly in FreeCAD Python Console.

Instructions for user:
1. Open FreeCAD (graphical interface).
2. Open View -> Panels -> Python console (if not already visible).
3. Paste and run this script in the Python console.
4. Observe the live 3D visualization, console log, and physical qualification results.
"""
import sys
from pathlib import Path

# Add project root to sys.path
REPO_ROOT = Path("/Users/badmc/Desktop/SMBS_BendSeq")
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import FreeCAD as App
import FreeCADGui as Gui
import Part

from smbs_core.kinematics2d.native_tool_loader import load_native_die, load_native_punch, check_tool_coverage
from smbs_core.kinematics2d.native_collision_3d import validate_workpiece_tooling_pose, qualify_machine_setup

print("=" * 60)
print("SMBS BendSeq D6.3.3 FreeCAD GUI Live Diagnostic")
print("=" * 60)

# Check active document
doc = App.ActiveDocument
if doc is None:
    print("Opening fixture L-(ok).FCStd...")
    fixture_path = "/Users/badmc/Desktop/bend_Seq/BendSeq_Fixtures_Source/L-(ok).FCStd"
    doc = App.openDocument(fixture_path)

print(f"Active Document: {doc.Name}")

# Load Punch and Die with 2x100mm assembly
punch_file = REPO_ROOT / "toollibrary" / "punch" / "puancon.FCStd"
die_file = REPO_ROOT / "toollibrary" / "die" / "matrica.FCStd"

print("Loading native 200mm tooling assembly...")
punch = load_native_punch(punch_file, ram_z_mm=50.0, section_offsets_xm=[-50.0, 50.0])
die = load_native_die(die_file, section_offsets_xm=[-50.0, 50.0])

# Add objects to document for visual inspection
p_obj = doc.addObject("Part::Feature", "D633_Punch_Assembly")
p_obj.Shape = punch.machine_shape
p_obj.ViewObject.ShapeColor = (0.8, 0.2, 0.2)  # Red

d_obj = doc.addObject("Part::Feature", "D633_Die_Assembly")
d_obj.Shape = die.machine_shape
d_obj.ViewObject.ShapeColor = (0.2, 0.2, 0.8)  # Blue

doc.recompute()
Gui.SendMsgToActiveView("ViewAxometric")
Gui.SendMsgToActiveView("ViewFit")

print("D6.3.3 Visual Objects created in FreeCAD tree:")
print(" - D633_Punch_Assembly (Red)")
print(" - D633_Die_Assembly (Blue)")

# Check tool coverage
cov_p = check_tool_coverage((-100.0, 100.0), punch.span_xm, punch.sections)
cov_d = check_tool_coverage((-100.0, 100.0), die.span_xm, die.sections)
print(f"Punch Coverage: {cov_p.status} (Covered: {cov_p.is_covered})")
print(f"Die Coverage:   {cov_d.status} (Covered: {cov_d.is_covered})")
print("=" * 60)
print("GUI DIAGNOSTIC COMPLETED.")
