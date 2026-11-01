# SMBS_BendSeq — PRODUCT & ENGINEERING MASTER SPECIFICATION
Version: 1.0 (design baseline) | 2026-11-01 | Language: Russian
Status: DESIGN PROPOSAL, NOT AN IMPLEMENTATION CLAIM
Repository: Badsmc/SMBS_BendSeq | Working branch: development/working-addon

> This document is the single source of truth for the *target behaviour*, implementation inventory, acceptance criteria and change control. It must not be confused with proof that a feature works. A status changes only when linked to code, automated test output and, where relevant, a FreeCAD physical test.

## 0. Цель, границы и неоспоримые правила

**Цель:** из сохранённой FreeCAD FCStd-модели листовой детали получить воспроизводимую последовательность гибов с достоверными знаковыми углами, положениями детали и инструмента, пошаговой 3D-симуляцией, явными результатами проверок и понятной диагностикой отказа. Ручной режим и автоматический режим используют **одну** геометрическую модель, один механизм кинематики и один физический валидатор.

**Не обещаем:** автоматически изготовить любую деталь, подтвердить безопасность без геометрии реального станка/оснастки, заменить проверку оператора и технолога, либо считать картинку доказательством технологичности.

Нормативные требования:
- MUST: никаких заглушек, «успешных» фиктивных расчётов, silent fallback на приближённую геометрию, молчаливого пропуска столкновений, недоказанных заявлений PRODUCTION_READY.
- MUST: один владелец каждого контракта и вычисления; запрещено параллельно поддерживать альтернативные пути fold/unfold, которые могут расходиться.
- MUST: детерминированность при одинаковых исходных данных, версиях ядра, конфигурации и бюджете; фиксировать версии, tolerances и seed (если используется).
- MUST: различать VALID, INVALID, UNKNOWN, RESOURCE_EXHAUSTED и ERROR; UNKNOWN не равен VALID или INVALID.
- MUST: реальные формы пуансона, матрицы, станины и упоров для окончательного допуска; отсутствие модели → не подтверждённый результат.
- MUST: каждый переход содержит идентификатор исходного гиба, знаковый угол, ось, неподвижную сторону, поворачиваемое поддерево и применённую матрицу преобразования.
- MUST: отдельная проверка прямого исполнения найденного обратным поиском плана.
- MUST: сохранение входных артефактов и диагностики любого отказа, включая исключения и время стадий.
- MUST: FreeCAD UI не выполняет собственную физическую математику, а лишь инициирует pipeline и отображает результаты.
- SHOULD: сначала правильность, затем производительность, затем оптимизация наладки.
- MUST NOT: автоматически сливать экспериментальную ветку в main.

## 1. Архитектурное решение

### 1.1. Разбить деталь на панели и гибы — ДА

Рекомендуемая модель — **PanelGraph + BendGraph + кинематические преобразования жёстких панелей**. Это не сеточное дробление каждого полигона: плоские участки листа становятся панелями, а области гибов — связями между ними.

- Panel: стабильный ID, топологическая область/face refs, локальная система координат, толщина, границы, B-Rep представление, parent/children для текущего кинематического дерева.
- Bend: стабильный ID из источника, геометрическая ось (точка + нормализованное направление), signed_angle в радианах, радиус, направление, стороны панелей, neutral-axis/allowance metadata, provenance (SheetMetal).
- State: набор применённых гибов, **матрицы панелей**, положение детали относительно станка, выбранная оснастка, геометрические/контрактные версии. Исходный B-Rep не мутировать.
- Один физический гиб может состоять из нескольких коллинеарных сегментов; группировать только при доказанной совместимости оси, угла, направления, радиуса и общей операции. Соседние +45° и -45° НЕ объединять.
- ID гибов сохраняются от извлечения до отчёта; недопустимы потери шагов или удвоение 10→14 без явной диагностики.

### 1.2. Геометрический движок: НЕ заменять OCC целиком сейчас

**Решение:** FreeCAD SheetMetal — источник данных о развёртке/гибах; OCCT/FreeCAD B-Rep — точная исходная и финальная геометрия. Внутри поиска использовать быстрый кинематический слой панелей и **многоступенчатую** проверку столкновений:
1. Дешёвые инварианты: граф, достижимость, зависимости, разрешённые знаковые углы, габариты станка.
2. Broad phase: AABB/BBox после преобразований, sweep/OBB/BVH при необходимости; отсутствие перекрытия с запасом → можно пропустить дорогую пару.
3. Narrow phase: точный OCCT контакт/пересечение для потенциально конфликтующих пар, с общей политикой tolerances.
4. Полная проверка финального кандидата с реальной геометрией и траекторией (не только конечными положениями).

