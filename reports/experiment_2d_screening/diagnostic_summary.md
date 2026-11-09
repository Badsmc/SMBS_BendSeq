# Диагностический отчёт: Исследование причины UNVERIFIED в 3D-валидаторе для модели Z-(ok).FCStd

**Репозиторий:** `Badsmc/SMBS_BendSeq`  
**Ветка:** `experiment/2d-search-screening`  
**Базовый коммит:** `763c8a0`  
**Окружение:** FreeCAD 1.1.3 (Libs: 1.1.3R20260725), OpenCASCADE (OCCT) 7.8.1, PySide6 6.8.3, SheetMetal Workbench  
**Дата:** 2026-11-09  

---

## 1. Точная техническая первопричина статуса UNVERIFIED и отсутствия вызовов OCCT

### Исходная проблема
При физическом тестировании модели `Z-(ok).FCStd` с оснасткой:
- Пуансон: `PUNCH_AP_1017_88°_H104,65_R0,8`
- Матрица: `T-DIE_AT_3015_88°_V12_H80`

поиск последовательности гибки возвращал:
- `status: UNVERIFIED`
- `found_sequence: null`
- `occt_boolean_calls: 0`
- `occt_boolean_elapsed_s: 0.0`
- `distance_calls: 0`

### Механизм возникновения и точка останова
1. **Геометрические параметры модели:**
   - Модель `Z-(ok).FCStd` имеет 2 гиба: `B01` и `B02`.
   - Длина каждого гиба составляет **200.0 мм** (оси гибов: `[(0.0, 13.7566, 0.0), (200.0, 13.7566, 0.0)]` и `[(200.0, 61.2699, 0.0), (0.0, 61.2699, 0.0)]`).
   - При центральном позиционировании диапазон гиба вдоль оси станка $X_m$:
     $$X_{m,\text{bend}} \in [-100.0, +100.0]\text{ мм}$$

2. **Геометрические параметры оснастки:**
   - Оснастка загружается из реальных файлов твердотельной геометрии библиотеки:
     - `toollibrary/punch/PUNCH_AP_1017_88°_H104,65_R0,8.FCStd`
     - `toollibrary/die/T-DIE_AT_3015_88°_V12_H80.FCStd`
   - Измеренная ширина инструментов вдоль рабочей оси $X_m$ составляет **100.0 мм** ($X_m \in [-50.0, +50.0]\text{ мм}$).
   - В соответствии с физической политикой бенчмарка (`physical_length_mode: LIBRARY_FIXED`), параметр `match_active_bend_length` установлен в `False`. Виртуальное растяжение инструментов запрещено.

3. **Точка раннего сброса (short-circuit):**
   - В функции `check_current_25d_pose()` (`smbs_core/kinematics2d/physical_validation_25d.py`, строки 303–311):
     ```python
     pcov = check_tool_coverage(span, punch.span_xm, punch.sections)
     dcov = check_tool_coverage(span, die.span_xm, die.sections)
     if not pcov.is_covered or not dcov.is_covered:
         return CurrentPosePhysicalReport(
             PhysicalEvidence.UNVERIFIED, sec, "INSUFFICIENT_TOOL_COVERAGE",
             "punch:" + pcov.reason + "; die:" + dcov.reason,
             ("2D_SECTION", "3D_TOOL_LENGTH"),
             tool_length_info=tool_len_info,
         )
     ```
   - Функция `check_tool_coverage()` определяет:
     `Active bend length (200.0mm, X_m [-100.0, 100.0]) exceeds tooling span (100.0mm, X_m [-50.0, 50.0]). Uncovered: left=50.0mm, right=50.0mm.`
   - Функция возвращает `PhysicalEvidence.UNVERIFIED` с кодом `INSUFFICIENT_TOOL_COVERAGE`.
   - В функции `evaluate_candidate_bend_step()` (`sequence_search.py`, строки 274–280) проверяется условие:
     ```python
     pose_ok = pose_rep.evidence_status in (
         PhysicalEvidence.SAMPLED_3D_CLEAR_NOT_CONTINUOUS,
         PhysicalEvidence.CONDITIONAL,
     )
     ```
   - Поскольку статус `UNVERIFIED` не является допустимым, вычисление траектории и 3D-интерференции полностью отменяется:
     `traj_rep = None`
   - В результате выполнение **не доходит** до `validate_workpiece_tooling_pose()`, где вызываются функции `Part.Shape.common()` и `distToShape()`. Поэтому счётчик вызовов OCCT остаётся строго равным **0**.

---

## 2. Физическая природа несовместимости детали и оснастки

- **Физический факт:** На листогибочном прессе невозможно корректно и безопасно согнуть листовую деталь с линией гиба длиной 200 мм одним сегментом инструмента длиной 100 мм. По 50 мм с каждого края заготовки остаются неподдержанными и не подвергаются гибке в матрице, что делает операцию технологически неосуществимой на выбранной одиночной станции.
- **Политика соответствия:** Ранее в бенчмарках использовался режим `match_active_bend_length=True`, который синтетически экструдировал 2D-профиль инструмента до длины 200 мм (виртуальный инструмент). С введением коммита `7aa6743` и `763c8a0` было жестко регламентировано: **физический бенчмарк требует реальной фиксированной оснастки из CAD-библиотеки**.
- **Заключение:** Это **подлинная технологическая несовместимость детали и выбранного набора инструмента**, зафиксированная системой валидации в строгом соответствии с принципом «fail-closed». Статус `UNVERIFIED` (с причиной `INSUFFICIENT_TOOL_COVERAGE`) предотвращает симуляцию заведомо нефизичного процесса.

---

## 3. Таблица сравнительных измерений BASELINE vs 2D_RISK

Измерения проведены на чистой сессии FreeCAD с холодным кэшем:

| Метрика | BASELINE (Mode A) | 2D_RISK / SCREENED (Mode B) | Разница / Экономия |
|---|---|---|---|
| **Итоговый статус поиска** | `UNVERIFIED` | `UNVERIFIED` | 0 |
| **Найденная последовательность** | `null` | `null` | Идентично |
| **Отказы кандидатов** | B01: coverage fail; B02: coverage fail | B01: coverage fail; B02: coverage fail | Идентично |
| **Stage A (2D эвристики)** | 2 | 2 | 0 |
| **Stage B (2D screening)** | 2 | 2 | 0 |
| **Stage C (3D попытки)** | 2 | 2 | 0 |
| **2D sampled sections** | 0 | 84 | +84 |
| **2D unverified stations** | 0 | 132 | +132 |
| **Вызовы OCCT boolean (`common`)** | **0** | **0** | 0 |
| **Время OCCT boolean** | **0.000 с** | **0.000 с** | 0.000 с |
| **Вызовы OCCT distance** | **0** | **0** | 0 |
| **Общее время выполнения** | **0.37 с** | **1.40 с** | -1.03 с (overhead 2D) |

---

## 4. Сформированные артефакты отчёта

В директории `reports/experiment_2d_screening/` сохранены полные воспроизводимые JSON-артефакты:
1. `baseline_z.json` — детальный отчёт о прогоне BASELINE со всеми кэшированными записями шагов.
2. `screened_z.json` — детальный отчёт о прогоне 2D_RISK с фиксацией многопозиционного 2D-скрининга.
3. `comparison_z.json` — каноническое сравнение по протоколу `compare_measurements()`.
4. `validation_trace_z.json` — трассировка геометрических параметров детали, оснастки и точки отказа.
5. `diagnostic_z_before.json` / `diagnostic_z_after.json` — зафиксированное состояние до и после инструментального аудита.
6. `diagnostic_summary.md` — данный документ.
