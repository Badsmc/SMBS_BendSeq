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
| 1 | `l_ok` | `L-(ok).FCStd` | 27.6 | 1 | `3cb365ee90059b0496181f5e8f4cff7215354beba00eb5fc5bc66c5e8c1faee2` | `73526a3182bc81e3557e03ce15f9b9cb1df1db4268e36e52292f75be9712a3d0` | `90c57566fe24f94dee498f8495f7289fbf2933e6d0fc482183e0e7290c3d2e2f` | None |
| 2 | `l_30_6_false` | `L-30-6-(false).FCStd` | 29.1 | 1 | `cc36dc044acaaa671231f28b4ee4b8d76dbd8e9f291ca879c94c9cb239bc7a61` | `867d959ceea8fca47eb46231c59bb765b4c1071d2b8fe09b437c35f29910d512` | `90c57566fe24f94dee498f8495f7289fbf2933e6d0fc482183e0e7290c3d2e2f` | None |
| 3 | `u_90deg_90deg_ok` | `U-90deg-90deg-(ok).FCStd` | 34.0 | 2 | `911014fbace780005c249a5b3ee28e67efcfef9e75525997230559e55b1f60ff` | `825eeb4d18d451fe836526eb61769dbe1cf93fa271887b4010a08e178eaeb724` | `f9bc02152530996aacbcc57e1079d6409f4cc392371830f15899d9537b868461` | None |
| 4 | `u_10_30_10_ok` | `U-10-30-10-(OK).FCStd` | 33.0 | 2 | `1d1f76ce32eca8f44d852a3a0e687130b9ee27eb7b40bb2278077596c56b0019` | `b743eed8f78bdf1bfa8a3ea0d5f088ba76e03ea630983cbbf789cb4aa7b8220f` | `f9bc02152530996aacbcc57e1079d6409f4cc392371830f15899d9537b868461` | None |
| 5 | `u_10_10_20_false` | `U-10-10-20-(false).FCStd` | 36.2 | 2 | `8d1a72123950e3b97b09337ff8d89e47265be7e58a2aaeececb50c66060c55ea` | `1395eeb3c03a58e8bce955a8286a07fe2374e64fdfc568015cf013f707f1ea5b` | `f9bc02152530996aacbcc57e1079d6409f4cc392371830f15899d9537b868461` | None |
| 6 | `u_60deg_30deg_ok` | `U-60deg-30deg-(ok).FCStd` | 42.4 | 2 | `eac5d192637c87c2c9d74e844a47514ea47fc890f555c1db202868ff83b544b6` | `20dcb4301d5ba59b027d11f5825bc5eeb6d218d6e35cbfe544c20b8f04c66ff9` | `97cfb8860d334260c58329bcc8a69b3835c8c5463c39c7df98eb539211e0249c` | None |
| 7 | `u_10_15_10_punch2_ok` | `U-10-15-10-(punch2-OK).FCStd` | 33.2 | 2 | `758952390cba22e17154a1c1e15ff950d8e6d6f3491c43c32872e9c76f372ea8` | `3e8c9ccc4b93d9bd679caba9d9f263d8abaf3728df3ed1d806dfe7b80ef5d4a6` | `cefafaef9cd0c381f65d0eb7f540ca2cd51cb6703a548d7441da3a4477e3db41` | None |
| 8 | `z_ok` | `Z-(ok).FCStd` | 38.1 | 2 | `70991b047515b2255761c0c1b75fa13101b7a2d4808c1050ef09153a808061eb` | `36d706dfdab95a9477e5d836ea7247fc26f29a0e671295f70823528f8fbf44eb` | `a7674fee1520f2579db4018ddc26f8edb5ff8922d9f6d1a40189ec353a0659fe` | None |
| 9 | `z_10_20_10_ok` | `Z-10-20-10-(ok).FCStd` | 36.8 | 2 | `8d6dae72df6e088d87ea466a2c2eb63ee8ceb7264a7536fb7dd0081d50a25694` | `b219a03bf9b8a82cf9c1ae02717ce01d8350d2bbd47fc1f24d3509d37fe32b13` | `a7674fee1520f2579db4018ddc26f8edb5ff8922d9f6d1a40189ec353a0659fe` | None |
| 10 | `z_10_20_10_false` | `Z-10-20-10-(false).FCStd` | 36.7 | 2 | `915672081b953337a7fe6d304a0e9987817ebfd7f2b1c678aeb4b8c9dfc321d4` | `96295ab9eeeb73606f6587424ea4e931448b1bfb917637841c2c366ffda89014` | `a7674fee1520f2579db4018ddc26f8edb5ff8922d9f6d1a40189ec353a0659fe` | None |
| 11 | `135_ok` | `135-(ok).FCStd` | 35.8 | 2 | `862511f2ce2c1b8ca7d0e80459c3d4a5fa82fb5a4985c7bb741bcfe832df7da9` | `c9294ae2087a3297a7a1c72688f1ce3e0bfa9da907c08a9dd978923df29a7516` | `d3275ab73d057091c45f8ea11aa4bef1efd9b630f90ffd4d32b487a21ae4de3e` | None |
| 12 | `box_100out_50in_ok` | `box.100out-50in-(ok).FCStd` | 84.6 | 8 | `935c7f0cd4acbeec981ce8e7d23d857f62c57e53f7f8d6896262451f47841bf1` | `6ecb8cdab4b4198c6b73a213e8787b649d29ef19ec3031023d8d67228a1c97a8` | `a16a665d98d67e93a9320118a071026486cb656630eeab99661164d9bb8bfcfa` | None |
| 13 | `box_100in_50out_false` | `box.100in-50out-(false).FCStd` | 82.5 | 8 | `561380e8f67c3b88939798782d8c366e60b29c9b58169e6bcf1224d084ee7b03` | `6acc1b46a8d4a6652eb142bdf5fca0835f8d9b1db316cbdb7a83d47c4056157f` | `5ff7cacdec826a1aefa2da7fc780ce8a56ee0846e4e2105a7402abdc8aee72c2` | None |
| 14 | `sqin_200_25_10_ok` | `SQin.200-25-10-(ok).FCStd` | 72.4 | 8 | `bd7c5dde1ce3e260906be32ea27c00684f88e20fc1ae602e1ffb1bc10be8c894` | `2ce8dbc733618aa12ffbe7817ebdd832e14eb16e3ff5c136f328d0526019a27e` | `d353a6fd42a3a05c61e29f63eb5370693af5d18f7fb78b4b694c2414a4a7299f` | None |
| 15 | `sqout_200_25_10_ok` | `SQout.200-25-10-(ok).FCStd` | 73.5 | 8 | `5ba0cbbac556f082e65d8362ea704b281cfb2ec10b77cbf10dcf45448386127b` | `4f185d1742566ecb5ce53aa24b787c88b22e92c2da01d9f8dfc207d58d927c4d` | `981f4a9fa0ba1ac03d9eb27fabe72570e5c92b2d0b50d4f7f64d633ec5e87a19` | None |
| 16 | `2_1_3_4_ok` | `2-1-3-4-(ok).FCStd` | 163.8 | 10 | `faaf8327d359695b22b1029c72db686ef3f7a4fa3764858b907fe23f6e91cb3d` | `1bc4d208ed2a15c30ebbaebf5a8947f121d58309df537ce2189d38c6d17cf360` | `c86243c9625bd5236495e9cfad5c5fcc7175c29501d0a0581a720709a6a80e03` | None |