**Нельзя** объявлять отсутствие столкновений по одному BBox, если пары/траектории не покрыты. **Нельзя** считать один лишь PanelGraph точной физической моделью радиуса гиба: область изгиба/толщина должны учитываться в narrow phase и итоговой проверке.

Альтернативы (не решение по умолчанию):
- pythonOCC/OCCT: та же геометрическая база; смена обёртки сама по себе не ускорит Boolean.
- FreeCAD native Part/OCC: полезен для прямой интеграции, но также OCC.
- trimesh/PyBullet/FCL: возможный **только вспомогательный broad-phase/приближённый collision backend** после проверки погрешностей, не источник окончательной CAD-точности.
- Open3D/mesh-only: не использовать как единственный источник истины из-за аппроксимации и сложности контактных допусков.
- Смена движка допустима лишь по benchmark на реальных L/U/C и по точности, памяти, воспроизводимости и времени; отдельный RFC и тесты.

### 1.3. Канонические компоненты и владение ответственностью

| Слой | Единственная ответственность | Запрет |
|---|---|---|
| SheetMetal adapter | читать исходную геометрию, unfold, thickness, signed bends, K-factor, provenance | самостоятельно планировать |
| FeatureExtractor/CanonicalPart | нормализовать ID, оси, знаки, панельные связи | молчаливо исправлять неоднозначности |
| KinematicEngine | единственная функция bend/unbend(state, bend, direction), матрицы и B-Rep reconstruction | создавать «preview» вместо истинного state |
| CollisionEngine | broad+narrow phase, контакт/допуск, swept path | принимать UNKNOWN за VALID |
| Machine/Tooling model | координаты, пуансон, матрица, пресс, упоры, пределы | синтетическую оснастку выдавать за REAL |
| PhysicalValidator | единственный oracle transition → VALID/INVALID/UNKNOWN + причины | искать последовательность |
| Search | DFS Backward, A*, Greedy по общему интерфейсу oracle | собственную collision logic |
| IndependentVerifier | повторить весь forward-план, сверить геометрию, оснастку и траекторию | доверять только флагу solver.success |
| Coordinator | запуск стадий, контрактные JSON, бюджеты, provenance, статусы | собственную геометрию |
| FreeCAD UI | действия, прогресс, выбор шага, отрисовка, лог | менять правила валидации |

Существующие классы/файлы сохранять, пока аудит не докажет необходимость изменения; новые классы вводить только с явным назначением и удалением/миграцией старого пути.

## 2. Полный пользовательский сценарий

### 2.1. Открытие Workbench

1. Пользователь открывает FCStd и активирует SMBS Workbench.
2. Команда «Диагностика» проверяет версии FreeCAD/SheetMetal, PythonOCC/OCCT, доступность backend, права записи и конфигурацию.
3. При отсутствии документа кнопки расчёта, ручного выбора и симуляции disabled; диагностика, настройки и справка enabled.
4. При изменённой/несохранённой модели расчёт disabled либо предлагает сохранить и пересчитать; нельзя тихо считать старый FCStd.
5. Смена документа/геометрии делает старый результат **STALE** и запрещает выдавать его за актуальный.

### 2.2. Панель SMBS (Task Panel / dock)

Обязательные зоны:
- **Источник:** документ, Body, версия, толщина, K-factor, число панелей/гибов, геометрический hash, статус распознавания.
- **Станок:** пресc/оси/габариты, пуансон, матрица, библиотека, задние упоры, полнота геометрии, режим EXPLORATORY/PRODUCTION.
- **Поиск:** Auto/Manual, DFS Backward/A*/Greedy, budget (секунды, узлы, geometry operations), «Старт», «Остановить», «Продолжить» только если checkpoint реально реализован.
- **Гибы:** таблица №, bend_id, угол signed°, радиус, длина, направление, сторона, статус, причина отказа; сортировка без потери ID.
- **Шаги:** №, bend_id, tool, ориентация/переворот, backgauge, collision, verifier, время.
- **Просмотр:** исходная согнутая деталь / развёртка / состояние шага / станок / коллизии, с переключателями и легендой.
- **Диагностика:** текущая стадия, счётчики кандидатов/отказов/boolean calls/cache hits, elapsed, ссылки на артефакты.

