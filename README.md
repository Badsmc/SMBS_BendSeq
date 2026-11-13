# SMBS BendSeq — performance development branch

**Branch: development/performance-optimization** (2026-11-13). This is a performance-development workspace based on `fix/conditional-endzone-volume-tolerance`, **not** the release/installable `smbs` branch.

**For contributors and AI agents, start here:**
- [AGENTS.md](AGENTS.md) — binding safety and implementation rules.
- [Optimization master plan](docs/PERFORMANCE_OPTIMIZATION_MASTER_PLAN.md) — architecture, order of changes, proof requirements.
- [Live agent handoff](docs/PERFORMANCE_AGENT_HANDOFF.md) — implemented vs unverified and the exact next task.
- [Benchmark protocol](docs/PERFORMANCE_BENCHMARK_PROTOCOL.md) — native FreeCAD cold/warm measurement.
- [Ready-to-use Antigravity prompt](docs/PERFORMANCE_AGENT_START_PROMPT.md).

Phase A1/A2 provide a read-only Desktop JSON performance analyzer and per-bend native kernel measurements. Headless CI passing does **not** mean the live FreeCAD baseline has been measured or the 10-bend sequence is safe. The following historical handmode README is retained only as a record of the earlier distribution; its branch/status statements are not authoritative for this optimization branch.

---

## Historical handmode distribution notes (not this branch's runtime contract)

# SMBS Handmode — PressBrakeSim 2.5D for FreeCAD

**Branch:** `handmode` — clean, installable workbench distribution.
**Source of this build:** `experiment/2d-backgauge-kinematics-cascade`
at commit `4b666384ab313951c40e3856266929804edbd682`.

## What is included

This distribution keeps the current functional workbench, its real geometry
and backgauge algorithms, the SheetMetal model adapter, and the tooling library.
No test suite, development scripts, benchmark reports, historical reports,
or synthetic test tools are shipped. The original demo CAD workpieces are included.

- `Init.py`, `InitGui.py` — direct FreeCAD addon entry points.
- `freecad_smbs/` — PressBrakeSim 2.5D workspace, active GUI commands,
  SheetMetal import, tooling UI, sequence catalog and backgauge configuration.
- `smbs_core/kinematics2d/` — real finite-radius bend model, 2D collision
  screening, automatic sequence search, alternative 180-degree loading poses,
  real CAD backgauge contact, machine placement, Full Cycle and Bend Only,
  partial result replay, and optional native 3D checks.
- `smbs_core/geometry_backend/` and `smbs_core/models/` — geometry/tooling
  adapters used by the workbench.
- `toollibrary/` — actual punch, die, backgauge, clamp, press and frame
  geometry with their sidecar metadata and original DXF files.
- `demo/parts/` — 16 original `.FCStd` workpieces for opening and testing in FreeCAD.
  `demo/MANIFEST.json` records their filenames, sizes and SHA-256 hashes.

### Commands

The **PressBrakeSim 2.5D** menu and toolbar provide the simulation workspace,
load part, tooling panel, automatic physical search, fast 2D candidates,
sequence catalog and backgauge configuration. The independent tooling library
command remains available. These features are not removed by cleanup.

## Install

Requires **FreeCAD 1.1.x**, the **SheetMetal Workbench** and FreeCAD's bundled
Python environment. The addon uses FreeCAD's `Part` / OpenCASCADE modules;
`pythonocc-core` is **not** required for the GUI workbench.

1. Download the ZIP for this **handmode** branch.
2. Extract the *contents* of that ZIP into one folder directly beneath
   your active FreeCAD user `Mod` directory. Do not nest another checkout.
3. Example for FreeCAD v1.1 on Windows:

```text
%APPDATA%\FreeCAD\v1-1\Mod\SMBS_Handmode\
    Init.py
    InitGui.py
    freecad_smbs\
    smbs_core\
    toollibrary\
    demo\
      MANIFEST.json
      parts\
        *.FCStd
    README.md
```

4. Remove or disable any **other installed SMBS_BendSeq or PressBrakeSim
   checkout** from the same FreeCAD Mod search paths. Duplicate registrations
   can make FreeCAD load stale Python modules.
5. Fully restart FreeCAD, then select **PressBrakeSim 2.5D** in the workbench
   selector. Select a SheetMetal model with a valid Unfold, and use **Load
   Part** followed by **Simulation / 2D candidates**.

Use the actual installed workbench folder, not the GitHub source branch, as
the authoritative runtime source. If the menu is missing, first inspect
FreeCAD **View → Panels → Report view** for an InitGui import error.

## Bending and operator-facing placement

Search checks both rigid machine-Z loading orientations. Where possible,
it prefers **the longer reachable sheet extent on the operator (-s) side**
and **the shorter extent towards the rear gauge (+s)**. Each automatic
candidate uses its own real backgauge S/Z placement and sampled collision
checks. The same selected pose is carried into 2.5D/3D display and Full Cycle.

For manual orders, the long-operator-side orientation is an
`ERGONOMIC_ONLY_NOT_CAD_VALIDATED` suggestion: it has **not** been checked
against all machine obstacles or verified rear-stop contact. The GUI displays
this evidence limitation.

The workstation preview shows operator to the left and gauge to the right;
a different camera angle does not change machine orientation. Use motion
JSONL diagnostics to inspect the per-bend `reverse_axis` and selected S/Z.

## Status and safety limitations

**Working experimental addon, not a certified manufacturing planner.**

- The last reported 10-bend reference search reached **7 of 10 bends** under
  the configured search time limit. A complete valid sequence has not been
  demonstrated for that model.
- Static CAD/2.5D contact is not a proof of collision-free continuous feed,
  retract or transfer.
- Independent full 3D trajectory validation for reverse-loaded bends is not
  implemented. The GUI must mark those checks `UNVERIFIED`.
- Actual CAD `Backgauge.FCStd` and configured tooling must be checked in
  FreeCAD; a visually playable animation does not certify a safe press-brake
  manufacturing sequence.
- A clean headless Python build validates import contracts and files, but
  **does not replace a real FreeCAD GUI and workpiece test**.

## Clean branch policy

`handmode` contains **only installable runtime code and required assets**.
Experiments, test fixtures, research documents, generated reports, tools and
regression tests remain available in the source/development branches of the
repository. They must be run there before publishing a new handmode build.

Keep `demo/parts/` and its validated manifest in this distribution.
Do not copy `tests/`, `reports/`, `demo/SMBendTests/` or other
non-runtime development artifacts into this branch as part of a normal release.
