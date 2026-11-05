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

## Findings from FreeCAD 1.1.3 diagnostic: native_d6_20261105T102337Z.json

Mesh Boolean is **NOT QUALIFIED**. The user-supplied report establishes:

- 200 mm3 coplanar overlap: all four Mesh Boolean modes (reused, copied, reverse operand order, retessellated) failed complete-repeatability qualification; many nonempty intersections are NOT closed solids.
- 162 mm3 staggered overlap: 38/40 correct under reuse; 32/40 copied; 30/40 reversed; 34/40 retessellated.
- Both separated and touching cases yielded 40/40 results in all Mesh modes, but a zero-volume Mesh intersection does NOT distinguish physical contact from clearance.
- Native `Part.common` returned correct 200 and 162 mm3 and 0 mm3 for separated and touching fixtures for all 10 control repetitions. `Part.distToShape` returned zero on both penetrating and touching fixtures; a separate distance/contact policy is mandatory.

**Decision:** do not adopt FreeCAD `Mesh.intersect` for physical collision certification. Prefer `Part` with conservative AABB filtering and state/pose caching. The claim that `Mesh.intersect` produces valid volumetric intersection bodies is not supported by the results.

### Next native Part benchmark

`tools/run_d6_part_benchmark.py` tests 1000 separated-box checks plus 100 each of positive-volume overlaps (including coplanar), exact face touch, 0.01 mm gap and 0.1 mm gap, using `smbs_core.kinematics2d.freecad_native_collision.classify_solids`. The test uses FreeCAD Part only, no external packages.

Run in the FreeCAD console after updating this branch:

```python
from pathlib import Path
import smbs_core, runpy
p = Path(smbs_core.__file__).resolve().parent.parent / "tools" / "run_d6_part_benchmark.py"
runpy.run_path(str(p), run_name="__main__")
```

Report: `%USERPROFILE%\\pressbrake_traces\\part_benchmark_<timestamp>.json`.

This is a **static geometric qualification**, not a validated sheet-metal bend sequence, real tool/die assembly, or continuous collision detector.

## Native Part benchmark — user-supplied real FreeCAD 1.1.3 (2026-11-05)

Report: `part_benchmark_20261105T110210Z.json`. All 1500 static checks passed (six fixtures, zero unexpected outcomes).

| Fixture | Iterations | Median ms | Result |
|---|---:|---:|---|
| Well separated | 1000 | 1.2130 | 1000 CLEAR |
| Coplanar 200 mm³ overlap | 100 | 4.91835 | 100 COLLISION; 199.99999999999994 mm³ |
| Staggered 162 mm³ overlap | 100 | 4.3214 | 100 COLLISION; 162 mm³ |
| Face touch | 100 | 5.14375 | 100 UNVERIFIED |
| 0.01 mm clearance | 100 | 4.0395 | 100 UNVERIFIED |
| 0.10 mm clearance | 100 | 1.22485 | 100 CLEAR |

**Decision:** the built-in FreeCAD Part backend passes *static cube* regression, while Mesh Boolean does not. This is not evidence for B01–B08, continuous trajectories, tool contact policy, or production safety. Production adapter stays disconnected pending state/pose integration and real imported geometry tests.

The AABB-separated path takes ~1.2 ms despite not calling `Part.common`. A *hypothesis* is that repeated `Shape.isValid()`/solid checks dominate. To measure that, a separate script `tools/run_d6_native_overhead.py` profiles the unchanged static boxes through: bounding boxes only, validation only, existing full classifier for separate and overlapping boxes, raw `Part.common`, raw `Part.distToShape`.

Run after updating `d6-freecad-native`:

```python
import runpy
from pathlib import Path
import smbs_core
p = Path(smbs_core.__file__).resolve().parent.parent / "tools" / "run_d6_native_overhead.py"
runpy.run_path(str(p), run_name="__main__")
```

Report: `%USERPROFILE%\\pressbrake_traces\\part_overhead_<timestamp>.json`.

No optimization is applied yet: if caching validation is later introduced, it MUST invalidate on shape change or FoldState pose update, and never reuse stale clearance results.

## Per-call overhead measured in FreeCAD 1.1.3

User report: `part_overhead_20261105T112019Z.json` (500 runs each):

| Operation | Median ms |
| --- | ---: |
| AABB only | 0.04310 |
| Shape null/valid/closed/volume tests | 1.22945 |
| Existing complete classifier, separated | 1.27695 |
| Existing complete classifier, intersecting | 5.11105 |
| Part.common only (intersecting) | 3.64880 |
| Part.distToShape only (separated) | 1.93790 |

**Finding:** repeated geometric validation is a significant avoidable cost on already validated, unchanged shapes. AABB alone is roughly 29.6 times faster than the current full separated classifier in this microbenchmark. This does **not** imply a whole-solver speedup by that factor because close-pair Boolean work and search state overhead remain.

### Experimental prepared-state path

`prepare_solid(shape)` creates and validates a copied solid, snapshots bounding-box numbers, and returns a `PreparedSolid`. `classify_prepared(a,b)` does AABB-first screening and FreeCAD Part Boolean/distance only when required, without repeating validity tests each call.

**Critical contract:** The prepared `Part.Shape` is treated as read-only and exists solely for a specific geometric pose/state. Any bend, placement, tool change, or shape mutation requires preparing a fresh state. Never use an identity-based global cache of a mutable FreeCAD object. Invalid geometries must not reach `classify_prepared` without preparation. Both methods fail closed on invalid inputs or errors.

This is currently a **separate experimental API**. The existing `classify_solids`, sequence search, FreeCAD UI and press-cycle animation are not changed.

A renewed `tools/run_d6_native_overhead.py` includes three extra cases:
- creating a validated prepared solid;
- repeated prepared disjoint checks;
- repeated prepared overlapping checks.

Use the documented runner and upload the new `part_overhead_*.json` file. This comparison will establish whether the expected saving materializes inside FreeCAD 1.1.3.

## Decision gates

- If `reuse` fails but `copies` succeeds consistently, isolate mutable Mesh state and update the adapter to use independent copies only; repeat on real imported punch and die.
- If `reverse_order` changes results, document Boolean operand-order dependence.
- If all mesh modes fail on identical overlap, **do not use Mesh.intersect in production**. Retain MeshPart for tessellation and FreeCAD `Part` with AABB-first prefilters for authoritative collision checks.
- If `Part.common` fails on the fixtures too, investigate input orientation, solids and coordinate-frame construction rather than switching libraries.

This diagnostic does not prove continuous approach/bend/retract/transfer clearance and must not be mapped to `StepEvidence.VERIFIED` until physically complete validation exists.