### 2.3. Кнопки и условия активации

| Команда | Enabled когда | Действие | Выход |
|---|---|---|---|
| Открыть/выбрать деталь | FreeCAD запущен | выбор активного Body/FCStd | источник |
| Анализ детали | документ сохранён, backend готов | SheetMetal → canonical graph → baseline | список гибов/ошибки |
| Оснастка | всегда при открытом UI | выбор REAL инструментов/пресса/упоров, проверка полноты | tooling status |
| Автоматический расчёт | baseline VALID, bends>0, нет активного run | запускает Coordinator + solver | run + кандидат/отказ |
| Ручной режим | baseline VALID, bends>0 | пользователь выбирает допустимый следующий гиб | интерактивное состояние |
| Проверить шаг | есть текущий candidate/state | oracle для одного перехода | VALID/INVALID/UNKNOWN + причины |
| Проверить последовательность | есть полный кандидат | независимый forward verifier | verification report |
| Симуляция | есть геометрически воспроизводимые шаги | проигрывание сохранённых state | 3D + timeline |
| Назад/Вперёд | есть соседний state | переключить шаг, не пересчитывать без необходимости | состояние |
| Play/Pause | есть ≥2 шага | анимация шагов | timeline |
| Показать коллизии | есть диагностируемая пара | выделить объекты, зазор/контакт | 3D overlay |
| Экспорт | есть run, прошедший schema validation | JSON/CSV/отчёт, с честными статусами | файлы |
| Остановить | run выполняется | cooperative cancellation | CANCELLED + partial artifacts |
| Диагностика | всегда | self-check + открыть лог | диагностический отчёт |
| Настройки | всегда | допуски, budget, политика, пути | валидированные настройки |
| Очистить результат | есть run | удалить только UI state, не исходный документ | clean view |

Нельзя активировать «экспорт для производства», если verification != VERIFIED или production_eligible != true. Для EXPLORATORY допустим отдельный экспорт с явным watermark «НЕ ПОДТВЕРЖДЕНО ДЛЯ ПРОИЗВОДСТВА».

**Существующие команды на 2026-11-01:** SMBS_RunActive, SMBS_OpenRun, SMBS_Calculate, SMBS_Simulate, SMBS_Tooling, SMBS_Verify, SMBS_Info, SMBS_Diagnostics, SMBS_Preferences в freecad_smbs/InitGui.py. Таблица выше — **целевой UX**, а не утверждение, что все кнопки реализованы. Новые команды добавлять только после удаления/переназначения дублирующих старых.

### 2.4. Auto: точная последовательность событий

A0 Validate document saved/current/recomputed → A1 Environment + tooling validation → A2 SheetMetal unfold + feature provenance → A3 canonical panel/bend graph → A4 strict baseline round-trip: folded → flat → folded; сравнить BBox, объём, расстояния/форму и углы по допускам → A5 создать immutable initial FoldState → A6 подготовить oracle/collision cache → A7 DFS Backward поиск допустимых обратных переходов → A8 перевернуть найденный порядок → A9 independent forward replay с полной оснасткой/траекторией → A10 сохранить JSON/геометрию/отчёт → A11 показать таблицу и 3D шаги.

На любой стадии ERROR/UNKNOWN/INVALID: остановиться с причиной и стадией; не переходить в «успех». Baseline UNKNOWN не должен давать PRODUCTION результат.

### 2.5. Manual: точная последовательность событий

M0 тот же A0–A5 → M1 показать доступные bend_id на текущей детали → M2 оператор выбирает линию/строку → M3 проверить допустимость (graph + oracle) → M4 VALID: применить тот же KinematicEngine и добавить шаг; INVALID: оставить state неизменным, выделить причину; UNKNOWN: показать неопределённость и разрешить только явно помеченное exploratory продолжение → M5 Undo/Redo через сохранённые immutable states → M6 после всех гибов вызвать тот же independent forward verifier → M7 результаты и экспорт по тем же правилам.

При выборе в 3D обязателен точный mapping face/edge → stable bend_id. Несколько сегментов одного гиба выделяются совместно только при доказанном объединении.

## 3. Алгоритмы и условия их применения

