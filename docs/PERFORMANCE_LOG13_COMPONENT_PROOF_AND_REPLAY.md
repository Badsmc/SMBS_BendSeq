# Log13: FreeCAD exact component proofs + single-blocker replay

Date: 2026-11-13. Original user-supplied private journal:
`SMBS_debug_log(20261113-144507).json`; full Desktop JSON was **not**
committed. Branch: `experiment/component-collision-oracle`.

## Exact Windows native outcome

Source Mod runtime composite fingerprint:
`cc6c178b8d0aea1322e7207a3ebbe1d65962ab3d7bfc9cc4f6992d0321ef146e`.
The ZIP reports branch/commit UNKNOWN. Same recorded 11-panel 10-bend
SheetMetal FCStd path, AP1017 / AT3015 V12, virtual punch/die equal to
active bend length, reversed orientation for all 10 bends, 10mm
Conditional, `arc_segments=128`. Actual part/punch/die file SHA-256
remains **NOT IN JOURNAL**. This is native FreeCAD observation, not a
source-binary-matched benchmark.

| Observation | This run | Earlier shadow OFF run |
| --- | ---: | ---: |
| Full 10-bend manual wall time | **1051.5764 s (17:31.6)** | 1046.8586 s |
| Wall difference | **+4.7178s (+0.45%)** | baseline |
| Full native joined-sheet builds | **297** | 297 |
| Native `Part.common` calls | **728** | 627 |
| Native distance calls | **548** | 548 |
| Component probes / tool screens | **11 / 22** | 0 |
| Component AABB-disjoint pairs | **330 / 462 (71.43%)** | not measured |
| Component candidates | **132** | not measured |
| Extra exact component Common | **101** | 0 |
| Positive component-to-punch proofs | **5** | not measured |
| Disagreements with FULL OCC (on these proofs) | **0** | not measured |
| Incomplete diagnostic screens | **0** | not measured |
| Isolated component-only RETRACT rebuilds | **1; 0.104333 s** | 0 |
| Total screen measured time | **2.268650 s** | 0 |

Kernel call accounting agrees exactly: baseline 627 + component 101
= 728. This mode is an **experiment with extra work**, not a production
speedup or full shape-equivalence certificate.

## Correctly preserved physics

- B07, B03, B05, B01, B09, B10:
  `SAMPLED_3D_CLEAR_NOT_CONTINUOUS`, NOT certified continuous clearance.
- B08, B04, B06: `CONDITIONAL` only, NOT real tool clearance.
- B02: **COLLISION PUNCH during RETRACT**, preserved.
  Refined first sampled blocker bracket is
  `u=(0.140625, 0.156250]`, with first sampled blocking
  `ram_z=-1.9313275824316825 mm` at 90°. Continuous interval not certified.

**Where shadow hits occurred:**

- B08 BENDING u=1.0, punch vs `P09`: PROVEN_PENETRATION, whole
  punch status PENETRATION but accepted only by Conditional end-zone policy.
- B04 BENDING u=1.0, punch vs `P09`: same Conditional caveat.
- B06 BENDING u=1.0, punch vs `P09`: same Conditional caveat.
- B02 BENDING u=1.0, punch vs `P09`: positive exact component Common;
  full OCC reported PENETRATION. Do not infer the BENDING phase was rejected
  from one sampled pose: the native phase outcome remains in the original report.
- **B02 RETRACT u=1.0, punch vs `P01`**: positive exact component Common,
  full OCC PENETRATION; not the *first* collision pose.

The independent full-sheet blocker attribution on **earliest sampled B02
RETRACT u=0.15625** instead reports `panel:P09` ≈ 500.926423 mm³,
`panel:P11` ≈ 500.926423 mm³, and the **central**
`bend_zone:B09` ≈ 2.751760 mm³. Distinguish these first-blocker
results from the separate shadow endpoint `P01` hit. The central zone
penetration must NOT be suppressed by virtual 10mm Conditional allowances.

## Engineering conclusion

1. The component screen genuinely detects **real positive BRep
   penetration**. Zero mismatches here means five observed positive
   comparisons agreed with the full OCC result; it does NOT prove
   component-only clearance, sheet topology, global overlap sum or swept
   continuous safety.
2. AABB skipped 71.43% of component/tool pair work, but the union/validation
   calls remain untouched; hence no observed speed improvement.
3. Approx. 0.104 seconds to reconstruct isolated components for the cached
   B02 RETRACT probe is promising, but it is **one sample** and is NOT
   enough to predict average component-build or full-run acceleration.
4. Three Conditional punch hits require complete end-zone classification
   before using positive hits to prune an automatic search. Never reject
   these by a generic `PROVEN_PENETRATION` predicate.
5. The remaining large native regions are full-sheet topology validity,
   full-sheet union, repeated pair sanity and exact distance/Common.
   A production no-fuse result would require an **independent** sheet
   integrity proof; that gate has not been established.

## New, inexpensive exact first-blocker replay

New tool: `tools/replay_native_component_blocker.py` (commit `a4c3c946`;
follow-up source-provenance/strictness changes). It is **NOT** the whole
D6 trajectory. It reconstructs ONLY the named panel/zone components at
the exact `first_blocker.sampled_machine_pose` from the original JSON.
It uses the original selected FCStd tool sources and virtual active-bend
length, independently computes the FreeCAD machine placement and compares
its full rotation and translation with the logged native values.
It requires unchanged stable bend IDs, signs and axis geometry.

In **FreeCAD's Python console**, with this experiment branch installed:

```python
from tools.replay_native_component_blocker import run
result = run(r"C:/Users/User/Desktop/SMBS_debug_log.json", bend_id="B02")
print(result["status"])
```

If the original log is backed up under the dated name, pass its full
file path instead. Default output:
`C:/Users/User/Desktop/SMBS_component_blocker_replay.json`.
This replay does **not** modify source FCStd/tool files or the developer log
and does **not** call `multiFuse`. It rechecks the three components
reported by prior independent attribution (P09, P11, bend_zone:B09)
using real exact native component-tool `Common`.
It reports each measured overlap and the difference from the original.
Any invalid source, machine transform, missing component, or mismatch
returns `UNVERIFIED`; no `CLEAR` result is possible. Input file SHA
values are recorded, but previous original log had no CAD SHA hashes,
so binary-identical provenance is NOT proven.

Run this standalone **instead of another 17-minute Manual check** when
the question is specifically whether the component path reproduces the
logged first physical blocker. It cannot qualify nonblocking samples,
unobserved intervals, machine fixtures or complete bend orders.

## Next gate

- Execute the direct FreeCAD replay, obtain exact component volumes for
  the earliest B02 RETRACT blocker, verify geometry/machine transform
  equality and record real FreeCAD runtime/elapsed.
- Develop a conditional-aware **proven-blocker search prefilter**; any
  component hit in the selected virtual punch end zone is not by itself
  a rejecting physical collision.
- Continue parallel work on an independently justified *positive clearance*
  filter without constructing a full sheet. Until that exists, no
  1–3-minute whole-run guarantee and no `FALLBACK_REQUIRED → CLEAR`.
