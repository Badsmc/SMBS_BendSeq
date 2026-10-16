# Отчёт Task 1.0.1-prep: Диагностика перед Fix Baseline

**Дата:** 2026-10-12  
**Репозиторий:** `/Users/badmc/Desktop/SMBS_BendSeq`  
**Ветка:** `stage-1-fix-correctness`  
**Окружение:** `/Applications/FreeCAD.app/Contents/Resources/bin/python` (Python 3.11.14, OCC 7.8.1.1, FreeCAD 1.1.3, SheetMetal Workbench)  
**Режим выполнения:** READ-ONLY (никакой код, схемы, манифесты не изменялись; коммиты не создавались).

---

## Часть 1. Проверка схем и валидация манифестов

### 1.1. Поиск схем `contracts/schemas/`
Каталог схем проекта: `smbs_core/contracts/schemas/`.  
Всего в каталоге обнаружено **12 JSON-схем** (Draft 2020-12):
- `baseline_request.schema.json`
- `baseline_result.schema.json`
- `canonical_part.schema.json`
- `envelope.schema.json`
- `geometry_result.schema.json`
- `input_manifest.schema.json`
- `part_bendseq.schema.json`
- `search_request.schema.json`
- `search_result.schema.json`
- `tooling_manifest.schema.json`
- `verification_request.schema.json`
- `verification_result.schema.json`

> **Критический факт 1:** Схемы `fixture_manifest.schema.json` в каталоге `contracts/schemas/` **нет** (отсутствует как класс).  
> **Критический факт 2:** Отдельной схемы для `bends.json` в каталоге `contracts/schemas/` также **нет**.

### 1.2. Проверка enum для `source_of_truth` / `source` (§3.2 ТЗ)
В схеме `smbs_core/contracts/schemas/canonical_part.schema.json` (строка 58) и в перечислении `smbs_core/protocols/file_store.py:ParameterSource` (строки 51–57) зафиксирован строгий список допустимых значений согласно §3.2 ТЗ:

```json
"enum": [
  "SHEETMETAL_NATIVE",
  "GEOMETRIC_INFERENCE",
  "USER_INPUT",
  "CONFIGURATION",
  "DEFAULT_POLICY",
  "UNKNOWN"
]
```

Значения `RECOMPUTED_FROM_FINAL_BODY` в этом перечне **нет**.

### 1.3. Результат валидации 16 манифестов (`fixtures/freecad/*/manifest.json`)
Попытка прямой валидации через `FileStore.validate(manifest, schema_name="fixture_manifest")` выбрасывает исключение:
`ContractError: Schema file not found for schema 'fixture_manifest': .../fixture_manifest.schema.json`.

При валидации поля `ground_truth.source_of_truth` на соответствие допустимому перечню §3.2:
- **15 из 16 манифестов:** значение `SHEETMETAL_NATIVE` (**PASS**).
- **1 из 16 манифестов (`135_ok/manifest.json`):** значение `RECOMPUTED_FROM_FINAL_BODY` (**FAIL** относительно §3.2).

> **Подтверждение Замечания A:** Замечание полностью подтверждено. В манифесте `135_ok` значение выходит за рамки допустимого перечня §3.2. Решение: значение поля `ground_truth.source_of_truth` должно быть возвращено к `SHEETMETAL_NATIVE`, а способ получения геометрии развёртки должен фиксироваться в `artifact_provenance: RECOMPUTED` и в `known_limitations`.

### 1.4. Проверка 16 файлов `bends.json`
Схема для `bends.json` отсутствует.
Анализ полей, фактически присутствующих в 16 файлах `fixtures/freecad/*/bends.json`:
- **Поля верхнего уровня:**
  - `part_requires_gooseneck`: `bool` — добавлено вне схемы.
  - `bends`: `list`
  - `extraction_method`: `str`
  - `labels_match_bend_operation`: `bool`
- **Поля внутри каждого элемента списка `bends`:**
  - `bend_id`: `str`
  - `angle_deg`: `float`
  - `direction`: `str` ("UP" / "DOWN")
  - `radius_internal_mm`: `float`
  - `source`: `str` ("SHEETMETAL_NATIVE")
  - `requires_gooseneck`: `bool` — добавлено вне схемы.
  - `operator_confirmed`: `bool` (присутствует только в `2_1_3_4_ok`) — добавлено вне схемы.
  - `step`: `int` (присутствует только в `2_1_3_4_ok`, задаёт порядок гибки 1..4) — добавлено вне схемы.