| Этап | Алгоритм | Когда | Результат |
|---|---|---|---|
| Unfold | SheetMetal WB canonical unfold | один раз на модель/изменение | flat geometry + bend metadata |
| Feature recognition | topology + provenance, signed angles | после unfold | PanelGraph/BendGraph |
| Round-trip | reverse/forward transforms + geometric metrics | до поиска | baseline VALID/INVALID/UNKNOWN |
| Pruning | dependency graph, invariant, reachability | до дорогой проверки перехода | исключённые кандидаты с reason |
| Collision broad phase | transformed BBox/OBB/BVH | для каждой релевантной пары | потенциальные пересечения |
| Collision narrow phase | OCCT exact contact/Boolean + tolerances | только для потенциальных пересечений | VALID/INVALID/UNKNOWN |
| Search default | DFS Backward с backtracking | обычный Auto | полный кандидат или честная причина |
| Search optional | A* | если корректная эвристика/контракты подтверждены | кандидат с optimality claim по доказательству |
| Search optional | Greedy | быстрый предварительный результат | без обещания полноты |
| Verification | независимый forward replay | каждый полный кандидат | VERIFIED/FAILED/UNKNOWN |
| Optimization | минимизация tool changes, flips, setup, time | только среди verified candidates | ранжирование |

**Текущая реализация:** в smbs_core/workers/solver/worker.py уже есть dispatch по "dfs", "astar", "greedy"; GUI сейчас по умолчанию задаёт "astar". Изменение дефолта на DFS допускается **после** тестов совместимости и performance; это требование к будущему релизу, не уже выполненная работа.

Ресурсные лимиты: wall-clock, explored nodes, geometry ops, RAM (если измеряется), cancellation; TIMEOUT и RESOURCE_EXHAUSTED не подменять NO_SOLUTION. Найденный кандидат не становится VERIFIED только от успеха solver.

Ключ кэша: canonical geometry hash + state (bend set и signed transforms) + tooling/machine geometry hashes + tolerance policy + backend/version. Не кэшировать VALID при смене конфигурации. Cache hit/miss в логе.

## 4. Геометрическая точность и физика

### 4.1. Знаки и направления
- Хранить угол как signed, единица явно radians в API и degrees в UI.
- Ось нормализована, ориентация согласована с выбранной неподвижной панелью.
- Преобразование bend и unbend взаимно обратные в заданных tolerances.
- Тест: соседние +45°/-45° на одной геометрической прямой дают противоположные вращения; не проходят сквозь базовую панель.
- Тест: 30°, 45°, 90°, 135° и отрицательные аналоги; радиус, толщина, K-factor; минимум одна неоднозначная линия.

### 4.2. Столкновения
Проверять: деталь↔деталь (с исключениями для разрешённого контакта соседних поверхностей), деталь↔пуансон, деталь↔матрица, деталь↔пресс/станина, деталь↔упоры; движение по траектории, а не только статические позиции. Явно задавать permitted contact, minimum clearance, contact tolerance, geometry tolerance, unknown handling. Если инструмент/упоры отсутствуют — нельзя заявлять полную физическую проверку.

### 4.3. Разделение представлений
- Authoritative B-Rep: источник и точная финальная проверка.
- Kinematic panels: дешёвые преобразования внутри поиска.
- Mesh: только отрисовка/ускорение broad-phase, не CAD-истина.
- View states: генерируются из сохранённого проверенного state; нельзя выдавать flat pattern с номерами за сложенную последовательность.

## 5. Вывод в FreeCAD: где и что видно

**Главное окно 3D:** реальная геометрия выбранного шага, полупрозрачные пуансон/матрица/пресс/упоры, выделение активного bend_id и движущейся панели. Отдельная вкладка/переключатель показывает flat pattern, но это не симуляция гибки.

**Правая Task Panel:** таблица гибов, таблица шагов, выбор solver и policy, кнопки, прогресс, статус. Клик по строке → точное состояние шага и выделение оси. В ручном режиме клик по линии на модели → подсветка кандидата, затем «Применить» после проверки.

**Нижняя Report/Console:** лог с run_id, stage, bend_id, candidate_id, elapsed, collision pair, reason, backend; traceback без скрытия. Лог сохраняется в run directory.

**Цвета:** зелёный = VERIFIED (только с production_eligible), жёлтый = EXPLORATORY/UNKNOWN/PARTIAL, красный = INVALID/FAILED, серый = NOT_RUN/STALE; цвета не заменяют текст.

