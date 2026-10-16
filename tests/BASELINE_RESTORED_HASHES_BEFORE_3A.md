# Baseline Restored Shapes SHA-256 Hashes (Before Task 1.0.1b-3a)

Данный документ фиксирует эталонные криптографические контрольные суммы (SHA-256) бинарных артефактов `restored.brep`, полученных штатным запуском пайплайна Stage 1 Baseline на всех 7 линейных фикстурах до внесения любых изменений этапа 3a (на коммите `937f416`).

## 1. Эталонные хэши restored.brep (Baseline)

| Фикстура | SHA-256 хэш `restored.brep` | Размер файла | Топологический статус |
| :--- | :--- | :---: | :---: |
| `u_bracket` | `ae1085e75f840d8c6f6b9be5fed605068c9b8655961776543361cd767f3512dd` | 22 970 байт | VALID (`topology_matches == True`) |
| `four_bend` | `d046f6b695a63cdf628e6b62e39b5d3c544e618c1c9a1f057efc63a70a515aa0` | 35 701 байт | VALID (`topology_matches == True`) |
| `z_ok` | `bfe21ab02b71758322682f74117c501da01486442066078e1cdaabc4dec80951` | 21 511 байт | VALID (`topology_matches == True`) |
| `z_10_20_10_ok` | `a94c00422303bc526d2fbc60b1368f72768f68e974e0abe5407d630d74ccb8ff` | 21 476 байт | VALID (`topology_matches == True`) |
| `135_ok` | `075a03853dcbf5e841c04e98a49d9811721d9739b2d17683a7eab943f37ec8b0` | 21 545 байт | VALID (`topology_matches == True`) |
| `u_60deg_30deg_ok` | `85a21113665720e953ee34925e57773dbd7b0378b6d1ce389fb28bae312ddf66` | 19 397 байт | VALID (`topology_matches == True`) |
| `u_90deg_90deg_ok` | `c31128864382054fad18ace95e5dbd9181f7e326ee4533e4dc4e6eb434f746db` | 21 515 байт | VALID (`topology_matches == True`) |

## 2. Инвариант неизменности линейного пути
После реализации поддержки древовидных деталей (Task 1.0.1b-3a) обязателен повторный прогон всех 7 фикстур.
Любое расхождение хотя бы одного хэша означает регрессию в алгоритме линейной гибки `fold_multi_bend_from_flat_pattern` или механизме канонизации. Коммит изменений при наличии расхождений запрещён.
