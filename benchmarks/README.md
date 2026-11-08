# Authoritative Forward vs Backward DFS Sequence Search Benchmark

Reproducible benchmark comparing Forward and Backward Depth-First Search (DFS) for sheet metal press brake bending sequence planning on native CAD parts in FreeCAD / OCCT without mocking or bypassing physics.

Branch: `benchmark/forward-backward-box-baseline`

---

## 1. Environment & Reproduction Commands

### Environment
- **Operating System**: macOS (Darwin 24.3.0)
- **FreeCAD Version**: 1.1.3 (build 39257)
- **OCCT Version**: 7.8.1
- **Python**: FreeCAD bundled Python 3.11
- **Tooling**:
  - Punch: `PUNCH_AP_1017_88°_H104,65_R0,8`
  - Die: `T-DIE_AT_3015_88°_V12_H80`
  - Tool length: matched to active bend length (`match_active_bend_length=True`)
  - Collision Mode: Strict (`allow_end_zone_collision=False`, `end_zone_length_mm=0.0`)

### Reproduction Commands

```bash
# 1. Z-(ok).FCStd (2 bends: B01, B02)
PYTHONPATH="/Users/badmc/Library/Application Support/FreeCAD/v1-1/Mod:/Applications/FreeCAD.app/Contents/Resources/lib:." \
/Applications/FreeCAD.app/Contents/Resources/bin/python tools/compare_forward_backward_search_z_part.py

# 2. box.100out-50in-(ok).FCStd (8 bends: B01..B08)
PYTHONPATH="/Users/badmc/Library/Application Support/FreeCAD/v1-1/Mod:/Applications/FreeCAD.app/Contents/Resources/lib:." \
/Applications/FreeCAD.app/Contents/Resources/bin/python tools/compare_forward_backward_search_box.py
```

Outputs:
- `reports/compare_forward_backward_z_part_report.json`
- `reports/compare_forward_backward_box_part_report.json`

---

## 2. Benchmark Results: Z-(ok).FCStd (2 Bends)

| Metric | Forward Search | Backward Search | Note |
| :--- | :---: | :---: | :--- |
| **Status** | `STRICT_VALIDATED` | `STRICT_VALIDATED` | Both strictly verified |
| **Found Sequence** | `['B01', 'B02']` | `['B02', 'B01']` | Symmetric valid routes |
| **Independent Forward Replay** | Pass (`CLEAR`) | Pass (`CLEAR`) | From flat blank (0°) |
| **Technological Penalty Cost** | 42.0 | 42.0 | 180° rotation + flip |
| **Search Time** | 237.99 s | 285.46 s | Pure exploration time |
| **Replay Time** | 221.62 s | 273.46 s | Independent forward replay |
| **Total Wall Time** | 459.61 s | 558.92 s | Cold session to finish |
| **States Visited (DFS)** | 2 | 2 | Depth 2 reached |
| **Transitions / Step Checks** | 2 | 2 | Equal step checks |
| **Pair Collision Checks** | 392 | 392 | Workpiece vs punch/die |
| **Actual Distance Queries (OCCT)** | 326 | 326 | Exact geometric queries |
| **Actual Common Queries (OCCT)** | 264 | 264 | Boolean intersection queries |
| **Solid Reconstructions** | 132 | 132 | Native multi-bend solids |
| **Direction with Fewer Heavy Checks** | **EQUAL** | **EQUAL** | Identical work on Z-part |

---

## 3. Benchmark Results: box.100out-50in-(ok).FCStd (8 Bends)

Budget per direction:
- `max_search_time_s`: 900.0 s
- `max_phase_time_s`: 180.0 s
- `max_states`: 50
- `max_checks`: 100

| Metric | Forward Search | Backward Search | Comparison |
| :--- | :---: | :---: | :--- |
| **Status** | `NOT_FOUND_WITHIN_SEARCH_LIMITS` | `NO_FEASIBLE_SEQUENCE` | Backward proved infeasibility |
| **Sequence Found** | None | None | No strict sequence exists |
| **Best Partial Sequence** | `['B01', 'B02', 'B03']` (3/8) | None (0/8) | Forward stalled at depth 3 |
| **Search Time** | 938.17 s (timeout) | **205.82 s** | **Backward 4.5x faster** |
| **States Visited (DFS)** | 4 | **1** | Backward pruned at root |
| **Step Evaluations (Transitions)** | 10 | **8** | Backward evaluated 8 root bends |
| **Pair Collision Checks** | 488 | **64** | **Backward 7.6x fewer** |
| **Actual Distance Queries (OCCT)** | 453 | **36** | **Backward 12.6x fewer** |
| **Actual Common Queries (OCCT)** | 336 | **14** | **Backward 24.0x fewer** |
| **Solid Reconstructions** | 161 | **6** | **Backward 26.8x fewer** |
| **Direction with Fewer Heavy Checks** | — | **BACKWARD** | **Dramatically fewer checks** |

---

## 4. Key Architectural Findings

1. **Why No Strict Sequence Exists on the Box with Current Tooling**:
   - Backward search proved that in the fully folded box state ($S = \{B01..B08\}$), **none of the 8 bends can serve as the final bend**:
     - Distal bends B05, B07 penetrate the punch body (692.5 mm³).
     - Distal bends B06, B08 penetrate the die body (3637.4 mm³).
     - Base bends B02, B04 collide during the Approach phase (68.4 mm³).
     - Base bends B01, B03 collide during the Bending phase at $u=1.0$ (442.6 mm³).
   - Therefore, under strict collision rules with standard straight punch AP1017 and die AT3015, manufacturing the closed box is physically blocked without window/gooseneck tooling or end-zone relief.

2. **Efficiency Advantage of Backward Search**:
   - **Forward Search** blindly accepted 3 early bends (`B01 -> B02 -> B03`) from the flat blank, then hit impenetrable collisions and timeouts on all candidate branches, exhausting its entire 900 s budget (938.17 s) with **453 distance queries**, **336 boolean intersections**, and **161 solid rebuilds** without reaching a conclusive verdict.
   - **Backward Search** evaluated all 8 candidates at the root state, verified with native OCCT that every candidate collides, and concluded `NO_FEASIBLE_SEQUENCE` in **205.82 s** with only **36 distance queries**, **14 common queries**, and **6 solid rebuilds**.