---

## Часть 2. Анализ `smbs_core/Geometry/sheetmetal_folder.py`

Файл содержит класс `SheetMetalFolder`. В файле определено ровно 5 методов:
1. `_to_occ_shape` (статический хелпер конвертации FreeCAD -> OCC TopoDS_Shape)
2. `fold_one_bend` (сборка 1 гиба L-профиля)
3. `fold_u_bracket` (сборка 2 гибов U-профиля)
4. `fold_4bend_stepped_busbar` (сборка 4 гибов ступенчатой шины)
5. `fold_from_canonical_part` (точка входа для `BaselineWorker`)

Функция с именем `fold_multi_bend` в файле **отсутствует** (вместо неё реализованы специализированные методы `fold_u_bracket` и `fold_4bend_stepped_busbar`).

### Сводная таблица функций

| Функция | Сигнатура | Что принимает | Что возвращает | Использует `aligned_flat`? | Строит синтетику (`Part.makeBox`)? | Сворачивает реально (`bend_solid`)? |
|---|---|---|---|---|---|---|
| `_to_occ_shape` | `(fc_shape: Part.Shape) -> TopoDS_Shape` | `Part.Shape` | `TopoDS_Shape` | **Нет** | **Нет** | **Нет** (экспорт/импорт BREP) |
| `fold_one_bend` | `(cls, l1=95.5, l2=47.5, bend_r=1.0, thickness=1.5, kfactor=0.44, width=50.0, angle_deg=90.0) -> TopoDS_Shape` | Числовые параметры фланцев и гиба | `TopoDS_Shape` | **Нет** | **Да** (`Part.makeBox` для полок f1, f2) | **Частично** (`bend_solid` только для сектора скругления) |
| `fold_u_bracket` | `(cls, l1=95.5, l_mid=50.0, l2=47.5, bend_r=1.0, thickness=1.5, kfactor=0.44, width=50.0) -> TopoDS_Shape` | Числовые параметры полок U-профиля | `TopoDS_Shape` | **Нет** | **Да** (`Part.makeBox` для f1, f_mid, f2) | **Частично** (`bend_solid` для сектора сгиба, затем поворот и сдвиг) |
| `fold_4bend_stepped_busbar` | `(cls, l1=40.0, l2=30.0, l3=50.0, l4=30.0, l5=40.0, bend_r=1.0, thickness=1.5, kfactor=0.44, width=50.0) -> TopoDS_Shape` | Числовые длины 5 полок шины | `TopoDS_Shape` | **Нет** | **Да** (`Part.makeBox` для 5 полок f1..f5) | **Частично** (`bend_solid` для сектора сгиба, затем 4 копии/трансляции) |
| `fold_from_canonical_part` | `(cls, canonical_part: Dict[str, Any], aligned_flat: Optional[TopoDS_Shape] = None) -> TopoDS_Shape` | Словарь `canonical_part`, опциональный `aligned_flat` | `TopoDS_Shape` | **НЕТ (аргумент игнорируется!)** | **Да** (делегирует в `fold_one_bend`, `fold_u_bracket`, `fold_4bend...`) | **Частично** (через синтетические хелперы) |

---

### Детальный разбор и цитаты строк

#### 1. `fold_one_bend` (строки 56–96)
```python
67:         neutral_r = bend_r + kfactor * thickness
68:         unfold_len = neutral_r * math.radians(angle_deg)
70:         f1 = Part.makeBox(l1, width, thickness, FreeCAD.Vector(0, 0, 0))
72:         bend_face = Part.makePolygon([
73:             FreeCAD.Vector(l1, 0, thickness),
74:             FreeCAD.Vector(l1 + unfold_len, 0, thickness),
75:             FreeCAD.Vector(l1 + unfold_len, width, thickness),
76:             FreeCAD.Vector(l1, width, thickness),
77:             FreeCAD.Vector(l1, 0, thickness)
78:         ])
...
83:         b_sector = SheetMetalBendSolid.bend_solid(
84:             sel_face=bend_face,
85:             sel_edge=bend_edge,
86:             bend_r=bend_r,
87:             thickness=thickness,
88:             neutral_radius=neutral_r,
89:             axis=axis,
90:             flipped=False
91:         )
93:         f2 = Part.makeBox(thickness, width, l2, FreeCAD.Vector(l1 + bend_r, 0, thickness + bend_r))
94:         fused = f1.fuse(b_sector).fuse(f2)
```
**Что делает:** Создаёт синтетическую коробку `f1`, генерирует синтетический прямоугольник зоны гиба `bend_face`, сворачивает его через `bend_solid` в сектор `b_sector`, создаёт вторую синтетическую коробку `f2` и сшивает их через `.fuse()`. Форма `aligned_flat` не используется.

