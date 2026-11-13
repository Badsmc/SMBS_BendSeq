# GitHub research: architecture for a 1–3-minute SMBS Manual native 3D check

Date: 2026-11-13. **This is an investigated engineering strategy, not a measured 1–3-minute result.**
Source branch: `development/performance-optimization`. User goal: ten-bend manual sequence ~22 min → 60–180 s, while never turning a real collision, conditional contact or an incomplete sampled path into a false `CLEAR`.

## 1. Facts verified in our code and latest real Windows FreeCAD journal

See `PERFORMANCE_LOG9_DIAGNOSTIC.md` for the exact setup and source fingerprint.

- Current reference run: **1321.3356 seconds**, manual order `B07 B03 B05 B01 B08 B04 B09 B10 B06 B02`; 11 rigid panels, 10 bend zones; native D6 with selected AP1017 and AT3015 V12, virtual active tool length, 180° setup, Conditional 10mm. B02 RETRACT has proven punch penetration; earlier bends B08/B04/B06 are CONDITIONAL. This is NOT a continuously certified safe part.
- 297 `build_multi_bend_sheet_solid` reconstructions, each running a many-component `multiFuse` and strict `isValid/isClosed/one-solid/volume` qualification.
- 627 `Part.common` (153.878s measured), 548 `distToShape` (179.738s), sheet qualification 139.231s, sheet union 88.146s, bend-zone handling 82.841s, panel rigid transforms 6.958s.
- These timed regions sum to ~650.79s; the **remaining ~670.5s** are not identified by these counters. They may include pairwise `check_solid_validity`, workpiece/machine transforms, tooling shape generation, component diagnostics, and general overhead. **Do not assert the cause without timed evidence.**
- 1–3 min needs **7.34×–22.02× throughput improvement**, i.e. ~86.4%–95.5% of current wall time eliminated. Micro-optimizations alone cannot be assumed sufficient.

### Actual hot path

`Manual → check_manual_3d_sequence → check_active_25d_bend_trajectory → AdaptiveTrajectoryValidator3D.create_state_snapshot → build_multi_bend_sheet_solid → check_pair_clearance (native BRep validity, AABB, OCC exact distance and Common) → Conditional classification`.

The production builder currently **always** computes the complete one-solid `multiFuse` and qualifies it before tool collision. It has cached rigid source prisms and folded bend zones, but still builds a new *joined whole sheet* for most newly sampled angles.

## 2. Research: candidate open-source implementations

