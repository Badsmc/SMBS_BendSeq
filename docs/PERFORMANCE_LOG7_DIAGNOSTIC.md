# Native D6 diagnostic: supplied SMBS_debug_log(7).json (2026-11-13)

**Status: historical runtime evidence only, NOT qualified performance data for current branch.**
Raw user journal remains private to the conversation; do not commit it. This document contains only minimal non-sensitive derived metrics.

## What actually ran

- Real FreeCAD Windows Python 3.11.14; 11 panels and 10 SheetMetal bends. Source part was `2-1-3-4-(ok) (1).FCStd`, not necessarily byte-identical with repository `demo/parts/2-1-3-4-(ok).FCStd`.
- Installed GUI module came from `FreeCAD/v1-1/Mod/SMBS_BendSeq-2D`. Git branch and commit both reported `UNKNOWN` (normal for a ZIP **but not a substitute for source identity**).
- AP1017 and AT3015 V12 native FCStd tooling; virtual active bend length, Conditional 10mm; Manual order B07 B03 B05 B01 B08 B04 B09 B10 B06 B02. 0° first run had native B03 punch collision at sampled 52.03125°, first sample 20.627748 mm³; 180° later runs reached B02.

## Runs and verdicts

| Manual native run | Wall time | Checked | End state |
|---|---:|---:|---|
| #1 | 78.1174 s | 2/10 | B03 BENDING PUNCH collision, orientation 0° |
| #2 | 1829.2286 s | 10/10 | B02 RETRACT PUNCH collision, orientation 180° |
| #3 | 1790.2005 s | 10/10 | Same B02 RETRACT PUNCH collision, orientation 180° |

The last two runs: B07/B03/B05/B01 and B09/B10 sampled-clear only (NOT continuous certification); B08, B04, B06 **CONDITIONAL**; B02 **COLLISION** (not safe). In #3 the sampled first B02 blocker at RETRACT was **1360.039981 mm³**, near ram Z=-0.994513 mm, angle=90°. Native component attribution reported P09 518.571837 mm³, P11 518.571837 mm³, bend_zone:B09 218.775684 mm³, P03 95.993798 mm³, P01 8.126826 mm³. The latter components span the tool's broad X range in their collision BBoxes; do NOT silently waive the overall hit as a 10mm end-zone contact. The RE-TRACT onset refinement hit a time limit, **but a specific native sampled colliding pose was found**, so the blocked result is not merely a timeout.

The last run's native metric event counts, aggregated over ten bends:

| Measured operation | Value |
|---|---:|
| Manual wall time | 1790.2005 s (29m50.2s) |
| BENDING phase wall time | 1595.5834 s |
| Full-sheet reconstructions (all phases) | 298 |
| Full-sheet reconstructions in BENDING | 288 |
| Instrumented OCC Part.common | 740 calls, 207.1570s |
| Instrumented native distances | 670 calls, 360.9721s |
| **Additional end-zone Common** (`punch_end_zone_common`) | **117 calls** |

These are instrumented category totals: Common + distance time is not whole-program wall time; sheet rebuilding and other work also contribute. Older run #2 was 1829.2286s, a difference of ~39s vs #3. **This is not evidence of optimization**, because runtime SHA and file hashes were not established and #2 lacks the kernel instrumented breakdown.

## Critical installed-version blocker

Log #3 contains `PERF_NATIVE_BEND_KERNEL` but **every event lacks** `geometry_build` and `shared_geometry_cache`. It also performs **117 `punch_end_zone_common` booleans**, whereas the current development branch already passes the first native punch Common directly to end-zone classification. Therefore this runtime is NOT proven to contain the later geometry timing and cross-bend exact BRep caching changes. The source path is `SMBS_BendSeq-2D` (ZIP), not a known git checkout. Do NOT attribute the ~30 minute result to the latest optimized code.

**Immediate gate:** first resolve the installed-source mismatch. Current branch adds SHA-256 fingerprints to `SMBS_RUNTIME_READY.runtime.installed_source_identity` and read-only `python -m tools.verify_installed_runtime --installed <Mod-SMBS-path>`. Once local source equality is established, rerun one reference Manual native D6 on the *same* user's FCStd and tools. Inspect new `PERF_NATIVE_BEND_KERNEL.geometry_build` and `shared_geometry_cache`, as well as statuses and exact Common counts. Only then benchmark a before/after speedup.

## Correctness limits

No full continuous collision certificate, gauge/clamps/frame/TRANSFER approval or manufacturing-safe verdict follows from this run. The B02 RETRACT punch collision is substantial and must not be silently allowed. The 2D vs native discrepancy for B03 at 180° is separately logged and may affect cheap prefilters; it must not force pruning a possibly feasible order.
