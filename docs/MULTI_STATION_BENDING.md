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
| B | Manual tooling, inventory, layout, assignment UI | Pending |
| C | Coin3D, station-specific machine pose, transfers and gauge | Pending |
| D | Shared-ram motion and all installed component checks | Pending |
| E | Bounded FAST search with station transitions and cached feasibility | Pending |
| F | FCStd snapshots, templates and PDF | Pending |
| G | Synthetic FreeCAD demos, complete regression inventory and 1/2/3/5-station benchmarks | Pending |

Geometry, mounting data, permitted ram stroke and load evidence must be checked
before reporting compatibility. Missing evidence stays unresolved. Sampled states
and unchecked motion intervals must stay distinct. The 30–60 s search target
has not been measured for multi-station search.
