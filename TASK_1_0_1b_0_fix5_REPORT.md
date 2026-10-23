# TASK 1.0.1b-0-fix5 REPORT: Исправление детектирования гибов, касающихся торцевых срезов и угловых вырезов

## 1. Введение и суть проблемы

В рамках задачи **Task 1.0.1b-0-fix5** исправлен дефект извлечения гибов в `FeatureExtractor._build_panel_edges` (`smbs_core/Geometry/feature_extractor.py`).

### Причина потери гибов:
В 3D-геометрии листовых деталей со сложной формой полок, коробчатых конструкций (`box_*`, `sq*`) и ступенчатых дверей (`2_1_3_4_ok`) цилиндрические грани сгибов геометрически граничат не только с 2 «настоящими» рабочими панелями (полками, соединяемыми сгибом), но и с торцевыми срезами соседних полок (relief cuts, notch cuts).
В результате:
- `adj_map` находил 3 смежные плоские грани;
- Условие `if len(connected_panels) == 2:` отбрасывало такие сгибы;
- В коробках терялось 4 из 8 гибов (извлекалось лишь 4 гиба первого яруса);
- В детали `2_1_3_4_ok` терялось 8 из 10 гибов (извлекалось лишь 2 гиба).

---

## 2. Анализ геометрии сопряжения (Line vs Circle)

При исследовании топологии B-Rep на OCC (`BRepAdaptor_Curve` и `TopExp.MapShapesAndAncestors`):
- Настоящие плоские панели соединяются с цилиндром гиба **вдоль образующей линии** цилиндра — общее ребро является строго **прямолинейным** (`GeomAbs_Line`).
- Торцевые срезы, вырезы углов (corner relief / notch) и торцы смежных полок пересекают цилиндр поперечно — их общее ребро является **круговой дугой** (`GeomAbs_Circle`) либо точкой/прочим контуром.

Поэтому геометрическим критерием принадлежности смежной плоской грани к рабочей полке гиба является:
$$\text{is\_real\_panel} \iff \exists e \in \text{common\_edges}(F_{cyl}, F_{flat}) : \text{curve\_type}(e) == \text{"line"}$$

---

## 3. Внесённые изменения

1. **`smbs_core/geometry_backend/backends/pythonocc_backend.py`**:
   - Добавлен метод `get_edge_curve_type(self, edge) -> str`, возвращающий `"circle"`, `"line"`, `"other"` или `"unknown"` на основе `BRepAdaptor_Curve`.
   - Метод зарегистрирован в абстрактном базовом классе `GeometryBackend` (`smbs_core/geometry_backend/base.py`).

2. **`smbs_core/Geometry/feature_extractor.py`**:
   - Построена карта смежности `edge_to_faces` через `TopExp.MapShapesAndAncestors(shape, TopAbs_EDGE, TopAbs_FACE)`.
   - Для каждого цилиндра и каждой смежной плоской грани определяются общие рёбра. Грань признаётся рабочей полкой только если разделяющее ребро — `"line"`.
   - Внедрена обязательная диагностика: если `len(connected_panels) > 2` или `< 2`, генерируются предупреждения `BEND_TOUCHES_MULTIPLE_PANELS` / `BEND_TOUCHES_INSUFFICIENT_PANELS` без тихого проглатывания.
   - В `_group_panels` скорректировано объединение копланарных граней: удалённые несвязанные грани одного штампа не объединяются в ложную мега-панель (объединяются только противолежащие грани толщины листа и смежные копланарные участки).

3. **`smbs_core/workers/geometry/worker.py`**:
   - В `_load_occ_shape` для формата `.fcstd` добавлено корректное извлечение финального твердотельного объекта детали (`Body` / терминальный солид) вместо произвольного первого промежуточного фичера дерева построения (что ранее приводило к считыванию только начального шага для `2_1_3_4_ok`).

4. **`smbs_core/workers/topology/worker.py`**:
   - Проброшен список `diagnostics` в вызов `extract_features`.

---

## 4. Шаг 3: Результаты сквозного прогона на всех 16 FCStd фикстурах

Прогон выполнен через пайплайн `GeometryWorker` + `TopologyWorker` на всех 16 оригинальных фикстурах FreeCAD:

