# Отчёт по задаче Task 1.0.1a: Закрытие долга по схемам фикстур

**Ветка:** `stage-1-fix-correctness`  
**Базовый коммит:** `5431542`  
**Окружение:** `/Applications/FreeCAD.app/Contents/Resources/bin/python` (Python 3.11.14, OCC 7.8.1.1, FreeCAD 1.1.3, SheetMetal Workbench)  

---

## 1. Пути созданных схем и их контрольные суммы SHA-256

Созданы две официальные схемы JSON Schema Draft 2020-12 в каталоге `smbs_core/contracts/schemas/`:

| Схема | Описание | Путь | Контрольная сумма SHA-256 |
|---|---|---|---|
| `fixture_manifest.schema.json` | Схема манифеста эталонных фикстур (§15.3 ТЗ) | `smbs_core/contracts/schemas/fixture_manifest.schema.json` | `sha256:f169bbfd53b62e19f5a9c181d5edae34c5b6435eec2aaa5abfc4f3e2b64dc7d9` |
| `bends.schema.json` | Схема метаданных гибов листовой детали (§15.3 ТЗ) | `smbs_core/contracts/schemas/bends.schema.json` | `sha256:c643941a19fbe2affa99a7c9ef807f5f4412984a5e4f2a3cf13d9e6790979a3d` |

---

## 2. Diff изменений `fixtures/freecad/135_ok/manifest.json`

Выполнено исправление семантики источника истины согласно Замечанию A:
- `ground_truth.source_of_truth` заменено с `RECOMPUTED_FROM_FINAL_BODY` на допустимое по §3.2 значение `SHEETMETAL_NATIVE`;
- В `input_files` для роли `FLAT_PATTERN` добавлено поле `artifact_provenance: "RECOMPUTED"`;
- Для остальных входных файлов добавлено `artifact_provenance: "DIRECT_EXPORT"`.

```diff
diff --git a/fixtures/freecad/135_ok/manifest.json b/fixtures/freecad/135_ok/manifest.json
index b27f47b..7db972f 100644
--- a/fixtures/freecad/135_ok/manifest.json
+++ b/fixtures/freecad/135_ok/manifest.json
@@ -16,19 +16,22 @@
       "role": "PART_3D",
       "path": "fixtures/freecad/135_ok/part_3d.brep",
       "content_hash": "sha256:862511f2ce2c1c06a20043963783b1370eaf92342666a5d7bc2a0057950897bf",
-      "units": "mm"
+      "units": "mm",
+      "artifact_provenance": "DIRECT_EXPORT"
     },
     {
       "role": "FLAT_PATTERN",
       "path": "fixtures/freecad/135_ok/flat_pattern.brep",
       "content_hash": "sha256:c9294ae2087a751548f2af12efc219e4799265d3ee05650764de6bb2e4cd154e",
-      "units": "mm"
+      "units": "mm",
+      "artifact_provenance": "RECOMPUTED"
     },
     {
       "role": "BENDS_METADATA",
       "path": "fixtures/freecad/135_ok/bends.json",
       "content_hash": "sha256:d3275ab73d057091c45f8ea11aa4bef1efd9b630f90ffd4d32b487a21ae4de3e",
-      "units": "mm"
+      "units": "mm",
+      "artifact_provenance": "DIRECT_EXPORT"
     }
   ],
   "ground_truth": {
@@ -61,7 +64,7 @@
     "die_v_opening_mm": 12.0,
     "requires_gooseneck": false,
     "negative_reason": null,
-    "source_of_truth": "RECOMPUTED_FROM_FINAL_BODY"
+    "source_of_truth": "SHEETMETAL_NATIVE"
   },
   "tolerance_profile": {
     "linear_tol_mm": 0.001,
@@ -76,4 +79,4 @@
     "Gooseneck параметры — только радиус рабочей части",
     "flat_pattern recomputed via getUnfold on final Bend001 body; original Тело_Unfold attached to intermediate Bend"
   ]
-}
\ No newline at end of file
+}
```

*(Для остальных 15 FreeCAD манифестов также добавлено `artifact_provenance: "DIRECT_EXPORT"` для всех `input_files`).*

