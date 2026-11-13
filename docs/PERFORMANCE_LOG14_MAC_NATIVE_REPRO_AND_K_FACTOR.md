# Log14 — macOS full manual D6 run: B02 reproduced, source mismatch traced to K-factor

Source logs supplied 2026-11-13:
- `SMBS_debug_log(20261113-195049).json`: **contains two sessions**, an older Windows baseline and a newer macOS run; not simply one macOS log.
- `SMBS_debug_log(20261113-144507)(3).json`: another Windows run with proven component shadow positives (already detailed in LOG13).

## Actual macOS run

- `SMBS_SESSION_STARTED`: macOS 12.7.6 x86_64.
- Original FCStd: `/Users/badmc/Desktop/Новая папка/2-1-3-4-(ok).FCStd`; selected `Body_Unfold`; part consists of 11 panels, 10 bends.
- Installed runtime fingerprint: `d3e9c97be385d959ab57f89d1990c82b3a3b338ffec7b7315838ad0c738753d2`; source branch/ZIP identity is not proof of matching Windows binaries.
- Manual order `B07 B03 B05 B01 B08 B04 B09 B10 B06 B02` and reversed machine axis for each.
- AP1017 punch, AT3015 V12 die, virtual length matched to bend, 128 arc segments, 64 max phase states. End-zone allowance: **20 mm** on Mac; compared Windows had **10 mm**.
- Start source sheet uses **K=0.40**, 1.5mm thickness, 1mm inside radius, BA 90° **2.5132741229mm**. Windows source uses **K=0.31**, BA 90° **2.3012166188mm**.
- Native Manual result: all ten requested steps; 6 `SAMPLED_3D_CLEAR_NOT_CONTINUOUS`, 3 `CONDITIONAL` (B08/B04/B06), B02 `COLLISION` on `RETRACT`. Still sampled-only; transfer/backgauge/clamps/frame unverified.
- Wall **1712.3593s** (28m32s); Mac `PERF_NATIVE_BEND_KERNEL` 10 entries, **627 native Common**, **549 distance**; **297 sheet unions** and **297 sheet qualifications**, **2970 bend-zone constructions**, **1024 pair-geometry sanity**, **312 native RAM events**.
- Component shadow probes **0** in **all 10 bend metrics**. User's hoped-for Mac run with `SMBS_COMPONENT_SHADOW_SAMPLES=2` therefore did **not** exercise component-shadow acceleration. The `adaptive_config.component_shadow_samples_per_bend=0` alone is not the sole evidence: actual `component_shadow.probes=0` confirms no shadow probes.
- Main instrumented expensive native regions: pair geometry sanity ~**325.064s**, sheet qualification ~**199.777s**, sheet union ~**152.282s**, bend zone build ~**134.339s**. These are measured distinct regions, not necessarily an exhaustive total; do not sum to claim total CPU.
- CPU/OS and geometry setup both differ between Mac and Windows; wall ratio **1.63x** is *not* proof of an OS regression.

## Why the previous Windows-to-Mac single-pose replay stopped on B01

The Windows journal's original sheet uses K 0.31 while Mac uses K 0.40.
Difference for each 90° `bend_allowance_mm` is **0.2120575041 mm** and for 45° **0.1060287521 mm**. Absolute line axes differ by multiples of this difference. Bend lengths remain identical; angles and topology IDs also remain the same. The axis delta is NOT a uniform 3D translation for all bends, so the original strict `source_bend_axis_changed:B01` is correct and MUST NOT be removed or bypassed.

Examples:
- B01 axis start Mac vs Windows delta ~(+0.530144,+0.636173,0) mm
- B02 delta ~(+0.424115,+0.530144,0) mm
- B07 delta ~(+0.106029,+0.636173,0) mm
- B09 delta ~(+0.424115,+0.159043,0) mm

This is a genuine different unfolded model, not merely a file rename or same rigidly translated sheet. The fail-closed replay is correct.

## Independent B02 corroboration

New Mac's first observed non-conditional blocker:
- B02 `RETRACT`, 90°, sampled bracket `u ∈ (0.140625, 0.15625]`, first blocking ram Z **−1.9313275824mm**.
- Full joined-sheet first-sample punch overlap **1004.604607mm³**; reported peak punch overlap **7210.65277mm³**; die overlap zero.
- Independent exact per-component BRep attribution (native FreeCAD):
  - panel:P09 punch **500.9264231432807mm³**.
  - panel:P11 punch **500.9264231432535mm³**.
  - bend_zone:B09 punch **2.7517602744230665mm³**, **central**, not waived by the virtual end-zone policy.
- Windows baseline logs show the same first bracket, first-sample and peak volumes; component volumes differ only in approximately 10^-11 mm³. The local macOS geometry has different K-factor and absolute placement but is physically consistent in machine-frame B02 collision outcome. This does not certify continuous clearance for other states.

## Comparison to previous Windows run

- Windows with post-fix shadow (`20261113-144507`): wall **1051.5764s**, **728 Common**, **548 distance**, **297 unions**, **5 exact component-proven positive hits**, **11 shadow probes**, zero positive/oracle mismatch; K=0.31, Conditional 10mm, runtime `cc6c178b...`.
- Windows with older 4-candidate shadow (`20261113-195049` earlier session): wall **1047.1784s**, **657 Common**, **548 distance**, **297 unions**, 20 shadow probes but 0 proven positives; K=0.31, Conditional 10mm, runtime `a875c255...`.
- Mac no shadow (`20261113-195049` later session): wall **1712.3593s**, **627 Common**, **549 distance**, **297 unions**, zero shadow probes; K=0.40, Conditional 20mm, runtime `d3e9c97b...`.
- Caution: none of these is a speedup of the production validator.

## Recommended next test: inexpensive *same-macOS-log* single-pose replay

Do NOT replay a Windows-origin K=0.31 journal against the Mac K=0.40 source. Select the macOS recording in `SMBS_debug_log(20261113-195049).json`; the recorder's combined two-session file contains a complete **later Mac** run. The replay parser selects the *last* matching completed collision and its own session's original source/tool files. With FreeCAD and latest experiment branch installed:

```python
from pathlib import Path
import tools.replay_native_component_blocker as replay
mac_log = Path.home() / "Desktop" / "SMBS_debug_log(20261113-195049).json"
print(replay.inspect_journal(mac_log, bend_id="B02"))
result = replay.run(
    mac_log, bend_id="B02",
    part="/Users/badmc/Desktop/Новая папка/2-1-3-4-(ok).FCStd",
)
print(result.get("status"))
```

This runs ONLY at the earliest B02 RETRACT hit; expect output `SMBS_component_blocker_replay.json`, not a whole 28-minute native trace. A positive 3-component volume agreement is necessary to qualify this isolated Mac replay; not yet executed by the assistant. NO continuous or safety certification.

## Next engineering target

- Stop repeating full 10-bend runs merely to fix logger/tool-source issues. Use existing real native Mac blocker log for point replay.
- Prioritize replacing/proving cheaper independent topology/clearance preconditions *without* relaxing native collision safety; the 297 repeated unions/qualifications and 1024 pair-sanity checks are expensive.
- Build an exact Conditional-aware **positive-hit-only filter** for the search engine; never interpret component AABB separation as clearance or a conditional end-zone positive as a mandatory collision.
- To compare performance fairly, test identical K-factor, Conditional length, source tooling, source code hashes, CPU and test conditions.
