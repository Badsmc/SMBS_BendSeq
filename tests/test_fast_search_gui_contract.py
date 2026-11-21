"""Pure AST contracts for the explicit, opt-in SMBS FAST UI.

Not a substitute for exercising Qt in a real FreeCAD installation.
"""
from __future__ import annotations

import ast
from pathlib import Path
import unittest


SOURCE = (Path(__file__).resolve().parents[1] /
          "freecad_smbs" / "commands" / "pressbrake_2d.py").read_text("utf-8")
TREE = ast.parse(SOURCE)


def function(name):
    matches = [n for n in ast.walk(TREE)
               if isinstance(n, ast.FunctionDef) and n.name == name]
    if len(matches) != 1:
        raise AssertionError(f"Expected one {name}, got {len(matches)}")
    return ast.get_source_segment(SOURCE, matches[0])


class FastSearchGUIContracts(unittest.TestCase):
    def test_visible_button_and_explicit_user_action(self):
        self.assertIn('setObjectName("SMBS_FastSequenceSearch")', SOURCE)
        self.assertIn(
            "self.fast_search_btn.clicked.connect(self._run_fast_sequence_search)",
            SOURCE,
        )
        self.assertIn("playback_layout.addWidget(self.fast_search_btn)", SOURCE)

    def test_search_requires_stopped_editable_session(self):
        body = function("_run_fast_sequence_search")
        self.assertIn("not self.session.can_reorder()", body)
        self.assertIn("self.timer.isActive()", body)
        self.assertIn("limit_seconds=1.0", body)
        self.assertIn("beam_width=32", body)
        self.assertIn("max_candidates=5", body)
        self.assertIn("self._last_fast_candidates_report = report", body)

    def test_results_dialog_is_nonblocking_and_apply_is_deferred(self):
        body = function("_run_fast_sequence_search")
        self.assertIn("dialog.open()", body)
        self.assertIn("dialog.finished.connect(on_finished)", body)
        self.assertIn("QtCore.QTimer.singleShot(", body)
        self.assertIn("self._apply_fast_sequence_candidate(", body)
        self.assertIn('setObjectName("SMBS_FAST_ResultsDialog")', body)
        self.assertIn("Gui.getMainWindow()", body)
        self.assertNotIn("dialog.exec()", body)
        self.assertNotIn("dialog.exec_()", body)
        self.assertNotIn("controller.reorder_operations(", body)

    def test_bounded_2d_runs_after_fast_dialog_and_preserves_source_indices(self):
        chooser = function("_run_fast_sequence_search")
        self.assertIn("screen_fast_shortlist(", chooser)
        self.assertIn("budget_seconds=3.0", chooser)
        self.assertIn("QtCore.QTimer.singleShot(0, check_fast_shortlist_2d)", chooser)
        self.assertLess(
            chooser.index("dialog.open()"),
            chooser.index("QtCore.QTimer.singleShot(0, check_fast_shortlist_2d)"),
        )
        self.assertIn("choices.setSortingEnabled(True)", chooser)
        self.assertIn("choices.sortItems(", chooser)
        self.assertIn("selected_source_index()", chooser)
        self.assertIn("int(QtCore.Qt.UserRole) + 1", chooser)
        self.assertIn("selected = all_candidates[index]", chooser)
        self.assertIn('"FAST_SHORTLIST_2D_SCREEN"', chooser)
        self.assertIn('setObjectName("SMBS_FastShortlist2DScreen")', chooser)

    def test_all_outer_sides_feedback_research_and_correct_index(self):
        chooser = function("_run_fast_sequence_search")
        self.assertIn("search_all_outer_sides(", chooser)
        self.assertIn("outline_vertices_xy", chooser)
        self.assertIn("outline_edge_polylines_xy", chooser)
        self.assertIn("outer_edge_polylines_xy=outer_cad_edges", chooser)
        self.assertIn("fixed_orientations=pinned", chooser)
        self.assertIn('"FAST_FORCED_ALL_SIDES_RESULT"', chooser)
        self.assertIn("all_candidates.append(found.candidate)", chooser)
        self.assertIn("transition_cache=screen_cache", chooser)
        self.assertIn("selected = all_candidates[index]", chooser)
        self.assertIn("selected_source_index()", chooser)
        self.assertIn("choices.selectRow(row)", chooser)
        self.assertIn('"FAST_SHORTLIST_2D_SCREEN_INITIAL"', chooser)

    def test_sortable_table_and_honest_preliminary_status(self):
        chooser = function("_run_fast_sequence_search")
        self.assertIn("QtWidgets.QTableWidget(dialog)", chooser)
        self.assertIn('setObjectName("SMBS_FastCandidateTable")', chooser)
        for column in ('"№"', '"2D"', '"Вес"', '"Старт"',
                       '"1-й гиб"', '"180°"', '"2D штраф"',
                       '"Порядок"', '"Причина"'):
            self.assertIn(column, chooser)
        self.assertIn("class _FastSortableItem", chooser)
        self.assertIn("isinstance(left, (float, int))", chooser)
        self.assertIn("choices.setSortingEnabled(True)", chooser)
        self.assertIn("int(QtCore.Qt.UserRole) + 1, source_index", chooser)
        self.assertIn("selected_source_index()", chooser)
        self.assertIn('"✅"', chooser)
        self.assertIn('"❌"', chooser)
        self.assertIn('"⚠️"', chooser)
        self.assertIn("3D: НЕ ПРОВЕРЕНО", chooser)

    def test_feedback_rerun_checks_new_candidates_first(self):
        chooser = function("_run_fast_sequence_search")
        self.assertIn("search_feedback_recovery(", chooser)
        self.assertIn('"FAST_FEEDBACK_RECOVERY_RESULT"', chooser)
        self.assertIn("budget_seconds=1.5", chooser)
        self.assertIn("priority_candidate_indices=range(", chooser)
        self.assertIn("feedback_rounds[0] < 3", chooser)
        self.assertIn("fixed_orientations=pinned", chooser)
        self.assertIn("selected = all_candidates[index]", chooser)

    def test_never_auto_apply_candidate(self):
        chooser = function("_run_fast_sequence_search")
        dialog_close = chooser.index("dialog.finished.connect(on_finished)")
        deferred = chooser.index("self._apply_fast_sequence_candidate(")
        self.assertLess(deferred, dialog_close)  # inside finished handler
        applying = function("_apply_fast_sequence_candidate")
        self.assertIn("controller.reorder_operations(selected.operation_ids)", applying)
        self.assertIn("_on_staged_plan_changed(", applying)
        self.assertIn("UNVERIFIED_3D_NOT_CHECKED", applying)
        self.assertIn("FAST_APPLY_SKIPPED", applying)
        self.assertIn("self._playback_alive()", applying)
        self.assertNotIn("_validate_export_manual_order(", applying)

    def test_preserves_per_stage_180_degree_choices(self):
        chooser = function("_run_fast_sequence_search")
        applying = function("_apply_fast_sequence_candidate")
        self.assertIn("self._manual_operation_orientation_overrides", chooser)
        self.assertIn("self._manual_orientation_overrides", chooser)
        self.assertIn("orientation = selected.orientations()", applying)

    def test_fast_poses_are_not_treated_as_explicit_user_pins(self):
        chooser = function("_run_fast_sequence_search")
        applying = function("_apply_fast_sequence_candidate")
        self.assertIn("self._fast_advisory_orientation_keys", chooser)
        self.assertIn("set(selected.operation_ids) - set(pinned)", applying)
        self.assertIn("panel_vertices_xy=self.session.panel_vertices_xy", chooser)
        manual = function("_set_manual_loading_override")
        self.assertIn(
            "self._fast_advisory_orientation_keys.discard(operation_id)", manual
        )
        snapshot = function("_write_embedded_smbs_json")
        self.assertIn("fast_advisory_orientation_ids=", snapshot)
        restore = function("_restore_embedded_smbs_json")
        self.assertIn("fast_advisory_orientation_ids", restore)

    def test_fast_keeps_normal_editable_table_without_native_gauge(self):
        apply = function("_apply_fast_sequence_candidate")
        self.assertIn("_fast_apply_skip_table_gauge = True", apply)
        staged = function("_on_staged_plan_changed")
        self.assertIn("_populate_operations_fast()", staged)
        self.assertIn("self.press_cycle = None", staged)
        refresh = function("_populate_operations_fast")
        self.assertIn("self._populate_operations()", refresh)
        self.assertNotIn("QtWidgets.QTreeWidgetItem()", refresh)

    def test_complete_selected_orientations_skip_cad_preview(self):
        loading = function("_effective_loading_orientation_map")
        self.assertIn("self._manual_orientation_overrides", loading)
        self.assertIn("self._manual_operation_orientation_overrides", loading)
        self.assertIn("if all(bend_id in known for bend_id in order):", loading)
        self.assertLess(
            loading.index("if all(bend_id in known for bend_id in order):"),
            loading.index("self._default_loading_orientation_map(order)"),
        )

    def test_lifetime_guard_when_host_clears_ui_during_gauge_refresh(self):
        apply = function("_apply_fast_sequence_candidate")
        self.assertIn("_fast_apply_skip_table_gauge = True", apply)
        self.assertIn("_fast_apply_skip_table_gauge = False", apply)
        self.assertIn("FAST_APPLY_HOST_DESTROYED", apply)
        table = function("_format_operation_row")
        self.assertIn('getattr(self, "_fast_apply_skip_table_gauge", False)', table)
        refresh = function("_update_operations_visual_state")
        self.assertIn("host_destroyed_during_row_format", refresh)
        self.assertIn("if self._qt_is_valid(self.operations):", refresh)
        staged = function("_on_staged_plan_changed")
        self.assertIn("STAGED_REFRESH_SKIPPED_HOST_DESTROYED", staged)


if __name__ == "__main__":
    unittest.main()
