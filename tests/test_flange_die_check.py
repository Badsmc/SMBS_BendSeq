from types import SimpleNamespace as NS
import ast
from pathlib import Path
import unittest
from freecad.bendseq.core.kinematics2d.flange_die_check import assess_flanges, die_opening_mm


class FlangeDieTest(unittest.TestCase):
    def test_changing_die_checks_all_bends_not_only_active_bend(self):
        session = NS(canonical_order=('B01', 'B02', 'B03'),
                     active_bend_id='B01', local_bend_dimensions_mm={'B01': 50, 'B02': 10, 'B03': 17})
        self.assertEqual(assess_flanges(session, NS(v_opening_mm=8))['short_bends'], ())
        self.assertEqual(assess_flanges(session, NS(v_opening_mm=32))['short_bends'],
                         (('B02', 10), ('B03', 17)))

    def test_catalog_and_authoritative_datum_opening(self):
        self.assertEqual(die_opening_mm(NS(v_opening_mm='32,0')), 32)
        self.assertEqual(die_opening_mm(NS(die_datums=NS(v_opening_mm=24))), 24)
        self.assertEqual(die_opening_mm(NS(die_datums=NS(left_shoulder=(-16, 0), right_shoulder=(16, 0)))), 32)
        self.assertIsNone(die_opening_mm(NS(name='V32', bbox=(-50, -50, 50, 0))))

    def test_unknown_values_are_explicitly_reported(self):
        check = assess_flanges(NS(canonical_order=('B01',), local_bend_dimensions_mm={}), NS())
        self.assertIsNone(check['opening_mm'])
        self.assertEqual(check['unknown_bends'], ('B01',))

    def test_selection_refreshes_table_and_requests_notification(self):
        source = Path(__file__).resolve().parents[1] / 'freecad/bendseq/commands/pressbrake_2d.py'
        tree = ast.parse(source.read_text())
        method = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == 'select_die')
        statements = ast.unparse(method)
        self.assertIn('self._update_operations_visual_state()', statements)
        self.assertIn('self._refresh_flange_warning(notify=True)', statements)

    def test_banner_and_popup_list_inactive_short_bends_and_do_not_repeat(self):
        from types import MethodType
        from freecad.bendseq.ui.localization import tr
        source = Path(__file__).resolve().parents[1] / 'freecad/bendseq/commands/pressbrake_2d.py'
        tree = ast.parse(source.read_text())
        method = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
                      and n.name == '_refresh_flange_warning')
        popups, events, display = [], [], {}
        scope = dict(tr=lambda key, **kw: tr(key, language='en', **kw),
                     _desktop_debug_event=lambda *a, **kw: events.append(kw),
                     _qt=lambda: (None, None, NS(QMessageBox=NS(warning=lambda *args: popups.append(args)))))
        exec(compile(ast.Module(body=[method], type_ignores=[]), '<test>', 'exec'), scope)
        workspace = NS(session=NS(canonical_order=('B01', 'B02'), active_bend_id='B01',
                                 local_bend_dimensions_mm={'B01': 50, 'B02': 10}),
                       tooling_setup=NS(die=NS(tool_id='V32', v_opening_mm=32)),
                       _flange_warning_signature=None, _flange_warning_notified=None,
                       _flange_warning_banner=NS(setText=lambda v: display.update(text=v),
                                                setVisible=lambda v: display.update(visible=v)))
        refresh = MethodType(scope['_refresh_flange_warning'], workspace)
        refresh(notify=True)
        self.assertTrue(display['visible'])
        self.assertIn('B02', display['text'])
        self.assertEqual(len(popups), 1)
        refresh(notify=True)
        self.assertEqual(len(popups), 1)
        workspace.tooling_setup.die = NS(tool_id='V8', v_opening_mm=8)
        refresh(notify=True)
        self.assertFalse(display['visible'])
        self.assertEqual(len(events), 2)
