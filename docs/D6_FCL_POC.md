# D6 FCL PoC: FreeCAD MeshPart + independent collision engine

Status: **EXPERIMENTAL**, no modifications to production 2D/D6 solver.

## Why

FreeCAD 1.1.3 local mesh test, recorded on 2026-11-05:
- 10 mm axis-aligned boxes penetrating 2 mm: true overlap = 200 mm3.
- FreeCAD Mesh Boolean returned 140/200/260/320 mm3 across 100 repetitions (41/39/16/4).
- The old PoC therefore FAILS reproducibility. The AABB-only clear path remains useful.

FCL measures contact and distance, **not the overlapping volume**. It must be evaluated for repeatable collision classification, not compared on an overlap-volume metric it doesn't claim to provide.

## Files

- `smbs_core/kinematics2d/fcl_collision_poc.py`: Mesh.Topology to NumPy triangles to FCL BVHModel. Static collision and distance queries fail closed to UNVERIFIED.
- `tools/run_d6_fcl_poc.py`: FreeCAD runtime benchmark of true MeshPart-generated BVH models and native FCL boxes.
- `tests/test_fcl_collision_poc.py`: repeatability/error contracts and optional real FCL geometry tests.
- `.github/workflows/d6-fcl-poc.yml`: tests real FCL geometry on GitHub Ubuntu, subject to wheel installation.

## How to run on FreeCAD Windows

Switch your local addon to branch `d6-fcl-poc`. In the FreeCAD Python console:

```python
import runpy
from pathlib import Path
import smbs_core
script = Path(smbs_core.__file__).resolve().parent.parent / "tools" / "run_d6_fcl_poc.py"
print(script, script.is_file())
if script.is_file():
    runpy.run_path(str(script), run_name="__main__")
```

If `python-fcl` is **not available** in FreeCAD's bundled Python, the tool writes a JSON report with `dependency_status.available=false` and `acceptance=NOT_RUN`. This is an actionable environment blocker, not a collision failure.

**Do not install the package blindly into the FreeCAD installation**. The Windows wheel must be ABI-compatible with FreeCAD's embedded Python 3.11 and DLL dependencies. Prefer a separate headless Python environment for FCL if package installation in FreeCAD is unsuitable. A verified adapter can exchange mesh coordinates and poses with that process. This PoC intentionally does not modify FreeCAD's Python packages.

Default output: `%USERPROFILE%\\pressbrake_traces\\fcl_poc_<timestamp>.json`.

## Acceptance

Must pass **every one** of 1000 repeated static checks for the same position:
- MeshPart -> FCL BVH: separated cubes return CLEAR, intersecting cubes return COLLISION.
- Native FCL primitive boxes: same expectations.
- Exact face contact is reported diagnostically; it is not assumed safe.
- Errors, nonfinite distances, absent dependencies are UNVERIFIED, not CLEAR.

Do not merge FCL to production purely on this result. Full solution requires:

1. Watertight finite-thickness meshes for all FoldState rigid panels and bending zones at any signed angle.
2. Correct machine-space/tool-local coordinate contracts, station lengths, backgauge, clamps and frame.
3. Explicit valid-support contact policy (punch and die intentionally contact sheet).
4. Continuous collision checking of setup, approach, bending, retract and transfer. FCL has CCD APIs but BVH mesh CCD support and effectiveness must be measured independently.
5. Forward replay validation of all steps and inverse/DFS sequence search using the same oracle, with fail-closed UNKNOWN.
6. Performance comparison on B01-B08, real imported tooling, fixture numerical validation.

## API bases

- python-fcl: https://github.com/BerkeleyAutomation/python-fcl
- FreeCAD Mesh.Topology: https://github.com/FreeCAD/FreeCAD-documentation/blob/main/wiki/Mesh_API.md

There is no FCL dependency in the main addon import path; existing FreeCAD UI remains untouched.
