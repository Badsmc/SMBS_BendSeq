"""Separate experimental search window; never mutates the live session while searching."""
from contextlib import nullcontext
from dataclasses import replace
from types import SimpleNamespace
import traceback

from smbs_core.kinematics2d.bbox_autosearch import SearchOperation, search


class BBoxAutoSearchCommand:
    def GetResources(self):
        return {'MenuText': 'Автопоиск BBox',
                'ToolTip': 'Экспериментальный поиск последовательностей в отдельном окне'}

    def IsActive(self):
        from .pressbrake_2d import get_workspace_widget
        w = get_workspace_widget()
        return bool(w and w.session and not w._task_progress_busy)

    def Activated(self):
        from .pressbrake_2d import get_workspace_widget, _qt, _desktop_debug_event
        from smbs_core.kinematics2d.manual_physical_validation import check_manual_3d_sequence
        from smbs_core.kinematics2d.native_sequence_geometry_cache import NativeSequenceGeometryCache
        from smbs_core.kinematics2d.motion_trajectory_3d import AdaptiveSamplingConfig
        from smbs_core.kinematics2d.panel_kinematics import FoldState
        from smbs_core.kinematics2d.operation_cost import compute_workpiece_machine_setup, score_sequence
        import FreeCADGui as Gui
        QtCore, _, QtWidgets = _qt()
        w = get_workspace_widget()
        if not w or not w.session or w._task_progress_busy:
            return
        w._stop_timer()
        if not w.session.can_reorder():
            QtWidgets.QMessageBox.information(Gui.getMainWindow(), 'Автопоиск',
                                             'Нажмите Reset перед поиском.')
            return
        session = w.session
        staged = session.controller.has_stages
        previous = {}
        operations = []
        for op in session.controller.operation_steps:
            operations.append(SearchOperation(op.operation_id, op.bend_id, op.target_angle_deg,
                                              previous.get(op.bend_id)))
            previous[op.bend_id] = op.operation_id
        tooling = replace(w.tooling_setup)
        if tooling.punch is None or tooling.die is None:
            QtWidgets.QMessageBox.information(Gui.getMainWindow(), 'Автопоиск',
                                             'Сначала выберите пуансон и матрицу.')
            return
        if tooling.tool_assembly is not None:
            # Preserve real inventory, clamp support and the existing 15 mm
            # shortfall policy rather than substituting an infinite tool.
            tooling.tool_assembly = w._calculate_common_tool_assembly(
                tuple(dict.fromkeys(o.bend_id for o in operations)))
            tooling.match_active_bend_length = False
        weights = w._selected_operation_weights()
        from smbs_core.kinematics2d.mesh_search_collision import MeshOracle, mesh_search_mode
        mesh_oracle = [None]
        rows = []
        stopped = [False]
        busy = [False]
        dialog = QtWidgets.QDialog(Gui.getMainWindow())
        dialog.setWindowTitle('Автопоиск BBox — эксперимент')
        dialog.resize(880, 520)
        layout = QtWidgets.QVBoxLayout(dialog)
        note = QtWidgets.QLabel(
            'Mesh-поиск: допускается проникновение до 1 мм; результат предварительный.\n'
            'Зажимы, задний упор и перенос детали не проверены. Результат не является допуском к станку.')
        note.setWordWrap(True)
        layout.addWidget(note)
        options = QtWidgets.QHBoxLayout()
        seconds = QtWidgets.QSpinBox(); seconds.setRange(5, 3600); seconds.setValue(60)
        count = QtWidgets.QSpinBox(); count.setRange(1, 30); count.setValue(5)
        nodes = QtWidgets.QSpinBox(); nodes.setRange(1, 100000); nodes.setValue(1000)
        for label, spin in [('Секунд', seconds), ('Вариантов', count), ('Узлов', nodes)]:
            options.addWidget(QtWidgets.QLabel(label)); options.addWidget(spin)
        layout.addLayout(options)
        mesh_enabled = QtWidgets.QCheckBox('Mesh-поиск вместо OCC (допуск 1 мм)')
        mesh_enabled.setChecked(True)
        layout.addWidget(mesh_enabled)
        table = QtWidgets.QTableWidget(0, 4)
        table.setHorizontalHeaderLabels(['Последовательность (угол / поворот)', 'Вес (оценка)', 'Статус', 'Проверка'])
        table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(table)
        status = QtWidgets.QLabel('Выберите параметры и нажмите «Найти».'); layout.addWidget(status)
        buttons = QtWidgets.QHBoxLayout(); layout.addLayout(buttons)
        start = QtWidgets.QPushButton('Найти'); stop = QtWidgets.QPushButton('Остановить')
        verify = QtWidgets.QPushButton('Проверить выбранный OCC')
        apply = QtWidgets.QPushButton('Открыть в симуляции'); close = QtWidgets.QPushButton('Закрыть')
        for b in (start, stop, verify, apply, close): buttons.addWidget(b)
        apply.setEnabled(False); stop.setEnabled(False)
        timer = QtCore.QTimer(dialog)
        iterator = [None]
        geometry_cache = [None]
        step_cache = {}

        def evaluate(path, poses, complete, *, exact=False):
            if w._closing or w.session is not session:
                raise RuntimeError('Исходная сессия закрыта или заменена')
            # Costs use exactly the selected operation targets and loading poses.
            state = FoldState(session.fold_state.graph, session.fold_state.fixed_panel)
            setups = []
            for op in path:
                from smbs_core.kinematics2d.sequence_search_2d import _setup_with_scored_tool_lengths
                setup = compute_workpiece_machine_setup(state, op.bend_id,
                              reverse_axis=poses[op.operation_id])
                setups.append(_setup_with_scored_tool_lengths(
                    setup, tooling, tooling.match_active_bend_length))
                state.set_angle(op.bend_id, op.target_angle_deg)
            cost = score_sequence(tuple(o.bend_id for o in path), setups, weights).total
            if staged:
                return {'status': 'UNVERIFIED_STAGES', 'cost': cost,
                        'reason': 'Промежуточные углы OCC не поддержаны текущим валидатором'}
            order = tuple(o.bend_id for o in path)
            isolated = SimpleNamespace(fold_state=session.fold_state, panel_build=session.panel_build,
                                       bend_order=order)
            use_mesh = not exact and mesh_enabled.isChecked()
            # The earlier loading poses do not change the folded sheet at this
            # step. Keep the ordered bend history and the current loading pose.
            step_key = (order, poses[path[-1].operation_id])
            cached = None if exact else step_cache.get(step_key)
            if cached is None:
                with mesh_search_mode(mesh_oracle[0]) if use_mesh else nullcontext():
                    report = check_manual_3d_sequence(
                        isolated, tooling, order,
                        match_active_bend_length=tooling.match_active_bend_length,
                        allow_end_zone_collision=tooling.allow_end_zone_collision,
                        end_zone_length_mm=tooling.end_zone_length_mm,
                        reverse_axis_by_bend={o.bend_id: poses[o.operation_id] for o in path},
                        auto_orientation=False, collision_forensics=exact,
                        search_last_step_only=not exact,
                        search_geometry_cache=None if exact else geometry_cache[0],
                        config=AdaptiveSamplingConfig(coarse_occ_screen=not exact,
                            initial_clearance_mm=float(w.cycle_config.initial_clearance_mm),
                            max_phase_time_s=min(float(seconds.value()), 30.0),
                            max_evaluated_states_per_phase=32))
                if not exact:
                    step_cache[step_key] = report
            else:
                report = cached
            report = dict(report)
            report['reverse_axis_by_bend'] = {
                o.bend_id: poses[o.operation_id] for o in path
            }
            if use_mesh:
                report['search_engine'] = 'SAMPLED_MESH'
                report['penetration_allowance_mm'] = 1.0
                report['mesh_deflection_mm'] = 0.1
                report['mesh_queries'] = mesh_oracle[0].queries
                report['mesh_builds'] = mesh_oracle[0].builds
                if report['status'] != 'COLLISION':
                    report['status'] = 'UNVERIFIED_MESH_CANDIDATE'
                report['reason'] = 'MESH_SAMPLED_1MM; OCC verification required; ' + report.get('reason', '')
            report['cost'] = cost
            return report

        def finish(message):
            timer.stop(); busy[0] = False
            mesh_enabled.setEnabled(True)
            start.setEnabled(True); stop.setEnabled(False)
            apply.setEnabled(bool(rows)); verify.setEnabled(bool(rows)); status.setText(message)
            for spin in (seconds, count, nodes): spin.setEnabled(True)
            _desktop_debug_event('BBOX_SEARCH_FINISHED', message=message, results=len(rows))

        def tick():
            try:
                event = next(iterator[0])
                if event['kind'] == 'progress':
                    status.setText('Узлов: {} · глубина: {}'.format(event['nodes'], event['depth']))
                elif event['kind'] == 'result':
                    rows.append(event); i = table.rowCount(); table.insertRow(i)
                    text = ' → '.join('{}: {}° / {}°'.format(o.operation_id, o.target_angle_deg,
                             180 if event['poses'][o.operation_id] else 0) for o in event['operations'])
                    report = event['report']
                    for col, value in enumerate((text, round(event['cost'], 2), report['status'],
                                                report.get('reason', ''))):
                        table.setItem(i, col, QtWidgets.QTableWidgetItem(str(value)))
                    _desktop_debug_event('BBOX_SEARCH_RESULT', sequence=text, report=report)
                else:
                    finish('{} · найдено {}'.format(event['reason'], len(rows)))
            except StopIteration:
                finish('Поиск завершён')
            except Exception as exc:
                _desktop_debug_event('BBOX_SEARCH_ERROR', error=str(exc), traceback=traceback.format_exc())
                finish('Ошибка: ' + str(exc))

        def begin():
            geometry_cache[0] = NativeSequenceGeometryCache(max_full_sheets=16)
            step_cache.clear()
            mesh_oracle[0] = MeshOracle(tolerance_mm=1.0, deflection_mm=0.1)
            mesh_enabled.setEnabled(False)
            rows.clear(); table.setRowCount(0); stopped[0] = False; busy[0] = True
            start.setEnabled(False); stop.setEnabled(True); apply.setEnabled(False); verify.setEnabled(False)
            for spin in (seconds, count, nodes): spin.setEnabled(False)
            iterator[0] = search(operations, evaluate, max_nodes=nodes.value(),
                                 max_results=count.value(), seconds=seconds.value(),
                                 cancelled=lambda: stopped[0] or w._closing)
            _desktop_debug_event('BBOX_SEARCH_STARTED', staged=staged, operations=len(operations))
            timer.start(0)

        def use():
            i = table.currentRow()
            if busy[0] or i < 0 or w._closing or w.session is not session:
                return
            result = rows[i]
            ops = result['operations']
            if staged:
                w._apply_reorder(lambda: session.controller.reorder_operations(
                    [o.operation_id for o in ops]))
                w._manual_operation_orientation_overrides.update(result['poses'])
            else:
                if not w._apply_manual_order([o.bend_id for o in ops]): return
                w._manual_orientation_overrides.update(
                    {o.bend_id: result['poses'][o.operation_id] for o in ops})
            if tooling.tool_assembly is not None:
                w._apply_tool_assembly(tooling.tool_assembly)
            w._manual_orientation_order = ()
            w._ergonomic_loading_cache_key = None
            w._last_manual_sequence_report = None
            w._clear_manual_check_feedback()
            w._configure_for_active_bend(); w._draw_flat_pattern(); w.draw()
            if w._smbs_allow_auto_embed:
                w._write_embedded_smbs_json(w._bound_smbs_document())
            dialog.accept()

        def verify_selected():
            i = table.currentRow()
            if busy[0] or i < 0:
                return
            if staged:
                status.setText('OCC-проверка предгибов и догибов пока не поддержана.')
                return
            result = rows[i]
            try:
                report = evaluate(result['operations'], result['poses'], True, exact=True)
                result['report'] = report
                table.setItem(i, 2, QtWidgets.QTableWidgetItem(report['status']))
                table.setItem(i, 3, QtWidgets.QTableWidgetItem(report.get('reason', '')))
                _desktop_debug_event('BBOX_SELECTED_VERIFY', report=report)
                status.setText('Проверка выбранного варианта: ' + report['status'])
            except Exception as exc:
                status.setText('Ошибка проверки: ' + str(exc))
                _desktop_debug_event('BBOX_SELECTED_VERIFY_ERROR', error=str(exc),
                                     traceback=traceback.format_exc())

        verify.setEnabled(False)
        verify.clicked.connect(verify_selected)
        timer.timeout.connect(tick)
        start.clicked.connect(begin); stop.clicked.connect(lambda: stopped.__setitem__(0, True))
        apply.clicked.connect(use); close.clicked.connect(dialog.reject)
        dialog.finished.connect(lambda *_: (stopped.__setitem__(0, True), timer.stop()))
        dialog.exec() if hasattr(dialog, 'exec') else dialog.exec_()
