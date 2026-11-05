# D6-Mesh-PoC: FreeCAD Mesh/MeshPart benchmark

**Status:** experimental only. Independent of the 2D/D6 playback and solver. No existing code is removed.

## Goal

Evaluate whether built-in FreeCAD `MeshPart.meshFromShape` + `Mesh.Mesh.intersect` are accurate and fast enough for *static* 3D tool-workpiece interference tests. Compare the same CAD pairs against `Part.Shape.common`. Do not confuse static mesh boolean checks with continuous collision detection (CCD).

## Implementation

- `smbs_core/kinematics2d/mesh_collision_poc.py`: conservative static collider and timings.
- `tools/run_d6_mesh_poc.py`: FreeCAD Python-console runner, uses actual installed FreeCAD MeshPart, Mesh and Part.
- `tests/test_mesh_collision_poc.py`: CI headless contract tests with synthetic mesh objects.
- `.github/workflows/d6-mesh-poc.yml`: CI compilation + pure-Python tests. **CI does not prove FreeCAD Mesh compatibility** without an actual FreeCAD runtime.

Source references:
- FreeCAD `src/Mod/MeshPart/App/AppMeshPartPy.cpp`: `meshFromShape` signature.
- FreeCAD `src/Mod/Mesh/App/Mesh.h`: `intersect(const MeshObject &)` boolean API.
- FreeCAD Mesh API documentation: mesh `isSolid()`, `hasSelfIntersections()`, `Volume` and `CountFacets`.

## Run in Windows FreeCAD 1.1.x

1. Checkout branch `d6-mesh-poc` of `Badsmc/SMBS_BendSeq`.
2. Open FreeCAD. Choose **View → Panels → Python console**.
3. Paste the following, replacing the file path with the actual cloned repository path:

```python
import runpy
runpy.run_path(r"C:\Users\User\Documents\SMBS_BendSeq\tools\run_d6_mesh_poc.py")
```

This is a **path example**, not a detected location. The script does not edit the FreeCAD document. It creates three in-memory pairs of Part boxes: separated (known CLEAR), 2 mm overlapping (known PENETRATION), and exactly touching (must remain UNVERIFIED unless contact can be proved robustly).

The default benchmark runs 1000 checks on separated boxes and 100 each on intersecting and touching boxes; it records a smaller B-Rep control benchmark for each case.

A JSON report is created by default in `~/pressbrake_traces/mesh_poc_<timestamp>.json` (Windows: user home). Its full path is printed in the FreeCAD console.

## Optional measurement of your real tools

Use the *FreeCAD document object names*, not the display labels; both existing objects must be valid closed CAD solids. If actual sheet and tooling are in one open document:

```python
import os, runpy
os.environ["SMBS_MESH_POC_SHEET"] = "Body"
os.environ["SMBS_MESH_POC_TOOL"] = "Punch"
runpy.run_path(r"C:\Users\User\Documents\SMBS_BendSeq\tools\run_d6_mesh_poc.py")
```

Replace `Body` and `Punch` with **the actual object names** from your FreeCAD document. If the solid objects are open in **different FreeCAD documents**, you may use the exact `DocumentName:ObjectName` form, e.g.:

```python
os.environ["SMBS_MESH_POC_SHEET"] = "PartModel:Body"
os.environ["SMBS_MESH_POC_TOOL"] = "PunchModel:Punch"
```

These are placeholders, not automatically detected names. The runner reads the source shapes without moving, merging, or editing either document. Because separate tool files are not necessarily in the same machine coordinate frame, report the result strictly as a geometry/throughput experiment, **not** evidence of valid press setup.

This **does not yet construct meshes from `kinematics2d.FoldState`** or run the eight bend cycles. It evaluates real static CAD objects when available, but their initial global placement may not correspond to press machine coordinates. Do not interpret that result as machine feasibility.

To choose a custom report destination:
```python
os.environ["SMBS_MESH_POC_OUTPUT"] = r"C:\Users\User\Desktop\mesh_poc_report.json"
```

## Real FreeCAD 1.1 test finding (2026-11-05)

The first local run showed:
- Separate 10 mm boxes: `CLEAR`; first single-call duration 39.535 ms (potential cold-start overhead; use benchmark medians rather than this value).
- 2 mm overlapping 10 x 10 x 10 mm boxes: `PENETRATION`, but Mesh Boolean reported **260 mm3**, whereas exact overlap is **200 mm3** (+30% error).
- Face touching: `UNVERIFIED`, no positive volume, as designed.

This means merely reporting `PENETRATION` is **not sufficient** to qualify Mesh Boolean: its numerical result violated an elementary geometric upper bound. D6 PoC now rejects such impossible volumes as `UNVERIFIED`. The fixture benchmark explicitly fails acceptance when its overlap-volume relative error exceeds 1%. The reference test remains experimental; do not switch the production collision engine until the cause is understood and 3D geometry validated.

## Classification contract

- `CLEAR`: **only** world-space AABBs have a gap strictly greater than configurable clearance.
- `PENETRATION`: boolean intersection produced finite positive overlap volume above the threshold.
- `UNVERIFIED`: touching/nearby bodies with no robust positive overlap; missing/damaged mesh, self-intersection, empty or failing Boolean.

A Mesh Boolean cannot by itself prove near-contact separation when it returns an empty result. Absence of positive overlap is not enough for a safe production decision. Triangle resolution, angular deflection, machine coordinate frames, and tolerance require validation.

## Acceptance / next architecture decision

1. Run tests on actual FreeCAD 1.1.x. All three known fixtures must match expected outcomes.
2. Record facet counts, tessellation time, mesh boolean median/p95 and OCC reference time.
3. If mesh booleans are too slow or fragile, **reuse the same tessellation** with an alternative BVH/FCL narrow phase; do not discard extracted tool geometry.
4. Independently construct watertight finite-thickness 3D meshes from the real `FoldState` and compare against the real tool solids over a sampled cycle, then introduce swept-volume/CCD validation for motion between samples.
5. Only after complete scope coverage connect results as production-valid `StepEvidence` in `sequence_search.py`.

**Do not merge into stable `2D` and do not label the current pilot as a production physical validator.**