#### 2. `fold_u_bracket` (строки 99–146)
```python
110:         neutral_r = bend_r + kfactor * thickness
111:         unfold_len = neutral_r * math.radians(90.0)
...
124:         s_up = SheetMetalBendSolid.bend_solid(...)
134:         f_mid = Part.makeBox(l_mid, width, thickness, FreeCAD.Vector(2.5, 0, 0))
136:         s2 = s_up.translated(FreeCAD.Vector(2.5 + l_mid, 0, 0))
137:         f2 = Part.makeBox(thickness, width, l2, FreeCAD.Vector(2.5 + l_mid + bend_r, 0, 2.5))
140:         s1.rotate(FreeCAD.Vector(0, 0, 0), FreeCAD.Vector(0, 0, 1), 180.0)
142:         f1 = Part.makeBox(thickness, width, l1, FreeCAD.Vector(0, 0, 2.5))
144:         fused = f1.multiFuse([s1, f_mid, s2, f2])
```
**Что делает:** Генерирует 3 коробки (`f1`, `f_mid`, `f2`) и один сектор сгиба `s_up`, который дублируется и трансформируется через rigid-body трансформации (`rotate`, `translate`). Сшивается через `multiFuse`. Входная развёртка не используется.

#### 3. `fold_4bend_stepped_busbar` (строки 149–206)
```python
186:         s_down = s_up.copy()
187:         s_down.rotate(FreeCAD.Vector(0, 0, 0), FreeCAD.Vector(1, 0, 1), 180.0)
191:         f1 = Part.makeBox(l1, width, thickness, FreeCAD.Vector(0, 0, 0))
192:         s1 = s_up.translated(FreeCAD.Vector(l1, 0, 0))
193:         f2 = Part.makeBox(thickness, width, l2, FreeCAD.Vector(l1 + bend_r, 0, 2.5))
195:         f3 = Part.makeBox(l3, width, thickness, FreeCAD.Vector(43.5, 0, 33.5))
197:         step_shift = FreeCAD.Vector(53.5, 0, 33.5)
201:         f5 = Part.makeBox(l5, width, thickness, FreeCAD.Vector(97.0, 0, 67.0))
203:         all_parts = [f1, s1, f2, s2, f3, s3, f4, s4, f5]
204:         fused = all_parts[0].multiFuse(all_parts[1:])
```
**Что делает:** Полная ручная компоновка шины из 5 коробок (`f1`..`f5`) с захардкоженными координатами смещений (`Vector(43.5, 0, 33.5)`, `Vector(97.0, 0, 67.0)`). 4 сгиба получены размножением единственного вызова `bend_solid`. Входная геометрия игнорируется.

#### 4. `fold_from_canonical_part` (строки 209–304)
```python
209:     def fold_from_canonical_part(
210:         cls,
211:         canonical_part: Dict[str, Any],
212:         aligned_flat: Optional[TopoDS_Shape] = None
213:     ) -> TopoDS_Shape:
...
223:         num_bends = len(bends)
226:         if num_bends == 1:
...
235:                 l1 = round(areas[0] / (2.0 * width), 2)
236:                 l2 = round(areas[1] / (2.0 * width), 2)
240:             return cls.fold_one_bend(l1=l1, l2=l2, ...)
250:         elif num_bends == 2:
...
273:             return cls.fold_u_bracket(l1=l1, l_mid=l_mid, l2=l2, ...)
284:         elif num_bends == 4:
...
290:             return cls.fold_4bend_stepped_busbar(l1=40.0, l2=30.0, l3=50.0, ...)
302:         else:
303:             raise NotImplementedError(f"Folding for {num_bends} bends is not yet implemented.")
```
**Что делает:** 
- Параметр `aligned_flat` объявлен в сигнатуре, но **не читается ни в одной строчке тела функции**!
- Длины полок грубо восстанавливаются делением площади полигона панели на 2 и на ширину: `l1 = round(areas[0] / (2.0 * width), 2)`.
- Для 4 гибов длины полок просто захардкожены константами: `l1=40.0, l2=30.0, l3=50.0, l4=30.0, l5=40.0`.
- Любое другое число гибов (3, 5, 6, 8, ...) выбрасывает `NotImplementedError`.

