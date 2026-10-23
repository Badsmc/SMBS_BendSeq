# Отчёт Task 1.0.1b-0: Fix get_face_normal + расширение canonical_part

**Дата:** 2026-10-14  
**Ветка:** `stage-1-fix-correctness`  
**Окружение:** `/Applications/FreeCAD.app/Contents/Resources/bin/python` (Python 3.11.14, OCC 7.8.1, FreeCAD 1.1.3 + SheetMetal)

---

## 1. Diffs

### 1.1. Diff `smbs_core/geometry_backend/backends/pythonocc_backend.py`
```diff
--- a/smbs_core/geometry_backend/backends/pythonocc_backend.py
+++ b/smbs_core/geometry_backend/backends/pythonocc_backend.py
@@ -38,3 +38,3 @@
-    from OCC.Core.TopAbs import TopAbs_FACE, TopAbs_SOLID, TopAbs_COMPOUND, TopAbs_EDGE
+    from OCC.Core.TopAbs import TopAbs_FACE, TopAbs_SOLID, TopAbs_COMPOUND, TopAbs_EDGE, TopAbs_REVERSED
@@ -361,7 +361,10 @@ class PythonOCCBackend(GeometryBackend):
         if surf.GetType() == GeomAbs_Plane:
             pln = surf.Plane()
             d = pln.Axis().Direction()
-            return (d.X(), d.Y(), d.Z())
+            nx, ny, nz = d.X(), d.Y(), d.Z()
+            if face.Orientation() == TopAbs_REVERSED:
+                return (-nx, -ny, -nz)
+            return (nx, ny, nz)
```

### 1.2. Diff `smbs_core/workers/sheetmetal/adapter.py`
```diff
--- a/smbs_core/workers/sheetmetal/adapter.py
+++ b/smbs_core/workers/sheetmetal/adapter.py
@@ -30,6 +30,7 @@ class NormalizedBendLine:
     start_point: List[float]
     end_point: List[float]
     confidence: ConfidenceLevel = ConfidenceLevel.CONFIRMED
+    bend_line_flat: Optional[Dict[str, List[float]]] = None
 
 
 @dataclass
@@ -45,6 +46,7 @@ class SheetMetalUnfoldResult:
     root_face_name: Optional[str] = None
     root_normal: Optional[List[float]] = None
     bend_lines: List[NormalizedBendLine] = field(default_factory=list)
+    bend_line_flat: Optional[Dict[str, Any]] = None
     diagnostics: List[DiagnosticEntry] = field(default_factory=list)
@@ -206,9 +208,25 @@ class SheetMetalAdapter:
                     # Extract line geometry endpoints
                     start_pt = [0.0, 0.0, 0.0]
                     end_pt = [0.0, 0.0, 0.0]
-                    if hasattr(b_info, "line") and hasattr(b_info.line, "StartPoint"):
-                        start_pt = [b_info.line.StartPoint.x, b_info.line.StartPoint.y, b_info.line.StartPoint.z]
-                        end_pt = [b_info.line.EndPoint.x, b_info.line.EndPoint.y, b_info.line.EndPoint.z]
+                    line_geom = getattr(b_info, "line", None)
+                    if line_geom is not None:
+                        if hasattr(line_geom, "Vertexes") and len(line_geom.Vertexes) >= 2:
+                            v0 = line_geom.Vertexes[0].Point
+                            v1 = line_geom.Vertexes[1].Point
+                            start_pt = [float(v0.x), float(v0.y), float(v0.z)]
+                            end_pt = [float(v1.x), float(v1.y), float(v1.z)]
+                        elif hasattr(line_geom, "StartPoint") and hasattr(line_geom, "EndPoint"):
+                            start_pt = [float(line_geom.StartPoint.x), float(line_geom.StartPoint.y), float(line_geom.StartPoint.z)]
+                            end_pt = [float(line_geom.EndPoint.x), float(line_geom.EndPoint.y), float(line_geom.EndPoint.z)]
+
+                    flat_line_dict = {
+                        "start": [start_pt[0], start_pt[1]],
+                        "end": [end_pt[0], end_pt[1]],
+                        "start_mm": start_pt,
+                        "end_mm": end_pt,
+                        "angle_deg": b_angle,
+                        "radius_mm": b_radius
+                    }
 
                     normalized_bends.append(
                         NormalizedBendLine(
@@ -220,11 +238,13 @@ class SheetMetalAdapter:
                             start_point=start_pt,
                             end_point=end_pt,
                             confidence=ConfidenceLevel.CONFIRMED,
+                            bend_line_flat=flat_line_dict,
                         )
                     )
 
             root_n = [root_normal.x, root_normal.y, root_normal.z] if root_normal else None
 
+            first_flat = normalized_bends[0].bend_line_flat if normalized_bends else None
             return SheetMetalUnfoldResult(
                 status=RunStatus.SUCCESS,
                 unfolded_shape=inplace_unbend,
@@ -236,6 +256,7 @@ class SheetMetalAdapter:
                 root_face_name=facename,
                 root_normal=root_n,
                 bend_lines=normalized_bends,
+                bend_line_flat=first_flat,
                 diagnostics=diagnostics,
             )
```

