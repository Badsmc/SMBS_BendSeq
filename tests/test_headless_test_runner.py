import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from smbs_core.kinematics2d import developer_debug_log as debug
from tools.run_headless_tests import main


class IsolatedTestRunnerTests(unittest.TestCase):
    def test_journals_are_isolated_and_environment_is_restored(self):
        saved = debug._JOURNAL
        directories = []

        def run(**kwargs):
            directory = Path(os.environ['SMBS_DEBUG_LOG_DIR'])
            directories.append(directory)
            journal = debug.get_debug_journal()
            self.assertEqual(journal.path.parent, directory)
            self.assertIn(str(os.getpid()), journal.path.name)
            self.assertTrue(journal.emit('ISOLATED_TEST'))
            return SimpleNamespace(result=SimpleNamespace(wasSuccessful=lambda: True))

        with patch.dict(os.environ, {'SMBS_DEBUG_LOG_DIR': 'previous'}):
            with patch('tools.run_headless_tests.unittest.main', side_effect=run):
                self.assertEqual(main(['tests.test_desktop_debug_log']), 0)
            self.assertEqual(os.environ['SMBS_DEBUG_LOG_DIR'], 'previous')
        self.assertIs(debug._JOURNAL, saved)
        self.assertFalse(directories[0].exists())

    def test_failed_tests_return_nonzero(self):
        result = SimpleNamespace(wasSuccessful=lambda: False)
        with patch('tools.run_headless_tests.unittest.main',
                   return_value=SimpleNamespace(result=result)):
            self.assertEqual(main(['tests.test_desktop_debug_log']), 1)

    def test_exception_restores_previous_journal(self):
        saved = debug._JOURNAL
        previous_env = os.environ.get('SMBS_DEBUG_LOG_DIR')
        with patch('tools.run_headless_tests.unittest.main', side_effect=RuntimeError('boom')):
            with self.assertRaisesRegex(RuntimeError, 'boom'):
                main([])
        self.assertIs(debug._JOURNAL, saved)
        self.assertEqual(os.environ.get('SMBS_DEBUG_LOG_DIR'), previous_env)