| Фикстура | Manifest bends | Extracted (до fix) | Extracted (после fix) | PASS/FAIL |
| :--- | :---: | :---: | :---: | :---: |
| **l_ok** | 1 | 1 | 1 | **PASS** |
| **l_30_6_false** | 1 | 1 | 1 | **PASS** |
| **u_10_10_20_false** | 2 | 2 | 2 | **PASS** |
| **u_10_15_10_punch2_ok** | 2 | 2 | 2 | **PASS** |
| **u_10_30_10_ok** | 2 | 2 | 2 | **PASS** |
| **u_90deg_90deg_ok** | 2 | 2 | 2 | **PASS** |
| **u_60deg_30deg_ok** | 2 | 2 | 2 | **PASS** |
| **135_ok** | 2 | 2 | 2 | **PASS** |
| **z_ok** | 2 | 2 | 2 | **PASS** |
| **z_10_20_10_ok** | 2 | 2 | 2 | **PASS** |
| **z_10_20_10_false** | 2 | 2 | 2 | **PASS** |
| **box_100in_50out_false** | 8 | 4 | 8 | **PASS** |
| **box_100out_50in_ok** | 8 | 4 | 8 | **PASS** |
| **sqin_200_25_10_ok** | 8 | 4 | 8 | **PASS** |
| **sqout_200_25_10_ok** | 8 | 4 | 8 | **PASS** |
| **2_1_3_4_ok** | 10 | 2 | 10 | **PASS** |

**Целевой результат достигнут на 100%:**
- Все 8-гибовые коробки (`box_*`, `sq*`): **8 из 8** гибов извлечены.
- 10-гибовая деталь `2_1_3_4_ok`: **10 из 10** гибов извлечены.
- Все 1- и 2-гибовые фикстуры: полное отсутствие регрессии (PASS).

---

## 5. Шаг 4: Результаты тестов

Прогон обязательного набора тестов:
1. `tests/fixture/test_fcstd_fixtures.py`: **OK** (2 tests in 1.006s)
2. `fixtures/synthetic/four_bend_verify.py`: **PASS** (все метрики и BRepCheck валидны)
3. `fixtures/synthetic/l_bracket_verify.py`: **PASS** (все метрики и BRepCheck валидны)
4. `fixtures/synthetic/u_bracket_verify.py`: **PASS** (все метрики и BRepCheck валидны)
5. `tests/test_contracts_and_filestore.py`: **OK** (7 tests in 0.168s)
6. `tests/test_stage1_baseline.py`: **OK** (3 tests in 5.483s)
7. `tests/test_sheetmetal_adapter_contract.py`: **OK** (3 tests in 1.518s)

---

## 6. Полные Diffs

### Diff 1: `smbs_core/geometry_backend/backends/pythonocc_backend.py`

```diff
diff --git a/smbs_core/geometry_backend/backends/pythonocc_backend.py b/smbs_core/geometry_backend/backends/pythonocc_backend.py
index d378ad1..0035043 100644
--- a/smbs_core/geometry_backend/backends/pythonocc_backend.py
+++ b/smbs_core/geometry_backend/backends/pythonocc_backend.py
@@ -420,3 +420,19 @@ class PythonOCCBackend(GeometryBackend):
         algo = BRepAlgoAPI_Cut(shape1, shape2)
         algo.Build()
         return algo.Shape() if algo.IsDone() else shape1
+
+    def get_edge_curve_type(self, edge: Any) -> str:
+        """Returns 'circle', 'line', 'other', or 'unknown'."""
+        from OCC.Core.BRepAdaptor import BRepAdaptor_Curve
+        from OCC.Core.GeomAbs import GeomAbs_Circle, GeomAbs_Line
+        try:
+            curve = BRepAdaptor_Curve(edge)
+            t = curve.GetType()
+            if t == GeomAbs_Circle:
+                return "circle"
+            elif t == GeomAbs_Line:
+                return "line"
+            else:
+                return "other"
+        except Exception:
+            return "unknown"
```

### Diff 2: `smbs_core/Geometry/feature_extractor.py`

