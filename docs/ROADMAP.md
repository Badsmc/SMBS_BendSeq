# Roadmap: SMBS/BendSeq FreeCAD Integration

## 1. Executive Summary

### Что можно начинать сейчас (без согласований и уточнений)
- **Требование 1** — цифры логического порядка гибов на развёртке в FreeCAD.
- **FreeCAD workbench scaffolding** (как часть требования 1) — переиспользуется требованиями 2, 3, 3.1, 4.
- Read-only research spike по FreeCAD Qt API для аннотаций (без коммитов кода).

### Что требует уточнений у пользователя (blocker)
- **Q1** (требование 3): как обозначать роль объекта в FCStd (пуансон/матрица/станина/упор).
- **Q2** (требование 4.1): точная математика удержания пуансона лапками станины.
- **Q3** (требование 4): что значит «оптимальный» набор инструмента (целевая функция).
- **Q13** (требование 5): что входит в «дальнейшую визуализацию».

### Что требует согласования инварианта #7
- `contracts/schemas/tooling_manifest.schema.json` — расширение под библиотеку (3), сэты (3.1), лапки (4.1).
- `contracts/schemas/tooling_selection.schema.json` — новый, результат подбора (4).
- Возможно `tooling_set.schema.json` / `tooling_station.schema.json` — уточнить упаковку в рамках #7.

### Что зависит от KL-8 / KL-12
- **Требование 2** — требует KL-12 fix (честная per-panel кинематика для анимации).
- **Требование 4** — выигрывает от KL-8 fix (collision-aware selection); базовый геометрический подбор независим.
- Требования 1, 3, 3.1, 4.1 — от KL-8/KL-12 не блокируются.

### Ключевые решения и рекомендованный порядок
1. Требование 1 стартует немедленно — не трогает контракты и не зависит от KL.
2. Библиотека инструмента (3) — фундамент для 3.1, 4, 4.1, 2; стартует после #7 и Q1.
3. Constraint checker лапок (4.1) идёт **до** алгоритма подбора (4).
4. Анимация (2) — после библиотеки (3) и KL-12 fix.
5. Требование 5 остаётся открытым блоком до ответа Q13.

---

## 2. Фазы

### Phase A: Foundation + Requirement 1 — цифры на развёртке
- **Требования:** 1
- **Обоснование группировки:** workbench scaffolding нужен всем последующим фазам; требование 1 не трогает схемы и не зависит от KL.
- **Оценка:** 3–4 сессии, 10–20 коммитов.
- **Требуемые уточнения:** Q4, Q5 (важные, не blocker — можно стартовать с дефолтами).
- **Требуемые согласования:** нет.
- **Риски:** FreeCAD Qt API для аннотаций; ручное GUI-тестирование; маппинг `bend_id` → визуальная линия.

### Phase B: Tooling library (Requirement 3)
- **Требования:** 3
- **Обоснование группировки:** фундамент для 3.1, 4, 4.1, 2.
- **Оценка:** 4–6 сессий, 14–28 коммитов.
- **Требуемые уточнения:** Q1 (blocker), Q8 (важное).
- **Требуемые согласования:** `tooling_manifest.schema.json` (extend).
- **Риски:** эвристика определения ролей объектов в FCStd; API импорта внешних FCStd; персистентность.

### Phase C: Tooling sets (Requirement 3.1)
- **Требования:** 3.1
- **Обоснование группировки:** логическое расширение библиотеки; можно параллельно с Phase D.
- **Оценка:** 3–4 сессии, 10–18 коммитов.
- **Требуемые уточнения:** Q9, Q10.
- **Требуемые согласования:** `tooling_manifest.schema.json` (extend) или новый `tooling_set.schema.json` — уточнить в #7.
- **Риски:** семантика неполного сэта; момент допустимой замены пуансона; UI-сложность.

### Phase D: Clamp constraints (Requirement 4.1)
- **Требования:** 4.1
- **Обоснование группировки:** constraint checker — предпосылка для корректного подбора (4); меньше по объёму, чем C.
- **Оценка:** 2–3 сессии, 7–14 коммитов.
- **Требуемые уточнения:** Q2 (blocker), Q12 (важное).
- **Требуемые согласования:** `tooling_manifest.schema.json` (extend) или новый `tooling_station.schema.json`.
- **Риски:** неоднозначная математика удержания; конфиг под конкретный станок; геометрическая аппроксимация реальных лапок.

### Phase E: Optimal tooling selection (Requirement 4)
- **Требования:** 4
- **Обоснование группировки:** использует библиотеку (B), сэты (C) и checker (D).
- **Оценка:** 4–6 сессий, 14–28 коммитов.
- **Требуемые уточнения:** Q3 (blocker), Q11 (важное).
- **Требуемые согласования:** новый `tooling_selection.schema.json`.
- **Риски:** неопределённая целевая функция; сложность assignment; интеграция с search (Stage 2); ручная валидация на реальных деталях.