Суммарный объем `fixtures/freecad/`: **1.6 МБ** (< 5 МБ согласно ТЗ).

---

## 3. Детализация фикстуры `2-1-3-4-(ok)` (Ground Truth от оператора)

Полное содержимое `fixtures/freecad/2_1_3_4_ok/bends.json`:
```json
{
  "part_requires_gooseneck": true,
  "bends": [
    {
      "step": 1,
      "bend_id": "BEND_02",
      "angle_deg": -45.0,
      "direction": "DOWN",
      "radius_internal_mm": 1.0,
      "requires_gooseneck": false,
      "source": "OPERATOR_CONFIRMED",
      "operator_confirmed": true
    },
    {
      "step": 2,
      "bend_id": "BEND_01",
      "angle_deg": 45.0,
      "direction": "UP",
      "radius_internal_mm": 1.0,
      "requires_gooseneck": false,
      "source": "OPERATOR_CONFIRMED",
      "operator_confirmed": true
    },
    {
      "step": 3,
      "bend_id": "BEND_03",
      "angle_deg": 90.0,
      "direction": "UP",
      "radius_internal_mm": 1.0,
      "requires_gooseneck": true,
      "source": "OPERATOR_CONFIRMED",
      "operator_confirmed": true
    },
    {
      "step": 4,
      "bend_id": "BEND_04",
      "angle_deg": 90.0,
      "direction": "UP",
      "radius_internal_mm": 1.0,
      "requires_gooseneck": true,
      "source": "OPERATOR_CONFIRMED",
      "operator_confirmed": true
    },
    {
      "step": 5,
      "bend_id": "BEND_05",
      "angle_deg": 90.0,
      "direction": "UP",
      "radius_internal_mm": 1.0,
      "requires_gooseneck": false,
      "source": "SHEETMETAL_NATIVE",
      "operator_confirmed": false
    },
    {
      "step": 6,
      "bend_id": "BEND_06",
      "angle_deg": 90.0,
      "direction": "UP",
      "radius_internal_mm": 1.0,
      "requires_gooseneck": false,
      "source": "SHEETMETAL_NATIVE",
      "operator_confirmed": false
    },
    {
      "step": 7,
      "bend_id": "BEND_07",
      "angle_deg": 90.0,
      "direction": "UP",
      "radius_internal_mm": 1.0,
      "requires_gooseneck": false,
      "source": "SHEETMETAL_NATIVE",
      "operator_confirmed": false
    },
    {
      "step": 8,
      "bend_id": "BEND_08",
      "angle_deg": 90.0,
      "direction": "UP",
      "radius_internal_mm": 1.0,
      "requires_gooseneck": false,
      "source": "SHEETMETAL_NATIVE",
      "operator_confirmed": false
    },
    {
      "step": 9,
      "bend_id": "BEND_09",
      "angle_deg": 90.0,
      "direction": "UP",
      "radius_internal_mm": 1.0,
      "requires_gooseneck": false,
      "source": "SHEETMETAL_NATIVE",
      "operator_confirmed": false
    },
    {
      "step": 10,
      "bend_id": "BEND_10",
      "angle_deg": 90.0,
      "direction": "UP",
      "radius_internal_mm": 1.0,
      "requires_gooseneck": false,
      "source": "SHEETMETAL_NATIVE",
      "operator_confirmed": false
    }
  ],
  "extraction_method": "SheetMetal_Unfold_Sketch_Bend_Labels",
  "labels_match_bend_operation": true
}
```