```diff
diff --git a/smbs_core/Geometry/feature_extractor.py b/smbs_core/Geometry/feature_extractor.py
index 0c41830..ae5fa0d 100644
--- a/smbs_core/Geometry/feature_extractor.py
+++ b/smbs_core/Geometry/feature_extractor.py
@@ -93,7 +93,12 @@ class FeatureExtractor:
             return (0.0, 0.0, 1.0)
         return (v[0] / mag, v[1] / mag, v[2] / mag)
 
-    def extract_features(self, shape: Any, known_thickness: Optional[float] = None) -> Dict[str, Any]:
+    def extract_features(
+        self,
+        shape: Any,
+        known_thickness: Optional[float] = None,
+        diagnostics: Optional[List[Dict[str, Any]]] = None,
+    ) -> Dict[str, Any]:
         if shape is None:
             raise FeatureExtractionError("Cannot extract features from None shape.")
 
@@ -108,7 +113,7 @@ class FeatureExtractor:
             else:
                 thickness = self._detect_thickness(shape)
 
-        bends = self._detect_bends(shape, thickness)
+        bends = self._detect_bends(shape, thickness, diagnostics=diagnostics)
 
         if not bends:
             raise FeatureExtractionError("No valid bend features detected in shape.")
@@ -184,7 +189,12 @@ class FeatureExtractor:
 
         return None
 
-    def _detect_bends(self, shape: Any, thickness: Optional[float]) -> Dict[str, Dict[str, Any]]:
+    def _detect_bends(
+        self,
+        shape: Any,
+        thickness: Optional[float],
+        diagnostics: Optional[List[Dict[str, Any]]] = None,
+    ) -> Dict[str, Dict[str, Any]]:
         if hasattr(shape, "get") and shape.get("type") in ("mock_shape", "mock_box"):
             return {
                 "BEND_01": {
@@ -262,7 +272,9 @@ class FeatureExtractor:
                 })
 
         panels, face_to_panel = self._group_panels(shape, adj_map, faces, unique_cyls, thickness)
-        edges = self._build_panel_edges(unique_cyls, panels, face_to_panel, faces, adj_map)
+        edges = self._build_panel_edges(
+            unique_cyls, panels, face_to_panel, faces, adj_map, shape=shape, diagnostics=diagnostics
+        )
         bends = self._canonicalize_bends(edges, panels, thickness)
         self._last_panels = panels
         return bends
@@ -337,17 +349,28 @@ class FeatureExtractor:
 
                 # Anti-parallel opposite sheet faces separated by thickness
                 is_thickness_pair = False
+                v_perp_sq = (
+                    (v[0] - (v[0]*ni[0] + v[1]*ni[1] + v[2]*ni[2])*ni[0])**2 +
+                    (v[1] - (v[0]*ni[0] + v[1]*ni[1] + v[2]*ni[2])*ni[1])**2 +
+                    (v[2] - (v[0]*ni[0] + v[1]*ni[1] + v[2]*ni[2])*ni[2])**2
+                )
+                dist_perp = math.sqrt(v_perp_sq)
+
                 if thickness is not None:
-                    is_thickness_pair = abs(dot + 1.0) < 1e-2 and abs(dist_along_n - thickness) < 0.25 * thickness
+                    is_thickness_pair = (
+                        abs(dot + 1.0) < 1e-2 and
+                        abs(dist_along_n - thickness) < 0.25 * thickness and
+                        dist_perp < 2.0 * thickness
+                    )
                 else:
-                    is_thickness_pair = abs(dot + 1.0) < 1e-2 and 0.2 <= dist_along_n <= 25.0
+                    is_thickness_pair = abs(dot + 1.0) < 1e-2 and 0.2 <= dist_along_n <= 25.0 and dist_perp < 5.0
 
                 if is_thickness_pair:
                     union(fi_id, fj_id)
                     has_pair[fi_id] = True
                     has_pair[fj_id] = True
-                # Coplanar segments on the same side
-                elif abs(dot - 1.0) < 1e-2 and dist_along_n < 1e-2:
+                # Coplanar segments on the same side (only merge if adjacent / connected)
+                elif abs(dot - 1.0) < 1e-2 and dist_along_n < 1e-2 and (fj_id in adj_map.get(fi_id, set())):
                     union(fi_id, fj_id)
 
         # 3. Filter out un-paired faces (keep only panels that form physical sheet thickness pairs)
@@ -404,7 +427,44 @@ class FeatureExtractor:
         face_to_panel: Dict[str, str],
         faces: List[Any],
         adj_map: Dict[str, Set[str]],
+        shape: Optional[Any] = None,
+        diagnostics: Optional[List[Dict[str, Any]]] = None,
     ) -> List[dict]:
+        edge_to_faces = None
+        if shape is not None and not getattr(shape, "IsNull", lambda: True)():
+            try:
+                from OCC.Core.TopExp import topexp
+                from OCC.Core.TopAbs import TopAbs_FACE, TopAbs_EDGE
+                from OCC.Core.TopTools import TopTools_IndexedDataMapOfShapeListOfShape
+                edge_to_faces = TopTools_IndexedDataMapOfShapeListOfShape()
+                topexp.MapShapesAndAncestors(shape, TopAbs_EDGE, TopAbs_FACE, edge_to_faces)
+            except Exception as e:
+                logger.debug("MapShapesAndAncestors failed: %s", e)
+                edge_to_faces = None
+
+        def find_common_edges(f1: Any, f2: Any) -> List[Any]:
+            if edge_to_faces is None:
+                return []
+            try:
+                from OCC.Core.TopExp import TopExp_Explorer
+                from OCC.Core.TopAbs import TopAbs_EDGE
+                from OCC.Core.TopTools import TopTools_ListIteratorOfListOfShape
+                common = []
+                exp = TopExp_Explorer(f1, TopAbs_EDGE)
+                while exp.More():
+                    e = exp.Current()
+                    adj_list = edge_to_faces.FindFromKey(e)
+                    it = TopTools_ListIteratorOfListOfShape(adj_list)
+                    while it.More():
+                        if it.Value().IsSame(f2):
+                            common.append(e)
+                            break
+                        it.Next()
+                    exp.Next()
+                return common
+            except Exception:
+                return []
+
         edges_by_pair = {}
         all_cyls = []
         for i, f in enumerate(faces):
@@ -416,8 +476,23 @@ class FeatureExtractor:
         for u in all_cyls:
             c_id = u['id']
             c_data = u['data']
+            cyl_face = faces[int(c_id)]
             adj_p_faces = [fid for fid in adj_map.get(c_id, set()) if fid in face_to_panel]
-            connected_panels = sorted(list({face_to_panel[fid] for fid in adj_p_faces}))
+
+            if edge_to_faces is not None:
+                real_panels = []
+                for fid in adj_p_faces:
+                    flat_face = faces[int(fid)]
+                    common_edges = find_common_edges(cyl_face, flat_face)
+                    # A true sheet flange panel meets the bend cylinder along a straight line edge (generatrix).
+                    # Transverse corner relief cuts / notches meet along circular arcs (or other non-line edges).
+                    is_real_panel = any(self.backend.get_edge_curve_type(e) == "line" for e in common_edges)
+                    if is_real_panel:
+                        real_panels.append(face_to_panel[fid])
+                connected_panels = sorted(list(set(real_panels)))
+            else:
+                connected_panels = sorted(list({face_to_panel[fid] for fid in adj_p_faces}))
+
             if len(connected_panels) == 2:
                 p_a, p_b = connected_panels
                 pair_key = (p_a, p_b)
@@ -442,6 +517,22 @@ class FeatureExtractor:
 
                 if pair_key not in edges_by_pair or edge_cand['radius'] < edges_by_pair[pair_key]['radius']:
                     edges_by_pair[pair_key] = edge_cand
+            elif len(connected_panels) > 2:
+                if diagnostics is not None:
+                    diagnostics.append({
+                        "code": "BEND_TOUCHES_MULTIPLE_PANELS",
+                        "message": f"cyl {c_id} touches {len(connected_panels)} real panels via line edges: {connected_panels}",
+                        "level": "WARNING",
+                        "stage": "feature_extraction"
+                    })
+            elif len(connected_panels) < 2:
+                if diagnostics is not None:
+                    diagnostics.append({
+                        "code": "BEND_TOUCHES_INSUFFICIENT_PANELS",
+                        "message": f"cyl {c_id} touches {len(connected_panels)} real panels via line edges",
+                        "level": "WARNING",
+                        "stage": "feature_extraction"
+                    })
 
         return list(edges_by_pair.values())
```

---

## 7. Открытые вопросы

1. **Нелинейный маппинг развёртки для разветвлённых графов (Tree/Star)**:
   - Извлечение канонических 3D-гибов теперь работает безупречно для всех 16 деталей (8 из 8 и 10 из 10).
   - В топологическом сопоставлении линий гиба с 2D-развёрткой `TopologyWorker._map_raw_bends_to_flat` в текущей реализации поддерживает 1D-линейные цепочки (`is_linear_chain`). Для звездчатых графов (`box_*`, `sq*`) и разветвлённых структур сейчас штатно выставляется диагностическое предупреждение `NON_LINEAR_TOPOLOGY_NOT_SUPPORTED` и `bend_line_flat_pattern` остаётся пустым. В рамках следующей задачи можно обобщить сопоставление на древовидные/произвольные графы.
2. **Переход к Task 1.0.1b-1 (реализация fold_from_canonical_part)**:
   - Входные данные `04_canonical_part.json` теперь содержат полные и точные наборы гибов и панелей для всех деталей каталога.