### 1.3. Diff `smbs_core/contracts/schemas/canonical_part.schema.json`
```diff
--- a/smbs_core/contracts/schemas/canonical_part.schema.json
+++ b/smbs_core/contracts/schemas/canonical_part.schema.json
@@ -176,6 +176,25 @@
                 "items": { "type": "number" },
                 "minItems": 3,
                 "maxItems": 3
+              },
+              "bend_line_flat_pattern": {
+                "type": "object",
+                "required": ["start_mm", "end_mm", "length_mm"],
+                "properties": {
+                  "start_mm": {
+                    "type": "array",
+                    "items": { "type": "number" },
+                    "minItems": 3,
+                    "maxItems": 3
+                  },
+                  "end_mm": {
+                    "type": "array",
+                    "items": { "type": "number" },
+                    "minItems": 3,
+                    "maxItems": 3
+                  },
+                  "length_mm": { "type": "number" }
+                }
               }
             }
           }
```

### 1.4. Diff `smbs_core/workers/topology/worker.py`
```diff
--- a/smbs_core/workers/topology/worker.py
+++ b/smbs_core/workers/topology/worker.py
@@ -113,9 +113,15 @@ class TopologyWorker:
         if canonical_panels:
             ref_panel_id = max(canonical_panels.keys(), key=lambda pid: canonical_panels[pid]["area_mm2"])
 
+        # Retrieve flat pattern bend lines from geometry_result if available
+        flat_bend_lines = []
+        flat_art = geom_result["payload"].get("flat_pattern_artifact")
+        if flat_art and isinstance(flat_art, dict):
+            flat_bend_lines = flat_art.get("bend_lines", [])
+
         # Assemble Bends
         canonical_bends: Dict[str, Any] = {}
-        for b_id, b_info in raw_bends.items():
+        for idx, (b_id, b_info) in enumerate(raw_bends.items()):
             p1 = b_info.get("parent_panel_id", "PANEL_01")
             p2 = b_info.get("child_panel_id", "PANEL_02")
             if p1 in canonical_panels and b_id not in canonical_panels[p1]["adjacent_bend_ids"]:
@@ -159,6 +165,19 @@ class TopologyWorker:
                 "axis_direction": [float(x) for x in b_info.get("axis", [1,0,0])]
             }
 
+            # Propagate bend_line_flat_pattern if available in geometry_result
+            if idx < len(flat_bend_lines):
+                fbl = flat_bend_lines[idx]
+                start_pt = fbl.get("start_point")
+                end_pt = fbl.get("end_point")
+                if start_pt and end_pt:
+                    l_val = math.sqrt(sum((end_pt[k] - start_pt[k])**2 for k in range(min(len(start_pt), len(end_pt)))))
+                    canonical_bends[b_id]["bend_line_flat_pattern"] = {
+                        "start_mm": [float(x) for x in start_pt[:3]],
+                        "end_mm": [float(x) for x in end_pt[:3]],
+                        "length_mm": round(float(l_val), 3)
+                    }
+
         # Provenance for thickness
         t_val = geom_result["payload"]["thickness_mm"]["value"] or raw_thickness
         t_source = geom_result["payload"]["thickness_mm"]["source"]
```

---

## 2. Полные `canonical_part.json` после фикса