- Подтверждена последовательность `BEND_02` (−45°) → `BEND_01` (+45°) → `BEND_03` (+90°) → `BEND_04` (+90°).
- Порядок загружается из выделенного операторского файла [fixtures/freecad/operator_sequence_2_1_3_4.json](file:///Users/badmc/Desktop/SMBS_BendSeq/fixtures/freecad/operator_sequence_2_1_3_4.json).
- Подтверждено `requires_gooseneck: true` для `BEND_03` и `BEND_04`, а также `part_requires_gooseneck: true` на верхнем уровне.

---

## 4. Подтверждение параметров специальных фикстур

1. **`U-10-15-10-(punch2-OK)` (`u_10_15_10_punch2_ok`):**
   - `part_requires_gooseneck: true` на верхнем уровне [bends.json](file:///Users/badmc/Desktop/SMBS_BendSeq/fixtures/freecad/u_10_15_10_punch2_ok/bends.json);
   - `requires_gooseneck: true` для каждого гиба;
   - В манифесте: `requires_gooseneck: true`, `source_of_truth: "SHEETMETAL_NATIVE"`, `expected_status: "POSITIVE"`, `production_eligible: true`.
2. **Все 5 негативных фикстур (`(false)`):**
   - `l_30_6_false`: `expected_status: "NEGATIVE"`, `production_eligible: false`, `negative_reason: "operator_confirmed_incompatible_on_R08_V12"`
   - `u_10_10_20_false`: `expected_status: "NEGATIVE"`, `production_eligible: false`, `negative_reason: "operator_confirmed_incompatible_on_R08_V12"`
   - `z_10_20_10_false`: `expected_status: "NEGATIVE"`, `production_eligible: false`, `negative_reason: "operator_confirmed_incompatible_on_R08_V12"`
   - `box_100in_50out_false`: `expected_status: "NEGATIVE"`, `production_eligible: false`, `negative_reason: "operator_confirmed_incompatible_on_R08_V12"`
   - Состояние всех: строго заблокированы от допуска в производство.
3. **Фикстура `135_ok`:**
   - В манифесте: `source_of_truth: "RECOMPUTED_FROM_FINAL_BODY"`;
   - В `known_limitations`: `"flat_pattern recomputed via getUnfold on final Bend001 body; original Тело_Unfold attached to intermediate Bend"`.
   - У остальных 15 фикстур: `source_of_truth: "SHEETMETAL_NATIVE"`.

---

## 5. Вывод интеграционного теста `test_fcstd_fixtures.py`

Запуск:
```bash
/Applications/FreeCAD.app/Contents/Resources/bin/python tests/fixture/test_fcstd_fixtures.py
```
Результат:
```text
..
----------------------------------------------------------------------
Ran 2 tests in 0.023s

OK
```
