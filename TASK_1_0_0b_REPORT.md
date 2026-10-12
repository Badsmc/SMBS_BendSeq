# TASK 1.0.0b REPORT: Загрузка 16 FCStd-фикстур и извлечение golden data

## 1. Окружение
- **Репозиторий:** `/Users/badmc/Desktop/SMBS_BendSeq`
- **Ветка:** `stage-1-fix-correctness`
- **Базовый Commit:** `c710577` (Task 1.0.0 закрыта)
- **Интерпретатор:** `/Applications/FreeCAD.app/Contents/Resources/bin/python`
  - FreeCAD Version: `1.1.3` (build `20260725`, Git `145529fe741292ff0b3977a01195bf0247425794`)
  - SheetMetal Workbench Version: `0.8.21` (согласно `package.xml`)
  - OCC Version: `7.8.1.1`
- **Источник фикстур:** `~/Desktop/BendSeq_Fixtures_Source/` (16 файлов .FCStd)
- **Дата:** 2026-10-12

---

## 2. Сводная таблица извлечённых фикстур

Все 16 фикстур успешно обработаны, артефакты экспортированы в `fixtures/freecad/<slug>/`, хэши SHA-256 вычислены.

| # | Фикстура (slug) | Файл источника | FCStd, КБ | Bends | Hash `part_3d.brep` | Hash `flat_pattern.brep` | Hash `bends.json` | Warning |
|---|---|---|---|:---:|---|---|---|:---:|
| 1 | `l_ok` | `L-(ok).FCStd` | 27.6 | 1 | `3cb365ee90059b0496181f5e8f4cff7215354beba00eb5fc5bc66c5e8c1faee2` | `73526a3182bc81e3557e03ce15f9b9cb1df1db4268e36e52292f75be9712a3d0` | `7115e24716a698ba5cb0da4ff19e34e569c73efb6e5e8e895c13bfa7be05f111` | None |
| 2 | `l_30_6_false` | `L-30-6-(false).FCStd` | 29.1 | 1 | `cc36dc044acaaa671231f28b4ee4b8d76dbd8e9f291ca879c94c9cb239bc7a61` | `867d959ceea8fca47eb46231c59bb765b4c1071d2b8fe09b437c35f29910d512` | `7115e24716a698ba5cb0da4ff19e34e569c73efb6e5e8e895c13bfa7be05f111` | None |
| 3 | `u_90deg_90deg_ok` | `U-90deg-90deg-(ok).FCStd` | 34.0 | 2 | `911014fbace780005c249a5b3ee28e67efcfef9e75525997230559e55b1f60ff` | `825eeb4d18d451fe836526eb61769dbe1cf93fa271887b4010a08e178eaeb724` | `8145883d05e34cb80895ecc2a6ce4667310f40f42a2a58be7e25824eadd0a1e7` | None |
| 4 | `u_10_30_10_ok` | `U-10-30-10-(OK).FCStd` | 33.0 | 2 | `1d1f76ce32eca8f44d852a3a0e687130b9ee27eb7b40bb2278077596c56b0019` | `b743eed8f78bdf1bfa8a3ea0d5f088ba76e03ea630983cbbf789cb4aa7b8220f` | `8145883d05e34cb80895ecc2a6ce4667310f40f42a2a58be7e25824eadd0a1e7` | None |
| 5 | `u_10_10_20_false` | `U-10-10-20-(false).FCStd` | 36.2 | 2 | `8d1a72123950e3b97b09337ff8d89e47265be7e58a2aaeececb50c66060c55ea` | `1395eeb3c03a58e8bce955a8286a07fe2374e64fdfc568015cf013f707f1ea5b` | `8145883d05e34cb80895ecc2a6ce4667310f40f42a2a58be7e25824eadd0a1e7` | None |
| 6 | `u_60deg_30deg_ok` | `U-60deg-30deg-(ok).FCStd` | 42.4 | 2 | `eac5d192637c87c2c9d74e844a47514ea47fc890f555c1db202868ff83b544b6` | `20dcb4301d5ba59b027d11f5825bc5eeb6d218d6e35cbfe544c20b8f04c66ff9` | `9bb19468b9247d51ee9393eb8327ffc5c249a857022d64f0ea6156e7924ef948` | None |
| 7 | `u_10_15_10_punch2_ok` | `U-10-15-10-(punch2-OK).FCStd` | 33.2 | 2 | `758952390cba22e17154a1c1e15ff950d8e6d6f3491c43c32872e9c76f372ea8` | `3e8c9ccc4b93d9bd679caba9d9f263d8abaf3728df3ed1d806dfe7b80ef5d4a6` | `8145883d05e34cb80895ecc2a6ce4667310f40f42a2a58be7e25824eadd0a1e7` | None |
| 8 | `z_ok` | `Z-(ok).FCStd` | 38.1 | 2 | `70991b047515b2255761c0c1b75fa13101b7a2d4808c1050ef09153a808061eb` | `36d706dfdab95a9477e5d836ea7247fc26f29a0e671295f70823528f8fbf44eb` | `d46818a12b9d33267ee595b1fa7a7b82fe782488825c57b7f1ecb646c0ca3c2a` | None |
| 9 | `z_10_20_10_ok` | `Z-10-20-10-(ok).FCStd` | 36.8 | 2 | `8d6dae72df6e088d87ea466a2c2eb63ee8ceb7264a7536fb7dd0081d50a25694` | `b219a03bf9b8a82cf9c1ae02717ce01d8350d2bbd47fc1f24d3509d37fe32b13` | `d46818a12b9d33267ee595b1fa7a7b82fe782488825c57b7f1ecb646c0ca3c2a` | None |
| 10 | `z_10_20_10_false` | `Z-10-20-10-(false).FCStd` | 36.7 | 2 | `915672081b953337a7fe6d304a0e9987817ebfd7f2b1c678aeb4b8c9dfc321d4` | `96295ab9eeeb73606f6587424ea4e931448b1bfb917637841c2c366ffda89014` | `d46818a12b9d33267ee595b1fa7a7b82fe782488825c57b7f1ecb646c0ca3c2a` | None |
| 11 | `135_ok` | `135-(ok).FCStd` | 35.8 | 2 | `862511f2ce2c1b8ca7d0e80459c3d4a5fa82fb5a4985c7bb741bcfe832df7da9` | `c9294ae2087a3297a7a1c72688f1ce3e0bfa9da907c08a9dd978923df29a7516` | `f95c64faba0040bc1829e2fdb9f268f71aa5a3b2b936d6ea2dbf6a47a1dc089f` | None |
| 12 | `box_100out_50in_ok` | `box.100out-50in-(ok).FCStd` | 84.6 | 8 | `935c7f0cd4acbeec981ce8e7d23d857f62c57e53f7f8d6896262451f47841bf1` | `6ecb8cdab4b4198c6b73a213e8787b649d29ef19ec3031023d8d67228a1c97a8` | `c7f33a4ad0f8eb8910b00511855a6d5c6436e84d41e7f3b89b4eb57a701e1948` | None |
| 13 | `box_100in_50out_false` | `box.100in-50out-(false).FCStd` | 82.5 | 8 | `561380e8f67c3b88939798782d8c366e60b29c9b58169e6bcf1224d084ee7b03` | `6acc1b46a8d4a6652eb142bdf5fca0835f8d9b1db316cbdb7a83d47c4056157f` | `cbd55682284d720c749339e8fc5500e31950e3957263b65a5cb35c91338df372` | None |
| 14 | `sqin_200_25_10_ok` | `SQin.200-25-10-(ok).FCStd` | 72.4 | 8 | `bd7c5dde1ce3e260906be32ea27c00684f88e20fc1ae602e1ffb1bc10be8c894` | `2ce8dbc733618aa12ffbe7817ebdd832e14eb16e3ff5c136f328d0526019a27e` | `f3d46712cd88289ea0eb680674256dbbfe9d297cbbf3295e8011270c538a7c29` | None |
| 15 | `sqout_200_25_10_ok` | `SQout.200-25-10-(ok).FCStd` | 73.5 | 8 | `5ba0cbbac556f082e65d8362ea704b281cfb2ec10b77cbf10dcf45448386127b` | `4f185d1742566ecb5ce53aa24b787c88b22e92c2da01d9f8dfc207d58d927c4d` | `9498be1f15da8fa900f913d3957bf8f4a33190875e53392a54952467d025b340` | None |
| 16 | `2_1_3_4_ok` | `2-1-3-4-(ok).FCStd` | 163.8 | 10 | `faaf8327d359695b22b1029c72db686ef3f7a4fa3764858b907fe23f6e91cb3d` | `1bc4d208ed2a15c30ebbaebf5a8947f121d58309df537ce2189d38c6d17cf360` | `2e56c5f396b8a8163f538356d7870a256f11c79e6065e89647ae027171e2efc5` | None |

