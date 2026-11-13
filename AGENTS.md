# AGENTS.md — AI development instructions for SMBS performance branch

This file originates from **development/performance-optimization**; in this
authorized child branch **experiment/component-occ-broadphase**, work
ONLY on the child branch. Do not check out or commit to the parent branch.
The rules and safety gates below remain binding. Read them before modifying code.

Experimental X1 implementation and exact native acceptance instructions:
[docs/COMPONENT_OCC_X1.md](docs/COMPONENT_OCC_X1.md). This is headless-tested,
not native-FreeCAD-qualified and not merged into public release branches. For full decisions and strict acceptance criteria, read:

1. [docs/PERFORMANCE_OPTIMIZATION_MASTER_PLAN.md](docs/PERFORMANCE_OPTIMIZATION_MASTER_PLAN.md)
2. [docs/PERFORMANCE_AGENT_HANDOFF.md](docs/PERFORMANCE_AGENT_HANDOFF.md)
3. [docs/PERFORMANCE_BENCHMARK_PROTOCOL.md](docs/PERFORMANCE_BENCHMARK_PROTOCOL.md)

## Mission

Accelerate SMBS FreeCAD native D6 sampled-3D trajectory validation and automatic bend-sequence search for 10+ bends. Be evidence-driven and exact. Communicate user-facing summaries in Russian. Do not invent tests, performance results, or work done in FreeCAD.

## Hard prohibitions

- Do not suppress collisions, increase collision tolerances without independently validated physical grounds, or report CONDITIONAL as PASS.
- Do not equate sampled checks with continuous collision clearance or 2D with native 3D.
- Do not reintroduce any simulator/UI bug fixed on the baseline branch.
- Do not change the user's source FCStd, SheetMetal Unfold bend identities, selected FCStd tools, virtual lengths, machine-frame orientation or physical policy silently.
- Do not remove FreeCAD Tasks state, desktop JSON developer journal, manual quick 2D, separate 3D validation, native intersection inspector or embedded FCStd session.
- Do not create duplicate/dead algorithms or broad rewrite before a measured bottleneck is established.
- Do not promote work to main/smbs without explicit request and native acceptance.
- Do not present Linux headless unit tests as proof of real FreeCAD/OpenCASCADE verification.

## Working instructions for an autonomous agent

1. Stay in **experiment/component-occ-broadphase** for X1; its parent is **development/performance-optimization**. Confirm clean worktree and read the three docs above plus COMPONENT_OCC_X1.md.
2. Determine the first unfinished gate in the handoff document. Work on *one* focused change with tests and report exact scope.
3. Run the relevant headless tests, compile check, available FreeCAD tests and CI. Record exact commands and results.
4. Use the Desktop SMBS_debug_log.json journal and read-only performance analyzer for before/after metrics. Keep original logs untouched.
5. When geometry changes, validate all four negative classes: real punch/die penetration, unknown/invalid geometry, Conditional end-zone, uninspected/time-budget exhausted. Any false clearance is grounds to revert.
6. For performance runs, preserve source/part/tool hashes, runtime module location, installation identity, exact 0°/180° setup, sampling configuration, cold/warm cache status and hardware.
7. Update the **handoff document in the same PR/series** with files changed, commit hash, CI link, current blockers and the precise next action.
8. Keep all experimentation on this development branch or a temporary child branch. Do not force push, delete old branches or rewrite history.

## Native code map

- `smbs_core/kinematics2d/motion_trajectory_3d.py`: D6 adaptive native sampled trajectories and scoped geometry/pose caches.
- `smbs_core/kinematics2d/native_bend_solid_3d.py`: full-sheet panel/bend-zone OCC reconstruction and multiFuse.
- `smbs_core/kinematics2d/native_collision_3d.py`: AABB/distance/Common, exact collision and Conditional classification.
- `smbs_core/kinematics2d/native_kernel_metrics.py`: instrumented Part.common/distToShape counters.
- `smbs_core/kinematics2d/manual_physical_validation.py`: Manual native per-bend orchestration, progress and Desktop events.
- `smbs_core/kinematics2d/sequence_search_2d.py`: Stage A/B search and caching, NEVER native-3D proof.
- `smbs_core/kinematics2d/developer_debug_log.py`: append-only developer journal and native 3D report serializer.
- `freecad_smbs/commands/pressbrake_2d.py`: FreeCAD GUI; do not freeze or redesign during perf changes.
- `smbs_core/kinematics2d/performance_report.py`: read-only analysis of real recorded sessions (Phase A).

## Working example (not a manufacturing approval)

Ten-bend `2-1-3-4-(ok).FCStd`, manual order B07 B03 B05 B01 B08 B04 B09 B10 B06 B02, 180° orientation, real selected AP1017 / AT3015 V12, explicit end-zone and virtual-length choices. Compare exactly matched settings and same FreeCAD runtime. A success status observed for 2D does not establish the entire 3D sequence.

## Definition of done

Every optimization needs (a) correct, reproducible headless tests; (b) actual native FreeCAD run and matching verdicts when CAD geometry is affected; (c) before/after timing and call counts; (d) documented limitations and handoff; (e) no old functional regression. Otherwise mark IMPLEMENTED / HEADLESS_TESTED only.
