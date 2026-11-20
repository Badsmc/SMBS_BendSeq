# Branch audit — 2026-11-20

Baseline: manualmod.ui / smbs at 35096db27469a360c991d298b11b9e7387a95332.

All branches below are ancestors of the baseline (verified using git merge-base --is-ancestor). Deleting these refs does not remove unique commits. Recover any branch by creating it at the recorded SHA. Main, ManualMode, core, 2d/3d/gui, archive and benchmark checkpoints, unique experiments, performance PR #12 and active fix/coin3d-reopen-20261120 are retained.

Obsolete PRs #3–11 already have their head commits in the baseline and will be closed. PR #12 remains open.

## Cleanup candidates and recovery manifest

| Branch | Commit SHA |
|---|---|
| GUI/v0.0.1 | ef4cc94995d1100c326ed7e7a00054cce55ca1a9 |
| GUI/v0.0.1-context-panel | 748faa6a11d7b8a71718719b7f9e1db2a2d5a851 |
| development/simulation-gui-polish | bedd81672d1649a0b1654fc9fb0e317c0539b9c8 |
| development/working-addon | 160812d1781a4972d9a77e07ae7b34bd4765ce4a |
| experiment/2d-backgauge-kinematics | 963b88344dbae1a14e54900d4d6d0fc717445fc3 |
| experiment/2d-backgauge-kinematics-cascade | 4b666384ab313951c40e3856266929804edbd682 |
| experiment/dxf-manual-bends | 0e8ed58b82ca9face01b2ebcb08e57e3c84e1d96 |
| experiment/fast-search-lift-retract | 0c6c0dfd02bd521b3e8487f4c3f4c7800d786b2a |
| experiment/fcstd-native-autosave | 80f74b384fe54bea5a0160a79c86cf13bd27043d |
| experiment/prebend-rebend | 716f84234ae4c8b51a60d7ee71a552f622fedc6e |
| feature/25d-physical-validation | 644f4972a5e6c09b6b8168d1e7dd5072a629c267 |
| feature/fcstd-embedded-smbs-json | 1443c12175563989249e199fdb994cd0b1e41efc |
| feature/manual-3d-tooling-validation-pan | 79a9039b8a5e8fe49ee0f9340e64c8a515d73692 |
| feature/manual-retract-evidence-fcstd | c97673773959715f896ff9b67a4940f611eec520 |
| feature/native-freecad-tasks-panel | f53906905a37804c62d854de575b2ba0ae410b49 |
| feature/native-occ-collision-inspector | 4db03d14ad783986ced2502cc5a939fc16ba717b |
| feature/visual-only-fcstd-backgauge | 0cfb05613275c5636a649c45de232c1179abe738 |
| fix/conditional-endzone-volume-tolerance | dcce3d99d9717f914a0e4077b4900213a48aa306 |
| fix/manual-collision-component-angle-diagnostics | bb1b579712928b383b9d29b0eac2e3411cbbdc14 |
| fix/manual-verify-embedded-fcstd-report | f67c61640bca638ec97d558ab6e3917672236fea |
| fix/native-tasks-reopen-desktop-debug-json | 67ab625b334dbb4b11d39c022ef0dd3ed8b9ae23 |
| fix/restore-legacy-manual-2d-check | 521bff5902b6b27cd038bd6cb9f18d33b2ba822f |
| fix/unified-native-manual-pose-regression | 85eb3fdcb2b9adb472f030da4b4a276e15d08eee |
| handmode | babfb3b6c8a8e397e037c13fb68b16da934bcf1a |
| integration/gui-2d-search-screening | 3e14852dedf7afbc04652a3682fbe1d6707f1200 |
| integration/prebend-rebend-manualmod-ui-20261119 | c28b564a13e0fbe514d039afa6dc1af6cc2d596b |
| pose | 85eb3fdcb2b9adb472f030da4b4a276e15d08eee |

## Remaining audit findings

Main still contains 6 commits outside manualmod.ui. Several experimental branches contain unique work or unrelated roots; no deletion is justified without a separate functional review. A new active Coin3D reopen fix branch appeared during this audit and is preserved. This audit verifies Git ancestry, not FreeCAD runtime stability. Windows Qt lifecycle, staged bend collision validation, TechDraw performance and persistence still require runtime verification.