### 2.1. L-синтетика (`fixtures/synthetic/l_bracket.brep`)
```json
{
  "content_hash": "9018a151e70b334005aa622e4efa81f1544bb39392ea0a41eac4ade141517b06",
  "created_at": "2026-10-14T08:45:25.520000+00:00",
  "diagnostics": [],
  "frame_convention": "STANDARD_CARTESIAN_MM",
  "input_fingerprint": "e9b066665f0ef13987c2183caf53e2bd7c08e42d981ec439e90002042947349c",
  "part_id": "l_bracket_synthetic",
  "payload": {
    "bends": {
      "BEND_01_99b767d8": {
        "angle_deg": {
          "confidence": "CONFIRMED",
          "source": "GEOMETRIC_INFERENCE",
          "value": -90.0
        },
        "axis_direction": [
          0.0,
          1.0,
          0.0
        ],
        "axis_point": [
          95.5,
          0.0,
          2.5
        ],
        "bend_id": "BEND_01_99b767d8",
        "bend_line_flat_pattern": {
          "end_mm": [
            96.80376095123977,
            50.0,
            5.551115123125783e-15
          ],
          "length_mm": 50.0,
          "start_mm": [
            96.80376095123978,
            0.0,
            5.551115123125783e-15
          ]
        },
        "child_panel_id": "PANEL_b5fbf3e2",
        "direction": {
          "confidence": "CONFIRMED",
          "source": "GEOMETRIC_INFERENCE",
          "value": "DOWN"
        },
        "k_factor": {
          "confidence": "CONFIRMED",
          "source": "SHEETMETAL_NATIVE",
          "value": 0.44
        },
        "length_mm": {
          "confidence": "CONFIRMED",
          "source": "GEOMETRIC_INFERENCE",
          "value": 50.0
        },
        "parent_panel_id": "PANEL_9fe06c8a",
        "radius_mm": {
          "confidence": "CONFIRMED",
          "source": "GEOMETRIC_INFERENCE",
          "value": 1.0
        }
      }
    },
    "geometry_artifact": {
      "content_hash": "e9b066665f0ef13987c2183caf53e2bd7c08e42d981ec439e90002042947349c",
      "file_path": "T/tmpuecpbmwq/geometry.brep",
      "format": "BREP"
    },
    "input_mode": "CANONICAL_PART",
    "panels": {
      "PANEL_9fe06c8a": {
        "adjacent_bend_ids": [
          "BEND_01_99b767d8"
        ],
        "area_mm2": 9550.0,
        "centroid": [
          47.75,
          25.0,
          0.75
        ],
        "normal": [
          0.0,
          0.0,
          1.0
        ],
        "panel_id": "PANEL_9fe06c8a"
      },
      "PANEL_b5fbf3e2": {
        "adjacent_bend_ids": [
          "BEND_01_99b767d8"
        ],
        "area_mm2": 4750.0,
        "centroid": [
          97.25,
          25.0,
          26.25
        ],
        "normal": [
          1.0,
          0.0,
          -0.0
        ],
        "panel_id": "PANEL_b5fbf3e2"
      }
    },
    "reference_panel_id": "PANEL_9fe06c8a",
    "thickness_mm": {
      "confidence": "CONFIRMED",
      "source": "SHEETMETAL_NATIVE",
      "unit": "mm",
      "value": 1.5
    }
  },
  "producer": {
    "name": "topology_worker",
    "version": "1.1"
  },
  "run_id": "run_l_bracket_synthetic",
  "schema_name": "canonical_part",
  "schema_version": "1.1",
  "status": "SUCCESS",
  "units": "mm"
}
```