Суммарный объем `fixtures/freecad/`: **1.6 МБ** (< 5 МБ согласно ТЗ).

---

## 3. Детализация фикстуры `2-1-3-4-(ok)` (Ground Truth от оператора)

Полное содержимое `fixtures/freecad/2_1_3_4_ok/bends.json`:
```json
{
  "bends": [
    {
      "bend_id": "BEND_02",
      "angle_deg": -45.0,
      "direction": "DOWN",
      "radius_internal_mm": 1.0,
      "source": "OPERATOR_CONFIRMED",
      "operator_confirmed": true,
      "requires_gooseneck": false
    },
    {
      "bend_id": "BEND_01",
      "angle_deg": 45.0,
      "direction": "UP",
      "radius_internal_mm": 1.0,
      "source": "OPERATOR_CONFIRMED",
      "operator_confirmed": true,
      "requires_gooseneck": false
    },
    {
      "bend_id": "BEND_03",
      "angle_deg": 90.0,
      "direction": "UP",
      "radius_internal_mm": 1.0,
      "source": "OPERATOR_CONFIRMED",
      "operator_confirmed": true,
      "requires_gooseneck": true
    },
    {
      "bend_id": "BEND_04",
      "angle_deg": 90.0,
      "direction": "UP",
      "radius_internal_mm": 1.0,
      "source": "OPERATOR_CONFIRMED",
      "operator_confirmed": true,
      "requires_gooseneck": true
    },
    {
      "bend_id": "BEND_05",
      "angle_deg": 90.0,
      "direction": "UP",
      "radius_internal_mm": 1.0,
      "source": "SHEETMETAL_NATIVE",
      "operator_confirmed": false,
      "requires_gooseneck": false
    },
    {
      "bend_id": "BEND_06",
      "angle_deg": 90.0,
      "direction": "UP",
      "radius_internal_mm": 1.0,
      "source": "SHEETMETAL_NATIVE",
      "operator_confirmed": false,
      "requires_gooseneck": false
    },
    {
      "bend_id": "BEND_07",
      "angle_deg": 90.0,
      "direction": "UP",
      "radius_internal_mm": 1.0,
      "source": "SHEETMETAL_NATIVE",
      "operator_confirmed": false,
      "requires_gooseneck": false
    },
    {
      "bend_id": "BEND_08",
      "angle_deg": 90.0,
      "direction": "UP",
      "radius_internal_mm": 1.0,
      "source": "SHEETMETAL_NATIVE",
      "operator_confirmed": false,
      "requires_gooseneck": false
    },
    {
      "bend_id": "BEND_09",
      "angle_deg": 90.0,
      "direction": "UP",
      "radius_internal_mm": 1.0,
      "source": "SHEETMETAL_NATIVE",
      "operator_confirmed": false,
      "requires_gooseneck": false
    },
    {
      "bend_id": "BEND_10",
      "angle_deg": 90.0,
      "direction": "UP",
      "radius_internal_mm": 1.0,
      "source": "SHEETMETAL_NATIVE",
      "operator_confirmed": false,
      "requires_gooseneck": false
    }
  ],
  "extraction_method": "SheetMetal_Unfold_Sketch_Bend_Labels",
  "labels_match_bend_operation": true
}
```

