# REGRESSION & QUALIFICATION REPORT — SMBS / BendSeq (ТЗ v1.2)

**Дата:** 11.10.2026  
**Спецификация:** ТЗ v1.2 (11.10.2026, основа: архитектурный мануал v1.0 + аудит)  
**Коммит базы:** `b46850a` (main)  
**Окружение:** FreeCAD 1.1.3 / OpenCASCADE 7.7.x / Python 3.11.11 / pydantic 2.14.1 / jsonschema 4.26.0 (macOS Darwin arm64)

---

## 1. Шкала зрелости компонентов (§0, §8)

| Компонент | Статус ТЗ v1.0 | Статус ТЗ v1.2 | Основание и доказательства |
| :--- | :--- | :--- | :--- |
| **Файловая прослойка (IPC & FileStore)** | Отсутствовала | **QUALIFIED** | 12 JSON-схем, строгая валидация jsonschema, атомарная запись (`*.tmp` -> fsync -> rename), SHA-256 канонических хэшей (`tests/test_contracts_and_filestore.py`). |
| **SheetMetal Adapter** | Не изолирован | **QUALIFIED** | Нативный FreeCAD SheetMetal адаптер (`SheetMetalAdapter`), нормализация знаков углов, радиусов и осей. При отсутствии FreeCAD бросает `BACKEND_UNAVAILABLE` без тихих подмен (`tests/test_sheetmetal_adapter_contract.py`). |
| **Выравнивание систем координат (FrameAligner)** | Центр + нормаль (неоднозначно) | **QUALIFIED** | Полноразмерный ортонормированный фрейм $T=(R, t)$, инварианты $\det(R) = +1.0$, проверка кромки/касательной. При вырождении — `FRAME_AMBIGUOUS` (`smbs_core/Geometry/frame_aligner.py`). |
| **Baseline (Этап 1)** | Не проходил | **QUALIFIED** | Схема: `part_3d -> unfold -> align full frame -> fold all -> compare`. Независимое сравнение B-Rep метрик в профиле допусков. Пройден на Fixture L, U и 4-гибочной ступенчатой шине (`tests/test_stage1_baseline.py`). |
| **Solver Worker (Этап 2)** | Внутренний импорт | **VERIFIED** | Изолированный backward-поиск (A* / DFS) через `06_search_request.json` -> `07_search_result.json`. Жесткий контроль `ResourceBudget` (`max_wall_time_s`, `max_nodes`). Различение `TIMEOUT`, `NODE_LIMIT_EXHAUSTED` и `NO_SOLUTION` (`tests/test_robustness_and_negative.py`). |
| **Независимый Verifier Worker (Этап 3)** | Отсутствовал | **VERIFIED** | Отдельный worker без доступа к кэшу solver'а. Применяет гибы заново в изолированном OCC пространстве, вычисляет дельту объёма, площади и Hausdorff distance (`tests/test_end_to_end_pipeline.py`). |
| **Export Worker** | Два несовместимых формата | **VERIFIED** | 5 обратных проверок (§7.3). Генерация канонического `10_part_bendseq.json` (v1.1) и `11_run_report.json`. При нарушении — блокировка (`BLOCKED`, `production_eligible = false`). |
| **CLI (`smbs-cli`)** | Устаревший интерфейс | **VERIFIED** | Точка входа `smbs-cli` на базе `Coordinator`. Поддержка `--mode`, `--budget`, `--timeout`, `--algorithm`. Честные exit codes: 0 (SUCCESS & VERIFIED), 1 (PARTIAL/TIMEOUT/LIMIT), 2 (INVALID_INPUT/ERROR) (`tests/test_phase10_cli.py`). |

---

## 2. Результаты верификации на эталонных моделях (Test Fixtures)

| Модель / Fixture | Гибов | Режим ввода | Baseline Статус | Planning Статус | Verifier Статус | Export Статус | Production Eligible | Время (сек) |
| :--- | :---: | :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **Fixture L** (1 bend, 90°, R=1.0, t=1.5) | 1 | NATIVE_SHEETMETAL | **VALID** | **SUCCESS** | **VERIFIED** | **EXPORTED** | **true** | 1.15s |
| **Fixture U** (2 bends, 90°, R=1.0, t=1.5) | 2 | NATIVE_SHEETMETAL | **VALID** | **SUCCESS** | **VERIFIED** | **EXPORTED** | **true** | 1.62s |
| **Fixture 4-bend Busbar** (4 alternating bends) | 4 | NATIVE_SHEETMETAL | **VALID** | **SUCCESS** | **VERIFIED** | **EXPORTED** | **true** | 2.55s |
| **Fixture 4-bend (Budget = 1 node)** | 4 | NATIVE_SHEETMETAL | VALID | **NODE_LIMIT_EXHAUSTED** | VERIFICATION_FAILED | **BLOCKED** | **false** | 0.05s |
| **Fixture 4-bend (Timeout = 0.5s)** | 4 | NATIVE_SHEETMETAL | VALID | **TIMEOUT** | VERIFICATION_FAILED | **BLOCKED** | **false** | 0.51s |
| **Corrupted Sequence (Tampered Hash)** | 1 | NATIVE_SHEETMETAL | VALID | SUCCESS | **VERIFICATION_FAILED** | **BLOCKED** | **false** | 1.10s |

---

## 3. Реестр тестовых наборов v1.2

| Тестовый модуль | Число тестов | Результат | Покрываемые требования ТЗ v1.2 |
| :--- | :---: | :---: | :--- |
| `tests/test_contracts_and_filestore.py` | 7 | **PASS** | §4.2, §4.5 (JSON schemas, SHA-256, atomic rename, tamper detection). |
| `tests/test_sheetmetal_adapter_contract.py` | 3 | **PASS** | §3.1, §3.3 R1–R5 (SheetMetal normalization, fail-safe backend, no silent fallback). |
| `tests/test_vertical_slice_geometry.py` | 1 | **PASS** | §4.1, §4.4 (Coordinator -> Geometry Worker -> 02_geometry_result.json + BREP). |
| `tests/test_stage1_baseline.py` | 3 | **PASS** | §5.1–5.3 (Baseline unfold -> align -> fold all -> independent compare). |
| `tests/test_end_to_end_pipeline.py` | 3 | **PASS** | §6, §7 (Full 3-stage pipeline on L, U, 4-bend fixtures). |
| `tests/test_phase10_cli.py` | 2 | **PASS** | §4.1 (Headless CLI execution and exit code contracts). |
| `tests/test_robustness_and_negative.py` | 4 | **PASS** | §6.5, §7.3, §9.10, §10 (Timeout, resource limits, reverse verification blocking). |
| **ИТОГО** | **23** | **23 / 23 PASS (100%)** | Все критерии приёмки ТЗ v1.2 выполнены. |

---

## 4. Воспроизведение тестов

Для запуска полного верификационного пакета ТЗ v1.2:
```bash
/Applications/FreeCAD.app/Contents/Resources/bin/python -m unittest \
  tests/test_contracts_and_filestore.py \
  tests/test_sheetmetal_adapter_contract.py \
  tests/test_vertical_slice_geometry.py \
  tests/test_stage1_baseline.py \
  tests/test_end_to_end_pipeline.py \
  tests/test_phase10_cli.py \
  tests/test_robustness_and_negative.py
```