---

## 3. Таблица валидации (38 файлов)

Проведено тестирование всех 38 файлов артефактов фикстур через `FileStore.validate`:

| Файл | Схема | Результат | Список ошибок / Примечание |
|---|---|---|---|
| `fixtures/freecad/135_ok/manifest.json` | `fixture_manifest` | **PASS** | — |
| `fixtures/freecad/2_1_3_4_ok/manifest.json` | `fixture_manifest` | **PASS** | — |
| `fixtures/freecad/box_100in_50out_false/manifest.json` | `fixture_manifest` | **PASS** | — |
| `fixtures/freecad/box_100out_50in_ok/manifest.json` | `fixture_manifest` | **PASS** | — |
| `fixtures/freecad/l_30_6_false/manifest.json` | `fixture_manifest` | **PASS** | — |
| `fixtures/freecad/l_ok/manifest.json` | `fixture_manifest` | **PASS** | — |
| `fixtures/freecad/sqin_200_25_10_ok/manifest.json` | `fixture_manifest` | **PASS** | — |
| `fixtures/freecad/sqout_200_25_10_ok/manifest.json` | `fixture_manifest` | **PASS** | — |
| `fixtures/freecad/u_10_10_20_false/manifest.json` | `fixture_manifest` | **PASS** | — |
| `fixtures/freecad/u_10_15_10_punch2_ok/manifest.json` | `fixture_manifest` | **PASS** | — |
| `fixtures/freecad/u_10_30_10_ok/manifest.json` | `fixture_manifest` | **PASS** | — |
| `fixtures/freecad/u_60deg_30deg_ok/manifest.json` | `fixture_manifest` | **PASS** | — |
| `fixtures/freecad/u_90deg_90deg_ok/manifest.json` | `fixture_manifest` | **PASS** | — |
| `fixtures/freecad/z_10_20_10_false/manifest.json` | `fixture_manifest` | **PASS** | — |
| `fixtures/freecad/z_10_20_10_ok/manifest.json` | `fixture_manifest` | **PASS** | — |
| `fixtures/freecad/z_ok/manifest.json` | `fixture_manifest` | **PASS** | — |
| `fixtures/freecad/135_ok/bends.json` | `bends` | **PASS** | — |
| `fixtures/freecad/2_1_3_4_ok/bends.json` | `bends` | **PASS** | — |
| `fixtures/freecad/box_100in_50out_false/bends.json` | `bends` | **PASS** | — |
| `fixtures/freecad/box_100out_50in_ok/bends.json` | `bends` | **PASS** | — |
| `fixtures/freecad/l_30_6_false/bends.json` | `bends` | **PASS** | — |
| `fixtures/freecad/l_ok/bends.json` | `bends` | **PASS** | — |
| `fixtures/freecad/sqin_200_25_10_ok/bends.json` | `bends` | **PASS** | — |
| `fixtures/freecad/sqout_200_25_10_ok/bends.json` | `bends` | **PASS** | — |
| `fixtures/freecad/u_10_10_20_false/bends.json` | `bends` | **PASS** | — |
| `fixtures/freecad/u_10_15_10_punch2_ok/bends.json` | `bends` | **PASS** | — |
| `fixtures/freecad/u_10_30_10_ok/bends.json` | `bends` | **PASS** | — |
| `fixtures/freecad/u_60deg_30deg_ok/bends.json` | `bends` | **PASS** | — |
| `fixtures/freecad/u_90deg_90deg_ok/bends.json` | `bends` | **PASS** | — |
| `fixtures/freecad/z_10_20_10_false/bends.json` | `bends` | **PASS** | — |
| `fixtures/freecad/z_10_20_10_ok/bends.json` | `bends` | **PASS** | — |
| `fixtures/freecad/z_ok/bends.json` | `bends` | **PASS** | — |
| `fixtures/synthetic/four_bend.manifest.json` | `fixture_manifest` | **FAIL** | 1) `input_files` отсутствует; 2) `source_of_truth`: `'ANALYTICAL_FORMULA'` не входит в перечень §3.2; 3) в `bend_sequence` отсутствуют поля `radius_internal_mm`, `requires_gooseneck`, `source` (использовались `radius_mm`, `length_mm`); 4) в `ground_truth` отсутствуют `bend_sequence_operator_confirmed`, `requires_gooseneck`, `negative_reason` |
| `fixtures/synthetic/l_bracket.manifest.json` | `fixture_manifest` | **FAIL** | 1) `input_files` отсутствует; 2) `source_of_truth`: `'ANALYTICAL_FORMULA'` не входит в перечень §3.2; 3) в `bend_sequence` отсутствуют `radius_internal_mm`, `requires_gooseneck`, `source`; 4) в `ground_truth` отсутствуют `bend_sequence_operator_confirmed`, `requires_gooseneck`, `negative_reason` |
| `fixtures/synthetic/u_bracket.manifest.json` | `fixture_manifest` | **FAIL** | 1) `input_files` отсутствует; 2) `source_of_truth`: `'ANALYTICAL_FORMULA'` не входит в перечень §3.2; 3) в `bend_sequence` отсутствуют `radius_internal_mm`, `requires_gooseneck`, `source`; 4) в `ground_truth` отсутствуют `bend_sequence_operator_confirmed`, `requires_gooseneck`, `negative_reason` |
| `fixtures/synthetic/four_bend.expected.json` | `synthetic_expected` | **N/A** | Аналитический файл ожидаемых метрик (§15.1, Task 1.0.0); схема для `.expected.json` в ТЗ не задана |
| `fixtures/synthetic/l_bracket.expected.json` | `synthetic_expected` | **N/A** | Аналитический файл ожидаемых метрик (§15.1, Task 1.0.0); схема для `.expected.json` в ТЗ не задана |
| `fixtures/synthetic/u_bracket.expected.json` | `synthetic_expected` | **N/A** | Аналитический файл ожидаемых метрик (§15.1, Task 1.0.0); схема для `.expected.json` в ТЗ не задана |