### Phase F: FreeCAD animation (Requirement 2)
- **Требования:** 2
- **Обоснование группировки:** требует библиотеки (B) для визуала оснастки и KL-12 fix для честной кинематики.
- **Оценка:** 4–6 сессий, 14–28 коммитов.
- **Требуемые уточнения:** Q6, Q7.
- **Требуемые согласования:** нет новых (использует `tooling_manifest` из Phase B); зависит от #7 фазы B.
- **Риски:** KL-12 fix меняет семантику `rotate_subtree` — код анимации должен соответствовать; QTimer и потокобезопасность GUI; позиционирование оснастки относительно линии гиба.

### Phase G: Open block (Requirement 5)
- **Требования:** 5
- **Обоснование группировки:** открытый блок, планирование невозможно до Q13.
- **Оценка:** TBD.
- **Требуемые уточнения:** Q13.
- **Требуемые согласования:** TBD.
- **Риски:** TBD.

---

## 3. Детально по требованиям

### Требование 1: Цифры на развёртке

- **Что уже есть:**
  - `smbs_core/viewer/run_loader.py` — загрузка `04/07/09.json` + `geometry.brep`.
  - `smbs_core/viewer/graph_builder.py` — `BendGraph` + `PanelGraph` из `04`.
  - `smbs_core/viewer/player.py` — читает `production_eligible` из `09`.
  - `07_search_result.json` — `candidate_sequence[i].bend_id` даёт логический порядок.
  - `04_canonical_part.json` — `bend_line_flat_pattern.{start_mm,end_mm}`, `axis_point`, `axis_direction`.
  - CLI pipeline (`smbs-cli part.FCStd`) — полный прогон в `runs/run_XXX/`.
- **Что добавить:**
  - FreeCAD workbench: `smbs_workbench/` (или в существующей структуре) — toolbar + команда «Рассчитать».
  - Команда: запуск пайплайна (или чтение существующего run) + загрузка `04/07/09`.
  - Маппинг `bend_id` → геометрическая линия развёртки по `bend_line_flat_pattern`.
  - Рендеринг номеров 1..N в 3D-виде FreeCAD (Draft/Annotation) или в TechDraw — по ответу Q4.
  - Honest marking: при `production_eligible=false` или отсутствии `09` — бейдж `UNVERIFIED` (по ответу Q5, дефолт — показывать с бейджем).
- **Зависимости:** нет.
- **Оценка:** 3–4 сессии, 10–20 коммитов.
- **Риски:** FreeCAD Qt API; ручное GUI-тестирование; корректность маппинга `bend_line_flat_pattern` на отображаемую геометрию.
- **Definition of Done:**
  - Пользователь открывает `part.FCStd` с загруженным SMBS workbench.
  - Нажимает «Рассчитать» → цифры 1..N появляются у линий гиба на развёртке.
  - Порядок соответствует `candidate_sequence` из `07`.
  - При `UNVERIFIED` — явный бейдж; без `09` — `UNVERIFIED`.
  - Нет крэша при отсутствующих файлах.
- **Требуемые уточнения у пользователя:**
  - Q4: Где отображать цифры — 3D-развёртка или 2D TechDraw? → определяет API и UX. → Ожидается: 3D-аннотации на развёрнутой детали, либо TechDraw-вид.
  - Q5: Что показывать при `production_eligible=false`? → инвариант #1. → Ожидается: показывать с бейджем `UNVERIFIED`, либо не показывать — решение пользователя; дефолт — с бейджем.