- Подтверждена последовательность `BEND_02` (−45°) → `BEND_01` (+45°) → `BEND_03` (+90°) → `BEND_04` (+90°).
- Подтверждено `requires_gooseneck: true` для `BEND_03` и `BEND_04`, а также в `ground_truth` манифеста.

---

## 4. Подтверждение параметров специальных фикстур

1. **`U-10-15-10-(punch2-OK)` (`u_10_15_10_punch2_ok`):**
   - В манифесте подтверждено: `requires_gooseneck: true`, `source_of_truth: "OPERATOR_CONFIRMED"`, `expected_status: "POSITIVE"`, `production_eligible: true`.
2. **Все 5 негативных фикстур (`(false)`):**
   - `l_30_6_false`: `expected_status: "NEGATIVE"`, `production_eligible: false`, `negative_reason: "operator_confirmed_incompatible_on_R08_V12"`
   - `u_10_10_20_false`: `expected_status: "NEGATIVE"`, `production_eligible: false`, `negative_reason: "operator_confirmed_incompatible_on_R08_V12"`
   - `z_10_20_10_false`: `expected_status: "NEGATIVE"`, `production_eligible: false`, `negative_reason: "operator_confirmed_incompatible_on_R08_V12"`
   - `box_100in_50out_false`: `expected_status: "NEGATIVE"`, `production_eligible: false`, `negative_reason: "operator_confirmed_incompatible_on_R08_V12"`
   - Состояние всех: строго заблокированы от допуска в производство.

