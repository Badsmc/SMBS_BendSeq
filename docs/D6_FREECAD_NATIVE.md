# D6 FreeCAD-native collision investigations

User requirement: **No FCL, no other pip packages, no separate Python.**

This branch is based on the last stable `d6-mesh-poc` test, not on the FCL branch. Existing FreeCAD 2.5D UI and bend sequence search are unchanged.

## Motivation

Actual FreeCAD 1.1.3 repeated static intersection test on identical boxes:
- True overlap 200 mm3.
- FreeCAD Mesh Boolean returned 140 mm3 (41x), 200 (39x), 260 (16x), 320 (4x). One-shot detection is not sufficient.
- We must locate the source of nondeterminism before deciding if the mesh engine should be used.

FreeCAD sources:
- `MeshObject::intersect` calls `MeshCore::SetOperations::Intersect` on transformed mesh kernels.
- `Mesh.copy()` is supported.
- `Part.Shape.common` and `distToShape` provide an independent B-Rep reference.

## Added implementation

1. `tools/run_d6_freecad_native.py`: FreeCAD in-process experiment. For four known box placements (coplanar overlap 200 mm3, non-coplanar overlap 162 mm3, separate 0, face touching 0), it tries 40 repetitions in each mode:
   - `reuse` same MeshPart meshes;
   - `copies` fresh `mesh.copy()` on each call;
   - `reverse_order` reverse the Boolean operands;
   - `retessellate` fresh MeshPart meshes on each call.
   Each mode records volume histogram, correctness, timing, and errors. `Part.common` and `Part.distToShape` are included as controls. **This does not manipulate FreeCAD documents.**
2. `smbs_core/kinematics2d/freecad_native_collision.py`: conservative FreeCAD native static-solid checker. It uses bounding boxes to reject obviously distant tools and `Part.common`/`Part.distToShape` for nontrivial cases. A touch, invalid geometry or failed boolean is `UNVERIFIED`. It does **not** automatically approve intended tool/sheet support contact.
3. `tests/test_freecad_native_collision.py`: pure-Python contracts, no FreeCAD dependency.
4. `.github/workflows/d6-freecad-native.yml`: CI compilation and backend contracts; **real geometry must be run inside FreeCAD**.

## Run on user's installed FreeCAD 1.1.3

Update the installed addon from branch `d6-freecad-native`. In FreeCAD menu **View → Panels → Python console**:

```python
from pathlib import Path
import smbs_core, runpy
script = Path(smbs_core.__file__).resolve().parent.parent / "tools" / "run_d6_freecad_native.py"
print("Script exists:", script.is_file(), script)
runpy.run_path(str(script), run_name="__main__")
```

Output: `%USERPROFILE%\\pressbrake_traces\\native_d6_<timestamp>.json`.

The first 640 static Mesh Boolean calls include the most expensive diagnostic variant (retessellation); timings may vary. Avoid running this during active FreeCAD animation.

## Decision gates

- If `reuse` fails but `copies` succeeds consistently, isolate mutable Mesh state and update the adapter to use independent copies only; repeat on real imported punch and die.
- If `reverse_order` changes results, document Boolean operand-order dependence.
- If all mesh modes fail on identical overlap, **do not use Mesh.intersect in production**. Retain MeshPart for tessellation and FreeCAD `Part` with AABB-first prefilters for authoritative collision checks.
- If `Part.common` fails on the fixtures too, investigate input orientation, solids and coordinate-frame construction rather than switching libraries.

This diagnostic does not prove continuous approach/bend/retract/transfer clearance and must not be mapped to `StepEvidence.VERIFIED` until physically complete validation exists.
