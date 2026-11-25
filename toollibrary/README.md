# Bundled tooling

The library contains FCStd solid models, metadata sidecars and original DXF profiles. Select the actual tooling before simulating or checking a part.

New profiles supplied on 2026-11-24:

| Model | Kind | Working height | Total CAD height | Geometry |
| --- | --- | --- | --- | --- |
| PUNCH_AP_1029_88°_H99,40_R0,6 | Punch | 99.4 mm | 129.4 mm | 88°, R0.6 |
| PUNCH_AP_1055_30°_H105_R0,5 | Punch | 105 mm | 135 mm | 30°, R0.5 |
| T-Die_AT_3018_88°_V25_H80 | Die | 80 mm | 80 mm | 88°, V25, shoulder R3 |

The FCStd models preserve the DXF lines and circular arcs. They use a **100 mm reference extrusion**, not an assertion of available stock. Set the real segment lengths and quantities in the library; no stock quantities have been invented. Punch total heights include the mounting shank.

AP 1029 and AT 3018 are the bundled defaults. Other existing production profiles remain available. The legacy generic `puancon` and `matrica` models were removed. Saved documents referring to those models require selecting replacement tooling.

Geometry validation does not certify a bending sequence, tool strength or collision clearance.