---

## 5. Анализ расхождений `_Bend_Labels` vs `Bend`

- Во всех 16 файлах `labels_match_bend_operation` выставлен в `true`.
- Особый случай `135-(ok)`: 
  - В файле `135-(ok).FCStd` развёртка `Тело_Unfold` была привязана к промежуточной операции `Bend` (1 гиб 45° / 135°), в то время как дерево модели содержало завершающую операцию `Bend001` (2 гиба).
  - Скрипт `load_fcstd.py` корректно выявил эту ситуацию и выполнил вычисление полного плоского паттерна для завершающего тела `Bend001`, извлекши оба гиба (+45° и −45°) в соответствии со спецификацией оператора.

---

## 6. Вывод интеграционного теста `test_fcstd_fixtures.py`

Запуск:
```bash
/Applications/FreeCAD.app/Contents/Resources/bin/python tests/fixture/test_fcstd_fixtures.py
```
Результат:
```text
..
----------------------------------------------------------------------
Ran 2 tests in 0.050s

OK
```

Все 16 манифестов проверены на валидность, SHA-256 хэши всех файлов совпадают, соответствие `bends_count == len(bend_sequence)` подтверждено.

---

## 7. Открытые вопросы к техническому руководителю

1. В Task 1.0.1 при включении фикстур в тестовый прогон пайплайна, фикстуры класса `FREECAD_NATIVE` будут читаться `GeometryWorker` напрямую из `part_3d.brep` и `flat_pattern.brep` (уже экспортированных) или `GeometryWorker` при необходимости должен уметь открывать `source.FCStd`?
2. В файле `135-(ok).FCStd` оригинальный объект `Тело_Unfold` имел 1 гиб, но твердое тело содержало 2 гиба `Bend001`. Мы экспортировали твердое тело со всеми 2 гибами. Требуется ли пересохранить исходный `.FCStd` в FreeCAD с перепривязкой развертки или оставить файл строго неизменным (as-is)?