---

## Часть 3. Проверка тестов

Поиск вызовов функций гибки по кодовой базе (`grep -rn "fold_one_bend\|fold_multi_bend\|fold_from_canonical_part"`):

| Файл : Строка | Вызываемая функция | Что проверяет | Результат запуска |
|---|---|---|---|
| `smbs_core/workers/baseline/worker.py:200` | `fold_from_canonical_part` | Восстановление 3D-тела в `BaselineWorker.run()` для независимого сравнения с эталоном | Интеграционный вызов в пайплайне |
| `tests/test_fold_one_bend.py:112` | `fold_one_bend` (локальная копия в тесте) | Восстановление 1 гиба L-профиля, проверка инвариантов (1 Solid, 1 Shell, 2 цилиндрические грани, $\Delta V < 10^{-3}$, $\Delta\text{BBox} < 10^{-3}$) против эталона | **PASS** |
| `tests/test_fold_multi_bend.py:127` | `fold_u_bracket` (локальная копия в тесте) | Восстановление 2 гибов U-профиля, инварианты (1 Solid, 1 Shell, 4 цилиндрические грани, $\Delta V < 10^{-4}$) против эталона | **PASS** |
| `tests/test_fold_multi_bend.py:227` | `fold_4bend_stepped_busbar` (локальная копия в тесте) | Восстановление 4 гибов ступенчатой шины, инварианты (1 Solid, 1 Shell, 8 цилиндрических граней, $\Delta V < 10^{-4}$) против эталона | **PASS** |
| `tests/test_stage1_baseline.py:56` | `Coordinator.start_run` -> `BaselineWorker` -> `fold_from_canonical_part` | Тест полного прогона baseline для L-профиля (`test_baseline_l_profile`) | **PASS** |
| `tests/test_stage1_baseline.py:71` | `Coordinator.start_run` -> `BaselineWorker` -> `fold_from_canonical_part` | Тест полного прогона baseline для U-канала (`test_baseline_u_channel`) | **PASS** |
| `tests/test_stage1_baseline.py:90` | `Coordinator.start_run` -> `BaselineWorker` -> `fold_from_canonical_part` | Тест полного прогона baseline для 4-гибочной шины (`test_baseline_4bend_stepped`) | **PASS** |

### Примечание по тестам:
В тестах `test_fold_one_bend.py` и `test_fold_multi_bend.py` функции `fold_one_bend`, `fold_u_bracket` и `fold_4bend_stepped_busbar` были продублированы локально внутри файлов тестов.  
Тесты `test_stage1_baseline.py` вызывают модуль через `Coordinator`, который дёргает `fold_from_canonical_part`. Они проходят успешно **только** потому, что эти три синтетические модели созданы с теми же размерами полок, которые захардкожены в `sheetmetal_folder.py`.

---

## Часть 4. Открытые вопросы перед Task 1.0.1 (Fix Baseline)

1. **Архитектура честного folding из `aligned_flat`:**  
   - Каков алгоритмический контракт преобразования `aligned_flat` в согнутую деталь?  
   - Реализуем ли мы разрез `aligned_flat` на панели по линиям гиба (`bend_lines`), физическое встраивание сгиба через `SheetMetalBendSolid.bend_solid` и поворот смежных граней? Либо используем вызов `SheetMetal` folding операций?
2. **Отсутствие схемы `fixture_manifest.schema.json`:**  
   - В каталоге схем нет `fixture_manifest.schema.json`. Следует ли добавить её в `contracts/schemas/` в рамках Task 1.0.1, чтобы валидация манифестов стала частью CI/автотестов?
3. **Исправление манифеста `135_ok/manifest.json`:**  
   - В соответствии с Замечанием A: вернуть `source_of_truth: SHEETMETAL_NATIVE`, добавить `artifact_provenance: RECOMPUTED` для `input_files[flat_pattern]`.
4. **Схема для `bends.json`:**  
   - Нужно ли оформить отдельную схему `bends_manifest.schema.json` или оставить `bends.json` как вспомогательный промежуточный экспорт для фикстур FreeCAD?
