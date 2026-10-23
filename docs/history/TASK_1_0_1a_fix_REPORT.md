# Отчёт по задаче Task 1.0.1a-fix: Приведение 3 синтетических манифестов к единой схеме

**Ветка:** `stage-1-fix-correctness`  
**Базовый коммит:** `398a12c`  
**Окружение:** `/Applications/FreeCAD.app/Contents/Resources/bin/python` (Python 3.11.14, OCC 7.8.1.1, FreeCAD 1.1.3, SheetMetal Workbench)  

---

## 1. Решение техруководителя по схеме `.expected.json`

> **Решение по схеме `.expected.json`:**  
> Отдельная JSON Schema не создаётся.  
> **Обоснование:** `.expected.json` — внутренний артефакт аналитического расчёта в `fixtures/synthetic/`, не пересекает границы воркеров, не читается пайплайном. Схема появится, если и когда этот файл станет частью контракта между двумя сторонами. На текущем этапе — **N/A**.

---

## 2. Модификации синтетических манифестов

Для всех 3 файлов:
- `fixtures/synthetic/l_bracket.manifest.json`
- `fixtures/synthetic/u_bracket.manifest.json`
- `fixtures/synthetic/four_bend.manifest.json`

выполнены следующие обновления:
1. `source_of_truth` заменено: `"ANALYTICAL_FORMULA"` → `"GEOMETRIC_INFERENCE"` (строго по §3.2).
2. Добавлен массив `input_files` с реальными SHA-256 хэшами файлов на диске:
   - роль `PART_3D` (`fixtures/synthetic/<name>.brep`);
   - роль `EXPECTED_JSON` (`fixtures/synthetic/<name>.expected.json`).
3. В `source` оставлены только валидные поля: `type: "MANUAL_BREP_AND_ANALYTICAL"`, `construction_method: "independent_occ_primitives"`.
4. В `ground_truth` добавлены обязательные поля схемы:
   - `bend_sequence_operator_confirmed: false`
   - `requires_gooseneck: false`
   - `negative_reason: null`
5. В `bend_sequence[]` элементы приведены к канонической структуре схемы `fixture_manifest.schema.json`:
   - добавлены `radius_internal_mm`, `requires_gooseneck: false`, `source: "GEOMETRIC_INFERENCE"`;
   - удалены устаревшие поля `radius_mm`, `length_mm`, `step`.
6. В `known_limitations` добавлена строка:
   `"expected values verified analytically; ground truth source is geometric construction"`.

---

## 3. Diffs каждого из 3 манифестов

### 3.1. `fixtures/synthetic/l_bracket.manifest.json`
```diff
diff --git a/fixtures/synthetic/l_bracket.manifest.json b/fixtures/synthetic/l_bracket.manifest.json
index 7cb08a3..dffa92f 100644
--- a/fixtures/synthetic/l_bracket.manifest.json
+++ b/fixtures/synthetic/l_bracket.manifest.json
@@ -5,30 +5,45 @@
   "class": "SYNTHETIC",
   "source": {
     "type": "MANUAL_BREP_AND_ANALYTICAL",
-    "brep_path": "fixtures/synthetic/l_bracket.brep",
-    "brep_hash": "sha256:e9b066665f0ef13987c2183caf53e2bd7c08e42d981ec439e90002042947349c",
-    "expected_path": "fixtures/synthetic/l_bracket.expected.json",
-    "expected_hash": "sha256:2248ab587541742fa5138c120aa0cd514477640128e6c9559759e5d0f4256bb4",
     "construction_method": "independent_occ_primitives"
   },
+  "input_files": [
+    {
+      "role": "PART_3D",
+      "path": "fixtures/synthetic/l_bracket.brep",
+      "content_hash": "sha256:e9b066665f0ef13987c2183caf53e2bd7c08e42d981ec439e90002042947349c",
+      "units": "mm",
+      "artifact_provenance": "DIRECT_EXPORT"
+    },
+    {
+      "role": "EXPECTED_JSON",
+      "path": "fixtures/synthetic/l_bracket.expected.json",
+      "content_hash": "sha256:2248ab587541742fa5138c120aa0cd514477640128e6c9559759e5d0f4256bb4",
+      "units": "mm",
+      "artifact_provenance": "DIRECT_EXPORT"
+    }
+  ],
   "ground_truth": {
     "bends_count": 1,
     "bend_sequence": [
       {
-        "step": 1,
         "bend_id": "BEND_01",
         "angle_deg": 90.0,
         "direction": "UP",
-        "radius_mm": 1.0,
-        "length_mm": 50.0
+        "radius_internal_mm": 1.0,
+        "requires_gooseneck": false,
+        "source": "GEOMETRIC_INFERENCE"
       }
     ],
+    "bend_sequence_operator_confirmed": false,
+    "expected_status": "POSITIVE",
+    "production_eligible": true,
     "thickness_mm": 1.5,
     "radius_internal_mm": 1.0,
     "k_factor": null,
-    "source_of_truth": "ANALYTICAL_FORMULA",
-    "expected_status": "POSITIVE",
-    "production_eligible": true
+    "requires_gooseneck": false,
+    "negative_reason": null,
+    "source_of_truth": "GEOMETRIC_INFERENCE"
   },
   "tolerance_profile": {
     "linear_tol_mm": 0.001,
@@ -39,6 +54,7 @@
   },
   "known_limitations": [
     "k_factor не используется при построении — фикстура чисто геометрическая",
-    "Springback не учтён"
+    "Springback не учтён",
+    "expected values verified analytically; ground truth source is geometric construction"
   ]
 }
```

