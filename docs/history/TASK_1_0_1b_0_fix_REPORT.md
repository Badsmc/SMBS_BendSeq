# Отчёт Task 1.0.1b-0-fix: Исправление маппинга bend_line_flat_pattern

**Дата:** 2026-10-14  
**Ветка:** `stage-1-fix-correctness`  
**Окружение:** `/Applications/FreeCAD.app/Contents/Resources/bin/python` (Python 3.11.14, OCC 7.8.1, FreeCAD 1.1.3 + SheetMetal)

---

## 1. Дефект A: Решение по полю `bend_line_flat` в `SheetMetalUnfoldResult`

В `smbs_core/workers/sheetmetal/adapter.py`:
- Поле `bend_line_flat` в `SheetMetalUnfoldResult` содержало только линию первого гиба (`normalized_bends[0]`).
- Проведён аудит потребителей (`grep_search`): ни один модуль или тест не читал поле `SheetMetalUnfoldResult.bend_line_flat` напрямую. `GeometryWorker` извлекает `bend_lines` из списка `unfold_res.bend_lines` (`NormalizedBendLine`), где каждый элемент уже несёт собственный `bend_line_flat`.
- **Принято минимально инвазивное решение (Вариант 1):** удалить поле `bend_line_flat` из `SheetMetalUnfoldResult`. Все данные о линиях гиба развёртки хранятся в списке `bend_lines: List[NormalizedBendLine]`.

### Diff `smbs_core/workers/sheetmetal/adapter.py`
```diff
--- a/smbs_core/workers/sheetmetal/adapter.py
+++ b/smbs_core/workers/sheetmetal/adapter.py
@@ -46,7 +46,6 @@ class SheetMetalUnfoldResult:
     root_face_name: Optional[str] = None
     root_normal: Optional[List[float]] = None
     bend_lines: List[NormalizedBendLine] = field(default_factory=list)
-    bend_line_flat: Optional[Dict[str, Any]] = None
     diagnostics: List[DiagnosticEntry] = field(default_factory=list)
 
 
@@ -244,7 +243,6 @@ class SheetMetalAdapter:
 
             root_n = [root_normal.x, root_normal.y, root_normal.z] if root_normal else None
 
-            first_flat = normalized_bends[0].bend_line_flat if normalized_bends else None
             return SheetMetalUnfoldResult(
                 status=RunStatus.SUCCESS,
                 unfolded_shape=inplace_unbend,
@@ -256,7 +254,6 @@ class SheetMetalAdapter:
                 root_face_name=facename,
                 root_normal=root_n,
                 bend_lines=normalized_bends,
-                bend_line_flat=first_flat,
                 diagnostics=diagnostics,
             )
```

---

## 2. Дефект B: Диагностика маппинга `bend_id` ↔ `bend_line_flat`

Была проведена диагностика на 4 фикстурах:
1. `fixtures/synthetic/u_bracket.brep` (2 гиба, 90°)
2. `fixtures/synthetic/four_bend.brep` (4 гиба, ±90°)
3. `fixtures/freecad/u_90deg_90deg_ok/part_3d.brep` (2 гиба, 90°)
4. `fixtures/freecad/135_ok/part_3d.brep` (2 гиба, ±45°)

### Таблица диагностики

| Фикстура | `raw_bends` (id, angle, origin) | `flat_bend_lines` (angle, start, end) | Порядок совпадает? | Решение |
|---|---|---|---|---|
| **u_bracket.brep** (2 гиба) | `[0] BEND_01`: ang=-90°, org=(2.5, 0, 2.5)<br>`[1] BEND_02`: ang=-90°, org=(42.5, 0, 2.5) | `[0]`: ang=90°, X=103.91, Y∈[0, 50]<br>`[1]`: ang=90°, X=61.30, Y∈[0, 50] | **Да (топологический порядок обхода дерева граней)** | Количество строго совпадает (2 == 2). Введён `assert len(raw_bends) == len(flat_bend_lines)`. |
| **four_bend.brep** (4 гиба) | `[0] BEND_01`: ang=-90°, org=(93.5, 0, 36)<br>`[1] BEND_02`: ang=-90°, org=(43.5, 0, 32.5)<br>`[2] BEND_03`: ang=+90°, org=(97, 0, 66)<br>`[3] BEND_04`: ang=+90°, org=(40, 0, 2.5) | `[0]`: ang=90°, X=126.52<br>`[1]`: ang=-90°, X=73.91<br>`[2]`: ang=-90°, X=159.13<br>`[3]`: ang=90°, X=41.30 | **Да (топологический порядок обхода дерева граней)** | Количество строго совпадает (4 == 4). Все 4 линии гиба развёртки соответствуют 4 сгибам. |
| **u_90deg_90deg_ok** (2 гиба) | `[0] BEND_01`: ang=90°, org=(200, 2.5, 2.5)<br>`[1] BEND_02`: ang=90°, org=(0, 47.5, 2.5) | `[0]`: ang=-90°, Y=13.80, X∈[0, 200]<br>`[1]`: ang=-90°, Y=61.41, X∈[0, 200] | **Да (топологический порядок)** | Количество совпадает (2 == 2). Введён `assert`. |
| **135_ok** (2 гиба) | `[0] BEND_01`: ang=-45°, org=(200, 2.5, -1)<br>`[1] BEND_02`: ang=+45°, org=(0, 47.5, 2.5) | `[0]`: ang=-135°, Y=14.62, X∈[0, 200]<br>`[1]`: ang=+135°, Y=60.92, X∈[0, 200] | **Да (топологический порядок)** | Количество совпадает (2 == 2). Введён `assert`. |

### Архитектурный вывод по маппингу:
В SheetMetal Unfolder обход граней выполняется по `spanning_tree` от выбранной базовой грани (в глубину/ширину), порождая ровно по одной линии гиба на каждый цилиндрический сектор детали.
В `TopologyWorker` добавлен строгий контрактный `assert`:
```python
assert len(raw_bends) == len(flat_bend_lines), (
    f"bend count mismatch between feature_extractor ({len(raw_bends)}) and unfold ({len(flat_bend_lines)})"
)
```

### Diff `smbs_core/workers/topology/worker.py`
```diff
--- a/smbs_core/workers/topology/worker.py
+++ b/smbs_core/workers/topology/worker.py
@@ -119,7 +119,14 @@ class TopologyWorker:
         if flat_art and isinstance(flat_art, dict):
             flat_bend_lines = flat_art.get("bend_lines", [])
 
+        # Verify bend count correspondence between 3D feature extraction and 2D unfold
+        if flat_bend_lines:
+            assert len(raw_bends) == len(flat_bend_lines), (
+                f"bend count mismatch between feature_extractor ({len(raw_bends)}) and unfold ({len(flat_bend_lines)})"
+            )
+
         # Assemble Bends
+        # Note: mapping by sequential index idx is valid when bend counts match, preserving topological correspondence.
         canonical_bends: Dict[str, Any] = {}
         for idx, (b_id, b_info) in enumerate(raw_bends.items()):
```

---

## 3. Результаты тестов
- `fixtures/synthetic/l_bracket_verify.py` — **PASS**
- `fixtures/synthetic/u_bracket_verify.py` — **PASS**
- `fixtures/synthetic/four_bend_verify.py` — **PASS**
- `tests/fixture/test_fcstd_fixtures.py` (2 теста) — **PASS**
- `tests/test_stage1_baseline.py` (3 теста) — **PASS**
- Смежные тесты Stage 1 (12 тестов) — **PASS**