**Статусы UI:** NOT_READY → READY → ANALYZING → BASELINE_VALID → SEARCHING → CANDIDATE_FOUND → VERIFYING → VERIFIED или EXPLORATORY/FAILED/UNKNOWN; параллельные ERROR/TIMEOUT/CANCELLED/RESOURCE_EXHAUSTED; при изменении документа STALE.

**Таблица шагов:** step, bend_id, signed angle, tool IDs, orientation, backgauge target, collision status, verification status, duration, warnings. При ошибке — геометрическая пара и причина, а не просто «последовательность не найдена».

## 6. Контракты и артефакты

Единый versioned run directory (использовать существующий file_store/protocols, не плодить второй формат):
- manifest: run_id, input file/hash, software/backend versions, geometry hashes, machine/tooling config, tolerances, algorithm, budget.
- geometry_result: canonical part, bend mapping, provenance, flat/folded artifacts.
- baseline_result: metrics, tolerance thresholds, pass/fail/unknown.
- search_result: ordered stable bend IDs, candidate states, termination reason, search stats, status.
- verification_result: per-step checks, collision pairs, physical clearance, final reconstruction metrics, production_eligible.
- report: человекочитаемый итог + ссылки на доказательства.
- debug: structured events, timings, exception stack, reason codes.

Проверять schema/hash до использования. Несовпадение schema, stale input, несуществующий artifact → CONTRACT_ERROR; не переходить на тихий fallback.

## 7. Статусы зрелости и правила доказательства

DECLARED (только ТЗ) → IMPLEMENTED (код + contract) → TESTED (автотесты) → VERIFIED (живые эталонные FCStd) → QUALIFIED (регрессия + performance) → PRODUCTION_READY (приёмка на конкретной оснастке/прессе).

**Текущая инвентаризация (ограничена просмотренными файлами, НЕ полный аудит):**

| Область | Наблюдаемое свидетельство | Максимум доказанной зрелости | Блокер |
|---|---|---|---|
| GUI command registry | freecad_smbs/InitGui.py: 9 команд | IMPLEMENTED | runtime/UI acceptance не проверены |
| GUI Auto entry | commands/run_active.py → smbs_bridge | IMPLEMENTED | end-to-end FreeCAD не проверен |
| Coordinator pipeline | coordinator/run_pipeline.py: geometry/topology/baseline/solver/verifier/export | IMPLEMENTED | результаты интеграции не подтверждены |
| Solver dispatch | workers/solver/worker.py: dfs/astar/greedy | IMPLEMENTED | точность/полнота не подтверждены |
| PhysicalValidator | импорт и использование в solver worker | IMPLEMENTED | real machine collision qualification отсутствует |
| Baseline | workers/baseline/worker.py | IMPLEMENTED | C/±45° round-trip не подтверждён |
| 3D simulation | команда simulate.py существует | IMPLEMENTED (command only) | реальное forward-state отображение не подтверждено |
| Manual workflow | в текущем InitGui.py нет отдельной Manual command | DECLARED для целевого UX | интеграция ручного режима не подтверждена |
| Performance | ранее сообщалось о ~2149 s и преобладании OCC booleans | NOT VERIFIED в текущей ветке | нужен reproducible profile |
| CI | в текущем root нет .github/workflows | NOT VERIFIED | настроить CI, проверить green на SHA |
| Production readiness | нет отчёта физической приёмки | NOT READY | реальные L/U/C + станок/оснастка |

Здесь статус IMPLEMENTED означает только наличие изученного кода, не работоспособность функции.

## 8. Эталонные тесты и критерии приёмки

- L: 1–2 гиба; корректные оси, знаки, исходная и восстановленная форма.
- U: 2–4 гиба; разные варианты порядка, доступ инструмента, упоры.
- C: сложная деталь с зависимыми панелями; никакого PREVIEW_ONLY как подтверждённой симуляции.
- Opposite 45: соседние +45/-45; без объединения и самопересечений.
- Multi-segment: несколько сегментов одной операции; стабильные ID, без 10→14.
- Invalid: заведомо невозможный гиб; явный INVALID + конкретная пара столкновения.
- Unknown: отсутствует точная геометрия оснастки; нет production_eligible.
- Timeout: лимит прервал поиск; статус не NO_SOLUTION; найденный кандидат проходит verifier.
- Stale: изменён документ после расчёта; запрет старого результата.
- Manual/Auto parity: одинаковый порядок даёт одинаковые состояния/проверки.
- Determinism: повтор на одинаковых входных данных даёт одинаковый список шагов и reason codes.
- Performance: фиксировать baseline, number of OCC booleans, median/p95 elapsed, memory; оптимизацию принимать только без потери корректности.