### 2.2. L-FCStd (`fixtures/freecad/l_ok/part_3d.brep`)
```json
{
  "content_hash": "a17895d4da818062b02b62d2ef46131f6bf93452318f9bc403e08c7e687fae36",
  "created_at": "2026-10-14T08:45:25.759595+00:00",
  "diagnostics": [],
  "frame_convention": "STANDARD_CARTESIAN_MM",
  "input_fingerprint": "3cb365ee9005374051bc0875a4b7e03fcb544c2b6f2efd7842f47abec3576ce8",
  "part_id": "l_ok_freecad",
  "payload": {
    "bends": {
      "BEND_01_abcae0c6": {
        "angle_deg": {
          "confidence": "CONFIRMED",
          "source": "GEOMETRIC_INFERENCE",
          "value": 90.0
        },
        "axis_direction": [
          1.0,
          0.0,
          0.0
        ],
        "axis_point": [
          0.0,
          47.5,
          2.5
        ],
        "bend_id": "BEND_01_abcae0c6",
        "bend_line_flat_pattern": {
          "end_mm": [
            200.0,
            48.80376095123977,
            1.4210854715202004e-14
          ],
          "length_mm": 200.0,
          "start_mm": [
            0.0,
            48.80376095123977,
            1.4210854715202004e-14
          ]
        },
        "child_panel_id": "PANEL_9607a07b",
        "direction": {
          "confidence": "CONFIRMED",
          "source": "GEOMETRIC_INFERENCE",
          "value": "UP"
        },
        "k_factor": {
          "confidence": "CONFIRMED",
          "source": "SHEETMETAL_NATIVE",
          "value": 0.44
        },
        "length_mm": {
          "confidence": "CONFIRMED",
          "source": "GEOMETRIC_INFERENCE",
          "value": 200.0
        },
        "parent_panel_id": "PANEL_19e2f5d7",
        "radius_mm": {
          "confidence": "CONFIRMED",
          "source": "GEOMETRIC_INFERENCE",
          "value": 1.0
        }
      }
    },
    "geometry_artifact": {
      "content_hash": "bd02a284bdf3e719156776a24ab460abf8b23dd81e92168888f00a47e3ce568c",
      "file_path": "T/tmp1nqzgyz5/geometry.brep",
      "format": "BREP"
    },
    "input_mode": "CANONICAL_PART",
    "panels": {
      "PANEL_19e2f5d7": {
        "adjacent_bend_ids": [
          "BEND_01_abcae0c6"
        ],
        "area_mm2": 19000.0,
        "centroid": [
          100.0,
          23.75,
          0.75
        ],
        "normal": [
          0.0,
          0.0,
          1.0
        ],
        "panel_id": "PANEL_19e2f5d7"
      },
      "PANEL_9607a07b": {
        "adjacent_bend_ids": [
          "BEND_01_abcae0c6"
        ],
        "area_mm2": 19000.0,
        "centroid": [
          100.0,
          49.25,
          26.250000000000004
        ],
        "normal": [
          -0.0,
          1.0,
          -0.0
        ],
        "panel_id": "PANEL_9607a07b"
      }
    },
    "reference_panel_id": "PANEL_19e2f5d7",
    "thickness_mm": {
      "confidence": "CONFIRMED",
      "source": "SHEETMETAL_NATIVE",
      "unit": "mm",
      "value": 1.5
    }
  },
  "producer": {
    "name": "topology_worker",
    "version": "1.1"
  },
  "run_id": "run_l_ok_freecad",
  "schema_name": "canonical_part",
  "schema_version": "1.1",
  "status": "SUCCESS",
  "units": "mm"
}
```

---

## 3. Вывод тестов

1. **Негативный тест извлечения TopologyWorker:**
   - L-синтетика: `thickness_mm = 1.5`, `bends_count = 1`, `panels_count = 2` (**PASS**).
   - L-FCStd: `thickness_mm = 1.5`, `bends_count = 1`, `panels_count = 2` (**PASS**).
2. **Синтетические верификаторы:**
   - `fixtures/synthetic/l_bracket_verify.py` (**PASS**).
   - `fixtures/synthetic/u_bracket_verify.py` (**PASS**).
   - `fixtures/synthetic/four_bend_verify.py` (**PASS**).
3. **FCStd-тест:**
   - `tests/fixture/test_fcstd_fixtures.py` (2 теста) (**PASS**).
4. **Тест пайплайна Baseline:**
   - `tests/test_stage1_baseline.py` (3 теста: L-profile, U-channel, 4bend) (**PASS**).
5. **Смежные тесты Stage 1:**
   - `tests/test_contracts_and_filestore.py`, `tests/test_vertical_slice_geometry.py`, `tests/test_sheetmetal_bend_solid.py`, `tests/test_fold_one_bend.py`, `tests/test_fold_multi_bend.py` (12 тестов) (**PASS**).

---

## 4. Открытые вопросы перед Task 1.0.1b-1

1. **Ориентация и порядок сгиба для smFold:**
   - `SheetMetalFoldCmd.smFold` принимает параметры `invertbend: bool` и `flipped: bool`. В каноническом `bend` направление зафиксировано как `"UP"` / `"DOWN"`. Необходимо убедиться в корректном маппинге `"UP"` / `"DOWN"` на флаги `smFold` для произвольных пространственных положений базовой грани.
2. **Многогибочные детали (U-bracket, 4-bend):**
   - Для последовательной гибки нескольких линий гиба через `smFold` потребуется итеративное сворачивание с обновлением FreeCAD FeaturePython или последовательный вызов `smFold` для каждого шага гибки.