---

## 4. Обновление и вывод интеграционного теста `test_fcstd_fixtures.py`

В тест `tests/fixture/test_fcstd_fixtures.py` добавлена валидация по схемам:
```python
# Schema validations via FileStore (Task 1.0.1a)
from smbs_core.protocols.file_store import FileStore
fs = FileStore()
fs.validate(manifest, "fixture_manifest")

# Verify bends.json matches ground truth and schema
bends_json_path = fixture_dir / "bends.json"
with open(bends_json_path, "r", encoding="utf-8") as bj:
    bends_obj = json.load(bj)
bends_for_validation = {
    "schema_name": "bends",
    "schema_version": "1.0",
    **bends_obj
}
fs.validate(bends_for_validation, "bends")
```

### Результат выполнения теста:
```text
$ /Applications/FreeCAD.app/Contents/Resources/bin/python -m unittest tests/fixture/test_fcstd_fixtures.py
..
----------------------------------------------------------------------
Ran 2 tests in 0.879s

OK
```

---

## 5. Открытые вопросы

1. **Синтетические манифесты (`fixtures/synthetic/*.manifest.json`):**
   - Они создавались на шаге Task 1.0.0 до появления формальной схемы `fixture_manifest.schema.json`.
   - В них `source_of_truth: "ANALYTICAL_FORMULA"`, нет массива `input_files` (пути к `.brep` и `.expected.json` были вложены в `source`), а в `bend_sequence` использовались поля `radius_mm` и `length_mm` вместо `radius_internal_mm` и `requires_gooseneck`.
   - Согласно правилу задачи («Схема описывает то, что уже есть; если данные кривые — правим данные, не схему»), нужно согласовать с техруководителем отдельное приведение этих 3 синтетических манифестов к единой схеме `fixture_manifest.schema.json` (добавив `input_files` с ролями `PART_3D` и `EXPECTED_JSON`, и заменив `ANALYTICAL_FORMULA` на допустимый источник истины, например `GEOMETRIC_INFERENCE` с описанием в `known_limitations`).
2. **Схема для `*.expected.json`:**
   - Нужно ли оформить отдельную схему `synthetic_expected.schema.json` для аналитических расчётов геометрии (`volume_mm3`, `bbox_mm`, `formulas` и др.)?