**Definition of Done для каждого пункта:** ссылка на commit + test ID + лог CI + FCStd model/hash + screenshot/video FreeCAD (где применимо) + run artifacts + acceptance verdict. Без этого статус не выше IMPLEMENTED.

## 9. План работ и порядок коммитов

**P0 — Inventory & contracts:** составить карту вызовов и файлов, найти дубли/мертвые пути; закрепить canonical types, status/reason codes, geometry provenance. Никаких массовых переносов до карты зависимостей.

**P1 — Geometry correctness:** единый bend/unbend, stable IDs, signed angles, panel graph, strict baseline. Приёмка L/U/C и ±45°.

**P2 — Oracle & tooling:** один PhysicalValidator, exact collision/contact, swept paths, реальные модели punch/die/press/backgauge, fail-closed.

**P3 — Search correctness:** DFS Backward default после тестов, корректные TIMEOUT/NO_SOLUTION/UNKNOWN, deterministic pruning/cache, обратный план → forward verifier.

**P4 — FreeCAD UX:** Task Panel, Auto/Manual parity, 3D state viewer, корректная таблица, diagnostics, cancellation.

**P5 — Performance:** профилировать OCC; broad-phase, incremental transforms, cache, parallelism только после доказанной корректности и потокобезопасности.

**P6 — Release qualification:** CI, эталонные FCStd, regression, ручная проверка на реальном прессе, документация, безопасная миграция в main.

Каждый этап = отдельные малые коммиты, tests + docs вместе с кодом. Не создавать пустые модули «на будущее».

## 10. Правила удаления и предотвращения мёртвого кода

1. Перед добавлением файла найти существующего владельца функции (по вызовам/imports, не по имени).
2. Новая реализация допустима только при чётко указанной замене; в том же PR убрать/перенаправить старые вызовы и тесты.
3. Не оставлять временные adapters, duplicate models, deprecated solver aliases, mock production paths, unreachable branches, unused exports.
4. Mock разрешён **только в tests**, с явным названием и запретом использования в production.
5. Статический поиск импортов/ссылок + тесты + FreeCAD smoke перед удалением. Не удалять файл только по отсутствию прямого импорта (динамическая регистрация FreeCAD).
6. CI: compile/import, unit, contract, geometry, integration, schema, dead-code/duplicate review; heavy OCC benchmarks отдельно.
7. Обязательный code review checklist: один владелец логики? нет fallback? статусы честны? есть evidence? сохранена backward compatibility? изменена документация?
8. Запрещены «except Exception: pass» на критических стадиях; UI может ловить исключение лишь с полным логом и ERROR status.
9. Не копировать закрытые коммерческие реализации; изучать только публично описанные методы и лицензированные библиотеки.

## 11. Реестр решений и открытых вопросов

**Принято:** SheetMetal source-of-truth; OCCT authoritative; PanelGraph+KinematicEngine; broad+narrow collision; common oracle; reverse DFS candidate; forward independent verifier; no fake production status; один путь для Auto/Manual.

**Требует проверки до внедрения:**
- как получить устойчивый signed bend ID из SheetMetal на всех тестовых FCStd;
- какая часть B-Rep реально нужна для narrow phase на каждом шаге;
- какие допуски применимы к конкретным радиусам, толщине и пресcу;
- нужен ли отдельный FCL broad-phase после профилирования;
- какие модели и форматы доступны для пуансона, матрицы, станины и упоров;
- можно ли безопасно повторно использовать геометрию и результаты OCCT между состояниями;
- какая структура сохранённых step states обеспечивает быстрый viewer без ложной геометрии;
- какие требования к FreeCAD 1.1.3/macOS, Python и pythonOCC в CI.

## 12. Как поддерживать этот документ

- При каждом PR обновлять строку инвентаризации и соответствующий acceptance evidence; не повышать статус по субъективной оценке.
- Все изменения архитектуры — короткий ADR с причиной, отвергнутыми альтернативами, влиянием на данные/тесты и миграцией.
- Если обнаружен конфликт между этим документом и фактическим кодом: документ определяет **цель**, код фиксирует **текущее состояние**, разница оформляется задачей, а не скрывается.
- Техническая задача считается закрытой только после выполнения её Definition of Done.
