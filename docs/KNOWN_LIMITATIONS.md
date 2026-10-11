# SMBS / BendSeq - Known Limitations

## 1. Zero-Thickness Simplification
Collision detection (`collision_detector.py`) occasionally relies on `TopoShape` bounding box overlaps or boolean intersections that assume perfect rigidity. Extreme near-misses (e.g., clearance < 0.1mm) might falsely register as a collision depending on OCC tolerance.

## 2. Flat Pattern Backgauge "X" Dimension
The calculation in `backgauge_calculator.py` uses an estimation (`max(20.0, bend_length * 0.5)`) for the backgauge depth `X`. True backgauge depth requires precise projection of the flat pattern's parallel rear edges. The output JSON safely marks `geometry_confirmed: False` and warns the operator.

## 3. Springback Compensation
Bend radii and angles assume static inputs. The database currently maps tooling strictly by basic rules (V = 6T). Complex springback coefficients and dynamic tool clearance (e.g. 30-degree deep V-dies for over-bending) are not yet implemented.

## 4. GUI & FreeCAD Sandbox
The add-on creates temporary document objects (`Simulation_Part`) that must be manually deleted by the user or ignored. There is no automated garbage collection of simulation features upon closing the task panel.

## 5. Symmetric Bend Matching
When two bends have identical lengths and exactly identical distance topologies relative to all other bends, they are marked as `AMBIGUOUS`. The addon will not arbitrarily bind them to sketch lines to prevent catastrophic machining errors.
