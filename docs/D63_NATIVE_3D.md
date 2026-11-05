# D6.3 — Native FreeCAD Part 3D geometry of one real SheetMetal bend

Branch `d6-native-3d`, forked from `d6-freecad-native`. No new dependencies or duplicated bend-angle/K-factor math.

## Real FreeCAD test result: 2026-11-05

The user ran `d63_single_bend_20261105T134731Z.json` in **FreeCAD 1.1.3** on a real positive 90° B01, with 200 mm bend line, 1.5 mm thickness, 1.0 mm inner radius, K=0.4. At all 0/25/50/75/100% bend fractions the constructed two-panel+zone union was a **valid, closed, single OCC solid**; therefore the geometric join implementation is promising.

**However the full-sheet volume is NOT conserved**:

| Angle | Full-sheet volume (mm³) |
|---|---:|
| 0° | 29253.982237 |
| 22.5° | 29271.652402 |
| 45° | 29289.314805 |
| 67.5° | 29306.961684 |
| 90° | 29324.585278 |

Final drift is **+70.603042 mm³ (+0.241345%)**. This report's earlier `PASS_SINGLE_BEND_GEOMETRY` verdict meant only successful closed-solid construction and **was not a trustworthy deformation acceptance verdict**. The corresponding bend-zone volume changed from 753.982237 to 824.585278 mm³.

The discrepancy is compatible with the present constant-thickness *concentric inner/outer arc* model paired with a SheetMetal flat allowance `BA=(r+K*t)*phi`. Under these simplifying assumptions its predicted change is `L*t²*(0.5-K)*phi`, or **+70.686 mm³**, close to the measured +70.603 mm³ (the remaining difference is polygonal sampling). This is a **model inconsistency**, not evidence of a bad OCC Boolean. Do not silently set K to 0.5: K=0.4 is authoritative input from SheetMetal and may represent nonuniform plastic strain/thickness change in real bending. A physically justified deformation law or an explicit geometric approximation contract is required.

### Implemented follow-up

- `smbs_core/kinematics2d/native_volume_gate.py`: stand-alone volume consistency check with an *explicit initial engineering acceptance threshold* of max(0.1 mm³, 0.05% of starting full-sheet volume). This threshold is provisional and not based on material-specific experimental data.
- `tools/run_d63_single_bend.py` now emits `zone_volume_consistency`, `full_sheet_volume_consistency`, `expected_ideal_sector_drift_mm3`, and `closed_connected_sheet_samples`.
- When the full OCC shape remains closed but full-sheet volume exceeds tolerance, the top-level result is **`UNVERIFIED_VOLUME_DRIFT`** instead of `PASS_SINGLE_BEND_GEOMETRY`. No motion or physical collision certification is issued.
- `tests/test_d63_volume_gate.py` contains actual user-measured FreeCAD values as regression fixtures.

**Next task:** rerun this corrected diagnostic on the same FCStd to verify `UNVERIFIED_VOLUME_DRIFT` and the detailed geometry evidence. Then choose a physically consistent deformation/volume conservation model using a real CAD comparison, and only after that proceed with complete-machine collision tests.

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