| Repository | Actual applicable idea | Important limitation | Decision |
|---|---|---|---|
| [MoheyElbaz/FreeCAD-ClashCheck](https://github.com/MoheyElbaz/FreeCAD-ClashCheck/blob/main/freecad/ClashCheck/core.py) | Split an assembly into solids; AABB first, face-BBox pruning, `a.common(b)` only on remaining pairs; record exact hit geometry and failures | Implements discrete solid interference, not press-brake motion; reported ~3 min for ~90 pieces; GPL-3.0-or-later licensing needs review before code reuse | **Adopt architecture, write our own code** |
| [open-mrp/dfm/brake](https://github.com/open-mrp/dfm/tree/main/brake) | Bend-state tree, rigid panel transforms, cached buffers, local spatial hash, tool and ram interference; stop entire-order search on first failure | Samples points/grid, explicitly can miss thin features and shallow overlaps; Go implementation, not native OCC CAD | **Use for search/screen concept only** |
| [flexible-collision-library/fcl](https://github.com/flexible-collision-library/fcl) and [BerkeleyAutomation/python-fcl](https://github.com/BerkeleyAutomation/python-fcl) | BVH, dynamic AABB tree, GJK-style collision/distance, continuous checks for moving *mesh/convex* objects | Mesh conversion and solver approximations, Windows/FreeCAD binary dependency; exact CAD penetration volume and rounded-sheet geometry not guaranteed | **Optional second-stage coarse screening** |
| [coal-library/coal](https://github.com/coal-library/coal) | Python bindings, BVH + fast GJK/EPA, safety margins, contact points | Not a drop-in replacement for CAD BRep Common; performance comparisons depend strongly on primitive vs mesh and workload | **Benchmark only if native component-first fails** |
| [FreeCAD/FreeCAD](https://github.com/FreeCAD/FreeCAD/blob/main/src/Mod/Part/App/TopoShape.pyi) | Native `Part.Shape.proximity` (triangulation/BVH-based), `BoundBox`, `common`, `multiFuse`, `distToShape`; existing FreeCAD wrapper handles OCC parallel boolean operations | `proximity` is triangulation-dependent; no mesh `CLEAR` certificate near small features. Current FreeCAD main uses `SetRunParallel(true)` in its Boolean wrapper, so simply “turn on parallel” is NOT an unexplored 10× switch | **Use in-place FreeCAD API before extra packages** |
| [KanadeK/bendseq](https://github.com/KanadeK/bendseq) | Deterministic 2D sequence planner, explainable per-step reject codes | Limited to open-chain sections, at most 7 bends in stated exact search scope; does not model arbitrary 3D sheet + machine | **Potential staged planning reference, not native CAD engine** |

`FreeCAD-ClashCheck` can be studied for its pairwise architecture and regression design, but avoid copying code into SMBS without an explicit license compatibility review.

FreeCAD source `FCBRepAlgoAPI_BooleanOperation.cpp` shows `SetRunParallel(Standard_True)` in the current FreeCAD C++ wrapper. The exact packaged FreeCAD 1.1.x configuration must be verified; do not promise that GPU or more cores automatically accelerates `Part.Shape.common`.

## 3. Recommended architecture: certified component-based 3D collision checking

For each *sampled* machine pose of the sheet:

```text
SheetMetal Unfold → authoritative source panels and real BendZone geometry
    ↓
Persistent source solid cache: 11 panel prisms; per-bend zone solids
    ↓
Rigidly transform panels and bent zones to current FoldState (no joined sheet)
    ↓
Conservative AABB/OBB per component against real punch/die
    ├── clearly separated: exclude from exact pair collision work
    └── possible intersection/near contact:
          exact native OCC Common / distance for that component+tool
          ├── proved positive solid collision: short-circuit candidate FAIL
          ├── any numerical ambiguity: UNKNOWN → full native whole-sheet
          └── conditionally end-zone-only: classify ALL affected components
    ↓
Candidate outcome: PROVEN_COLLISION / POSSIBLE_CLEAR / UNKNOWN
    ↓
Independent full-sheet native OCC replay + integrity check for shortlisted plans
    ↓
Final labeled result: COLLISION / CONDITIONAL / SAMPLED_CLEAR /
                      UNVERIFIED, with continuous-sweep claim only if separately proved
```

**Mathematical basis**: if the authoritative sheet at a given pose is the union
`S = S1 ∪ S2 ∪ … ∪ Sn` of all physically valid transformed panel/zone solids,
then `S ∩ T = ⋃(Si ∩ T)`. Therefore (a) a proven positive-volume
`Si ∩ T` **exceeding the strict collision tolerance** is sufficient to
reject an operation; and (b) if **every** component can be certified
separated (not just by a loose sampled mesh), the union is separated.
No full-sheet OCC fusion is needed to prove *these per-pose geometric
predicates*.

**Important safety boundaries**:
- Component union must represent the same sheet, with no overlaps/gaps
  silently dropped. One full-sheet reference and subsequent differential
  checks (including signed angles and special holes/reliefs) are compulsory
  until component representation is proven.
- Multiple individually small component Common volumes could combine to exceed
  the global 1e-4mm³ tolerance; never declare CLEAR by comparing each
  component to the global threshold separately. Sum only when component
  interior nonoverlap is proven; otherwise use a whole-sheet OCC fallback.
- Correct `Conditional` requires evaluating both central and end-zone
  contributions across **all** intersecting components. A first accepted
  endpoint collision alone does not establish conditional safety.
- Potential contact/tolerance/containment cannot be rejected using raw
  AABB separation below the applicable near/contact threshold.
- Component-only checking does **not** automatically prove the sheet remains
  one valid closed solid during motion; keep full native BRep integrity gate
  and label uncertain intermediate configurations UNVERIFIED.
- Current native D6 is *sampled*, not certified continuous. Fast FCL
  `continuousCollide` on mesh does not automatically certify exact FreeCAD
  BRep motion; distinguish that level of evidence explicitly.

## 4. Missing timing instrumentation BEFORE replacing the production oracle

Currently ~670s of the 1321s is outside the named expensive timers. Add
**per-bend/per-phase counters for**:
1. `check_solid_validity` on workpiece/punch/die (call count, elapsed, isValid specifically).
2. Workpiece `Shape.copy`, rigid `transformShape`, punch ram transform and tooling section assembly.
3. Tool evaluation `validate_workpiece_tooling_pose`, Conditional attribution and end-zone bookkeeping.
4. Per-component candidate counts: total 21, AABB rejected, within near threshold, Common needed, verified positive overlap.
5. Time outside kernel+geometry regions, with no double counting.

These metrics determine whether additional validation and transform overhead
are responsible for the unaccounted time. **Without eliminating most of it,
no 1–3 minute target is feasible even if all currently timed work vanished.**

## 5. Ordered implementation in branch development/performance-optimization

### Experiment X0 — Instrumentation and candidate statistics (low risk)

Create `PERF_COMPONENT_BROADPHASE` events, without changing results:
build/transform candidate components for the same sampled poses and count
AABB-pass fractions, whether authoritative union has a proven hit and where
it originated. Keep production native oracle completely unchanged.
Measure overhead separately, avoid doubling baseline timings when reporting.

**Gate X0**: real Windows FreeCAD run, 10 bends, same selected tools,
same source SHA and no change to physical statuses.

### Experiment X1 — Exact component-only *collision rejection*

Run exact Common only for AABB-near components. If any single component Common
is proven `> volume_tolerance_mm3`, reject without full sheet fusion.
Uncertain results MUST defer to the old complete oracle. Include
`reason=COMPONENT_PROVEN_PENETRATION`, exact panel/zone IDs, tool ID, volume,
intersection bounds and pose provenance. On successful rejections, do not
continue computing expensive unrelated machine poses.

**Gate X1**: every rejection agrees with the authoritative whole-sheet OCC
oracle on recorded poses; compare first blocker and log of B02 RETRACT.
No false positive threshold acceptance or Conditional override.

### Experiment X2 — Certified component per-pose clearance

Establish exact source-geometry equivalence and conservative per-pose
separation across **all** components, or downgrade to UNKNOWN.
If all component boxes are strictly separated beyond near/contact margins,
a fast CLEAR for that sampled pose may be justified with an independent
validation gate. For ambiguous poses use exact component pairs and/or whole
sheet. Do not report complete sequence PASS until coverage and full
postcheck criteria are met.

**Gate X2**: exact differential testing vs old native D6 on varied
FCStd parts, including 45° and -45°, thin bends, holes, close-contact,
P09/P11/B09, real end zones, and bad OCC solids.

### Experiment X3 — Thin-beam and tool-sweep acceleration

Only after X1/X2 are correct:
- Native `Part.Shape.proximity` / simple BVH or FCL can quickly rank
  likely nearest panels and identify intervals requiring OCC.
- Cheap 2.5D section filters where tooling and geometry are truly prismatic,
  **without** claiming 2D clearance is full 3D safe.
- Bounded adaptive motion intervals and early concrete rejections.
- Multi-process FreeCADCmd *on independent auto-search candidates* after
  one-process per-candidate equivalence and peak RAM metrics.

### Measured decision gates

Run identical 10-bend manual order (3 cold/3 warm if feasible) with
instrumented actual FreeCAD code; compare old and new per-bend status,
first true blocker, sampled phase and unresolved intervals, exact BRep
penetration evidence, operations, peak RAM and wall time.

Progress checkpoints: 1321s baseline → <600s → <300s → target 60–180s.
These are **aspirational decision gates**, not performance promises.
If the component path cannot beat 300s with unchanged verdicts, investigate
native-C++/FCL broadphase or optional operator `FAST_SCREENED` mode that
has separate uncertainty semantics, not a fraudulent `FULL_3D_VERIFIED`.

## 6. Decision

**Do not replace OCC with Coal/GPU immediately.** Use the authoritative
existing FreeCAD/OpenCASCADE shapes, but **stop rebuilding and fusing every
component into one giant solid simply to find a collision**. Pairwise
component broadphase is the most plausible architecture for a ~7×+ gain
in SMBS, and it is already demonstrated in independent FreeCAD tooling.

Implement only on this experimental branch with headless tests and live
native differential verification; no release installation, no altered
physical collision criteria, and no disappearance of Desktop diagnostics.