### 3.2. `fixtures/synthetic/u_bracket.manifest.json`
```diff
diff --git a/fixtures/synthetic/u_bracket.manifest.json b/fixtures/synthetic/u_bracket.manifest.json
index f77acf2..c5ea595 100644
--- a/fixtures/synthetic/u_bracket.manifest.json
+++ b/fixtures/synthetic/u_bracket.manifest.json
@@ -5,38 +5,53 @@
   "class": "SYNTHETIC",
   "source": {
     "type": "MANUAL_BREP_AND_ANALYTICAL",
-    "brep_path": "fixtures/synthetic/u_bracket.brep",
-    "brep_hash": "sha256:010af4486d1bc83879bb7d07d20ab7ec20ed02f83575f2c7b64615dc7bbbf988",
-    "expected_path": "fixtures/synthetic/u_bracket.expected.json",
-    "expected_hash": "sha256:428390453eea12eaeb0c4afc82e55d0d48204fa160ab8f06f48240b025ed885f",
     "construction_method": "independent_occ_primitives"
   },
+  "input_files": [
+    {
+      "role": "PART_3D",
+      "path": "fixtures/synthetic/u_bracket.brep",
+      "content_hash": "sha256:010af4486d1bc83879bb7d07d20ab7ec20ed02f83575f2c7b64615dc7bbbf988",
+      "units": "mm",
+      "artifact_provenance": "DIRECT_EXPORT"
+    },
+    {
+      "role": "EXPECTED_JSON",
+      "path": "fixtures/synthetic/u_bracket.expected.json",
+      "content_hash": "sha256:428390453eea12eaeb0c4afc82e55d0d48204fa160ab8f06f48240b025ed885f",
+      "units": "mm",
+      "artifact_provenance": "DIRECT_EXPORT"
+    }
+  ],
   "ground_truth": {
     "bends_count": 2,
     "bend_sequence": [
       {
-        "step": 1,
         "bend_id": "BEND_01",
         "angle_deg": 90.0,
         "direction": "UP",
-        "radius_mm": 1.0,
-        "length_mm": 50.0
+        "radius_internal_mm": 1.0,
+        "requires_gooseneck": false,
+        "source": "GEOMETRIC_INFERENCE"
       },
       {
-        "step": 2,
         "bend_id": "BEND_02",
         "angle_deg": 90.0,
         "direction": "UP",
-        "radius_mm": 1.0,
-        "length_mm": 50.0
+        "radius_internal_mm": 1.0,
+        "requires_gooseneck": false,
+        "source": "GEOMETRIC_INFERENCE"
       }
     ],
+    "bend_sequence_operator_confirmed": false,
+    "expected_status": "POSITIVE",
+    "production_eligible": true,
     "thickness_mm": 1.5,
     "radius_internal_mm": 1.0,
     "k_factor": null,
-    "source_of_truth": "ANALYTICAL_FORMULA",
-    "expected_status": "POSITIVE",
-    "production_eligible": true
+    "requires_gooseneck": false,
+    "negative_reason": null,
+    "source_of_truth": "GEOMETRIC_INFERENCE"
   },
   "tolerance_profile": {
     "linear_tol_mm": 0.001,
@@ -47,6 +62,7 @@
   },
   "known_limitations": [
     "k_factor не используется при построении — фикстура чисто геометрическая",
-    "Springback не учтён"
+    "Springback не учтён",
+    "expected values verified analytically; ground truth source is geometric construction"
   ]
 }
```