- **Требуемые согласования (инвариант #7):** нет. Нумерация вычисляется на лету из `04`+`07`; новый артефакт не создаётся.
- **Связь с KL-8/KL-12:** не блокируется. KL-8 влияет на `production_eligible`, но не на нумерацию; KL-12 не затрагивает маппинг `bend_id`.

---

### Требование 2: Анимация гибки в FreeCAD

- **Что уже есть:**
  - `smbs_core/viewer/replayer.py` — пошаговый backward unfold (через `backend.rotate_subtree`).
  - `smbs_core/viewer/player.py` — таймлайн (reset/next/prev).
  - `smbs_core/viewer/render.py` — HLR + PIL → PNG.
  - `smbs_core/viewer/cli.py` — `view <run_dir>`.
- **Что добавить:**
  - FreeCAD-анимация: движение объектов по таймлайну (QTimer), синхронизация с `candidate_sequence`.
  - Визуализация оснастки: пуансон, матрица, станина, упор — из библиотеки (Phase B).
  - Timeline UI в FreeCAD.
  - Интеграция с KL-12 fix — per-panel кинематика.
- **Зависимости:** Phase B (библиотека оснастки), KL-12 fix.
- **Оценка:** 4–6 сессий, 14–28 коммитов.
- **Риски:** KL-12 fix меняет семантику `rotate_subtree`; потокобезопасность GUI; позиционирование оснастки относительно линии гиба; производительность на сложных деталях.
- **Definition of Done:**
  - Пользователь загружает run с оснасткой из библиотеки.
  - Анимация проигрывает шаги гибки; пуансон, матрица, станина, упор видимы и движутся согласованно.
  - Каждый шаг honest-marked; при `UNVERIFIED` — бейдж.
  - Нет `PREVIEW_ONLY` по причине KL-12 (KL-12 fix выполнен).
- **Требуемые уточнения у пользователя:**
  - Q6: Скорость/длительность анимации? → влияет на таймлайн. → Ожидается: например, 1 с на гиб, настраиваемо.
  - Q7: Должна ли оснастка двигаться (пуансон опускается, упор переставляется) или быть статичной? → сложность и реализм. → Ожидается: пуансон опускается, упор переставляется между гибами.
- **Требуемые согласования (инвариант #7):** нет новых; использует `tooling_manifest` из Phase B.
- **Связь с KL-8/KL-12:** **требует KL-12 fix** (per-panel кинематика). KL-8 влияет на `production_eligible`, но анимация играет с бейджем.

---

### Требование 3: Библиотека инструмента (FCStd)

- **Что уже есть:**
  - `contracts/schemas/tooling_manifest.schema.json` — простой контракт оснастки.
  - `place_part_on_tool_station` — существующая логика размещения.
- **Что добавить:**
  - Хранилище библиотеки: директория FCStd + индекс метаданных (по #7).
  - Модуль импорта FCStd: определение ролей (пуансон/матрица/станина/упор) — по Q1.
  - FreeCAD-команда добавления инструмента в библиотеку.
  - Персистентность: местоположение — по Q8.
- **Зависимости:** нет (но требует #7 и Q1 до кода).
- **Оценка:** 4–6 сессий, 14–28 коммитов.
- **Риски:** эвристика ролей объектов; API импорта внешних FCStd; права/локальность хранилища; версионирование геометрии.
- **Definition of Done:**
  - Пользователь добавляет FCStd в библиотеку через workbench.
  - Роли определены (авто + подтверждение) — по Q1.
  - Библиотека персистентна между сессиями.
  - Список инструментов с размерами.
  - Повторный импорт обновляет существующую запись.
- **Требуемые уточнения у пользователя:**
  - Q1: Как обозначать роль объекта в FCStd? → определяет импорт. → Ожидается: соглашение об именовании + подтверждение в UI, либо свойства объекта.
  - Q8: Где хранить библиотеку — проект / пользовательский конфиг / оба? → персистентность и шаринг. → Ожидается: пользовательский конфиг по умолчанию, проектный override.
- **Требуемые согласования (инвариант #7):** `tooling_manifest.schema.json` — extend: роли, геометрия (путь FCStd, имя объекта), размеры.
- **Связь с KL-8/KL-12:** не блокируется.

---

### Требование 3.1: Набор/сэт из библиотеки

- **Что уже есть:** библиотека из Phase B.
- **Что добавить:**
  - Понятие «сэт» — именованная коллекция инструментов.
  - UI выбора: конкретный сэт или вся библиотека.
  - Логика замены пуансона: флаг + предложенная точка замены (по Q10).
- **Зависимости:** Phase B.
- **Оценка:** 3–4 сессии, 10–18 коммитов.
- **Риски:** семантика неполного сэта; момент допустимой замены; UI-сложность.
- **Definition of Done:**
  - Пользователь создаёт именованный сэт из библиотеки.
  - Может выбрать сэт или «вся библиотека» для run.
  - Система флагует гибы, требующие замены пуансона.
- **Требуемые уточнения у пользователя:**
  - Q9: «Сэт» — сохраняемый артефакт или временный выбор? → хранение. → Ожидается: сохраняемый артефакт.
  - Q10: При необходимости замены пуансона — авто-планирование или только флаг? → scope. → Ожидается: флаг + предложенная точка, пользователь подтверждает.
- **Требуемые согласования (инвариант #7):** `tooling_manifest.schema.json` (extend) или новый `tooling_set.schema.json` — уточнить в #7.
- **Связь с KL-8/KL-12:** не блокируется.

---

### Требование 4: Расчёт набора инструмента

- **Что уже есть:**
  - Stage 2 search — `candidate_sequence`.
  - `tooling_manifest` (после Phase B).
- **Что добавить:**
  - Алгоритм подбора: вход — доступные пуансоны (тип, длина, кол-во) и гибы; выход — назначение по гибам (по Q3).
  - Модуль `smbs_core/tooling/selection.py` (предлагаемый путь).
  - Интеграция с search (post-process или внутри A*/DFS).
  - Учёт constraint checker из Phase D.
- **Зависимости:** Phase B, Phase D.
- **Оценка:** 4–6 сессий, 14–28 коммитов.
- **Риски:** неопределённая целевая функция; сложность assignment; интеграция с search; ручная валидация.
- **Definition of Done:**
  - Для последовательности гибов и доступных пуансонов система выдаёт назначение по гибам.
  - Назначение удовлетворяет constraint checker (4.1).
  - Отчёт о заменах.
  - Результат сохраняется в `tooling_selection`.
- **Требуемые уточнения у пользователя:**
  - Q3: Что значит «оптимальный» — минимум пуансонов / замен / стоимости? → целевая функция. → Ожидается: минимум замен при удовлетворении всех гибов.
  - Q11: Подбор длины — per-bend или per-sequence? → алгоритм. → Ожидается: per-bend с учётом соседних гибов.
- **Требуемые согласования (инвариант #7):** новый `tooling_selection.schema.json`.
- **Связь с KL-8/KL-12:** выигрывает от KL-8 fix (collision-aware selection); базовый геометрический подбор независим.

---

### Требование 4.1: Учёт лапок станины

- **Что уже есть:** ничего специфичного.
- **Что добавить:**
  - Модель лапок: длина, промежуток, количество.
  - Constraint checker: пуансон должен надёжно удерживаться (по Q2).
  - Интеграция в selection (Phase E).
- **Зависимости:** Phase B (модель станины/лапок в библиотеке).
- **Оценка:** 2–3 сессии, 7–14 коммитов.
- **Риски:** неоднозначная математика; конфиг под станок; геометрическая аппроксимация.
- **Definition of Done:**
  - Checker валидирует удержание пуансона лапками при заданной конфигурации.
  - Selection (4) использует checker.
  - Невалидное назначение отклоняется с понятной причиной.
- **Требуемые уточнения у пользователя:**
  - Q2: Точная математика удержания. Промежуток 50 мм, пуансон 10 мм — «не держится». Нужно перекрытие ≥ X мм с каждой стороны? Или полное нахождение в зоне лапок? → constraint. → Ожидается: рабочая длина пуансона полностью поддерживается зонами лапок (перекрытие ≥ X мм).
  - Q12: Могут ли лапки переставляться между гибами? → solver. → Ожидается: фиксированы на последовательность; перестановка = событие «замена».
- **Требуемые согласования (инвариант #7):** `tooling_manifest.schema.json` (extend) или новый `tooling_station.schema.json`.
- **Связь с KL-8/KL-12:** не блокируется.

---

### Требование 5: Дальнейшая визуализация (открытый блок)

- **Что уже есть:** TBD.
- **Что добавить:** TBD до Q13.
- **Зависимости:** TBD.
- **Оценка:** TBD.
- **Риски:** TBD.
- **Definition of Done:** TBD.
- **Требуемые уточнения у пользователя:**
  - Q13: Что входит в «дальнейшую визуализацию»? (коллизии, усилия гибки, экспорт в форматы станков, отчётность?) → без этого нельзя планировать. → Ожидается: список приоритетных функций.
- **Требуемые согласования (инвариант #7):** TBD.
- **Связь с KL-8/KL-12:** TBD.

---

## 4. Матрица зависимостей

| Требование | Зависит от требований | Зависит от KL | Можно параллельно с |
|---|---|---|---|
| 1 | — | — | 3 (разработка библиотеки) |
| 2 | 3 | KL-12 | — |
| 3 | — | — | 1 |
| 3.1 | 3 | — | 4.1 |
| 4 | 3, 4.1 | KL-8 (желательно) | — |
| 4.1 | 3 | — | 3.1, 1 |
| 5 | TBD | TBD | TBD |

---

## 5. Матрица согласований

| Требование | Требуемые уточнения (кол-во) | Требуемые согласования #7 (файлы) | Что можно начинать без них |
|---|---|---|---|
| 1 | 2 (важные, не blocker) | нет | ✅ можно начинать сейчас |
| 2 | 2 (важные) | нет новых (использует `tooling_manifest` из 3) | ❌ требует KL-12 + Phase B |
| 3 | 2 (1 blocker: Q1) | `tooling_manifest.schema.json` (extend) | ❌ сначала #7 + Q1 |
| 3.1 | 2 (важные) | `tooling_manifest.schema.json` (extend) или `tooling_set.schema.json` (new) | ❌ требует Phase B + #7 |
| 4 | 2 (1 blocker: Q3) | `tooling_selection.schema.json` (new) | ❌ требует Phase B, D + #7 |
| 4.1 | 2 (1 blocker: Q2) | `tooling_manifest.schema.json` (extend) или `tooling_station.schema.json` (new) | ❌ требует Phase B + #7 |
| 5 | 1 (blocker для 5: Q13) | TBD | ❌ открытый блок |

---

## 6. Открытые вопросы к пользователю

### Blocker (без ответа нельзя начинать код)

- **Q1** [требование 3]: Как обозначать роль объекта в FCStd (пуансон/матрица/станина/упор)? Почему blocker: импорт библиотеки и определение ролей невозможны без соглашения. Ожидается: соглашение об именовании + подтверждение в UI, либо свойства объекта.
- **Q2** [требование 4.1]: Точная математика удержания пуансона лапками. Почему blocker: constraint checker нельзя написать корректно. Ожидается: рабочая длина пуансона полностью поддерживается зонами лапок (перекрытие ≥ X мм).
- **Q3** [требование 4]: Что значит «оптимальный» набор? Почему blocker: целевая функция определяет алгоритм. Ожидается: минимум замен при удовлетворении всех гибов.
- **Q13** [требование 5]: Что входит в «дальнейшую визуализацию»? Почему blocker для 5: без scope нельзя планировать. Ожидается: список приоритетных функций.

### Важные (влияют на дизайн, но можно начать с допущения)

- **Q4** [требование 1]: Где отображать цифры — 3D-развёртка или TechDraw? Дефолт: 3D-аннотации.
- **Q5** [требование 1]: Что показывать при `production_eligible=false`? Дефолт: цифры с бейджем `UNVERIFIED` (соответствует инварианту #1).
- **Q6** [требование 2]: Скорость/длительность анимации? Дефолт: 1 с на гиб, настраиваемо.
- **Q7** [требование 2]: Должна ли оснастка двигаться? Дефолт: пуансон опускается, упор переставляется.
- **Q8** [требование 3]: Где хранить библиотеку? Дефолт: пользовательский конфиг + проектный override.
- **Q9** [требование 3.1]: «Сэт» — сохраняемый артефакт? Дефолт: да.
- **Q10** [требование 3.1]: Замена пуансона — авто или флаг? Дефолт: флаг + предложенная точка, пользователь подтверждает.
- **Q11** [требование 4]: Подбор длины — per-bend или per-sequence? Дефолт: per-bend с учётом соседних гибов.
- **Q12** [требование 4.1]: Могут ли лапки переставляться между гибами? Дефолт: фиксированы; перестановка = «замена».

### Nice-to-have (можно уточнить позже)

- Детали визуализации оснастки (цвета, подписи), детали UI библиотеки (поиск/фильтры), формат отчёта о заменах.

---

## 7. Суммарные оценки

- **До MVP (только требование 1):** 3–4 сессии, 10–20 коммитов.
- **До полного roadmap (1–4.1, 2; без 5):** 20–29 сессий, 69–136 коммитов.
- **До production-grade (с KL-12 fix и KL-8 fix):** +4–7 сессий → 24–36 сессий.

---

## 8. Рекомендованный порядок (если отличается от пользовательского)

> **Revised order (2026-10-20, Stage 8 close):**
> KL-12 v1 ✅ → KL-8 ✅ → **Req 6a (Search SLA, Lazy A*)** → Req 4 →
> Req 2 (after KL-12 v2 side edges) → Req 6b (only if 6a misses SLA).
> KL-12 v3 (planar self-collision) tracked under KL-8 SELF / Req 4 enforcement.
> Original §8 order (requirement 4 → animation → KL-12/KL-8 backlog) superseded.
> Rationale: KL-12 v1 unblocked KL-8, which surfaced 3 silent bugs (place_part
> greedy return, REAL→SIMPLIFIED downgrade, FeatureExtractor mock contract).
> Req 6a is behavior-preserving search hygiene — low risk, required for
> "guaranteed 2-4 min" user-facing promise.

**Пользовательский:** 1 → 2 → 3 → 3.1 → 4 → 4.1 → 5.

**Рекомендованный:** 1 → 3 → 4.1 → 3.1 → 4 → 2 → 5.

**Обоснование:**
- 1 — без зависимостей и согласований, даёт ценность немедленно.
- 3 — фундамент для 3.1, 4, 4.1, 2.
- 4.1 — constraint checker нужен для корректного 4; меньше по объёму, чем 3.1.
- 3.1 и 4.1 можно менять местами.
- 4 — после 3 и 4.1.
- 2 — после 3 и KL-12 fix; может стартовать параллельно с 3.1/4, если KL-12 fix готов.
- 5 — остаётся открытым до Q13.

---

## 9. Что можно начинать прямо сейчас без единого согласования

- **Требование 1** — целиком.
- **FreeCAD workbench scaffolding** (в рамках требования 1) — будет переиспользован требованиями 2, 3, 3.1, 4.
- **Research spike** по FreeCAD Qt API для аннотаций (read-only, без коммитов кода).

---

## 10. Интеграция в проект

- **Файл:** `docs/ROADMAP.md` — living document.
- **Связь с `docs/PROGRESS.md`:**
  - В `PROGRESS.md` после Stage 4 detail, перед Known Limitations, добавляется раздел `## Future Stages` со строкой:
    `See docs/ROADMAP.md for the full integration roadmap (FreeCAD UI, tooling library, optimal tooling selection). Requirements 1–5 with dependencies, estimates, and open questions.`
  - Краткое резюме: MVP (требование 1) можно начинать немедленно; требования 2–5 требуют #7 и/или KL-12.
- **Связь с `docs/ARCHITECTURE.md`:**
  - Расширение секции Stage 4 Viewer (Layer 5) или добавление `### Future extensions (Layer 6+)` со строкой:
    `See docs/ROADMAP.md for details. Layers 6+ cover FreeCAD GUI integration (bend annotations, tooling animation), tooling library (FCStd import), optimal tooling selection (with clamp constraints). Depend on KL-12 fix for honest per-panel kinematics.`
- **Правила обновления:**
  - Когда требование закрыто — строка перемещается из Roadmap в `PROGRESS.md` как новый Stage (или часть Stage). Roadmap уменьшается, PROGRESS растёт.
  - Roadmap не дублирует PROGRESS; он хранит только будущее и открытые вопросы.
- **Что НЕ входит в интеграцию:**
  - Roadmap не меняет `contracts/schemas/*` — любые изменения проходят через отдельные #7-согласования, перечисленные в матрице.
  - Roadmap не добавляет тесты — тесты добавляются в code-коммитах.
  - Roadmap не планирует KL-8/KL-12 fix — только фиксирует зависимость.

---

*Статус: черновик на review. После принятия — интегрируется аддитивно в `docs/` без изменения схем и тестов. Baseline 350/5F/0E/3xF не сдвигается.*

---

## 11. Coordinator addendum (post-review, decisions recorded)

This section records user answers and coordinator notes added after the initial roadmap draft. It does not change phases, dependencies, or estimates in sections 1–10; it resolves open questions and records deferred #7 decisions.

### Resolved user questions

- **Q4 (requirement 1)** — **RESOLVED: 3D annotations** on the unfolded part, not TechDraw. Rationale: matches user mental model ("на развёртке появятся цифры"), faster to implement, honest marking visible in 3D viewport.
- **Q7 (requirement 2)** — **RESOLVED: tooling moves** during animation — punch descends on bend, backgauge repositions between bends. Rationale: user expects "показ оснастки" as motion, not static props.

These answers override the "Важные, можно с допущением" classification of Q4/Q7 in Section 6. Q4/Q7 are now **closed** and should not be re-asked in future sessions.

### Resolved user questions (Q1 added 2026-10-18)

- **Q1 (requirement 3)** — **RESOLVED: fallback chain (option D)**:
  1. Read FCStd object property `SMBS_Role` (`punch`/`die`/`frame`/`backgauge`).
  2. If absent, read object name (`PUNCH`/`DIE`/`FRAME`/`BACKGAUGE`).
  3. If neither, prompt user with dropdown in import dialog.
  Rationale: property (explicit) overrides name (implicit); UI fallback prevents automation from blocking. Q1 is no longer a blocker for Phase B.

### Pending #7 decisions (deferred to their phases)

- **`tooling_selection.json` location** (Phase E) — run-dir artifact (`runs/run_XXX/tooling_selection.json`) vs separate library store (`library/selections/<name>.json`). Decision deferred to Phase E start.
- **`tooling_placement` for animation** (Phase F) — (a) compute on-the-fly from `tooling_manifest` + `candidate_sequence` + `bend_line_flat_pattern` (no new schema, no #7) vs (b) persist to `tooling_placement.json` per run (new #7). Recommendation: (a) on-the-fly for MVP. Decision deferred to Phase F start.
- **`tooling_set.schema.json` vs extending `tooling_manifest.schema.json`** (Phase C) — new file vs extend existing. Decision deferred to Phase C start.

### Coordinator note on estimates

Requirement 1's estimate in Section 2 (Phase A: 3–4 sessions, 10–20 commits) is a **risk-adjusted upper bound**. Coordinator's narrower MVP scope (3D annotations only, workbench scaffolding shared with later phases) estimates **~1.5 sessions / 3–5 commits**. Treat Phase A's number as a buffer for FreeCAD Qt API surprises, not as a floor.

### Coordinator decision log (2026-10-18)

- **KL-12 (per-panel kinematics)** — deferred to backlog. H2 approach (runtime face_indices enrichment) tested and rejected: `rotate_subtree` loses cylinder faces on multi-step unfold, invalidates indices after step 1, and `intersect_volume` on planar compounds = 0. Fix requires either bbox approximation (Variant A, ~1 session, honest approximation marking) or solid-preserving split+fuse (Variant B, ~2–3 sessions, full KL-12 closure). **KL-12 does not block requirements 3, 3.1, 4, 4.1** — only requirement 2 (animation). Roadmap §8 order honored: requirements 3 → 4.1 → 3.1 → 4 → 2 → 5.
- **KL-8 (real tooling collision)** — blocked by KL-12. Approach-stage die collision fixed in S.19a/S.19a2 (`place_part_on_tool_station` now uses die V-groove axis + mounting_plane normal, tries both axis signs). Next stage `SELF` returns UNKNOWN because `face_indices` is empty → step rejected under PRODUCTION → NO_SOLUTION. KL-8 fix resumes when KL-12 is closed.
- **Current focus:** **requirement 3 (Phase B — Tooling Library)**. Q1=D resolved (fallback chain: property → name → UI). Q8 pending user answer (library storage location). #7 agreement required for `tooling_manifest.schema.json` extension before Phase B code.

### Phase B agreement (2026-10-18)

**#7 — APPROVED with minimal scope (3 optional fields):**

```diff
--- a/smbs_core/contracts/schemas/tooling_manifest.schema.json
+++ b/smbs_core/contracts/schemas/tooling_manifest.schema.json
@@ -62,6 +62,9 @@  (payload.punch)
             "tooling_status": { "type": "string", "enum": ["REAL", "SIMPLIFIED", "MISSING"] },
             "model_path": { "type": ["string", "null"] },
             "model_hash": { "type": ["string", "null"] },
+            "target_object_name": { "type": ["string", "null"] },
+            "smbs_role": { "type": ["string", "null"], "enum": ["punch", "die", "frame", "backgauge", "ram", "bed", null] },
+            "smbs_role_source": { "type": ["string", "null"], "enum": ["property", "name", "sidecar", "inferred", null] },
             "segment_layout_mm": {
@@ -80,6 +83,9 @@  (payload.die)
             "tooling_status": { "type": "string", "enum": ["REAL", "SIMPLIFIED", "MISSING"] },
             "model_path": { "type": ["string", "null"] },
             "model_hash": { "type": ["string", "null"] },
+            "target_object_name": { "type": ["string", "null"] },
+            "smbs_role": { "type": ["string", "null"], "enum": ["punch", "die", "frame", "backgauge", "ram", "bed", null] },
+            "smbs_role_source": { "type": ["string", "null"], "enum": ["property", "name", "sidecar", "inferred", null] },
             "segment_layout_mm": {
```

**Rationale:**
- `target_object_name` — already used in `run_pipeline.py:184,198`, missing from schema. Real contract bug.
- `smbs_role` + `smbs_role_source` — needed to record Q1=D fallback chain outcome in the artifact (otherwise role resolution is invisible to downstream consumers).
- All three fields are **optional** and `type: ["string", "null"]` → backward-compatible with all 41 existing manifests.
- `mounting_type` (PROMECAM/AMADA/TRUMPF/WILA/...) — **REMOVED from proposal**: speculative, no consumer code, no UI justification. Backlog if needed later.

**Q8 — APPROVED with MVP scope (2 levels):**

- **Level 1:** `<repo>/toollibrary/` — built-in, demo, tests (default fallback).
- **Level 2:** `--library-path <path>` CLI argument — explicit override.
- **Deferred:** `~/.smbs/tooling/` user-global path (cross-platform complexity; add when real user has >10 tools).

**Q1=D — 4-level fallback chain confirmed (property API works in headless):**

Test verified: `App.openDocument()` + `obj.PropertiesList` + `obj.addProperty("App::PropertyString", "SMBS_Role", ...)` all work headless. Chain:
1. `property` — read `SMBS_Role` from FCStd object property.
2. `name` — read object name (`PUNCH`, `DIE`, `FRAME`, `BACKGAUGE`, `RAM`, `BED`).
3. `sidecar` — read `.tooling.json` (existing behavior).
4. `inferred` — heuristic BBox + `confirmed=False`.

**Phase B revised estimate (honest, post-KL-12 lessons):**

Original roadmap: 4–6 sessions / 14–28 commits. **Coordinator revision: ~5 sessions / ~9 commits.** Sub-steps:

- **S.22a** — `ToolingCatalog` (scan + index sidecar files) — 2 commits
- **S.22b** — Q1=D 4-level fallback in `FcstdToolingLoader` — 2 commits
- **S.22c** — `ToolingLibrary` manager (add/get/list, headless) — 2 commits
- **S.22d** — `SMBS_ToolingLibrary.FCMacro` (FreeCAD UI, manual test) — 1 commit
- **S.22e** — Coordinator integration (`punch_id`/`die_id` + `--library-path`) — 2 commits

**Non-goals for Phase B MVP:**
- `mounting_type` field
- `~/.smbs/tooling/` user-global storage
- Datum editing UI in macro
- Auto-detection of datums (sidecar remains manual)
- Import wizard beyond basic role resolution

### Phase D — Requirement 4.1 (Clamp constraints) — COMPLETE (2026-10-19)

**Status:** DONE. All sub-steps (S.24a–S.24c) committed and pushed. Requirement 4.1 from roadmap §3 is functionally complete.

**Delivered:**
- `tooling_library_config.schema.json` — контракт для `<library>/config.json` (#7 approved, S.24a)
- `clamp_validator.py` — `ClampConfig`, `PunchSegment`, `validate_punch_chain` (5 Q2 rules), `load/save_clamp_config`, `validate_config_dict`
- `ToolingLibrary.get_clamp_config / set_clamp_config / ensure_clamp_config`
- `smbs-cli --clamps-config PATH` + `Coordinator.start_run(machine_config["clamps_config"])` plumbing
- Baseline: 449 tests / 5F / 0E / 3xF (+27 tests from Stage 6)

**Deferred (backlog):**
- UI-редактор раскладки лапок в FCMacro (JSON-файл достаточно для MVP).
- Auto-repositioning clamps (requirement 4).
- Enforcement of validate_punch_chain during planning (requirement 4, Phase E).

**Next:** requirement 3.1 (Tooling sets) per roadmap §8 order (3 → 4.1 → 3.1 → 4 → 2 → 5). KL-12 / KL-8 remain in backlog.

### Q2 — Clamp constraint spec (RESOLVED 2026-10-18)

**Status:** Spec accepted, implementation deferred to requirement 4.1 start.

**Machine model (`<library>/config.json`):**
- `clamps.width_mm` — clamp block width (user-configurable, default 150mm).
- `clamps.gap_between_mm` — gap between blocks (user-configurable, default 50mm).
- `clamps.positions_mm` — block centers.
- `clamps.repositionable` — bool (operator can move/remove blocks between bends).

**Punch chain validation rules:**
1. At least 2 clamp blocks under the punch chain.
2. All punch-to-punch joints must fall inside a clamp zone `[center - W/2, center + W/2]` (tolerance 0.1mm).
3. No gaps between punches: `end_i == start_{i+1}`.
4. End punches (first, last) may overhang by ≤ 30% of their own length (user-configurable ratio).
5. Punch total length = bend line length − 10..15mm (selection rule, belongs to requirement 4).

**If no valid configuration exists:** operator moves/removes blocks. Constraint checker only validates a given configuration (does NOT auto-reposition). Auto-repositioning is backlog (requirement 4 optimal selection).

**Example (valid):**
- Blocks at `[75, 275, 475]` centers, W=150, gap=50 → zones `[0..150], [200..350], [400..550]`.
- Punch chain: `[0..200]` (200mm) + `[200..400]` (200mm) + `[400..475]` (75mm).
- Joint at 200 → in zone `[200..350]` ✓. Joint at 400 → in zone `[400..550]` ✓.
- Left edge 0 = on block 1 (overhang 0, OK). Right edge 475 < block 3 end 550 → punch end is INSIDE block 3, overhang = 0, OK.
- Chain length = 475mm (target bend line = 485-490mm after -10..15mm tolerance). At least 3 blocks under chain (block 1, 2, 3) ≥ 2 required ✓.
- **Result: VALID.**

**Example (invalid — overhang fail):**
- Same blocks/zones. Punch chain: `[0..200]` (200mm) + `[200..400]` (200mm) + `[400..700]` (300mm).
- Right edge 700 > last block end 550 → overhang = 150mm. 30% of last punch (300mm) = 90mm → **150 > 90 ✗ FAIL**.
- Fix: shrink last punch to end ≤ 550 (e.g. `[400..640]`, overhang = 90mm = 30% of 240mm → OK) or add block at `[650..800]` center=725.

### Phase B — COMPLETE (2026-10-18)

**Status:** DONE. All sub-steps (S.22a–S.22h) committed and pushed. Requirement 3 (Tooling Library) from roadmap §3 is functionally complete.

**Delivered:**
- `ToolingCatalog` (scan + index), `ToolingLibrary` (add/remove/list/get/has_tool), Q1=D 4-level role resolution in `FcstdToolingLoader`, FreeCAD UI macro (`SMBS_ToolingLibrary.FCMacro`), Coordinator + CLI integration (`--punch-id`/`--die-id`/`--library-path`).
- 3 new schema fields in `tooling_manifest.schema.json`: `target_object_name`, `smbs_role`, `smbs_role_source` (all optional, backward-compatible).
- Baseline: 415 tests / 5F / 0E / 3xF (was 407 before Phase B tests, 407+8=S.22g; total Stage 5 +48 tests from 367 baseline).

**Deferred / backlog:**
- INFERRED heuristic (honest error instead — see KL-13 and Q1=D).
- `~/.smbs/tooling/` user-global path (Q8 MVP = 2 levels).
- Datum editing UI, sidecar wizard.
- "Use in pipeline" button in macro.
- KL-15 (target_object_name disambiguation, P2) — see PROGRESS.md.

**Next:** requirement 4.1 (Clamp constraints) per roadmap §8 order (3 → 4.1 → 3.1 → 4 → 2 → 5). KL-12 / KL-8 remain in backlog.

### Status of sections 1–10

Sections 1–10 of this document are the original roadmap and remain authoritative for phases, dependencies, matrices, and detailed requirement descriptions. This addendum is a **delta**, not a replacement.
