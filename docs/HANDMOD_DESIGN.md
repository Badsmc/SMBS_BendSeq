# HandMod-V.0.0.1 — Manual Bend Sequence Mode

## Цель

Режим **ручного** задания последовательности гибки.
Автоматический поиск (DFS / A* / Greedy) **не используется**.

Пользователь:
1. Создаёт / открывает деталь листового металла в FreeCAD.
2. Делает **Unfold** (SheetMetal).
3. На развёртке **вручную** выбирает линии гиба (в порядке, который считает правильным).
4. Линии попадают в окно списка.
5. Нажимает **«Рассчитать»**.
6. Аддон считает **только** заданную вручную последовательность:
   - валидирует порядок (физика, коллизии, оснастка),
   - считает backgauge (X, R, Z),
   - выводит параметры шагов,
   - рисует аннотации на развёртке,
   - (опционально) симулирует.

## Что исключается из автоматического режима

Файлы / модули, которые **ищут** последовательность (не нужны в HandMod):

- `smbs_core/Solvers/astar_backward.py`
- `smbs_core/Solvers/dfs_backward.py`
- `smbs_core/Solvers/greedy_unfold.py`
- поиск внутри `smbs_core/coordinator/run_pipeline.py` (часть SearchWorker)
- автоматический вызов `search_algorithm` в `run_pipeline_on_fcstd`

Остальное ядро (геометрия, FoldState, Physical Oracle, verifier, tooling, annotations, simulation) **переиспользуется**.

## Новый UX

### Команда
`SMBS_ManualSequence` — «Ручная последовательность»

### Диалог / Task Panel
- Список выбранных линий гиба (порядок = порядок гибки).
- Кнопки: ↑ ↓, удалить, очистить.
- Кнопка **«Рассчитать»**.
- Статус валидации + таблица параметров (backgauge, угол, инструмент).

### Поток данных
```
Selection (edges on Unfold)
    → ManualBendPicker (собирает ordered bend_ids / edge refs)
    → ManualSequenceWorker (валидация + расчёт параметров без search)
    → RunArtifacts (тот же формат 04/07/09, но 07 = fixed sequence)
    → Annotations + Verify + Simulate (как в AutoMod)
```

## Статус реализации

- [x] Ветка `HandMod-V.0.0.1` создана от AutoMod
- [ ] Команда + UI выбора линий
- [ ] ManualSequenceWorker (без поиска)
- [ ] Интеграция с существующим verifier / annotations
- [ ] Убрать/заглушить auto-search entry points в workbench

## Версия

HandMod-V.0.0.1 — первый срез ручного режима.