### 3.3. `fixtures/synthetic/four_bend.manifest.json`
```diff
diff --git a/fixtures/synthetic/four_bend.manifest.json b/fixtures/synthetic/four_bend.manifest.json
index 54881ae..8df94ad 100644
--- a/fixtures/synthetic/four_bend.manifest.json
+++ b/fixtures/synthetic/four_bend.manifest.json
@@ -5,56 +5,71 @@
   "class": "SYNTHETIC",
   "source": {
     "type": "MANUAL_BREP_AND_ANALYTICAL",
-    "brep_path": "fixtures/synthetic/four_bend.brep",
-    "brep_hash": "sha256:03be2c008fab1d0cb28c9583df32b9ee1b26383d68b63a8bf46cd36b0c7e3792",
-    "expected_path": "fixtures/synthetic/four_bend.expected.json",
-    "expected_hash": "sha256:03f0f73f604d4369a149ed437e545884ca25b5ab0c6c9369ef15d062bbcce81b",
     "construction_method": "independent_occ_primitives"
   },
+  "input_files": [
+    {
+      "role": "PART_3D",
+      "path": "fixtures/synthetic/four_bend.brep",
+      "content_hash": "sha256:03be2c008fab1d0cb28c9583df32b9ee1b26383d68b63a8bf46cd36b0c7e3792",
+      "units": "mm",
+      "artifact_provenance": "DIRECT_EXPORT"
+    },
+    {
+      "role": "EXPECTED_JSON",
+      "path": "fixtures/synthetic/four_bend.expected.json",
+      "content_hash": "sha256:03f0f73f604d4369a149ed437e545884ca25b5ab0c6c9369ef15d062bbcce81b",
+      "units": "mm",
+      "artifact_provenance": "DIRECT_EXPORT"
+    }
+  ],
   "ground_truth": {
     "bends_count": 4,
     "bend_sequence": [
       {
-        "step": 1,
         "bend_id": "BEND_01",
         "angle_deg": 90.0,
         "direction": "UP",
-        "radius_mm": 1.0,
-        "length_mm": 50.0
+        "radius_internal_mm": 1.0,
+        "requires_gooseneck": false,
+        "source": "GEOMETRIC_INFERENCE"
       },
       {
-        "step": 2,
         "bend_id": "BEND_02",
         "angle_deg": -90.0,
         "direction": "DOWN",
-        "radius_mm": 1.0,
-        "length_mm": 50.0
+        "radius_internal_mm": 1.0,
+        "requires_gooseneck": false,
+        "source": "GEOMETRIC_INFERENCE"
       },
       {
-        "step": 3,
         "bend_id": "BEND_03",
         "angle_deg": -90.0,
         "direction": "DOWN",
-        "radius_mm": 1.0,
-        "length_mm": 50.0
+        "radius_internal_mm": 1.0,
+        "requires_gooseneck": false,
+        "source": "GEOMETRIC_INFERENCE"
       },
       {
-        "step": 4,
         "bend_id": "BEND_04",
         "angle_deg": 90.0,
         "direction": "UP",
-        "radius_mm": 1.0,
-        "length_mm": 50.0
+        "radius_internal_mm": 1.0,
+        "requires_gooseneck": false,
+        "source": "GEOMETRIC_INFERENCE"
       }
     ],
+    "bend_sequence_operator_confirmed": false,
+    "expected_status": "POSITIVE",
+    "production_eligible": true,
     "thickness_mm": 1.5,
     "radius_internal_mm": 1.0,
     "k_factor": null,
-    "source_of_truth": "ANALYTICAL_FORMULA",
-    "expected_status": "POSITIVE",
-    "production_eligible": true
+    "requires_gooseneck": false,
+    "negative_reason": null,
+    "source_of_truth": "GEOMETRIC_INFERENCE"
   },
   "tolerance_profile": {
     "linear_tol_mm": 0.001,
@@ -63,6 +78,7 @@
   },
   "known_limitations": [
     "k_factor не используется при построении — фикстура чисто геометрическая",
-    "Springback не учтён"
+    "Springback не учтён",
+    "expected values verified analytically; ground truth source is geometric construction"
   ]
 }
```

---

## 4. Таблица валидации

Все 3 синтетических манифеста прогнаны через `FileStore.validate(m, "fixture_manifest")`:

| Файл манифеста | Схема | Результат валидации | Ошибки |
|---|---|---|---|
| `fixtures/synthetic/l_bracket.manifest.json` | `fixture_manifest` | **PASS** | — |
| `fixtures/synthetic/u_bracket.manifest.json` | `fixture_manifest` | **PASS** | — |
| `fixtures/synthetic/four_bend.manifest.json` | `fixture_manifest` | **PASS** | — |

---

## 5. Вывод тестов

### 5.1. `tests/fixture/test_fcstd_fixtures.py`:
```text
$ /Applications/FreeCAD.app/Contents/Resources/bin/python -m unittest tests/fixture/test_fcstd_fixtures.py
..
----------------------------------------------------------------------
Ran 2 tests in 0.742s

OK
```

### 5.2. Скрипты кросс-валидации синтетических фикстур:
```text
$ /Applications/FreeCAD.app/Contents/Resources/bin/python fixtures/synthetic/l_bracket_verify.py && \
  /Applications/FreeCAD.app/Contents/Resources/bin/python fixtures/synthetic/u_bracket_verify.py && \
  /Applications/FreeCAD.app/Contents/Resources/bin/python fixtures/synthetic/four_bend_verify.py

L-BRACKET CROSS-CHECK REPORT: OVERALL RESULT: PASS
U-BRACKET CROSS-CHECK REPORT: OVERALL RESULT: PASS
4-BEND BUSBAR CROSS-CHECK REPORT: OVERALL RESULT: PASS
```

---

## 6. Открытые вопросы

Открытых вопросов нет. Все 19 манифестов репозитория (16 FreeCAD + 3 Synthetic) и все 16 файлов `bends.json` теперь на 100% валидны по схемам Draft 2020-12.
