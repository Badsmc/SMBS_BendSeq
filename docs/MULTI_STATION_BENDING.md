# Multi-Station Bending

Target branch: `feature/multi-station-bending`, based on `Dev-0.2.1` at
`cb64893`. The feature branch includes split-bend playback and segment sizing.
The project version stays 0.2.0.

## Architecture

- `BendingStation` represents an installed pair and its actual local section
  intervals. A station's X datum translates both tools into machine X. The
  intersection of punch and die coverage determines usable longitudinal ranges.
  An inactive station is excluded from assignments but its tools remain mounted.
- `StationSetup` holds machine limits, stations and operation-ID assignments.
  Pre-bend and finish use independent station IDs. The physical fold graph and
  `FoldState` continue to be the source of geometry.
- The existing `machine_station_planner` and `multi_station_screening` modules
  sample cross sections of one pose with one tool pair. These are sampling
  positions, not installed stations. Keep them as consumers of machine geometry.
- Machine pose already accepts a desired machine X midpoint. The station layer
  will select an eligible midpoint from actual tool coverage and pass it through
  this contract, including at both ends of transfers.
- Shared strokes require one station for every member. A continuous tool cannot
  bridge a different bend line in the gap. Grouped forming remains unverified
  until the existing collision engine samples the simultaneous geometry.
- Keep the legacy single-pair path when a session has no station configuration.
  A separate station setup must never be silently discarded to run a legacy check.

## Phases and evidence

| Phase | Scope | Status |
| --- | --- | --- |
| A | Architecture, station and operation-assignment contracts | Implemented and covered by headless regression tests |
| B | Manual tooling, inventory, layout, assignment UI | Layout, stock and session draft editor implemented; native FreeCAD acceptance pending |
| C | Coin3D, station-specific machine pose, transfers and gauge | Machine Pose and static native station preview tested; cycle, transfers and gauge pending |
| D | Shared-ram motion and all installed component checks | Pending |
| E | Bounded FAST search with station transitions and cached feasibility | Pending |
| F | FCStd snapshots, templates and PDF | Pending |
| G | Synthetic FreeCAD demos, complete regression inventory and 1/2/3/5-station benchmarks | Pending |

Geometry, mounting data, permitted ram stroke and load evidence must be checked
before reporting compatibility. Missing evidence stays unresolved. Sampled states
and unchecked motion intervals must stay distinct. The 30–60 s search target
has not been measured for multi-station search.

### Longitudinal layout

The existing segment planner selects stocked, clamp-supported chains. The
station layer reserves the mounted segments of all other stations before
replanning one pair and requires full coverage for the requested span. Manual
validation rejects an out-of-bounds station, insufficient inventory, insufficient
spacing or an unsupported punch. Auto layout searches fixed-clamp event
positions from left to right in station order. A failed search is evidence about
this layout order, not proof that no arrangement exists. Height compatibility,
tool mounting datums and workpiece clearance require further integration.

Phase A passed 115 headless tests. The Phase B layout backend passed 119.

### Preparing a layout

Load a part, then open **Tooling → Stations**. Enter the press limits in
machine X, the minimum gap and the required span of each pair. Choose tools
from the existing library. **Calculate segments** uses the inventory sidecars
and actual configured clamps. Set X numerically or drag a colored span to a
different location. **Duplicate station at X** reserves a second copy of the
segments, and requires a distinct valid mounting position. Use the operation
selector to assign each step, including staged bends, independently.

The result is an explicitly labeled session draft. The operation table shows
the proposed assignments. The current simulation, checks and export continue
using the existing single pair until the machine and verification adapters are
integrated. The draft is not persisted in FCStd at this stage. Installed clamp
geometry is used for longitudinal mounting checks; height, mating datums, and
motion clearance are not checked by the layout editor.

The Qt editor was exercised offscreen with two stations, synthetic stock,
duplicating and removing a station, assigning a stage, and auto layout.
The full headless suite passed with 119 tests.

### Machine Pose adapter

`resolve_station_poses` uses the assigned station's usable X intervals and the
existing midpoint generator. It resolves one authoritative Machine Pose frame,
then places the full span at each candidate X coordinate. No candidate is
returned if coverage is insufficient or interrupted. A shared stroke requires
the same enabled station, equal current angles and the existing geometrical
shared-stroke rules. PREBEND and FINISH can resolve independently at different
positions. The returned frame is only a rigid placement. It has no native
contact, gauge or collision verdict.

Three new headless tests cover fixed station coordinates, grouped alignment in
both loading orientations, insufficient coverage across a gap and independent
stage positions. The full suite passed with 122 tests.

### Installed Coin3D preview

**Tooling → Stations → Preview stations in 3D** draws the current station
layout in the existing Coin3D viewer. The installed CAD meshes reuse the
mesh screening loader and fixed machine-X segment spans. Punches share the
existing punch motion node. Dies remain fixed. Disabled stations are still
visible as installed tooling. The selected operation's assigned station is
highlighted, and Machine Pose places the sheet in the state before that
operation. The original session angles are preserved. An unassigned selection
shows the tooling alone. Standard playback uses the existing single-pair
controller. The station preview is a separate static view.

The preview rechecks stock and clamp positions and requires verified native
FCStd geometry. For now, it rejects different punch top or die base heights
measured from the native meshes. This restriction supports a common visual
mount plane. It does not validate mounting datums, force or ram travel.
Station-specific gauge and collision overlays are omitted. Failures and
preview results are logged as `STATION_LAYOUT_REJECTED` and
`STATION_PREVIEW_*` in the existing debug journal when enabled.

Validation: 124 headless tests and an offscreen Coin3D smoke test using the
available FreeCAD 1.0 runtime. The smoke test changed ram Z without rebuilding
the tool nodes and switched back to the single-pair viewer path. Native CAD
loading for an actual multi-station job and full FreeCAD 1.1 GUI acceptance
remain pending.
