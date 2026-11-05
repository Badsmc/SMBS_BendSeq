# D6.3 — Native FreeCAD Part 3D geometry of one real SheetMetal bend

Branch `d6-native-3d`, forked from `d6-freecad-native`. No new dependencies or duplicated bend-angle/K-factor math.

## Work completed

- `smbs_core/kinematics2d/native_bend_solid_3d.py`: constructs a genuinely closed FreeCAD `Part.Shape` solid by extruding the existing positive/negative `BendZone.section_profile` along its real SheetMetal bend axis. It uses the current `FoldState` angle and its fixed panel's actual transform. Duplicate flat/intermediate polygon points are filtered; invalid geometry fails closed.
- The same file contains `build_single_bend_sheet_solid`, which takes two actual rigid `PanelBuilder.panel_faces`, gives each the real sheet thickness, transforms with `FoldState`, and attempts to join those panel solids with the bend-zone solid. It **requires** exactly one bend and two panels. It rejects non-connected/non-closed/multi-solid assemblies and abnormal union volume; it cannot silently return a compound as a verified sheet. **No live FreeCAD full-sheet acceptance has yet been observed.**
- `tools/run_d63_single_bend.py`: generates a JSON report from the selected real SheetMetal `Body_Unfold` and five angle fractions. Reports independent `pass` flags for closed zone solid and attempted full sheet. On multi-bend parts it can inspect the active bend zone but explicitly marks full-sheet assembly NOT_RUN.
- `tests/test_d63_native_bend_solid.py`: pure maths checks at 0, 25, 50, 75, 100 percent for both signs and selected non-90 angles, plus optional live FreeCAD Part tests when FreeCAD is installed.
- `.github/workflows/d63-native-3d.yml`: headless Python contracts. GitHub Actions without FreeCAD cannot certify real OCC solids.

## Real FreeCAD 1.1.3 test

1. Update the installed addon to the **d6-native-3d** branch and restart FreeCAD so Python imports use this build.
2. Open a **real SheetMetal FCStd** file containing a SheetMetal unfold and select the exact `Body_Unfold` object (same input as PressBrakeSim).
3. Open **View → Panels → Python console**, then:

```python
import runpy
from pathlib import Path
import smbs_core
root = Path(smbs_core.__file__).resolve().parent.parent
script = root / "tools" / "run_d63_single_bend.py"
print("D6.3 source:", script, "exists:", script.is_file())
_ = runpy.run_path(str(script), run_name="__main__")
```

JSON report is written to **`<installed-addon>/reports/d63_single_bend_*.json`** using the common `report_paths` module. The script prints FAIL with traceback when a model cannot be processed, and writes a JSON diagnostic even in failure.

### Meaning of status

- `PASS_ZONE_ONLY`: all sampled section solids are valid OCC solids, but full-sheet union is not verified or unsupported (e.g. >1 bend).
- `PASS_SINGLE_BEND_GEOMETRY`: all 5 sampled two-panel single-bend unions satisfy connected closed `Part.Shape`/volume tests. **No machine/tool collisions certified.**
- `FAIL`: one or more zone geometry computations, FreeCAD operations, or source preparation failed. Inspect each `full_single_bend_sheet.reason` in the JSON.

## Safety and correctness gates

This prototype deliberately **does not**:
- claim to model multi-bend parts with a missing subset of rigid panels;
- infer missing SheetMetal radius, K-factor, bend direction, machine pose or tooling;
- mark a collision-free machine path based on a closed zone solid;
- modify the user's source FreeCAD document;
- replace production D6 search or D7 weighting with a synthetic success.

Next steps: obtain user FreeCAD reports for a real single-bend L part with both positive and negative bend signs, resolve any OCC solid joining issue, then extend geometry assembly to a complete connected multi-bend sheet and bind the single-cycle physically validated oracle. Only afterwards integrate with DFS and D7.

## Definition of complete D6.3

- Native OCC full-sheet model for a selected **real** single-bend SheetMetal model at all sampled angles;
- validated material thickness and generated shape topology;
- stationary and moving rigid faces join the deformed bending zone without gaps/overlap;
- comparison against source CAD at final angle (within justified tolerance);
- tests of punch/die setup, approach, bend contact and retract with explicit permitted-contact model.

The implemented substage currently addresses geometry construction and diagnosis, **not the entire D6.3 acceptance list**.
