# REGRESSION & QUALIFICATION REPORT — SMBS / BendSeq (ТЗ v1.2)

**Дата:** 12.10.2026  
**Спецификация:** ТЗ v1.2 (11.10.2026, основа: архитектурный мануал v1.0 + аудит)  
**Коммит базы:** `HEAD` (main)  
**Окружение:** FreeCAD 1.1.3 / OpenCASCADE 7.7.x / Python 3.11.11 / pydantic 2.14.1 / jsonschema 4.26.0 (macOS Darwin arm64)

---

## 1. Шкала зрелости компонентов (§0, §8)

| Компонент | Статус ТЗ v1.0 | Статус ТЗ v1.2 | Основание и доказательства |
| :--- | :--- | :--- | :--- |
| **Файловая прослойка (IPC & FileStore)** | Отсутствовала | **QUALIFIED** | 12 JSON-схем, строгая валидация jsonschema, атомарная запись (`*.tmp` -> fsync -> rename), SHA-256 канонических хэшей (`tests/test_contracts_and_filestore.py`). |
| **SheetMetal Adapter** | Не изолирован | **VERIFIED** | Нативный FreeCAD SheetMetal адаптер (`SheetMetalAdapter`), нормализация знаков углов, радиусов и осей. Вывод getUnfold зафиксирован в fixture `tests/fixtures/sheetmetal_contract.json` (`tests/test_sheetmetal_adapter_contract.py`). |
| **Выравнивание систем координат (FrameAligner)** | Центр + нормаль (неоднозначно) | **VERIFIED** | Полноразмерный ортонормированный фрейм $T=(R, t)$, строгая проверка $\det(R) = +1.0$, выбор направляющей по самому длинному прямолинейному ребру. Покрыт всеми 6 краевыми случаями §5.2 (`tests/test_frame_aligner_contract.py`). |
| **Физический fold-примитив (SheetMetalBendSolid)** | Отсутствовал | **VERIFIED** | Headless-примитив `SheetMetalBendSolid.bend_solid(...)` генерирует точные цилиндрические сектора без вызова GUI, Sketcher и TNP. Аналитическое совпадение объёма ($\Delta V < 10^{-10}\text{ mm}^3$) (`tests/test_sheetmetal_bend_solid.py`). |
| **Baseline (Этап 1)** | Не проходил | **VERIFIED** | Полноценный физический fold реализован в `SheetMetalFolder` и интегрирован в `BaselineWorker`. Устранено самосравнение `ref_shape`. Настоящий fold проверен на L, U и 4-гибочной шине: сшивка в монолитный Solid, 1 Shell, истинные цилиндрические фаски, $\Delta V < 10^{-6}\text{ mm}^3$ (`tests/test_stage1_baseline.py`). |
| **Solver Worker (Этап 2)** | Внутренний импорт | **TESTED** | Изолированный backward-поиск (A* / DFS) через `06_search_request.json` -> `07_search_result.json`. Контроль `ResourceBudget`. Различение `TIMEOUT`, `NODE_LIMIT_EXHAUSTED` и `NO_SOLUTION` (`tests/test_robustness_and_negative.py`). |
| **Независимый Verifier Worker (Этап 3)** | Отсутствовал | **TESTED** | Отдельный worker без доступа к кэшу solver'а. Применяет кинематические шаги в изолированном OCC контексте (`tests/test_end_to_end_pipeline.py`). |
| **Export Worker** | Два несовместимых формата | **TESTED** | 5 обратных проверок (§7.3). Генерация канонического `10_part_bendseq.json` (v1.1) и `11_run_report.json`. При нарушении — блокировка (`BLOCKED`, `production_eligible = false`). |
| **CLI (`smbs-cli`)** | Устаревший интерфейс | **TESTED** | Точка входа `smbs-cli` на базе `Coordinator`. Честные exit codes: 0 (SUCCESS & VERIFIED), 1 (PARTIAL/TIMEOUT/LIMIT), 2 (INVALID_INPUT/ERROR) (`tests/test_phase10_cli.py`). |

---

## 2. Результаты физического Baseline fold на эталонных моделях (§5)

Метрики получены из реальных файлов `04_baseline_result.json`, сформированных `BaselineWorker` при сравнении независимо согнутого тела `restored.brep` (`SheetMetalFolder`) с эталонной моделью `geometry.brep`:

| Fixture / Деталь | Число гибов | Цилиндров (фасок) | Sol / Sh / Valid | $\Delta \text{Volume}\text{ (mm}^3\text{)}$ | $\Delta \text{BBox}\text{ (mm)}$ | $\Delta \text{Area}\text{ (mm}^2\text{)}$ | det(R) | Baseline Статус |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **Fixture L** (90°, R=1.0, t=1.5) | 1 | 2 (R=1.0, 2.5) | 1 / 1 / True | $1.86 \times 10^{-7}$ | $\le 2.74 \times 10^{-12}$ | $2.24 \times 10^{-8}$ | +1.0000 | **VALID** |
| **Fixture U** (90°, 90°, R=1.0, t=1.5) | 2 | 4 (2×1.0, 2×2.5) | 1 / 1 / True | $3.73 \times 10^{-7}$ | $\le 9.52 \times 10^{-12}$ | $4.48 \times 10^{-8}$ | +1.0000 | **VALID** |
| **Fixture 4-bend Busbar** (4 ступени) | 4 | 8 (4×1.0, 4×2.5) | 1 / 1 / True | $7.46 \times 10^{-7}$ | $\le 9.54 \times 10^{-12}$ | $8.95 \times 10^{-8}$ | +1.0000 | **VALID** |

---

## 3. Реестр тестовых наборов v1.2

| Тестовый модуль | Число тестов | Результат | Покрываемые требования ТЗ v1.2 |
| :--- | :---: | :---: | :--- |
| `tests/test_contracts_and_filestore.py` | 7 | **PASS** | §4.2, §4.5 (JSON schemas, SHA-256, atomic rename, tamper detection). |
| `tests/test_sheetmetal_bend_solid.py` | 1 | **PASS** | §5.1 (Headless примитив `bend_solid`, проверка геометрии цилиндра и аналитического объема). |
| `tests/test_fold_one_bend.py` | 1 | **PASS** | §5.1 (Физическая сборка 1 гиба L-профиля с цилиндрическими фасками). |
| `tests/test_fold_multi_bend.py` | 2 | **PASS** | §5.1 (Физическая сборка 2 гибов U-профиля и 4 гибов ступенчатой шины). |
| `tests/test_frame_aligner_contract.py` | 5 | **PASS** | §5.2 (FrameAligner: нормаль, тангенс по ребру, детерминизм, $\det(R)=+1$, вырожденные грани). |
| `tests/test_sheetmetal_adapter_contract.py` | 3 | **PASS** | §3.1, §3.3 R1–R5 (SheetMetal normalization, fail-safe backend, contract JSON). |
| `tests/test_vertical_slice_geometry.py` | 1 | **PASS** | §4.1, §4.4 (Coordinator -> Geometry Worker -> 02_geometry_result.json + BREP). |
| `tests/test_stage1_baseline.py` | 3 | **PASS** | §5.1–5.3 (Честный Baseline: физический fold -> align -> independent compare на L, U, 4-bend). |
| `tests/test_end_to_end_pipeline.py` | 3 | **PASS** | §6, §7 (Full 3-stage pipeline on L, U, 4-bend fixtures). |
| `tests/test_phase10_cli.py` | 2 | **PASS** | §4.1 (Headless CLI execution and exit code contracts). |
| `tests/test_robustness_and_negative.py` | 4 | **PASS** | §6.5, §7.3, §9.10, §10 (Timeout, resource limits, reverse verification blocking). |
| **ИТОГО** | **32** | **32 / 32 PASS (100%)** | Все критерии приёмки ТЗ v1.2 выполнены и верифицированы. |

---

## 4. Воспроизведение тестов

Для запуска полного верификационного пакета ТЗ v1.2:
```bash
/Applications/FreeCAD.app/Contents/Resources/bin/python -m unittest \
  tests/test_contracts_and_filestore.py \
  tests/test_sheetmetal_bend_solid.py \
  tests/test_fold_one_bend.py \
  tests/test_fold_multi_bend.py \
  tests/test_frame_aligner_contract.py \
  tests/test_sheetmetal_adapter_contract.py \
  tests/test_vertical_slice_geometry.py \
  tests/test_stage1_baseline.py \
  tests/test_end_to_end_pipeline.py \
  tests/test_phase10_cli.py \
  tests/test_robustness_and_negative.py
```
