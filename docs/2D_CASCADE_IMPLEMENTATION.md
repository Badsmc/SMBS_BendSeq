# 2D Collision Cascade — experimental implementation

Branch: `experiment/2d-backgauge-kinematics-cascade`

## Candidate evaluation order

1. Compute candidate machine setup and run cheap Stage A input, bend-zone and tool-coverage screening.
2. If Stage A rejects, **do not** request CAD backgauge contact.
3. Resolve required backgauge position and reject targets outside configured Z range (default **-20 to +20 mm**). The range is absolute machine-position travel, not a per-transition displacement limit.
4. In each sampled machine pose, check **punch first**. A confirmed 2D punch intersection avoids die construction and die contact tests in that sample.
5. Otherwise check die; if punch or die obstructs that sample, skip expensive backgauge CAD phase collision tests. Retain existing 2D-vs-3D evidence labels.
6. Check backgauge only for surviving punch/die samples. This step is still a sampled geometric test, not an interbend swept path verification.
7. Only after rejecting infeasible candidates, compute transition cost for each surviving DFS successor. Sort successors by available cost, known-vs-unknown completeness, group preference, and stable ID. Preserve DFS backtracking, final replay, and resource bounds.

## Physical-safety limitations

- No arbitrary `vertical_flange / supporting_flange` ratio is a hard rejection criterion. A long vertical flange and short support flange are a *risk signal*, not a geometric proof of invalidity. Hard-pruning requires verified die support geometry / minimum contact area and tool clearance for the actual orientation.
- Existing Stage B screens sampled sections. It does **not** certify continuous movement, 3D punch ends, backgauge repositioning between bends, tool clamping, or stability.
- Stage B still may count 2D interference as a planar-model rejection, not a 3D collision certificate.
- Cost-guided DFS eagerly evaluates eligible siblings to rank them. This can improve early solution quality but also increase breadth and time; benchmark against the parent branch before claiming a speedup.
- Existing backgauge automatic contact may still identify a supporting panel different from the kinematically fixed panel. This is not repaired by cascade ordering.

## Validation

Added `tests/test_collision_cascade.py` to verify:
- PUNCH-first collision skips DIE computation.
- DIE is checked when PUNCH is clear.
- Unverified punch does not silently become clear.
- Clear two-tool verdict remains equivalent to the original oracle.
- Selected-tool screening does not fabricate missing-tool verdicts.

Previously added backgauge travel and height-cost tests remain inherited.

Recommended checks:

```bash
python -m unittest tests/test_collision_cascade.py tests/test_contact_oracle_2d.py tests/test_sequence_search_2d.py tests/test_backgauge_z_travel_limits.py tests/test_backgauge_height_cost.py
```

Then compare same 10-bend FCStd, tooling, tolerances, max-search-time, and initial setup across the parent and this branch. Capture evaluated steps, visited nodes, CAD phase checks, max depth, complete candidates, cost breakdown, and wall time. Do not interpret an incomplete search as proven impossible.

**Status:** Committed implementation, runtime validation in FreeCAD and performance benchmark pending.
